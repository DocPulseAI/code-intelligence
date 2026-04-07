import json
import sys
import types


if "flasgger" not in sys.modules:
    flasgger_stub = types.ModuleType("flasgger")

    class _Swagger:  # pragma: no cover - trivial test bootstrap shim
        def __init__(self, *_args, **_kwargs):
            pass

    flasgger_stub.Swagger = _Swagger
    sys.modules["flasgger"] = flasgger_stub

from api import (
    app,
    _load_report_from_file,
    _parse_boolean_field,
    _parse_stdout_report,
    _require_json_object,
)


def test_parse_boolean_field_accepts_bool_values():
    with app.app_context():
        ok, value, err = _parse_boolean_field({"new_user": True}, "new_user", default=False)
    assert ok is True
    assert value is True
    assert err is None


def test_parse_boolean_field_rejects_non_boolean():
    with app.app_context():
        ok, value, err = _parse_boolean_field({"new_user": "true"}, "new_user", default=False)
    assert ok is False
    assert value is None
    body, status_code = err
    assert status_code == 400
    assert body.get_json()["error"] == "new_user must be a boolean (true/false), not str"


def test_require_json_object_rejects_non_json_content_type():
    with app.test_request_context("/analyze", method="POST", data="{}", content_type="text/plain"):
        ok, data, err = _require_json_object()
    assert ok is False
    assert data is None
    body, status_code = err
    assert status_code == 400
    assert body.get_json()["error"] == "Content-Type must be application/json"


def test_require_json_object_rejects_non_object_payload():
    with app.test_request_context("/analyze", method="POST", json=["not", "an", "object"]):
        ok, data, err = _require_json_object()
    assert ok is False
    assert data is None
    body, status_code = err
    assert status_code == 400
    assert body.get_json()["error"] == "Request body must be a JSON object"


def test_require_json_object_accepts_dict_payload():
    payload = {"repo_url": "https://github.com/org/repo"}
    with app.test_request_context("/analyze", method="POST", json=payload):
        ok, data, err = _require_json_object()
    assert ok is True
    assert data == payload
    assert err is None


def test_parse_stdout_report_handles_valid_and_invalid_json():
    assert _parse_stdout_report('{"ok": true}') == {"ok": True}
    assert _parse_stdout_report("not-json") is None
    assert _parse_stdout_report("   ") is None


def test_load_report_from_file_reads_valid_json(monkeypatch):
    monkeypatch.setattr("api.os.path.exists", lambda _path: True)
    sample_json = '{"analysis": "ok"}'

    class _DummyFile:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return sample_json

    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: _DummyFile())

    assert _load_report_from_file() == {"analysis": "ok"}


def test_load_report_from_file_returns_none_on_decode_error(monkeypatch):
    monkeypatch.setattr("api.os.path.exists", lambda _path: True)

    class _DummyFile:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return "{invalid-json}"

    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: _DummyFile())

    assert _load_report_from_file() is None
