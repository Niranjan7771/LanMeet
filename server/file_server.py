from __future__ import annotations

import asyncio
import json
import logging
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import aiofiles

from shared.protocol import ControlAction, FileOffer

from .session_manager import SessionManager

logger = logging.getLogger(__name__)

_LENGTH_STRUCT = struct.Struct("!I")


@dataclass(slots=True)
class StoredFile:
    file_id: str
    filename: str
    total_size: int
    uploader: str
    path: Path


class FileServer:
    """Handles TCP uploads and downloads for file sharing."""

    def __init__(
        self,
        host: str,
        port: int,
        storage_dir: Path,
        session_manager: SessionManager,
    ) -> None:
        self._host = host
        self._port = port
        self._storage_dir = storage_dir
        self._session_manager = session_manager
        self._server: Optional[asyncio.AbstractServer] = None
        self._files: Dict[str, StoredFile] = {}
        self._lock = asyncio.Lock()
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_connection, self._host, self._port)
        sockets = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        logger.info("File server listening on %s", sockets)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        await self.cleanup_storage()

    async def list_files(self) -> list[FileOffer]:
        async with self._lock:
            return [FileOffer(file.file_id, file.filename, file.total_size, file.uploader) for file in self._files.values()]

    async def get_file(self, file_id: str) -> Optional[StoredFile]:
        async with self._lock:
            return self._files.get(file_id)

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            header = await self._read_json(reader)
            action = header.get("action")
            if action == "upload":
                await self._handle_upload(header, reader, writer)
            elif action == "download":
                await self._handle_download(header, writer)
            else:
                raise ValueError(f"Unsupported file action: {action}")
        except asyncio.IncompleteReadError:
            logger.warning("File transfer connection incomplete from %s", peer)
        except Exception:
            logger.exception("Error while handling file transfer from %s", peer)
            await self._send_json(writer, {"status": "error", "reason": "server_error"})
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_upload(self, header: dict, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        username = header.get("username")
        filename = header.get("filename")
        total_size = int(header.get("total_size", 0))
        if not username or not filename or total_size <= 0:
            raise ValueError("Invalid upload header")
        if await self._session_manager.get_client(username) is None:
            raise PermissionError("Uploader not connected")

        file_id = uuid.uuid4().hex
        target_path = self._storage_dir / file_id
        received = 0

        async with aiofiles.open(target_path, "wb") as file_obj:
            while received < total_size:
                chunk = await self._read_chunk(reader)
                if chunk is None:
                    break
                await file_obj.write(chunk)
                received += len(chunk)
                await self._session_manager.broadcast(
                    ControlAction.FILE_PROGRESS,
                    {
                        "file_id": file_id,
                        "filename": filename,
                        "uploader": username,
                        "received": received,
                        "total_size": total_size,
                    },
                )

        if received != total_size:
            target_path.unlink(missing_ok=True)
            raise IOError("File upload interrupted")

        stored = StoredFile(
            file_id=file_id,
            filename=filename,
            total_size=total_size,
            uploader=username,
            path=target_path,
        )
        async with self._lock:
            self._files[file_id] = stored

        await self._send_json(writer, {"status": "ok", "file_id": file_id})
        offer = FileOffer(file_id, filename, total_size, username)
        await self._session_manager.broadcast(ControlAction.FILE_OFFER, offer.to_dict(), exclude={username})

    async def _handle_download(self, header: dict, writer: asyncio.StreamWriter) -> None:
        file_id = header.get("file_id")
        if not file_id:
            raise ValueError("file_id required for download")
        stored = await self.get_file(file_id)
        if stored is None:
            raise FileNotFoundError(file_id)

        await self._send_json(
            writer,
            {
                "status": "ok",
                "file_id": stored.file_id,
                "filename": stored.filename,
                "total_size": stored.total_size,
                "uploader": stored.uploader,
            },
        )

        async with aiofiles.open(stored.path, "rb") as file_obj:
            while True:
                chunk = await file_obj.read(64 * 1024)
                if not chunk:
                    break
                await self._write_chunk(writer, chunk)
        await self._write_chunk(writer, b"")

    async def _read_json(self, reader: asyncio.StreamReader) -> dict:
        length_bytes = await reader.readexactly(_LENGTH_STRUCT.size)
        (length,) = _LENGTH_STRUCT.unpack(length_bytes)
        payload = await reader.readexactly(length)
        return json.loads(payload.decode("utf-8"))

    async def _send_json(self, writer: asyncio.StreamWriter, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        writer.write(_LENGTH_STRUCT.pack(len(payload)))
        writer.write(payload)
        await writer.drain()

    async def _read_chunk(self, reader: asyncio.StreamReader) -> Optional[bytes]:
        length_bytes = await reader.readexactly(_LENGTH_STRUCT.size)
        (length,) = _LENGTH_STRUCT.unpack(length_bytes)
        if length == 0:
            return None
        return await reader.readexactly(length)

    async def cleanup_storage(self) -> None:
        async with self._lock:
            files = list(self._files.values())
            self._files.clear()
        for stored in files:
            try:
                stored.path.unlink(missing_ok=True)
            except Exception:
                logger.exception("Failed to delete stored file %s", stored.file_id)

    async def _write_chunk(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(_LENGTH_STRUCT.pack(len(data)))
        if data:
            writer.write(data)
        await writer.drain()
