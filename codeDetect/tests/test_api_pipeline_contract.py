import json
import sys
import types
from unittest.mock import patch


if "flasgger" not in sys.modules:
    flasgger_stub = types.ModuleType("flasgger")

    class _Swagger:
        def __init__(self, *_args, **_kwargs):
            pass

    flasgger_stub.Swagger = _Swagger
    sys.modules["flasgger"] = flasgger_stub

from api import app


def _mock_subprocess_result(stdout_payload: dict):
    class _Result:
        returncode = 0
        stdout = json.dumps(stdout_payload)
        stderr = ""

    return _Result()


def test_analyze_returns_pipeline_metadata_envelope():
    report = {
        "context": {"commit_sha": "abc12345"},
        "analysis_summary": {"highest_severity": "MINOR"},
    }
    payload = {
        "repo_url": "https://github.com/example/repo",
        "branch": "main",
        "project_id": "proj-1",
        "run_id": "run-123",
        "ref_name": "main",
        "ref_type": "default_branch",
        "is_preview": False,
        "baseline_ref": "default",
    }

    with app.test_client() as client, patch("api.subprocess.run", return_value=_mock_subprocess_result(report)):
        res = client.post("/analyze", json=payload)

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "success"
    assert body["project_id"] == "proj-1"
    assert body["pipeline_metadata"]["run_id"] == "run-123"
    assert body["pipeline_metadata"]["ref_name"] == "main"
    assert body["pipeline_metadata"]["ref_type"] == "default_branch"
    assert body["pipeline_metadata"]["is_preview"] is False
    assert body["pipeline_metadata"]["baseline_ref"] == "default"
    assert body["pipeline_metadata"]["project_id"] == "proj-1"
    assert body["pipeline_metadata"]["commit_sha"] == "abc12345"
    assert body["report"]["pipeline_metadata"]["run_id"] == "run-123"

