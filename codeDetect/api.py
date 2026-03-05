"""
Code Change Detector - REST API
Flask web service for analyzing code changes
"""

import asyncio
import atexit
import json
import os
import signal
import tempfile
import shutil
import importlib.util
import threading
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify
from flasgger import Swagger
import subprocess
import sys
import logging
from typing import Optional, Any, Awaitable, Callable
from src.azure_servicebus_client import send_message_to_queue
from service_bus import Epic1ServiceBusWorker

app = Flask(__name__)
swagger = Swagger(app, config={
    "headers": [],
    "specs": [
        {
            "endpoint": 'apispec_1',
            "route": '/apispec_1.json',
            "rule_filter": lambda rule: True,  # all in
            "model_filter": lambda tag: True,  # all in
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs/"
})

# ===== Service Bus Listener Setup =====
async def process_message(payload: dict) -> dict:
    """
    EPIC-1: Perform code impact analysis and format output for EPIC-2.
    Handles both WebhookQueueMessage (raw webhook) and EpicTaskMessage formats.
    """
    import tempfile
    import subprocess
    import json
    from datetime import datetime

    # Determine message format and extract fields
    # Format 1: EpicTaskMessage (direct from backend after project lookup)
    if payload.get("taskType") == "analyze":
        project_id = payload.get("projectId")
        commit_sha = payload.get("commitSha")
        branch = payload.get("branch", "main")
        repo_url = payload.get("repoUrl")
        github_token = payload.get("githubToken")
        LOG.info(f"Received EpicTaskMessage format: project={project_id}, repo={repo_url}")

    # Format 2: WebhookQueueMessage (raw GitHub webhook)
    elif payload.get("source") == "github" and payload.get("event") == "push":
        github_payload = payload.get("payload", {})
        project_id = None  # No project ID in raw webhooks
        repo_url = github_payload.get("repository", {}).get("html_url")
        if repo_url and not repo_url.endswith('.git'):
            repo_url = f"{repo_url}.git"
        branch = (github_payload.get("ref") or "").replace("refs/heads/", "") or "main"
        commit_sha = github_payload.get("head_commit", {}).get("id")
        github_token = None  # Not provided in raw webhooks
        LOG.info(f"Received WebhookQueueMessage format: repo={repo_url}, branch={branch}")

    else:
        # Unknown format - log and return error structure
        LOG.error(f"Unknown message format: {list(payload.keys())}")
        return {
            "taskType": "generate-docs",
            "projectId": payload.get("projectId"),
            "repoUrl": "",
            "branch": "main",
            "commitSha": "",
            "githubToken": None,
            "payload": {
                "impact_report": {
                    "report": {
                        "files": [],
                        "api_contract": {"endpoints": []},
                        "summary": f"Error: Unknown message format - {list(payload.keys())}",
                        "error": "Unknown message format"
                    },
                    "project_id": payload.get("projectId"),
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                },
                "source": "epic1",
            },
        }

    if not repo_url:
        LOG.error("Missing repoUrl in payload")
        return {
            "taskType": "generate-docs",
            "projectId": project_id,
            "repoUrl": "",
            "branch": branch,
            "commitSha": commit_sha,
            "githubToken": github_token,
            "payload": {
                "impact_report": {
                    "report": {
                        "files": [],
                        "api_contract": {"endpoints": []},
                        "summary": "Error: Missing repository URL",
                        "error": "Missing repoUrl in payload"
                    },
                    "project_id": project_id,
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                },
                "source": "epic1",
            },
        }

    try:
        # Clone and analyze repository using git_manager and analysis modules

        # Import analysis modules
        from src.git_manager import GitManager
        from src.file_filter import FileFilter
        from src.parsers.ts_parser import TSParser
        from src.parsers.js_parser import JSParser
        from src.parsers.python_parser import PythonParser
        from src.parsers.java_parser import JavaParser
        from src.parsers.schema_detector import SchemaDetector
        from src.scorers import SeverityCalculator
        from src.intelligence.tech_stack_model import build_tech_stack
        from src.intelligence.api_surface import build_api_surface

        LOG.info(f"Analyzing repository: {repo_url} branch: {branch}")

        # Clone the target repository — GitManager clones when given a URL
        git_mgr = GitManager(repo_url, github_token, branch)

        try:
            metadata = git_mgr.get_metadata()
            repo_name = (
                git_mgr.repo_slug.split("/")[-1] if git_mgr.repo_slug
                else metadata.get("repository", project_id)
            )

            # Get changed files; fall back to full file list on error
            changed_files = []
            try:
                changed_files = git_mgr.get_changed_files(commit_sha) if commit_sha else []
                changed_files = [f for f in changed_files if f.get("change_type") != "ERROR"]
            except Exception:
                pass

            # Always get full file inventory for deep analysis
            all_files = git_mgr.list_all_files()
            all_file_paths = [f["path"] for f in all_files if f.get("path") and f.get("change_type") != "ERROR"]

            # Build a read_file callback for intelligence modules
            def _read_file(path):
                return git_mgr.get_file_content(path)

            # Detect tech stack
            tech_stack = build_tech_stack(all_file_paths, _read_file)
            LOG.info(json.dumps({"event": "tech_stack_detected", "stack": tech_stack}, separators=(",", ":")))

            # Determine which files to analyze (changed or all if no changes)
            files_to_analyze = changed_files if changed_files else all_files

            # Analyze each file: extract features, endpoints, severity
            changes = []
            all_endpoints = []
            all_packages = set()
            severity_counts = {"MAJOR": 0, "MINOR": 0, "PATCH": 0}
            database_models = []

            PARSER_MAP = {
                ".ts": TSParser, ".tsx": TSParser,
                ".js": JSParser, ".jsx": JSParser,
                ".py": PythonParser,
                ".java": JavaParser,
            }

            for file_entry in files_to_analyze[:200]:
                fpath = file_entry.get("path", "")
                if not fpath or FileFilter.should_exclude_from_analysis(fpath):
                    continue

                ext = os.path.splitext(fpath)[1].lower()
                content = _read_file(fpath) or ""
                if not content:
                    continue

                # Parse file features
                features = {}
                parser_cls = PARSER_MAP.get(ext)
                if parser_cls:
                    try:
                        features = parser_cls.analyze(content)
                    except Exception:
                        features = {}

                # Detect schema/database tags
                schema_tags = []
                try:
                    schema_tags = SchemaDetector.analyze(fpath, content) or []
                except Exception:
                    pass

                # Compute severity
                sev = SeverityCalculator.assess(ext, features, schema_tags)
                severity_counts[sev] = severity_counts.get(sev, 0) + 1

                # Classify component
                path_lower = fpath.lower()
                if path_lower.startswith(("frontend/", "client/", "src/components/")):
                    component = "frontend"
                elif path_lower.startswith(("backend/", "server/", "src/routes/")):
                    component = "backend"
                elif any(k in path_lower for k in ("migration", "schema", "model", ".sql")):
                    component = "database"
                elif any(k in path_lower for k in ("docker", "terraform", ".github/workflows", "deploy", "infra")):
                    component = "infra"
                else:
                    component = "backend"

                # Collect endpoints
                endpoints = features.get("api_endpoints", []) or features.get("api_routes", []) or []
                for ep in endpoints:
                    all_endpoints.append({
                        "method": ep.get("verb") or ep.get("method") or "GET",
                        "path": ep.get("route") or ep.get("path") or "",
                        "source_file": fpath,
                        "line": ep.get("line", 0),
                    })

                # Collect packages/dependencies
                for dep in features.get("dependencies", []):
                    all_packages.add(dep)

                # Collect database models
                if schema_tags:
                    database_models.append({"file": fpath, "tags": schema_tags})

                changes.append({
                    "file": fpath,
                    "change_type": file_entry.get("change_type", "ADDED"),
                    "component": component,
                    "severity": sev,
                    "features": features,
                    "schema_tags": schema_tags,
                })

            # Build API surface from detected endpoints
            api_surface = build_api_surface(all_endpoints) if all_endpoints else []

            # Determine highest severity
            if severity_counts.get("MAJOR", 0) > 0:
                highest_severity = "MAJOR"
            elif severity_counts.get("MINOR", 0) > 0:
                highest_severity = "MINOR"
            else:
                highest_severity = "PATCH"

            generated_at = datetime.utcnow().isoformat() + "Z"

            # Build impact report with actual analysis
            impact_report = {
                "report": {
                    "context": {
                        "repository": repo_name,
                        "branch": branch,
                        "commit_sha": commit_sha,
                        "full_sha": metadata.get("full_sha", commit_sha),
                        "author": metadata.get("author", "unknown"),
                        "author_email": metadata.get("author_email", ""),
                        "commit_timestamp": metadata.get("intent", {}).get("timestamp", ""),
                        "generated_at": generated_at,
                    },
                    "changes": changes[:100],
                    "files": [{"path": c["file"], "change_type": c["change_type"], "severity": c["severity"],
                               "component": c["component"], "features": c.get("features", {})}
                              for c in changes[:100]],
                    "api_contract": {
                        "endpoints": all_endpoints[:200],
                    },
                    "api_surface": api_surface,
                    "affected_packages": sorted(all_packages),
                    "analysis_summary": {
                        "highest_severity": highest_severity,
                        "breaking_changes_detected": severity_counts.get("MAJOR", 0) > 0,
                        "total_files_analyzed": len(changes),
                        "severity_distribution": severity_counts,
                    },
                    "database_impact": {
                        "models": database_models,
                        "model_count": len(database_models),
                    },
                    "infra_analysis": {
                        "docker": "docker" in (tech_stack.get("infra") or []),
                        "ci_workflow": (tech_stack.get("ci") or [None])[0] if tech_stack.get("ci") else None,
                    },
                    "tech_stack": tech_stack,
                    "summary": f"Analyzed {len(changes)} files: {len(all_endpoints)} endpoints, stack={tech_stack.get('backend_framework') or 'unknown'}",
                    "commit_sha": commit_sha,
                    "branch": branch,
                },
                "project_id": project_id,
                "generated_at": generated_at,
            }

            LOG.info(json.dumps({
                "event": "analysis_complete",
                "files": len(changes),
                "endpoints": len(all_endpoints),
                "tech_stack": tech_stack,
            }, separators=(",", ":")))
        finally:
            git_mgr.cleanup()

    except Exception as e:
        LOG.error(f"Analysis failed: {str(e)}")
        # Return minimal valid structure on error so pipeline continues
        impact_report = {
            "report": {
                "files": [],
                "api_contract": {"endpoints": []},
                "summary": f"Analysis error: {str(e)}",
                "error": str(e),
            },
            "project_id": project_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    return {
        "taskType": "generate-docs",
        "projectId": project_id,
        "repoUrl": repo_url,
        "branch": branch,
        "commitSha": commit_sha,
        "githubToken": github_token,
        "payload": {
            "impact_report": impact_report,
            "source": "epic1",
        },
    }


class BackgroundAsyncRunner:
    def __init__(self, worker: Epic1ServiceBusWorker) -> None:
        self.worker = worker
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._started = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run_loop, name="epic1-servicebus-listener", daemon=True)
            self._thread.start()
            self._started.wait(timeout=10)
            LOG.info(json.dumps({"event": "background_runner_started"}, separators=(",", ":")))

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        self._started.set()
        try:
            self._loop.run_until_complete(self.worker.run(self._stop_event))
        except Exception as exc:
            LOG.error(json.dumps({"event": "background_runner_crashed", "error": str(exc)}, separators=(",", ":")))
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def stop(self, timeout: float = 30.0) -> None:
        with self._lock:
            if not self._thread:
                return
            if self._loop and self._stop_event:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                LOG.warning(json.dumps({"event": "background_runner_stop_timeout"}, separators=(",", ":")))
            else:
                LOG.info(json.dumps({"event": "background_runner_stopped"}, separators=(",", ":")))
            self._thread = None
            self._loop = None
            self._stop_event = None
            self._started.clear()


_listener_started = False
worker = Epic1ServiceBusWorker(process_message=process_message)
runner = BackgroundAsyncRunner(worker)


def start_listener() -> None:
    global _listener_started
    if _listener_started:
        return
    runner.start()
    _listener_started = True


@app.before_request
def ensure_listener_started() -> None:
    start_listener()


def _shutdown_handler(signum: int, _frame: Any) -> None:
    LOG.info(json.dumps({"event": "signal_received", "signal": signum}, separators=(",", ":")))
    runner.stop()


signal.signal(signal.SIGTERM, _shutdown_handler)
signal.signal(signal.SIGINT, _shutdown_handler)
atexit.register(runner.stop)
# ===== End Service Bus Listener Setup =====

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
            "servicebus_publish": "/servicebus/publish",
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


@app.route('/servicebus/publish', methods=['POST'])
def publish_servicebus_message():
    """
    Publish a message to Azure Service Bus queue
    ---
    tags:
      - Integration
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - message
          properties:
            message:
              description: String, object, or list payload sent to Service Bus.
            queue_name:
              type: string
              description: Optional override for SERVICEBUS_QUEUE_NAME.
            connection_string:
              type: string
              description: Optional override for SERVICEBUS_CONNECTION_STRING.
    responses:
      200:
        description: Message published
      400:
        description: Invalid payload or missing config
      500:
        description: Publish failed
    """
    ok, data, err = _require_json_object()
    if not ok:
        return err

    if "message" not in data:
        return jsonify({"error": "message is required"}), 400

    try:
        send_message_to_queue(
            message_body=data["message"],
            connection_string=data.get("connection_string"),
            queue_name=data.get("queue_name"),
        )
        return jsonify({"status": "published"}), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        LOG.exception("Service Bus publish failed")
        return jsonify({"error": "Service Bus publish failed", "details": str(exc)}), 500


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
