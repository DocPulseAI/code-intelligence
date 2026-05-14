"""
Code Change Detector - REST API
Flask web service for analyzing code changes
"""

import json
import os
import tempfile
import shutil
import importlib.util
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flasgger import Swagger
import subprocess
import sys
import logging
import time
from uuid import uuid4
from typing import Optional

app = Flask(__name__)
swagger = Swagger(app, config={
    "headers": [],
    "specs": [
        {
            "endpoint": 'apispec_1',
            "route": '/apispec_1.json',
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs/"
})

# Configuration
REPORTS_DIR = Path('/tmp/code-detector-reports')
REPORTS_DIR.mkdir(exist_ok=True)
LOG = logging.getLogger("epic1.api")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


LOG_BODY_MAX_CHARS = max(200, _int_env("EPIC1_LOG_BODY_MAX_CHARS", 1200))
EPIC1_INTERNAL_TOKEN = os.getenv("EPIC1_INTERNAL_TOKEN", "").strip()


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)


def _truncate_text(value: Optional[str], max_chars: int = LOG_BODY_MAX_CHARS) -> str:
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...(truncated)"


def _log_event(level: int, event_id: str, message: str, **fields):
    payload: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "service": "epic1",
        "event_id": event_id,
        "message": message,
    }
    payload.update(fields)
    LOG.log(level, json.dumps(payload, default=str))


def _is_internal_request() -> bool:
    if not EPIC1_INTERNAL_TOKEN:
        return False
    supplied = (request.headers.get("x-epic-internal-token") or "").strip()
    return bool(supplied) and supplied == EPIC1_INTERNAL_TOKEN


_configure_logging()


def _check_runtime_dependencies() -> dict:
    """Startup self-check for critical runtime dependencies."""
    checks: dict[str, dict] = {}

    def _mark(module_name: str) -> bool:
        ok = importlib.util.find_spec(module_name) is not None
        checks[module_name] = {"ok": ok}
        return ok

    tree_sitter_ok = _mark("tree_sitter")
    tree_sitter_lang_ok = _mark("tree_sitter_languages")
    protobuf_ok = _mark("google.protobuf")
    gitpython_ok = _mark("git")

    process_pool_ok = True
    process_pool_mode = "process_pool"
    try:
        if hasattr(os, "sysconf"):
            # Mirrors the ProcessPool semaphore capability check in restricted sandboxes.
            _ = os.sysconf("SC_SEM_NSEMS_MAX")
    except Exception as e:
        process_pool_ok = False
        process_pool_mode = "sequential_fallback"
        checks["process_pool"] = {"ok": False, "detail": str(e)}
    else:
        checks["process_pool"] = {"ok": True}

    overall_ok = tree_sitter_ok and tree_sitter_lang_ok and protobuf_ok and gitpython_ok
    return {
        "overall_ok": overall_ok,
        "process_pool_mode": process_pool_mode,
        "checks": checks,
    }


DEPENDENCY_STATUS = _check_runtime_dependencies()
_log_event(
    logging.INFO,
    "EPIC1_DEPENDENCY_CHECK",
    "Startup dependency check complete",
    dependencies=DEPENDENCY_STATUS,
)


def _parse_boolean_field(data: dict, field_name: str, default: bool = False):
    """Return (ok, value, error_response). Enforces explicit boolean type in JSON."""
    value = data.get(field_name, default)
    if isinstance(value, bool):
        return True, value, None
    return False, None, (jsonify({
        "error": f"{field_name} must be a boolean (true/false), not {type(value).__name__}"
    }), 400)


def _require_json_object():
    """Validate request body is JSON object. Returns (ok, data, error_response)."""
    if not request.is_json:
        return False, None, (jsonify({"error": "Content-Type must be application/json"}), 400)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return False, None, (jsonify({"error": "Request body must be a JSON object"}), 400)
    return True, data, None


