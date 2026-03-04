import pytest

from src.azure_servicebus_client import send_message_to_queue


class _FakeSender:
    def __init__(self):
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def send_messages(self, message):
        self.messages.append(message)


class _FakeClient:
    def __init__(self):
        self.sender = _FakeSender()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_queue_sender(self, queue_name):
        self.queue_name = queue_name
        return self.sender


def test_send_message_uses_env(monkeypatch):
    monkeypatch.setenv("SERVICEBUS_CONNECTION_STRING", "Endpoint=sb://demo/")
    monkeypatch.setenv("SERVICEBUS_QUEUE_NAME", "q1")

    created = {}

    def _factory(conn_str):
        created["conn_str"] = conn_str
        created["client"] = _FakeClient()
        return created["client"]

    send_message_to_queue(
        {"hello": "world"},
        client_factory=_factory,
        message_factory=lambda text: text,
    )

    assert created["conn_str"] == "Endpoint=sb://demo/"
    assert created["client"].queue_name == "q1"
    assert created["client"].sender.messages == ['{"hello":"world"}']


def test_send_message_missing_env_raises(monkeypatch):
    monkeypatch.delenv("SERVICEBUS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("SERVICEBUS_QUEUE_NAME", raising=False)

    with pytest.raises(ValueError, match="SERVICEBUS_CONNECTION_STRING"):
        send_message_to_queue("hello", client_factory=lambda conn_str: _FakeClient(), message_factory=str)
