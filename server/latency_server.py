from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class _LatencyProtocol(asyncio.DatagramProtocol):
    def __init__(self, expected_key: Optional[str]) -> None:
        self._expected_key = expected_key
        self._transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # pragma: no cover - network callback
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:  # pragma: no cover - network callback
        if self._transport is None:
            return
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.debug("Discarding malformed latency probe from %s", addr)
            return

        if self._expected_key:
            token = payload.get("pre_shared_key")
            if token != self._expected_key:
                logger.warning("Latency probe rejected due to invalid key from %s", addr)
                return

        response = {
            "timestamp_ms": payload.get("timestamp_ms"),
            "server_timestamp_ms": int(time.time() * 1000),
        }
        if "username" in payload:
            response["username"] = payload["username"]
        if "sequence" in payload:
            response["sequence"] = payload["sequence"]
        if "echo" in payload:
            response["echo"] = payload["echo"]

        try:
            message = json.dumps(response, separators=(",", ":")).encode("utf-8")
            self._transport.sendto(message, addr)
        except Exception:
            logger.exception("Failed to respond to latency probe for %s", addr)


class LatencyServer:
    """Responds to UDP latency probes from clients."""

    def __init__(self, *, pre_shared_key: Optional[str] = None) -> None:
        self._pre_shared_key = pre_shared_key
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[_LatencyProtocol] = None

    async def start(self, host: str, port: int) -> None:
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: _LatencyProtocol(self._pre_shared_key),
            local_addr=(host, port),
        )
        self._transport = transport
        self._protocol = protocol
        logger.info("Latency server listening on %s:%s", host, port)

    async def stop(self) -> None:
        if self._transport is None:
            return
        self._transport.close()
        self._transport = None
        self._protocol = None
        logger.info("Latency server stopped")
