"""Asterisk AudioSocket ingest for live calls (CLAUDE.md decision D-2).

Raw 8 kHz signed-linear PCM stays in this Python process. Three-second windows are resampled to
16 kHz, analysed, zeroed by the supplied analyser, and only the derived result is sent to the Go
session orchestrator. The queue is bounded and sheds its oldest window under backpressure so the
telephony socket keeps draining.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import numpy as np
from scipy.signal import resample_poly

ASTERISK_SAMPLE_RATE = 8_000
ANALYSIS_SAMPLE_RATE = 16_000
WINDOW_SECONDS = 3
HOP_SECONDS = 1
WINDOW_BYTES = ASTERISK_SAMPLE_RATE * WINDOW_SECONDS * 2
HOP_BYTES = ASTERISK_SAMPLE_RATE * HOP_SECONDS * 2
MAX_PENDING_WINDOWS = 3

FRAME_HANGUP = 0x00
FRAME_UUID = 0x01
FRAME_DTMF = 0x03
FRAME_AUDIO_8K = 0x10
FRAME_ERROR = 0xFF

log = logging.getLogger("voiceshield.audiosocket")


class GatewayLiveClient:
    """Sends metadata and derived windows to the loopback Go gateway."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("VOXGUARD_GATEWAY_URL", "http://127.0.0.1:8000")).rstrip("/")

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{self.base_url}{path}", json=body)
            response.raise_for_status()
            return response.json()

    async def start(self, call_id: str) -> None:
        await self._post(
            f"/internal/live-sessions/{call_id}",
            {
                "label": "Live Asterisk call",
                "language": os.getenv("VOXGUARD_LIVE_LANGUAGE", "en"),
            },
        )

    async def push(self, call_id: str, window: dict[str, Any]) -> None:
        await self._post(f"/internal/live-sessions/{call_id}/windows", window)

    async def close(self, call_id: str) -> None:
        await self._post(f"/internal/live-sessions/{call_id}/close")


class AudioSocketIngest:
    """Bounded AudioSocket TCP server with one analysis worker per call."""

    def __init__(
        self,
        analyser: Callable[[np.ndarray, str | None], dict[str, Any]],
        gateway: GatewayLiveClient | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.analyser = analyser
        self.gateway = gateway or GatewayLiveClient()
        self.host = host or os.getenv("VOXGUARD_AUDIOSOCKET_HOST", "127.0.0.1")
        self.port = port if port is not None else int(os.getenv("VOXGUARD_AUDIOSOCKET_PORT", "9019"))
        self.server: asyncio.AbstractServer | None = None
        self.error: str | None = None

    @property
    def state(self) -> str:
        return "listening" if self.server is not None else "unavailable"

    @property
    def bound_port(self) -> int | None:
        if self.server is None or not self.server.sockets:
            return None
        return int(self.server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        try:
            self.server = await asyncio.start_server(self._handle, self.host, self.port)
            self.error = None
        except OSError as error:
            # File simulation remains useful when live ingest cannot bind. Health reports the
            # degraded feature explicitly instead of making the whole ML sidecar unavailable.
            self.server = None
            self.error = str(error)
            log.error("AudioSocket listener unavailable: %s", error)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    @staticmethod
    async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytearray]:
        header = await reader.readexactly(3)
        length = int.from_bytes(header[1:3], "big")
        payload = bytearray(await reader.readexactly(length))
        return header[0], payload

    @staticmethod
    def _to_analysis_rate(raw: bytearray) -> np.ndarray:
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32)
        pcm /= 32768.0
        try:
            return resample_poly(pcm, ANALYSIS_SAMPLE_RATE, ASTERISK_SAMPLE_RATE).astype(np.float32)
        finally:
            pcm.fill(0)
            raw[:] = b"\x00" * len(raw)

    async def _consume(
        self,
        call_id: str,
        queue: asyncio.Queue[tuple[int, np.ndarray] | None],
        failed: asyncio.Event,
    ) -> None:
        while True:
            item = await queue.get()
            if item is None:
                return
            chunk_index, audio = item
            try:
                result = await asyncio.to_thread(self.analyser, audio, None)
                result = {"chunk_index": chunk_index, **result}
                await self.gateway.push(call_id, result)
            except Exception as error:  # connection remains drained; Go session closes degraded
                audio.fill(0)
                failed.set()
                log.error("Live analysis stopped for %s: %s", call_id, error)
                return

    async def _enqueue(
        self,
        queue: asyncio.Queue[tuple[int, np.ndarray] | None],
        item: tuple[int, np.ndarray],
    ) -> None:
        if queue.full():
            dropped = queue.get_nowait()
            if dropped is not None:
                dropped[1].fill(0)
        queue.put_nowait(item)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        call_id: str | None = None
        audio_buffer = bytearray()
        queue: asyncio.Queue[tuple[int, np.ndarray] | None] = asyncio.Queue(MAX_PENDING_WINDOWS)
        failed = asyncio.Event()
        consumer: asyncio.Task[None] | None = None
        chunk_index = 0
        try:
            while True:
                frame_type, payload = await self._read_frame(reader)
                try:
                    if frame_type == FRAME_HANGUP:
                        break
                    if frame_type == FRAME_UUID:
                        if call_id is not None or len(payload) != 16:
                            raise ValueError("AudioSocket UUID frame must be the first 16-byte identity frame")
                        call_id = str(UUID(bytes=bytes(payload)))
                        await self.gateway.start(call_id)
                        consumer = asyncio.create_task(self._consume(call_id, queue, failed))
                        continue
                    if frame_type == FRAME_DTMF:
                        continue
                    if frame_type == FRAME_ERROR:
                        raise ConnectionError("Asterisk reported an AudioSocket error")
                    if frame_type != FRAME_AUDIO_8K:
                        raise ValueError(f"unsupported AudioSocket frame type 0x{frame_type:02x}")
                    if call_id is None:
                        raise ValueError("AudioSocket audio arrived before the UUID frame")

                    audio_buffer.extend(payload)
                    while len(audio_buffer) >= WINDOW_BYTES:
                        raw_window = bytearray(audio_buffer[:WINDOW_BYTES])
                        audio_buffer[:HOP_BYTES] = b"\x00" * HOP_BYTES
                        del audio_buffer[:HOP_BYTES]
                        chunk_index += 1
                        audio = self._to_analysis_rate(raw_window)
                        if failed.is_set():
                            audio.fill(0)
                        else:
                            await self._enqueue(queue, (chunk_index, audio))
                finally:
                    payload[:] = b"\x00" * len(payload)
        except asyncio.IncompleteReadError:
            pass
        except Exception as error:
            log.warning("AudioSocket call %s ended: %s", call_id or "unidentified", error)
        finally:
            audio_buffer[:] = b"\x00" * len(audio_buffer)
            if consumer is not None:
                if not consumer.done():
                    while queue.full():
                        dropped = queue.get_nowait()
                        if dropped is not None:
                            dropped[1].fill(0)
                    queue.put_nowait(None)
                await consumer
            while not queue.empty():
                pending = queue.get_nowait()
                if pending is not None:
                    pending[1].fill(0)
            if call_id is not None:
                try:
                    await self.gateway.close(call_id)
                except Exception as error:
                    log.error("Could not close live session %s: %s", call_id, error)
            writer.close()
            await writer.wait_closed()
