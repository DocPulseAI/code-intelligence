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
from datetime import datetime
from flask import Flask, request, jsonify
from flasgger import Swagger
import subprocess
import sys
import logging
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
if not LOG.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )


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
LOG.info("Startup dependency check: %s", json.dumps(DEPENDENCY_STATUS))


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

        # Build command
        cmd = ['python', 'main.py', repo_url]
        if github_token:
            cmd.append(github_token)
        cmd.append(branch)
        if new_user:
            cmd.append('--new-user')

        # Run analysis with timeout
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.dirname(os.path.abspath(__file__))
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

        payload = {
            "status": "success",
            "report": report
        }
        if project_id is not None:
            payload["project_id"] = project_id
        return jsonify(payload)

    except subprocess.TimeoutExpired:
        return _error_response("analysis", "Analysis timeout (> 5 minutes)", True, _load_report_from_file(), 504)
    except Exception as e:
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

        # Build command
        cmd = ['python', 'main.py', repo_path]
        if new_user:
            cmd.append('--new-user')

        # Run analysis
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.dirname(os.path.abspath(__file__))
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

        payload = {
            "status": "success",
            "report": report
        }
        if project_id is not None:
            payload["project_id"] = project_id
        return jsonify(payload)

    except subprocess.TimeoutExpired:
        return _error_response("analysis", "Analysis timeout (> 5 minutes)", True, _load_report_from_file(), 504)
    except Exception as e:
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
