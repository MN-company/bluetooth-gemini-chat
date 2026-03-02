from __future__ import annotations

import asyncio
import base64
import gzip
import io
import json
import mimetypes
import threading
import time
import uuid
import random
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from ble_protocol import DEFAULT_MAX_PACKET_SIZE, FrameAssembler, FrameCodec, TransportIdGenerator

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except Exception:  # pragma: no cover - Pillow is optional but recommended
    Image = None
    ImageOps = None
    UnidentifiedImageError = Exception

SERVICE_UUID = "8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c01"
WRITE_CHAR_UUID = "8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c02"
NOTIFY_CHAR_UUID = "8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c03"
MAX_GATT_ATTRIBUTE_VALUE_BYTES = 512
MAX_IMAGE_BYTES = 140 * 1024
TARGET_IMAGE_BYTES = 56 * 1024
MAX_IMAGE_DIMENSION = 768
MAX_REQUEST_BYTES = 220 * 1024
PING_INTERVAL_SECONDS = 7.0
PING_TIMEOUT_SECONDS = 20.0
RECONNECT_BASE_SECONDS = 1.5
RECONNECT_MAX_SECONDS = 8.0

EventSink = Callable[[dict[str, Any]], None]


class BleChatClient:
    def __init__(self, event_sink: EventSink) -> None:
        self._event_sink = event_sink
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._client: BleakClient | None = None
        self._max_packet_size = DEFAULT_MAX_PACKET_SIZE
        self._assembler = FrameAssembler()
        self._transport_ids = TransportIdGenerator()
        self._thread_started = False
        self._closing = False
        self._last_connected_address: str | None = None
        self._heartbeat_task: asyncio.Task[Any] | None = None
        self._reconnect_task: asyncio.Task[Any] | None = None
        self._pending_pings: dict[str, float] = {}
        self._last_pong_monotonic = time.monotonic()
        self._auto_reconnect_enabled = True

    def start(self) -> None:
        if self._thread_started:
            return
        self._closing = False
        self._thread.start()
        self._thread_started = True

    def stop(self) -> None:
        if not self._thread_started:
            return

        self._closing = True

        disconnect_future = self._run_coro(self._disconnect())
        try:
            disconnect_future.result(timeout=5)
        except Exception:
            pass

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
        self._thread_started = False

    def scan_devices(self) -> None:
        self._run_coro(self._scan_devices())

    def connect(self, address: str) -> None:
        self._last_connected_address = address
        self._stop_reconnect()
        self._run_coro(self._connect(address))

    def disconnect(self) -> None:
        self._last_connected_address = None
        self._stop_reconnect()
        self._run_coro(self._disconnect())

    def set_auto_reconnect(self, enabled: bool) -> None:
        self._auto_reconnect_enabled = bool(enabled)
        if not self._auto_reconnect_enabled:
            self._stop_reconnect()

    def send_prompt(
        self,
        prompt: str,
        model: str | None = None,
        image_path: str | None = None,
        image_target_bytes: int | None = None,
        image_max_dimension: int | None = None,
        context_blocks: list[dict[str, Any]] | None = None,
        memory_turns: list[dict[str, str]] | None = None,
        enable_web_search: bool = False,
        thinking_enabled: bool = False,
        thinking_budget: int | None = None,
        include_thoughts: bool = False,
        active_container_id: str | None = None,
    ) -> str:
        request_id = str(uuid.uuid4())
        message = {
            "type": "prompt",
            "messageId": request_id,
            "prompt": prompt,
            "enableWebSearch": bool(enable_web_search),
            "thinkingEnabled": bool(thinking_enabled),
            "includeThoughts": bool(include_thoughts),
        }
        if model is not None and model.strip():
            message["model"] = model.strip()
        if isinstance(thinking_budget, int):
            message["thinkingBudget"] = thinking_budget
        if active_container_id is not None:
            message["activeContainerId"] = active_container_id

        if context_blocks:
            message["contextBlocks"] = context_blocks
        if memory_turns:
            message["conversationMemory"] = memory_turns

        if image_path is not None:
            image_file, raw, mime_type = self._prepare_image_payload(
                image_path,
                target_bytes=image_target_bytes,
                max_dimension=image_max_dimension,
            )
            message["imageMimeType"] = mime_type
            message["imageBase64"] = base64.b64encode(raw).decode("ascii")
            message["imageName"] = image_file.name

        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError(
                f"Request payload too large ({len(payload)} bytes). "
                f"Reduce prompt/context/image (max {MAX_REQUEST_BYTES} bytes)."
            )
        self._run_coro(self._send_payload(payload, request_id))
        return request_id

    def send_container(self, container_dict: dict[str, Any]) -> str:
        """Transfer a full container to Android. Returns request_id for ACK matching.

        Payload is gzip-compressed (level 6) to minimise BLE packet count.
        `terms` are stripped from chunks — Android recomputes them from `text`.
        Android detects compression via the magic 4-byte prefix b'gz:\\x01'.
        """
        request_id = str(uuid.uuid4())

        # Strip terms to reduce size; Android recomputes them on load
        lean_chunks = [
            {"source": ch["source"], "page": ch.get("page", 0), "text": ch["text"]}
            for ch in container_dict.get("chunks", [])
        ]
        message = {
            "type": "load_container",
            "messageId": request_id,
            "containerId": container_dict["id"],
            "containerName": container_dict["name"],
            "chunks": lean_chunks,
        }
        raw_json = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        compressed = gzip.compress(raw_json, compresslevel=6)
        # Prefix so Android can detect this is compressed: magic b"gz\x01" + compressed bytes
        payload = b"gz\x01" + compressed

        raw_kb = len(raw_json) // 1024
        cmp_kb = len(payload) // 1024
        ratio = 100 - int(len(payload) / max(len(raw_json), 1) * 100)
        self._emit({"type": "status", "text": f"Container compressed: {raw_kb}KB → {cmp_kb}KB (-{ratio}%)"})

        MAX_CONTAINER_BYTES = 4 * 1024 * 1024
        if len(payload) > MAX_CONTAINER_BYTES:
            raise ValueError(
                f"Container too large even after compression ({cmp_kb}KB). "
                f"Split into smaller containers."
            )
        self._run_coro(self._send_payload(payload, request_id, reliable=True))
        return request_id

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coro(self, coro: Any) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _emit(self, event: dict[str, Any]) -> None:
        self._event_sink(event)

    async def _scan_devices(self) -> None:
        self._emit({"type": "status", "text": "Scanning BLE devices..."})
        try:
            payload = []
            target_uuid = SERVICE_UUID.lower()

            # Prefer scanning with explicit service UUID to avoid large unrelated device lists.
            try:
                discovered = await BleakScanner.discover(
                    timeout=6.0,
                    return_adv=True,
                    service_uuids=[SERVICE_UUID],
                )
                for device, adv_data in discovered.values():
                    service_uuids = {uuid.lower() for uuid in (adv_data.service_uuids or [])}
                    if service_uuids and target_uuid not in service_uuids:
                        continue

                    payload.append(
                        {
                            "name": device.name or adv_data.local_name or "Gemini Bridge",
                            "address": device.address,
                        }
                    )
            except TypeError:
                # Backward-compatible fallback if bleak backend does not support return_adv.
                devices = await BleakScanner.discover(timeout=6.0, service_uuids=[SERVICE_UUID])
                for device in devices:
                    payload.append(
                        {
                            "name": device.name or "Unknown",
                            "address": device.address,
                        }
                    )

            self._emit({"type": "scan_result", "devices": payload})
            if not payload:
                self._emit(
                    {
                        "type": "status",
                        "text": "No Gemini bridge found. Keep Android bridge service active and retry Scan.",
                    }
                )
        except Exception as exc:
            self._emit({"type": "error", "text": f"Scan failed: {exc}"})

    async def _connect(self, address: str, from_reconnect: bool = False) -> None:
        await self._disconnect(silent=True)
        if from_reconnect:
            self._emit({"type": "status", "text": f"Reconnecting to {address}..."})
        else:
            self._emit({"type": "status", "text": f"Connecting to {address}..."})

        try:
            client = BleakClient(address, disconnected_callback=self._on_disconnected)
            await client.connect(timeout=15.0)
            await client.get_services()

            write_char = client.services.get_characteristic(WRITE_CHAR_UUID)
            if write_char is None:
                raise RuntimeError("Write characteristic not found on device")

            write_size = getattr(write_char, "max_write_without_response_size", DEFAULT_MAX_PACKET_SIZE)
            if not isinstance(write_size, int) or write_size < DEFAULT_MAX_PACKET_SIZE:
                write_size = DEFAULT_MAX_PACKET_SIZE

            self._max_packet_size = min(write_size, MAX_GATT_ATTRIBUTE_VALUE_BYTES)
            await client.start_notify(NOTIFY_CHAR_UUID, self._on_notification)
            self._client = client
            self._last_connected_address = address
            self._pending_pings.clear()
            self._last_pong_monotonic = time.monotonic()
            self._stop_reconnect()
            self._start_heartbeat()

            device_name = (client.address or address).strip()
            self._emit(
                {
                    "type": "connected",
                    "address": address,
                    "device": device_name,
                    "max_packet_size": self._max_packet_size,
                }
            )
            self._emit({"type": "link_status", "state": "healthy", "text": "Link healthy"})
        except Exception as exc:
            self._client = None
            if from_reconnect:
                self._emit({"type": "status", "text": f"Reconnect failed: {exc}"})
            else:
                self._emit({"type": "error", "text": f"Connection failed: {exc}"})
                if self._auto_reconnect_enabled and self._last_connected_address == address:
                    self._start_reconnect()

    async def _disconnect(self, silent: bool = False) -> None:
        self._stop_heartbeat()
        if self._client is None:
            return

        client = self._client
        self._client = None

        try:
            if client.is_connected:
                try:
                    await client.stop_notify(NOTIFY_CHAR_UUID)
                except Exception:
                    pass
                await client.disconnect()
        finally:
            if not silent:
                self._emit({"type": "disconnected"})

    def _start_heartbeat(self) -> None:
        self._stop_heartbeat()
        self._heartbeat_task = self._loop.create_task(self._heartbeat_loop())

    def _stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _heartbeat_loop(self) -> None:
        while True:
            client = self._client
            if client is None or not client.is_connected:
                return

            seconds_since_pong = time.monotonic() - self._last_pong_monotonic
            if seconds_since_pong > PING_TIMEOUT_SECONDS:
                self._emit(
                    {
                        "type": "link_status",
                        "state": "timeout",
                        "text": f"Link timeout ({seconds_since_pong:.1f}s since last pong)",
                    }
                )
                try:
                    await client.disconnect()
                except Exception:
                    pass
                return

            ping_id = str(uuid.uuid4())
            self._pending_pings[ping_id] = time.monotonic()
            ping_message = {
                "type": "ping",
                "messageId": ping_id,
                "clientTsMs": int(time.time() * 1000),
            }
            payload = json.dumps(ping_message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            await self._send_payload(payload, ping_id, emit_sent_event=False)

            # Keep map bounded even if notifications are lost.
            if len(self._pending_pings) > 30:
                oldest = sorted(self._pending_pings.items(), key=lambda item: item[1])[:-20]
                for key, _ in oldest:
                    self._pending_pings.pop(key, None)

            await asyncio.sleep(PING_INTERVAL_SECONDS)

    def _start_reconnect(self) -> None:
        if self._closing or self._last_connected_address is None or not self._auto_reconnect_enabled:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = self._loop.create_task(self._reconnect_loop(self._last_connected_address))

    def _stop_reconnect(self) -> None:
        task = self._reconnect_task
        self._reconnect_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _reconnect_loop(self, address: str) -> None:
        attempt = 1
        while not self._closing:
            if self._client is not None and self._client.is_connected:
                return
            if self._last_connected_address != address:
                return

            self._emit({"type": "status", "text": f"Auto-reconnect attempt {attempt}..."})
            await self._connect(address, from_reconnect=True)
            if self._client is not None and self._client.is_connected:
                self._emit({"type": "status", "text": "Reconnected"})
                return

            base_backoff = min(RECONNECT_BASE_SECONDS * (1.6 ** (attempt - 1)), RECONNECT_MAX_SECONDS)
            # Small jitter reduces reconnect collisions in multi-client scenarios.
            backoff = base_backoff + random.uniform(0.0, 0.4)
            await asyncio.sleep(backoff)
            attempt += 1

    async def _send_payload(self, payload: bytes, request_id: str, emit_sent_event: bool = True, reliable: bool = False) -> None:
        client = self._client
        if client is None or not client.is_connected:
            if emit_sent_event:
                self._emit({"type": "error", "text": "Not connected"})
            return

        transport_id = self._transport_ids.next()
        packets = FrameCodec.encode_message(
            transport_id=transport_id,
            payload=payload,
            max_packet_size=self._max_packet_size,
        )
        packet_count = len(packets)
        if packet_count > 220:
            self._emit({"type": "status", "text": f"Sending large payload ({packet_count} BLE packets)..."})

        try:
            throttle_every = 12 if packet_count > 140 else 5
            throttle_delay = 0.0015 if packet_count > 140 else 0.003
            progress_step = max(packet_count // 12, 1)

            for idx, packet in enumerate(packets, start=1):
                try:
                    # If reliable=True, force response=True for guaranteed delivery
                    await client.write_gatt_char(WRITE_CHAR_UUID, packet, response=reliable)
                except BleakError:
                    await client.write_gatt_char(WRITE_CHAR_UUID, packet, response=True)

                if idx % progress_step == 0 or idx == packet_count:
                    pct = int((idx / packet_count) * 100)
                    self._emit(
                        {
                            "type": "transfer_progress",
                            "request_id": request_id,
                            "current_packets": idx,
                            "total_packets": packet_count,
                            "percent": pct,
                        }
                    )

                if not reliable and idx % throttle_every == 0:
                    await asyncio.sleep(throttle_delay)

            if emit_sent_event:
                self._emit({"type": "sent", "request_id": request_id})
        except Exception as exc:
            if emit_sent_event:
                self._emit({"type": "error", "text": f"Send failed: {exc}"})

    def _prepare_image_payload(
        self,
        image_path: str,
        target_bytes: int | None = None,
        max_dimension: int | None = None,
    ) -> tuple[Path, bytes, str]:
        target_limit = max(8 * 1024, int(target_bytes or TARGET_IMAGE_BYTES))
        max_dim = max(256, int(max_dimension or MAX_IMAGE_DIMENSION))
        image_file = Path(image_path)
        try:
            raw = image_file.read_bytes()
        except OSError as exc:
            raise ValueError(f"Cannot read image file: {exc}") from exc

        if not raw:
            raise ValueError("Selected image is empty")

        mime_type, _ = mimetypes.guess_type(image_path)
        if mime_type is None or not mime_type.startswith("image/"):
            raise ValueError("Selected file is not a supported image format")

        if len(raw) <= target_limit:
            return image_file, raw, mime_type

        if Image is None:
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError(
                    "Image too large for BLE bridge. Install Pillow or use a smaller image "
                    f"(<= {MAX_IMAGE_BYTES // 1024} KB)."
                )
            return image_file, raw, mime_type

        optimized = self._optimize_image_to_jpeg(
            image_file,
            target_bytes=target_limit,
            max_dimension=max_dim,
        )
        if optimized is not None:
            raw = optimized
            mime_type = "image/jpeg"

        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError(
                f"Image too large after optimization ({len(raw)} bytes). "
                f"Use a smaller image (max {MAX_IMAGE_BYTES} bytes)."
            )

        return image_file, raw, mime_type

    def _optimize_image_to_jpeg(
        self,
        image_file: Path,
        target_bytes: int = TARGET_IMAGE_BYTES,
        max_dimension: int = MAX_IMAGE_DIMENSION,
    ) -> bytes | None:
        try:
            with Image.open(image_file) as img:  # type: ignore[arg-type]
                if ImageOps is not None:
                    img = ImageOps.exif_transpose(img)

                if img.mode in ("RGBA", "LA"):
                    alpha = img.getchannel("A")
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    background.paste(img.convert("RGB"), mask=alpha)
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                if max(img.size) > max_dimension:
                    img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

                best: bytes | None = None
                for quality in (76, 68, 60, 52, 44, 36):
                    buffer = io.BytesIO()
                    img.save(buffer, format="JPEG", quality=quality, optimize=True)
                    candidate = buffer.getvalue()
                    if best is None or len(candidate) < len(best):
                        best = candidate
                    if len(candidate) <= target_bytes:
                        return candidate
                return best
        except (UnidentifiedImageError, OSError):
            return None

    def _on_notification(self, _: Any, data: bytearray) -> None:
        try:
            complete_payload = self._assembler.add_packet(bytes(data))
        except Exception as exc:
            self._emit({"type": "error", "text": f"Invalid packet from phone: {exc}"})
            return

        if complete_payload is None:
            return

        try:
            message = json.loads(complete_payload.decode("utf-8"))
        except Exception as exc:
            self._emit({"type": "error", "text": f"Invalid message JSON: {exc}"})
            return

        if message.get("type") == "pong":
            self._last_pong_monotonic = time.monotonic()
            message_id = message.get("messageId")
            rtt_ms: int | None = None
            if isinstance(message_id, str):
                sent_at = self._pending_pings.pop(message_id, None)
                if sent_at is not None:
                    rtt_ms = int((time.monotonic() - sent_at) * 1000)

            event: dict[str, Any] = {"type": "link_quality"}
            if isinstance(rtt_ms, int):
                event["rtt_ms"] = rtt_ms
            self._emit(event)
            return

        self._emit({"type": "incoming", "message": message})

    def _on_disconnected(self, _: BleakClient) -> None:
        self._client = None
        self._stop_heartbeat()
        self._emit({"type": "disconnected"})
        self._loop.call_soon_threadsafe(self._start_reconnect)
