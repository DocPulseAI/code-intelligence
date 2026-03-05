import json
import logging
import os
from typing import Any

from flask import Flask, jsonify

LOG = logging.getLogger("epic1.main")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def process_message(payload: dict) -> dict:
    """
    Transform EPIC-1 analyze task into EPIC-2 generate-docs task payload.
    """
    # Extract fields with fallback from nested payload (handles both structured
    # EPIC task messages and legacy/webhook-format messages).
    nested = payload.get("payload") or {}
    if not isinstance(nested, dict):
        nested = {}

    project_id = (
        payload.get("projectId")
        or payload.get("project_id")
        or nested.get("projectId")
        or nested.get("project_id")
    )
    commit_sha = (
        payload.get("commitSha")
        or payload.get("commit_sha")
        or payload.get("commit_hash")
        or nested.get("commitSha")
        or nested.get("commit_sha")
        or nested.get("commit_hash")
        # Also try GitHub webhook push payload structure
        or (nested.get("head_commit") or {}).get("id")
    )
    branch_raw = (
        payload.get("branch")
        or nested.get("branch")
        or nested.get("ref", "")
    )
    branch = branch_raw.replace("refs/heads/", "") if branch_raw else "main"

    repo_url = (
        payload.get("repoUrl")
        or payload.get("repo_url")
        or nested.get("repoUrl")
        or nested.get("repo_url")
        or nested.get("repository_url")
        # GitHub webhook push payload nesting
        or (nested.get("repository") or {}).get("clone_url")
        or (nested.get("repository") or {}).get("html_url")
    )

    github_token = (
        payload.get("githubToken")
        or payload.get("github_token")
        or nested.get("githubToken")
        or nested.get("github_token")
    )

    # Validate required fields — skip unprocessable messages
    if not project_id or not commit_sha:
        LOG.error("Missing projectId or commitSha — dead-lettering message")
        raise ValueError("Message missing required projectId and/or commitSha; cannot process")

    if not repo_url:
        LOG.error("Missing repoUrl in payload")
        impact_report = {
            "report": {
                "files": [],
                "api_contract": {"endpoints": []},
                "summary": "Analysis failed: missing repository URL",
                "error": "Missing repoUrl in payload",
            },
            "project_id": project_id,
            "generated_at": payload.get("receivedAt") or "",
        }
    else:
        try:
            from datetime import datetime
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
                    # Filter out error entries
                    changed_files = [f for f in changed_files if f.get("change_type") != "ERROR"]
                except Exception:
                    pass

                # Always get full file inventory for deep analysis
                all_files = git_mgr.list_all_files()
                all_file_paths = [f["path"] for f in all_files if f.get("path") and f.get("change_type") != "ERROR"]

                # Build a read_file callback for intelligence modules
                def read_file(path: str):
                    return git_mgr.get_file_content(path)

                # Detect tech stack
                tech_stack = build_tech_stack(all_file_paths, read_file)
                LOG.info(json.dumps({"event": "tech_stack_detected", "stack": tech_stack}, separators=(",", ":")))

                # Determine which files to analyze (changed or all if no changes)
                files_to_analyze = changed_files if changed_files else all_files
                file_paths_to_analyze = [
                    f.get("path", "") for f in files_to_analyze
                    if f.get("path") and f.get("change_type") != "ERROR"
                ]

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
                    path = file_entry.get("path", "")
                    if not path or FileFilter.should_exclude_from_analysis(path):
                        continue

                    ext = os.path.splitext(path)[1].lower()
                    content = read_file(path) or ""
                    if not content:
                        continue

                    # Parse file features
                    features = {}
                    parser = PARSER_MAP.get(ext)
                    if parser:
                        try:
                            features = parser.analyze(content)
                        except Exception:
                            features = {}

                    # Detect schema/database tags
                    schema_tags = []
                    try:
                        schema_tags = SchemaDetector.analyze(path, content) or []
                    except Exception:
                        pass

                    # Compute severity
                    severity = SeverityCalculator.assess(ext, features, schema_tags)
                    severity_counts[severity] = severity_counts.get(severity, 0) + 1

                    # Classify component
                    path_lower = path.lower()
                    if path_lower.startswith("frontend/") or path_lower.startswith("client/") or path_lower.startswith("src/components/"):
                        component = "frontend"
                    elif path_lower.startswith("backend/") or path_lower.startswith("server/") or path_lower.startswith("src/routes/"):
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
                            "source_file": path,
                            "line": ep.get("line", 0),
                        })

                    # Collect packages/dependencies
                    for dep in features.get("dependencies", []):
                        all_packages.add(dep)

                    # Collect database models
                    if schema_tags:
                        database_models.append({"file": path, "tags": schema_tags})

                    changes.append({
                        "file": path,
                        "change_type": file_entry.get("change_type", "ADDED"),
                        "component": component,
                        "severity": severity,
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

        except Exception as exc:
            LOG.error(json.dumps({"event": "analysis_failed", "error": str(exc)}, separators=(",", ":")))
            impact_report = {
                "report": {
                    "files": [],
                    "api_contract": {"endpoints": []},
                    "summary": f"Analysis error: {str(exc)}",
                    "error": str(exc),
                },
                "project_id": project_id,
                "generated_at": payload.get("receivedAt") or "",
            }

    return {
        "taskType": "generate-docs",
        "projectId": project_id,
        "repoUrl": repo_url or "",
        "branch": branch,
        "commitSha": commit_sha,
        "githubToken": github_token,
        "payload": {
            "impact_report": impact_report,
            "source": "epic1",
        },
    }


app = Flask(__name__)


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok", "service": "epic1-consumer"}), 200


@app.get("/")
def root() -> Any:
    return jsonify(
        {
            "service": "DocPulse EPIC-1",
            "mode": "http",
        }
    ), 200


if __name__ == "__main__":
    # Container-friendly default bind/port.
    app.run(host="0.0.0.0", port=8000)
