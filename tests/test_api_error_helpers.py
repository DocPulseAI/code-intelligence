import sys
import types

if "flasgger" not in sys.modules:
    flasgger_stub = types.ModuleType("flasgger")

    class _Swagger:  # pragma: no cover
        def __init__(self, *_args, **_kwargs):
            pass

    flasgger_stub.Swagger = _Swagger
    sys.modules["flasgger"] = flasgger_stub

from api import _error_response, app


def test_error_response_has_required_shape_and_status_code():
    with app.app_context():
        body, status_code = _error_response(
            stage="analysis",
            details="timeout",
            retry_possible=True,
            report={"partial": True},
            status_code=504,
        )

    payload = body.get_json()
    assert status_code == 504
    assert payload["error"] == "Analysis failed"
    assert payload["stage"] == "analysis"
    assert payload["details"] == "timeout"
    assert payload["retry_possible"] is True
    assert payload["report"] == {"partial": True}


def test_error_response_uses_unknown_error_when_details_missing():
    with app.app_context():
        body, status_code = _error_response(
            stage="analysis",
            details="",
            retry_possible=False,
            status_code=500,
        )

    payload = body.get_json()
    assert status_code == 500
    assert payload["details"] == "Unknown error"
    assert payload["retry_possible"] is False
