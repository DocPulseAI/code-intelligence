"""Azure Service Bus helper utilities."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional


def _get_required_env(name: str, value: Optional[str] = None) -> str:
    resolved = (value if value is not None else os.getenv(name, "")).strip()
    if not resolved:
        raise ValueError(f"Missing required environment variable: {name}")
    return resolved


def send_message_to_queue(
    message_body: Any,
    connection_string: Optional[str] = None,
    queue_name: Optional[str] = None,
    client_factory: Optional[Callable[..., Any]] = None,
    message_factory: Optional[Callable[[str], Any]] = None,
) -> None:
    """
    Send a message to Azure Service Bus queue.

    Environment fallback:
    - SERVICEBUS_CONNECTION_STRING
    - SERVICEBUS_QUEUE_NAME
    """
    resolved_conn = _get_required_env("SERVICEBUS_CONNECTION_STRING", connection_string)
    resolved_queue = _get_required_env("SERVICEBUS_QUEUE_NAME", queue_name)

    if client_factory is None or message_factory is None:
        try:
            from azure.servicebus import ServiceBusClient, ServiceBusMessage
        except ImportError as exc:
            raise RuntimeError(
                "azure-servicebus is not installed. Run: pip install azure-servicebus"
            ) from exc

        if client_factory is None:
            client_factory = ServiceBusClient.from_connection_string
        if message_factory is None:
            message_factory = ServiceBusMessage

    payload = message_body
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, separators=(",", ":"))
    if not isinstance(payload, str):
        payload = str(payload)

    with client_factory(conn_str=resolved_conn) as client:
        with client.get_queue_sender(queue_name=resolved_queue) as sender:
            sender.send_messages(message_factory(payload))
