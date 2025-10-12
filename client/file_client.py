from __future__ import annotations

import asyncio
import json
import struct
from typing import AsyncIterator, Awaitable, Callable, Optional, Tuple

from fastapi import UploadFile

from shared.protocol import DEFAULT_FILE_PORT

_LENGTH_STRUCT = struct.Struct("!I")

ProgressCallback = Callable[[int, int], Awaitable[None]]


class FileClient:
    """Client helper for uploading and downloading files via the file server."""

    def __init__(self, host: str, port: int = DEFAULT_FILE_PORT, username: str = "") -> None:
        self._host = host
        self._port = port
        self._username = username

    async def upload(self, upload_file: UploadFile, *, progress: Optional[ProgressCallback] = None) -> str:
        filename = upload_file.filename or "upload.bin"
        await upload_file.seek(0)
        total_size = upload_file.size or 0
        if total_size == 0:
            # UploadFile.size may be missing; fallback by reading once.
            content = await upload_file.read()
            total_size = len(content)
            await upload_file.seek(0)
            buffered = content
        else:
            buffered = b""

        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            header = {
                "action": "upload",
                "username": self._username,
                "filename": filename,
                "total_size": total_size,
            }
            await self._send_json(writer, header)

            sent = 0
            if buffered:
                await self._write_chunk(writer, buffered)
                sent += len(buffered)
                if progress:
                    await progress(sent, total_size)
            else:
                while True:
                    chunk = await upload_file.read(64 * 1024)
                    if not chunk:
                        break
                    await self._write_chunk(writer, chunk)
                    sent += len(chunk)
                    if progress:
                        await progress(sent, total_size)
            await self._write_chunk(writer, b"")

            response = await self._read_json(reader)
            if response.get("status") != "ok":
                raise RuntimeError(response.get("reason", "upload_failed"))
            return str(response["file_id"])
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def download(self, file_id: str) -> Tuple[dict, AsyncIterator[bytes]]:
        reader, writer = await asyncio.open_connection(self._host, self._port)

        header = {"action": "download", "file_id": file_id}
        await self._send_json(writer, header)

        response = await self._read_json(reader)
        if response.get("status") != "ok":
            writer.close()
            await writer.wait_closed()
            raise FileNotFoundError(file_id)

        async def stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    chunk = await self._read_chunk(reader)
                    if chunk is None:
                        break
                    yield chunk
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        return response, stream()

    async def _send_json(self, writer: asyncio.StreamWriter, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        writer.write(_LENGTH_STRUCT.pack(len(payload)))
        writer.write(payload)
        await writer.drain()

    async def _read_json(self, reader: asyncio.StreamReader) -> dict:
        length_bytes = await reader.readexactly(_LENGTH_STRUCT.size)
        (length,) = _LENGTH_STRUCT.unpack(length_bytes)
        payload = await reader.readexactly(length)
        return json.loads(payload.decode("utf-8"))

    async def _write_chunk(self, writer: asyncio.StreamWriter, chunk: bytes) -> None:
        writer.write(_LENGTH_STRUCT.pack(len(chunk)))
        if chunk:
            writer.write(chunk)
        await writer.drain()

    async def _read_chunk(self, reader: asyncio.StreamReader) -> Optional[bytes]:
        length_bytes = await reader.readexactly(_LENGTH_STRUCT.size)
        (length,) = _LENGTH_STRUCT.unpack(length_bytes)
        if length == 0:
            return None
        return await reader.readexactly(length)