def _load_report_from_file() -> Optional[dict]:
    output_path = os.path.join(os.path.dirname(__file__), 'impact_report.json')
    if os.path.exists(output_path):
        try:
            with open(output_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return None
    return None


def _parse_stdout_report(stdout_text: str) -> Optional[dict]:
    text = stdout_text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _error_response(stage: str,
                    details: str,
                    retry_possible: bool,
                    report: Optional[dict] = None,
                    status_code: int = 500):
    payload = {
        "error": "Analysis failed",
        "stage": stage,
        "details": details or "Unknown error",
        "retry_possible": retry_possible
    }
    if report:
        payload["report"] = report
    return jsonify(payload), status_code


@app.before_request
def _before_request_log():
    request_id = request.headers.get("X-Request-Id") or str(uuid4())
    request.environ["epic1.request_id"] = request_id
    request.environ["epic1.started_at"] = time.perf_counter()
    _log_event(
        logging.INFO,
        "EPIC1_HTTP_REQUEST_START",
        "Incoming HTTP request",
        request_id=request_id,
        method=request.method,
        path=request.path,
        remote_addr=request.remote_addr,
        internal_request=_is_internal_request(),
    )


@app.after_request
def _after_request_log(response):
    request_id = request.environ.get("epic1.request_id")
    started_at = request.environ.get("epic1.started_at")
    duration_ms = None
    if isinstance(started_at, (float, int)):
        duration_ms = round((time.perf_counter() - float(started_at)) * 1000, 2)
    response.headers["X-Request-Id"] = str(request_id or "")
    _log_event(
        logging.INFO,
        "EPIC1_HTTP_REQUEST_END",
        "Completed HTTP request",
        request_id=request_id,
        method=request.method,
        path=request.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response

@app.route('/', methods=['GET', 'HEAD'])
def root():
    """
    Root endpoint - API information
    ---
    tags:
      - System
    responses:
      200:
        description: API information
        schema:
          type: object
          properties:
            service:
              type: string
              example: Code Change Detector API
            version:
              type: string
              example: 1.0.0
            endpoints:
              type: object
    """
    return jsonify({
        "service": "Code Change Detector API",
        "version": "1.0.0",
        "status": "healthy",
        "dependency_status": {
            "overall_ok": DEPENDENCY_STATUS["overall_ok"],
            "process_pool_mode": DEPENDENCY_STATUS["process_pool_mode"],
        },
        "endpoints": {
            "health": "/health",
            "health_dependencies": "/health/dependencies",
            "docs": "/docs/",
            "analyze": "/analyze",
            "analyze_local": "/analyze/local"
        },
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint
    ---
    tags:
      - System
    responses:
      200:
        description: API is healthy
        schema:
          type: object
          properties:
            status:
              type: string
              example: healthy
            service:
              type: string
              example: Code Change Detector API
            timestamp:
              type: string
    """
    return jsonify({
        "status": "healthy",
        "service": "Code Change Detector API",
        "dependency_status": {
            "overall_ok": DEPENDENCY_STATUS["overall_ok"],
            "process_pool_mode": DEPENDENCY_STATUS["process_pool_mode"],
        },
        "timestamp": datetime.utcnow().isoformat()
    })


@app.route('/health/dependencies', methods=['GET'])
def health_dependencies():
    """
    Detailed dependency health endpoint
    ---
    tags:
      - System
    responses:
      200:
        description: Dependency status details
    """
    payload = {
        "status": "ok" if DEPENDENCY_STATUS["overall_ok"] else "degraded",
        "service": "Code Change Detector API",
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": DEPENDENCY_STATUS,
    }
    status_code = 200 if DEPENDENCY_STATUS["overall_ok"] else 503
    return jsonify(payload), status_code


@app.route('/analyze', methods=['POST'])
def analyze():
    """
    Analyze a repository
    ---
    tags:
      - Analysis
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - repo_url
          properties:
            repo_url:
              type: string
              description: URL of the git repository to analyze
              example: https://github.com/owner/repo
            github_token:
              type: string
              description: GitHub Personal Access Token (optional)
            project_id:
              type: string
              description: External project identifier for client-side correlation (optional)
            branch:
              type: string
              description: Branch to analyze
              default: main
            new_user:
              type: boolean
              description: If true, perform a full-repo baseline scan
    responses:
      200:
        description: Analysis successful
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            report:
              type: object
              description: Detailed analysis report
      400:
        description: Invalid input
      500:
        description: Internal server error
      504:
        description: Analysis timeout
    """
    try:
        ok, data, error = _require_json_object()
        if not ok:
            body, status_code = error
            return body, status_code

        repo_url = data.get('repo_url')

        if not isinstance(repo_url, str) or not repo_url.strip():
            return jsonify({"error": "repo_url is required"}), 400

        repo_url = repo_url.strip()
        project_id = data.get('project_id')
        if project_id is not None:
            if not isinstance(project_id, str) or not project_id.strip():
                return jsonify({"error": "project_id must be a non-empty string"}), 400
            project_id = project_id.strip()

        github_token = data.get('github_token', os.environ.get('GITHUB_TOKEN'))
        if github_token is not None and not isinstance(github_token, str):
            return jsonify({"error": "github_token must be a string"}), 400

        branch = data.get('branch', 'main')
        if not isinstance(branch, str) or not branch.strip():
            return jsonify({"error": "branch must be a non-empty string"}), 400
        branch = branch.strip()

        ok, new_user, error = _parse_boolean_field(data, 'new_user', default=False)
        if not ok:
            body, status_code = error
            return body, status_code

        request_id = request.environ.get("epic1.request_id")
        trace_id = data.get("trace_id")
        if trace_id is not None and not isinstance(trace_id, str):
            return jsonify({"error": "trace_id must be a string"}), 400
        trace_id = trace_id.strip() if isinstance(trace_id, str) and trace_id.strip() else request_id
        run_id = data.get("run_id")
        if run_id is not None and not isinstance(run_id, str):
            return jsonify({"error": "run_id must be a string"}), 400
        run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else None
        ref_name = data.get("ref_name")
        if ref_name is not None and not isinstance(ref_name, str):
            return jsonify({"error": "ref_name must be a string"}), 400
        ref_name = ref_name.strip() if isinstance(ref_name, str) and ref_name.strip() else None
        ref_type = data.get("ref_type")
        if ref_type is not None and not isinstance(ref_type, str):
            return jsonify({"error": "ref_type must be a string"}), 400
        ref_type = ref_type.strip() if isinstance(ref_type, str) and ref_type.strip() else None
        baseline_ref = data.get("baseline_ref")
        if baseline_ref is not None and not isinstance(baseline_ref, str):
            return jsonify({"error": "baseline_ref must be a string"}), 400
        baseline_ref = baseline_ref.strip() if isinstance(baseline_ref, str) and baseline_ref.strip() else None
        is_preview = bool(data.get("is_preview", False))
        _log_event(
            logging.INFO,
            "EPIC1_ANALYZE_REQUEST",
            "Starting repository analysis request",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            ref_name=ref_name,
            ref_type=ref_type,
            baseline_ref=baseline_ref,
            is_preview=is_preview,
            repo_url=repo_url,
            branch=branch,
            project_id=project_id,
            new_user=new_user,
        )

        # Build command
        cmd = ['python', 'main.py', repo_url]
        if github_token:
            cmd.append(github_token)
        cmd.append(branch)
        if new_user:
            cmd.append('--new-user')

        # Run analysis with timeout
        started_at = time.perf_counter()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        _log_event(
            logging.INFO if result.returncode == 0 else logging.ERROR,
            "EPIC1_ANALYZE_SUBPROCESS_DONE" if result.returncode == 0 else "EPIC1_ANALYZE_SUBPROCESS_FAILED",
            "Analysis subprocess completed" if result.returncode == 0 else "Analysis subprocess failed",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            return_code=result.returncode,
            duration_ms=duration_ms,
            stderr_preview=_truncate_text(result.stderr),
        )

        if result.returncode != 0:
            report = _parse_stdout_report(result.stdout) or _load_report_from_file()
            if report and report.get("status") == "error":
                return _error_response(
                    report.get("stage", "analysis"),
                    report.get("details", result.stderr),
                    bool(report.get("retry_possible", True)),
                    _load_report_from_file()
                )
            if report and report.get("error"):
                return _error_response(
                    "analysis",
                    report.get("error"),
                    False,
                    _load_report_from_file()
                )
            return _error_response(
                "analysis",
                (result.stderr or "Analysis process exited with non-zero status").strip(),
                True,
                report
            )

        # Parse JSON output (prefer full stdout, fallback to impact_report.json)
        report = _parse_stdout_report(result.stdout) or _load_report_from_file()

        if not report:
            return _error_response("analysis", "Failed to parse analysis output", True)

        envelope = {
            "run_id": run_id,
            "ref_name": ref_name,
            "ref_type": ref_type,
            "is_preview": is_preview,
            "baseline_ref": baseline_ref,
            "project_id": project_id,
            "commit_sha": report.get("context", {}).get("commit_sha") if isinstance(report, dict) else None,
        }
        if isinstance(report, dict):
            report.setdefault("pipeline_metadata", envelope)

        payload = {
            "status": "success",
            "report": report,
            "pipeline_metadata": envelope,
        }
        if project_id is not None:
            payload["project_id"] = project_id
        _log_event(
            logging.INFO,
            "EPIC1_ANALYZE_SUCCESS",
            "Repository analysis completed successfully",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            highest_severity=report.get("analysis_summary", {}).get("highest_severity"),
            files_analyzed=report.get("analysis_summary", {}).get("total_files_changed"),
        )
        return jsonify(payload)

    except subprocess.TimeoutExpired:
        _log_event(
            logging.ERROR,
            "EPIC1_ANALYZE_TIMEOUT",
            "Repository analysis timed out",
            request_id=request.environ.get("epic1.request_id"),
        )
        return _error_response("analysis", "Analysis timeout (> 5 minutes)", True, _load_report_from_file(), 504)
    except Exception as e:
        _log_event(
            logging.ERROR,
            "EPIC1_ANALYZE_EXCEPTION",
            "Unhandled error while analyzing repository",
            request_id=request.environ.get("epic1.request_id"),
            error=str(e),
        )
        return _error_response("analysis", str(e), True, _load_report_from_file())

@app.route('/analyze/local', methods=['POST'])
def analyze_local():
    """
    Analyze a local repository path
    ---
    tags:
      - Analysis
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - repo_path
          properties:
            repo_path:
              type: string
              description: Local path to the repository
              example: /path/to/local/repo
            project_id:
              type: string
              description: External project identifier for client-side correlation (optional)
            new_user:
              type: boolean
              description: If true, perform a full-repo baseline scan
    responses:
      200:
        description: Analysis successful
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            report:
              type: object
      400:
        description: Invalid input
      404:
        description: Path not found
      500:
        description: Internal server error
    """
    try:
        ok, data, error = _require_json_object()
        if not ok:
            body, status_code = error
            return body, status_code

        repo_path = data.get('repo_path')
        project_id = data.get('project_id')
        if project_id is not None:
            if not isinstance(project_id, str) or not project_id.strip():
                return jsonify({"error": "project_id must be a non-empty string"}), 400
            project_id = project_id.strip()

        ok, new_user, error = _parse_boolean_field(data, 'new_user', default=False)
        if not ok:
            body, status_code = error
            return body, status_code

        if not isinstance(repo_path, str) or not repo_path.strip():
            return jsonify({"error": "repo_path is required"}), 400
        repo_path = repo_path.strip()

        if not os.path.exists(repo_path):
            return jsonify({"error": "Repository path does not exist"}), 404
        if not os.path.isdir(repo_path):
            return jsonify({"error": "repo_path must be a directory"}), 400

        request_id = request.environ.get("epic1.request_id")
        trace_id = data.get("trace_id")
        if trace_id is not None and not isinstance(trace_id, str):
            return jsonify({"error": "trace_id must be a string"}), 400
        trace_id = trace_id.strip() if isinstance(trace_id, str) and trace_id.strip() else request_id
        run_id = data.get("run_id")
        if run_id is not None and not isinstance(run_id, str):
            return jsonify({"error": "run_id must be a string"}), 400
        run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else None
        ref_name = data.get("ref_name")
        if ref_name is not None and not isinstance(ref_name, str):
            return jsonify({"error": "ref_name must be a string"}), 400
        ref_name = ref_name.strip() if isinstance(ref_name, str) and ref_name.strip() else None
        ref_type = data.get("ref_type")
        if ref_type is not None and not isinstance(ref_type, str):
            return jsonify({"error": "ref_type must be a string"}), 400
        ref_type = ref_type.strip() if isinstance(ref_type, str) and ref_type.strip() else None
        baseline_ref = data.get("baseline_ref")
        if baseline_ref is not None and not isinstance(baseline_ref, str):
            return jsonify({"error": "baseline_ref must be a string"}), 400
        baseline_ref = baseline_ref.strip() if isinstance(baseline_ref, str) and baseline_ref.strip() else None
        is_preview = bool(data.get("is_preview", False))
        _log_event(
            logging.INFO,
            "EPIC1_LOCAL_ANALYZE_REQUEST",
            "Starting local repository analysis request",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            ref_name=ref_name,
            ref_type=ref_type,
            baseline_ref=baseline_ref,
            is_preview=is_preview,
            repo_path=repo_path,
            project_id=project_id,
            new_user=new_user,
        )

        # Build command
        cmd = ['python', 'main.py', repo_path]
        if new_user:
            cmd.append('--new-user')

        # Run analysis
        started_at = time.perf_counter()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        _log_event(
            logging.INFO if result.returncode == 0 else logging.ERROR,
            "EPIC1_LOCAL_ANALYZE_SUBPROCESS_DONE" if result.returncode == 0 else "EPIC1_LOCAL_ANALYZE_SUBPROCESS_FAILED",
            "Local analysis subprocess completed" if result.returncode == 0 else "Local analysis subprocess failed",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            return_code=result.returncode,
            duration_ms=duration_ms,
            stderr_preview=_truncate_text(result.stderr),
        )

        if result.returncode != 0:
            report = _parse_stdout_report(result.stdout) or _load_report_from_file()
            if report and report.get("status") == "error":
                return _error_response(
                    report.get("stage", "analysis"),
                    report.get("details", result.stderr),
                    bool(report.get("retry_possible", True)),
                    _load_report_from_file()
                )
            if report and report.get("error"):
                return _error_response(
                    "analysis",
                    report.get("error"),
                    False,
                    _load_report_from_file()
                )
            return _error_response(
                "analysis",
                (result.stderr or "Analysis process exited with non-zero status").strip(),
                True,
                report
            )

        # Parse JSON output (prefer full stdout, fallback to impact_report.json)
        report = _parse_stdout_report(result.stdout) or _load_report_from_file()

        if not report:
            return _error_response("analysis", "Failed to parse analysis output", True)

        envelope = {
            "run_id": run_id,
            "ref_name": ref_name,
            "ref_type": ref_type,
            "is_preview": is_preview,
            "baseline_ref": baseline_ref,
            "project_id": project_id,
            "commit_sha": report.get("context", {}).get("commit_sha") if isinstance(report, dict) else None,
        }
        if isinstance(report, dict):
            report.setdefault("pipeline_metadata", envelope)

        payload = {
            "status": "success",
            "report": report,
            "pipeline_metadata": envelope,
        }
        if project_id is not None:
            payload["project_id"] = project_id
        _log_event(
            logging.INFO,
            "EPIC1_LOCAL_ANALYZE_SUCCESS",
            "Local repository analysis completed successfully",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            highest_severity=report.get("analysis_summary", {}).get("highest_severity"),
            files_analyzed=report.get("analysis_summary", {}).get("total_files_changed"),
        )
        return jsonify(payload)

    except subprocess.TimeoutExpired:
        _log_event(
            logging.ERROR,
            "EPIC1_LOCAL_ANALYZE_TIMEOUT",
            "Local repository analysis timed out",
            request_id=request.environ.get("epic1.request_id"),
        )
        return _error_response("analysis", "Analysis timeout (> 5 minutes)", True, _load_report_from_file(), 504)
    except Exception as e:
        _log_event(
            logging.ERROR,
            "EPIC1_LOCAL_ANALYZE_EXCEPTION",
            "Unhandled error while analyzing local repository",
            request_id=request.environ.get("epic1.request_id"),
            error=str(e),
        )
        return _error_response("analysis", str(e), True, _load_report_from_file())

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    port = int(os.getenv("PORT", "5000"))
    app.run(host='0.0.0.0', port=port, debug=False)
