import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus.exceptions import ServiceBusError

LOG = logging.getLogger("epic1.service_bus")


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _log(level: int, event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    LOG.log(level, json.dumps(payload, default=str, separators=(",", ":")))


class Epic1ServiceBusWorker:
    def __init__(
        self,
        process_message: Callable[[dict], Awaitable[dict]],
        *,
        poll_wait_seconds: int = 5,
        idle_sleep_seconds: float = 0.25,
        error_backoff_seconds: float = 3.0,
    ) -> None:
        self.process_message = process_message
        self.poll_wait_seconds = poll_wait_seconds
        self.idle_sleep_seconds = idle_sleep_seconds
        self.error_backoff_seconds = error_backoff_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        _configure_logging()
        _log(logging.INFO, "listener_started")

        while not stop_event.is_set():
            conn_str = os.getenv("SERVICE_BUS_CONNECTION_STRING", "").strip()
            source_queue = os.getenv("EPIC1_QUEUE_NAME", "epic1-impact").strip() or "epic1-impact"
            next_queue = os.getenv("NEXT_QUEUE_NAME", "epic2-generate").strip() or "epic2-generate"

            if not conn_str:
                _log(
                    logging.ERROR,
                    "missing_connection_string",
                    env_var="SERVICE_BUS_CONNECTION_STRING",
                    action="retrying",
                )
                await self._sleep_with_cancel(stop_event, self.error_backoff_seconds)
                continue

            try:
                await self._consume_loop(stop_event, conn_str, source_queue, next_queue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log(logging.ERROR, "listener_loop_error", error=str(exc), action="retrying")
                await self._sleep_with_cancel(stop_event, self.error_backoff_seconds)

        _log(logging.INFO, "listener_stopped")

    async def _consume_loop(
        self,
        stop_event: asyncio.Event,
        conn_str: str,
        source_queue: str,
        next_queue: str,
    ) -> None:
        async with ServiceBusClient.from_connection_string(conn_str=conn_str, logging_enable=False) as client:
            async with client.get_queue_receiver(
                queue_name=source_queue,
                max_wait_time=self.poll_wait_seconds,
                prefetch_count=10,
            ) as receiver, client.get_queue_sender(queue_name=next_queue) as sender:
                _log(
                    logging.INFO,
                    "service_bus_connected",
                    source_queue=source_queue,
                    next_queue=next_queue,
                )

                while not stop_event.is_set():
                    messages = await receiver.receive_messages(
                        max_message_count=10,
                        max_wait_time=self.poll_wait_seconds,
                    )

                    if not messages:
                        await self._sleep_with_cancel(stop_event, self.idle_sleep_seconds)
                        continue

                    for message in messages:
                        if stop_event.is_set():
                            break
                        await self._handle_message(receiver, sender, message, source_queue, next_queue)

    async def _handle_message(self, receiver: Any, sender: Any, message: Any, source_queue: str, next_queue: str) -> None:
        message_id = str(getattr(message, "message_id", ""))

        try:
            raw_text = self._message_to_text(message)
            payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                raise ValueError("Message payload must be a JSON object")

            processed = await self.process_message(payload)
            outbound = ServiceBusMessage(json.dumps(processed, separators=(",", ":")))
            await sender.send_messages(outbound)
            await receiver.complete_message(message)

            _log(
                logging.INFO,
                "message_processed",
                message_id=message_id,
                source_queue=source_queue,
                next_queue=next_queue,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            _log(logging.ERROR, "invalid_message_payload", message_id=message_id, error=str(exc))
            await self._safe_dead_letter(receiver, message, reason="invalid_payload", description=str(exc))
        except ServiceBusError as exc:
            _log(logging.ERROR, "service_bus_operation_failed", message_id=message_id, error=str(exc))
            await self._safe_abandon(receiver, message)
        except Exception as exc:
            _log(logging.ERROR, "message_processing_failed", message_id=message_id, error=str(exc))
            await self._safe_abandon(receiver, message)

    @staticmethod
    def _message_to_text(message: Any) -> str:
        body = message.body
        if isinstance(body, (str, bytes, bytearray, memoryview)):
            data = bytes(body) if not isinstance(body, str) else body.encode("utf-8")
            return data.decode("utf-8")

        chunks: list[bytes] = []
        for part in body:
            if isinstance(part, bytes):
                chunks.append(part)
            elif isinstance(part, memoryview):
                chunks.append(part.tobytes())
            elif isinstance(part, bytearray):
                chunks.append(bytes(part))
            else:
                chunks.append(str(part).encode("utf-8"))

        return b"".join(chunks).decode("utf-8")

    @staticmethod
    async def _safe_abandon(receiver: Any, message: Any) -> None:
        try:
            await receiver.abandon_message(message)
        except Exception as exc:
            _log(logging.ERROR, "abandon_failed", error=str(exc))

    @staticmethod
    async def _safe_dead_letter(receiver: Any, message: Any, *, reason: str, description: str) -> None:
        try:
            await receiver.dead_letter_message(message, reason=reason, error_description=description[:1024])
        except Exception as exc:
            _log(logging.ERROR, "dead_letter_failed", error=str(exc))

    @staticmethod
    async def _sleep_with_cancel(stop_event: asyncio.Event, seconds: float) -> None:
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
