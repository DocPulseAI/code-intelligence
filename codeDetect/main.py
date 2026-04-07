"""
Code Change Detector - CLI Entry Point
Complete implementation with all intelligence layers
"""

import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable
from typing import Optional

# Configure logging
LOG = logging.getLogger("epic1.cli")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
_JWT_HINTS = {"protect", "auth", "authenticate", "verifytoken", "jwt"}
_RBAC_HINTS = {"rbac", "role", "authorize"}
_GITHUB_TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")


def _looks_like_github_token(value: Optional[str]) -> bool:
    token = str(value or "").strip()
    return token.startswith(_GITHUB_TOKEN_PREFIXES)


def _singularize_word(token: str) -> str:
    t = (token or "").lower()
    if t.endswith("ies") and len(t) > 3:
        return t[:-3] + "y"
    if t.endswith(("sses", "xes", "zes", "ches", "shes")) and len(t) > 3:
        return t[:-2]
    if t.endswith("s") and len(t) > 1 and not t.endswith("ss"):
        return t[:-1]
    return t


def _pluralize_word(token: str) -> str:
    t = (token or "").lower()
    if t.endswith("ies") and len(t) > 3:
        return t
    if t.endswith(("sses", "xes", "zes", "ches", "shes")):
        return t
    if t.endswith("s") and not t.endswith("ss"):
        return t
    if t.endswith("y") and len(t) > 1 and t[-2] not in "aeiou":
        return t[:-1] + "ies"
    if t.endswith(("s", "x", "z", "ch", "sh")):
        return t + "es"
    return t + "s"


def _normalize_openapi_path(path: str) -> str:
    segments = []
    for seg in str(path or "").split("/"):
        if not seg:
            continue
        segments.append(f"{{{seg[1:]}}}" if seg.startswith(":") else seg)
    return "/" + "/".join(segments)


def _extract_route_middlewares(content: str, method: str, raw_path: str) -> list[str]:
    # Parse line-oriented Express route declarations and return middleware identifiers.
    normalized_candidate = _normalize_openapi_path(raw_path)
    route_call_re = re.compile(rf"\.{method.lower()}\s*\((.+)\)")
    for line in (content or "").splitlines():
        s = line.strip()
        if f".{method.lower()}(" not in s:
            continue
        match = route_call_re.search(s)
        if not match:
            continue
        args_blob = match.group(1)
        args = [a.strip() for a in args_blob.split(",") if a.strip()]
        if not args:
            continue
        route_arg = args[0].strip().strip("\"'")
        if route_arg:
            normalized_route = _normalize_openapi_path(route_arg)
            # Match either exact normalized path or suffix path when candidate already has mount prefix.
            if normalized_route != normalized_candidate and not normalized_candidate.endswith(normalized_route):
                continue
        # First arg is route path and last arg is handler when middleware exists.
        if len(args) <= 2:
            return []
        middle = args[1:-1]
        tokens: list[str] = []
        for item in middle:
            token = item.split(".")[0].strip()
            token = token.replace("(", "").replace(")", "")
            if token:
                tokens.append(token)
        return tokens
    return []


def _detect_auth(middleware: list[str]) -> dict:
    lowered = [m.lower() for m in middleware]

    def _token_parts(token: str) -> set[str]:
        parts = re.split(r"[^a-z0-9]+", token)
        # Split camelCase deterministically into lowercase words.
        camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token)
        return {p.lower() for p in parts + camel_parts if p}

    token_parts = [_token_parts(m) for m in lowered]
    has_jwt = any(any(h in parts for h in _JWT_HINTS) for parts in token_parts)
    has_rbac = any(any(h in parts for h in _RBAC_HINTS) for parts in token_parts)

    if has_jwt and has_rbac:
        auth_type = "JWT+RBAC"
    elif has_jwt:
        auth_type = "JWT"
    elif has_rbac:
        auth_type = "RBAC"
    else:
        auth_type = "Public"

    filtered = []
    for m, parts in zip(middleware, token_parts):
        if any(h in parts for h in _JWT_HINTS) or any(h in parts for h in _RBAC_HINTS):
            filtered.append(m)

    return {
        "required": auth_type != "Public",
        "type": auth_type,
        "middleware": filtered,
    }


def _build_operation_id(method: str, openapi_path: str) -> str:
    method = method.upper()
    segs = [s for s in openapi_path.strip("/").split("/") if s and s not in {"api", "v1", "v2", "v3"}]
    has_id = any(s.startswith("{") and s.endswith("}") for s in segs)
    nouns = [s for s in segs if not (s.startswith("{") and s.endswith("}"))]
    noun_parts = [_singularize_word(s) for s in nouns] or ["resource"]

    if method == "GET" and not has_id:
        verb = "get"
        noun_parts[-1] = _pluralize_word(noun_parts[-1])
    elif method == "POST":
        verb = "create"
    elif method == "DELETE":
        verb = "delete"
    elif method == "PATCH":
        verb = "update"
    elif method == "PUT":
        verb = "replace"
    else:
        verb = method.lower()

    parts: list[str] = [verb]
    for seg in noun_parts:
        parts.append(seg[:1].upper() + seg[1:])
    if has_id and openapi_path.rstrip("/").endswith("}"):
        parts.extend(["By", "Id"])
    return "".join(parts)


def _build_summary(method: str, openapi_path: str) -> str:
    segs = [s for s in openapi_path.strip("/").split("/") if s and s not in {"api", "v1", "v2", "v3"}]
    has_id = any(s.startswith("{") and s.endswith("}") for s in segs)
    nouns = [s for s in segs if not (s.startswith("{") and s.endswith("}"))]
    if method.upper() == "PATCH" and nouns:
        resource = nouns[0]
    else:
        resource = nouns[-1] if nouns else "resource"
    singular = _singularize_word(resource)
    plural = _pluralize_word(singular)
    singular_title = singular[:1].upper() + singular[1:]
    plural_title = plural[:1].upper() + plural[1:]

    method = method.upper()
    if method == "GET" and not has_id:
        return f"List {plural_title}"
    if method == "POST":
        return f"Create {singular_title}"
    if method == "DELETE" and has_id:
        return f"Delete {singular_title} By Id"
    if method == "PATCH":
        suffix_parts = nouns[1:] if nouns else []
        suffix = " ".join(seg[:1].upper() + seg[1:] for seg in suffix_parts)
        if suffix:
            return f"Update {singular_title} {suffix}".strip()
        return f"Update {singular_title}"
    return f"{method.title()} {singular_title}"


def _stable_generated_at(metadata: dict, commit_sha: str) -> str:
    ts = str((metadata or {}).get("intent", {}).get("timestamp") or "").strip()
    if ts:
        return ts
    if commit_sha and commit_sha != "error":
        return f"commit:{commit_sha}"
    return "1970-01-01T00:00:00Z"


def _build_api_contract_endpoints(candidates: list[dict]) -> tuple[list[dict], list[str]]:
    """Compatibility helper retained for hardening tests.

    Accepts parsed route candidates and returns OpenAPI-normalized endpoints and warnings.
    """
    endpoints: list[dict] = []
    warnings: list[str] = []

    for c in candidates or []:
        method = str(c.get("method", "GET")).upper()
        raw_path = str(c.get("path", ""))
        if method.lower() not in _ROUTE_METHODS or not raw_path:
            continue
        openapi_path = _normalize_openapi_path(raw_path)
        middleware = _extract_route_middlewares(c.get("content", ""), method, raw_path)
        auth = _detect_auth(middleware)
        endpoint = {
            "method": method,
            "path": openapi_path,
            "source_file": c.get("source_file"),
            "line_start": c.get("line_start"),
            "line_end": c.get("line_end"),
            "operation_id": _build_operation_id(method, openapi_path),
            "summary": _build_summary(method, openapi_path),
            "auth": auth,
        }
        endpoints.append(endpoint)

    endpoints.sort(key=lambda ep: (ep.get("method", ""), ep.get("path", "")))
    return endpoints, warnings


def main():
    """CLI entry point for code analysis."""
    # Parse arguments
    args = sys.argv[1:]
    if len(args) < 1:
        LOG.error("Usage: python main.py <repo_url_or_path> [github_token] [branch] [--new-user]")
        sys.exit(1)

    repo_input = args[0]
    github_token = None
    branch = "main"
    new_user = False

    # Parse optional arguments
    positional_args: list[str] = []
    for arg in args[1:]:
        low = arg.lower()
        if low in {"--new-user", "--new-user=true", "--new-user=1", "--new-user=yes", "--new-user=y", "--new-user=on"}:
            new_user = True
            continue
        if low in {"--new-user=false", "--new-user=0", "--new-user=no", "--new-user=n", "--new-user=off"}:
            new_user = False
            continue
        if arg.startswith("--"):
            continue
        positional_args.append(arg)

    if positional_args:
        first = positional_args[0]
        if _looks_like_github_token(first):
            github_token = first
            if len(positional_args) > 1:
                branch = positional_args[1]
        else:
            branch = first
            if len(positional_args) > 1 and _looks_like_github_token(positional_args[1]):
                github_token = positional_args[1]

    # Determine if it's a URL or local path
    is_url = repo_input.startswith(("http://", "https://", "git@"))

    try:
        # Import dependencies
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
        from src.intelligence.repository_classifier import classify_repository_type
        from src.intelligence.documentation_contract import build_documentation_contract
        from src.intelligence.architecture_model import build_architecture_model
        from src.intelligence.data_model_graph import build_data_model
        from src.intelligence.schema_diff_engine import extract_canonical_models, diff_schema_models
        from src.intelligence.repository_evidence import build_repository_evidence
        from src.intelligence.architecture_reconstructor import reconstruct_architecture
        from src.intelligence.dependency_graph_engine import build_dependency_graph
        from src.intelligence.call_graph_engine import build_call_graph
        from src.intelligence.code_intelligence_builder import build_code_intelligence
        from src.intelligence.impact_analysis_engine import build_impact_analysis
        from src.intelligence.dependency_analysis_engine import analyze_dependencies
        from src.intelligence.call_graph_analysis_engine import analyze_call_graph
        from src.intelligence.repository_intelligence_engine import analyze_repository_intelligence
        from src.intelligence.search_index_builder import build_search_index
        from src.breaking_change_engine import compare_reports
        from src.risk_scoring import score_report_risk
        from src.baseline_store import BaselineStore
        from src.serialization.stable_json import dump_stable_file

        # Initialize Git manager
        git_mgr = GitManager(repo_input, github_token, branch)

        try:
            # Get repository metadata
            metadata = git_mgr.get_metadata()
            repo_name = git_mgr.repo_slug.split("/")[-1] if git_mgr.repo_slug else metadata.get("repository", "unknown")
            commit_sha = metadata.get("full_sha", metadata.get("commit_sha", "HEAD"))
            project_id = git_mgr.repo_slug or repo_name

            # Get changed or all files
            changed_files = []
            if not new_user:
                try:
                    changed_files = git_mgr.get_changed_files(commit_sha)
                    changed_files = [f for f in changed_files if f.get("change_type") != "ERROR"]
                except Exception:
                    pass

            # Always get full file inventory
            all_files = git_mgr.list_all_files()
            all_file_paths = [f["path"] for f in all_files if f.get("path") and f.get("change_type") != "ERROR"]

            # Build read_file callback
            def read_file(path: str):
                return git_mgr.get_file_content(path)

            # Build intelligence layers
            LOG.info("Building tech stack analysis...")
            tech_stack = build_tech_stack(all_file_paths, read_file)

            # Determine files to analyze
            files_to_analyze = changed_files if (changed_files and not new_user) else all_files
            file_paths_to_analyze = [
                f.get("path", "") for f in files_to_analyze
                if f.get("path") and f.get("change_type") != "ERROR"
            ]

            # Analyze files
            LOG.info(f"Analyzing {len(file_paths_to_analyze)} files...")
            changes = []
            all_endpoints = []
            all_packages = set()
            severity_counts = {"MAJOR": 0, "MINOR": 0, "PATCH": 0}
            database_models = []
            file_features: dict[str, dict] = {}
            file_schema_tags: dict[str, list] = {}

            PARSER_MAP = {
                ".ts": TSParser, ".tsx": TSParser,
                ".js": JSParser, ".jsx": JSParser,
                ".py": PythonParser,
                ".java": JavaParser,
            }

            for file_entry in files_to_analyze[:200]:  # Limit for performance
                path = file_entry.get("path", "")
                if not path or FileFilter.should_exclude_from_analysis(path):
                    continue

                ext = os.path.splitext(path)[1].lower()
                content = read_file(path) or ""
                if not content:
                    continue

                # Parse features
                features = {}
                parser = PARSER_MAP.get(ext)
                if parser:
                    try:
                        features = parser.analyze(content)
                    except Exception:
                        features = {}

                # Detect schema
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
                        "source_file": path,
                        "line": ep.get("line", 0),
                    })

                # Collect dependencies
                for dep in features.get("dependencies", []):
                    if dep.startswith((".", "..")):
                        file_dir = os.path.dirname(path)
                        norm_dep = os.path.normpath(os.path.join(file_dir, dep)).replace("\\", "/")
                        all_packages.add(norm_dep)
                    else:
                        all_packages.add(dep)

                # Collect database models
                if schema_tags:
                    database_models.append({"file": path, "tags": schema_tags})

                # Collect per-file features for repository evidence
                file_features[path] = features
                file_schema_tags[path] = schema_tags

                changes.append({
                    "file": path,
                    "change_type": file_entry.get("change_type", "ADDED"),
                    "component": component,
                    "severity": severity,
                    "features": features,
                    "schema_tags": schema_tags,
                })

            # Sort baseline root elements for determinism
            all_endpoints = sorted(all_endpoints, key=lambda ep: (ep.get("method", ""), ep.get("path", "")))
            database_models = sorted(database_models, key=lambda m: m.get("file", ""))
            changes = sorted(changes, key=lambda c: c.get("file", ""))

            # Build advanced intelligence layers
            LOG.info("Building API surface...")
            api_surface = build_api_surface(all_endpoints) if all_endpoints else []

            LOG.info("Building architecture model...")
            architecture = build_architecture_model(all_file_paths, read_file)

            LOG.info("Building data model...")
            data_model = build_data_model(all_file_paths, read_file)

            LOG.info("Extracting canonical schema models...")
            schema_models = extract_canonical_models(all_file_paths, read_file)

            # Build repository evidence graph
            LOG.info("Building repository evidence...")
            repository_evidence = build_repository_evidence(
                all_file_paths, read_file, file_features, file_schema_tags, tech_stack, include_extended=True
            )

            LOG.info("Reconstructing architecture...")
            arch_recon = reconstruct_architecture(repository_evidence)
            architecture_reconstruction = arch_recon.get("architecture_reconstruction", {})

            LOG.info("Building dependency graph...")
            dependency_graph = build_dependency_graph(repository_evidence, changes)

            LOG.info("Building call graph...")
            call_graph = build_call_graph(repository_evidence)

            LOG.info("Analyzing call graph hierarchy...")
            call_analysis_res = analyze_call_graph(call_graph, repository_evidence)
            call_graph_analysis = call_analysis_res.get("call_graph_analysis", {})

            LOG.info("Building code intelligence section...")
            code_intelligence = build_code_intelligence(
                repository_evidence=repository_evidence,
                call_graph=call_graph,
                dependency_graph=dependency_graph,
                read_file=read_file,
            )

            LOG.info("Analyzing dependencies...")
            dep_analysis_res = analyze_dependencies(dependency_graph)
            dependency_analysis = dep_analysis_res.get("dependency_analysis", {})
            impact_analysis = build_impact_analysis(
                repository_evidence, dependency_graph, call_graph, changes
            )

            LOG.info("Building search index...")
            search_index_res = build_search_index(
                repository_evidence,
                architecture_reconstruction,
                dependency_graph,
                call_graph,
                read_file,
            )
            search_index = search_index_res.get(
                "search_index",
                {"symbols": [], "references": [], "apis": [], "modules": []},
            )

            LOG.info("Generating deep repository intelligence reasoning...")
            repo_intel = analyze_repository_intelligence(
                ev_apis_dict if 'ev_apis_dict' in locals() else {"endpoints": repository_evidence.get('apis', [])},
                dependency_graph,
                call_graph,
                architecture_reconstruction,
                changes,
                database_models,
                dependency_analysis
            )

            # ---------------------------------------------------------------

            # Override api_surface and api_contract with resolved endpoints
            # from repository_evidence.apis (which carry full resolved paths
            # and controller mappings from the mount resolution engine).
            # ---------------------------------------------------------------
            ev_apis = repository_evidence.get("apis", [])
            if ev_apis:
                LOG.info(f"Overriding api_surface with {len(ev_apis)} resolved endpoints from repository_evidence")
                # Build enriched endpoint list for api_surface
                resolved_endpoints_for_surface = []
                for a in ev_apis:
                    if a.get("method") and a.get("path"):
                        method = str(a.get("method", "GET")).upper()
                        path = str(a.get("path", ""))
                        normalized_key = f"{method.lower()} {path.lower()}"
                        op_segments = [
                            seg for seg in path.replace(":", "").replace("{", "").replace("}", "").split("/")
                            if seg
                        ]
                        op_suffix = "".join(seg[:1].upper() + seg[1:] for seg in op_segments) or "Root"
                        operation_id = f"{method.lower()}{op_suffix}"
                        endpoint_hash = hashlib.sha256(
                            f"v1|{method}|{path}".encode("utf-8")
                        ).hexdigest()
                        ep = {
                            "operation_id": operation_id,
                            "method": method,
                            "path": path,
                            "normalized_key": normalized_key,
                            "summary": "",
                            "description": "",
                            "tags": [str(a.get("module", "")).strip()] if str(a.get("module", "")).strip() else [],
                            "auth": {
                                "required": bool(a.get("auth_required", False)),
                                "type": "unknown",
                            },
                            "request": {
                                "path_params": [],
                                "query_params": [],
                                "headers": [],
                                "body_schema": None,
                            },
                            "responses": [],
                            "example": {},
                            "source": {"controller": a.get("controller", "")},
                            "confidence": 0.8,
                            "warnings": [],
                            "controller": a.get("controller", ""),
                            "component": a.get("module", ""),
                            "source_file": a.get("source_file", ""),
                            "router_file": a.get("router_file", ""),
                            "line": a.get("line", 0),
                            "endpoint_hash": endpoint_hash,
                        }
                        if "auth_required" in a:
                            ep["auth_required"] = a["auth_required"]
                        resolved_endpoints_for_surface.append(ep)
                api_surface = build_api_surface(resolved_endpoints_for_surface)
                # Also replace all_endpoints so api_contract gets resolved paths
                all_endpoints = sorted(
                    resolved_endpoints_for_surface,
                    key=lambda ep: (ep.get("method", ""), ep.get("path", ""))
                )
            # ---------------------------------------------------------------

            # Classify repository
            LOG.info("Classifying repository type...")
            repository_type = classify_repository_type(all_file_paths, read_file, len(all_endpoints))

            # Determine highest severity
            if severity_counts.get("MAJOR", 0) > 0:
                highest_severity = "MAJOR"
            elif severity_counts.get("MINOR", 0) > 0:
                highest_severity = "MINOR"
            else:
                highest_severity = "PATCH"

            def extract_schema_relationships(models: list[dict]) -> list[dict]:
                rels = []
                seen = set()
                for model in models:
                    model_name = str(model.get("model_name", "")).strip()
                    fields = model.get("fields", {})
                    if not model_name or not isinstance(fields, dict):
                        continue
                    for fname, fmeta in fields.items():
                        if not isinstance(fmeta, dict):
                            continue
                        ref = str(fmeta.get("ref", "")).strip()
                        if not ref:
                            continue
                        key = (model_name, str(fname), ref)
                        if key in seen:
                            continue
                        seen.add(key)
                        rels.append(
                            {
                                "from": f"{model_name}.{fname}",
                                "to": ref,
                                "type": "references",
                                "field": str(fname),
                            }
                        )
                return sorted(rels, key=lambda r: (r.get("from", ""), r.get("to", "")))

            schema_relationships = extract_schema_relationships(schema_models)

            quality_warnings = list(repository_evidence.get("quality_warnings", []))
            if len(all_endpoints) == 0:
                quality_warnings.append("QUALITY_WARNING: endpoint_count=0 (no API endpoints detected)")
            if repository_evidence.get("api_mounts"):
                non_root_mounts = [m for m in repository_evidence.get("api_mounts", []) if str(m.get("base_path", "")).strip() not in ("", "/")]
                if non_root_mounts:
                    unresolved = []
                    for ep in all_endpoints:
                        path = str(ep.get("path", "")).strip()
                        if not any(path == m.get("base_path", "") or path.startswith(str(m.get("base_path", "")).rstrip("/") + "/") for m in non_root_mounts):
                            src = str(ep.get("source_file", ""))
                            if src and "/routes/" in src.replace("\\", "/"):
                                unresolved.append(f"{ep.get('method', 'GET')} {path}")
                    if unresolved:
                        quality_warnings.append(
                            "QUALITY_WARNING: endpoints missing expected mount prefixes: "
                            + ", ".join(sorted(set(unresolved))[:5])
                        )
            if any(not isinstance(m.get("fields"), dict) or len(m.get("fields", {})) == 0 for m in schema_models):
                quality_warnings.append("QUALITY_WARNING: one or more schema entities are missing field definitions")

            # Build current report (normalized structure for baseline comparison)
            generated_at = _stable_generated_at(metadata, commit_sha)
            current_normalized_report = {
                "api_surface": api_surface,
                "api_contract": {"endpoints": all_endpoints[:200]},
                "data_model": data_model,
                "schema_analysis": {
                    "models": schema_models,
                    "models_detected": len(schema_models),
                    "relationships": schema_relationships,
                },
                "architecture_reconstruction": architecture_reconstruction,
                "dependency_graph": dependency_graph,
                "dependency_analysis": dependency_analysis,
                "call_graph": call_graph,
                "call_graph_analysis": call_graph_analysis,
                "impact_analysis": impact_analysis,
                "search_index": search_index,
                "code_intelligence": code_intelligence,
            }


            # Load baseline and detect breaking changes
            baseline_store = BaselineStore()
            baseline_report = None
            baseline_commit = None
            breaking_changes = []
            baseline_required = bool(os.environ.get("CODE_DETECT_BASELINE_DIR")) and not new_user

            if not new_user:
                LOG.info("Loading baseline...")
                baseline_report = baseline_store.load_baseline(project_id, branch, commit_sha)
                if baseline_required and not baseline_report:
                    print(
                        json.dumps(
                            {
                                "error": "Analysis failed",
                                "stage": "analysis",
                                "details": "Baseline commit required for breaking change detection",
                                "retry_possible": False,
                                "report": {},
                            },
                            ensure_ascii=True,
                            separators=(",", ":"),
                        )
                    )
                    sys.exit(1)
                if baseline_report:
                    baseline_commit = baseline_report.get("commit_sha")
                    LOG.info(f"Comparing against baseline commit: {baseline_commit}")
                    # Detect breaking changes
                    breaking_changes = compare_reports(
                        baseline_report.get("report", {}),
                        current_normalized_report
                    )
                    # Add schema diffs
                    schema_diffs = diff_schema_models(
                        baseline_report.get("report", {}),
                        current_normalized_report
                    )
                    breaking_changes.extend(schema_diffs)
                    # Sort by severity
                    SEVERITY_ORDER = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}
                    breaking_changes = sorted(
                        breaking_changes,
                        key=lambda d: (
                            -SEVERITY_ORDER.get(str(d.get("severity", "PATCH")), 1),
                            str(d.get("file", "")),
                            str(d.get("entity", "")),
                        ),
                    )

            # Build documentation contract
            breaking_detected = len([bc for bc in breaking_changes if bc.get("severity") == "MAJOR"]) > 0
            documentation_contract = build_documentation_contract(repository_type, breaking_detected)

            # Calculate risk score
            risk_analysis = score_report_risk(current_normalized_report, breaking_changes)

            internal_modules_list = []
            external_dependencies_list = []
            for pkg in sorted(all_packages):
                if pkg.startswith(("./", "../", "src/", "app/")):
                    internal_modules_list.append(pkg)
                else:
                    external_dependencies_list.append(pkg)

            # Build complete impact report matching schema
            impact_report = {
                "schema_version": "epic1-impact/v3",
                "version": "1.0.0",
                "status": "success",
                "meta": {
                    "tool_version": "1.0.0",
                    "generated_at": generated_at,
                },
                "project_id": project_id,
                "branch": branch,
                "commit_sha": commit_sha,
                "baseline_commit": baseline_commit,
                "analysis_summary": {
                    "total_files": len(changes),
                    "highest_severity": highest_severity,
                    "breaking_changes_detected": breaking_detected,
                },
                "breaking_changes": breaking_changes[:100],
                "statistics": risk_analysis.get("statistics", {
                    "total_changes": len(breaking_changes),
                    "major": len([bc for bc in breaking_changes if bc.get("severity") == "MAJOR"]),
                    "minor": len([bc for bc in breaking_changes if bc.get("severity") == "MINOR"]),
                    "patch": len([bc for bc in breaking_changes if bc.get("severity") == "PATCH"]),
                }),
                "severity": risk_analysis.get("severity", highest_severity),
                "deterministic": True,
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
                    "analysis_summary": {
                        "highest_severity": highest_severity,
                        "breaking_changes_detected": breaking_detected,
                        "total_files": len(changes),
                        "total_files_analyzed": len(changes),
                        "severity_distribution": severity_counts,
                    },
                    "changes": changes[:100],
                    "files": [
                        {
                            "path": c["file"],
                            "change_type": c["change_type"],
                            "severity": c["severity"],
                            "component": c["component"],
                            "features": c.get("features", {}),
                        }
                        for c in changes[:100]
                    ],
                    "repository_type": repository_type,
                    "tech_stack": tech_stack,
                    "documentation_contract": documentation_contract,
                    "architecture": architecture,
                    "api_surface": api_surface[:200],
                    "api_contract": {"endpoints": all_endpoints[:200]},
                    "api_summary": {
                        "added": len([bc for bc in breaking_changes if bc.get("type") == "ENDPOINT_ADDED"]),
                        "modified": len([bc for bc in breaking_changes if bc.get("type") == "ENDPOINT_MODIFIED"]),
                        "removed": len([bc for bc in breaking_changes if bc.get("type") == "ENDPOINT_REMOVED"]),
                    },
                    "data_model": data_model,
                    "schema_analysis": {
                        "models": schema_models,
                        "models_detected": len(schema_models),
                        "relationships": schema_relationships,
                    },
                    "quality_warnings": quality_warnings,
                    "affected_packages": sorted(all_packages),
                    "database_impact": {
                        "models": database_models,
                        "model_count": len(database_models),
                        "schema_changed": len([bc for bc in breaking_changes if "SCHEMA" in bc.get("type", "") or "ENTITY" in bc.get("type", "")]) > 0,
                        "tables_affected": [m.get("file", "") for m in database_models[:10]],
                    },
                    "risk_analysis": {
                        "operational_risk": "HIGH" if risk_analysis.get("score", 0) > 40 else "MEDIUM" if risk_analysis.get("score", 0) > 15 else "LOW",
                        "blast_radius": "APPLICATION" if len(breaking_changes) > 5 else "SERVICE" if len(breaking_changes) > 0 else "LOCAL",
                        "test_scope": ["unit", "integration"] if breaking_detected else ["unit"],
                        "migration_required": any("SCHEMA" in bc.get("type", "") or bc.get("severity") == "MAJOR" for bc in breaking_changes),
                    },
                    "breaking_change_details": breaking_changes[:50],
                    "change_complexity_score": min(100, len(changes) * 0.1 + len(breaking_changes) * 2),
                    "change_summary": metadata.get("intent", {}).get("message", "No commit message"),
                    "infra_analysis": {
                        "docker": "docker" in (tech_stack.get("infra") or []),
                        "ci_workflow": (tech_stack.get("ci") or [None])[0] if tech_stack.get("ci") else None,
                    },
                    "summary": f"Analyzed {len(changes)} files: {len(all_endpoints)} endpoints, {len(breaking_changes)} breaking changes, stack={tech_stack.get('backend_framework') or 'unknown'}",
                    "doc_contract": documentation_contract,
                    "dependency_classification": {
                        "external_dependencies": external_dependencies_list[:50],
                        "internal_modules": internal_modules_list[:50],
                        "static_assets": [],
                        "dev_dependencies": [],
                    },
                    "repository_evidence": repository_evidence,
                    "architecture_reconstruction": architecture_reconstruction,
                    "architecture_insights": repo_intel.get("architecture_insights", {}),
                    "corrected_call_graph": repo_intel.get("corrected_call_graph", {}),
                    "dead_function_analysis": repo_intel.get("dead_function_analysis", {}),
                    "impact_propagation": repo_intel.get("impact_propagation", {}),

                    "dependency_graph": dependency_graph,
                    "dependency_analysis": dependency_analysis,
                    "call_graph": call_graph,
                    "call_graph_analysis": call_graph_analysis,
                    "impact_analysis": impact_analysis,
                    "search_index": search_index,
                    "code_intelligence": code_intelligence,
                },
            }

            # Save baseline
            baseline_store.save_baseline(project_id, branch, commit_sha, {
                "commit_sha": commit_sha,
                "report": current_normalized_report,
            })
            LOG.info(f"Baseline saved for {project_id}/{branch}/{commit_sha}")

            # Validate Report Data Integrity
            def validate_impact_report(report: dict) -> None:
                """Strict structural validation of the impact report. Aborts on failure."""
                r = report.get("report", {})
                ev = r.get("repository_evidence", {})

                # 1. No undefined components (modules)
                components = {c["name"] for c in ev.get("modules", []) if "name" in c}

                # 2. Duplicate Endpoints Validation + APIs belong to components
                seen_endpoints = set()
                for api in ev.get("apis", []):
                    method = api.get("method", "").upper()
                    path = api.get("path", "")
                    key = f"{method} {path}"

                    if key in seen_endpoints:
                        LOG.error(f"Validation Error: Duplicate endpoint found - {key}")
                        sys.exit(2)
                    seen_endpoints.add(key)

                    if not api.get("controller"):
                        LOG.error(f"Validation Error: API endpoint {key} missing controller reference")
                        sys.exit(2)

                    comp = api.get("module")
                    if not comp or comp not in components:
                        LOG.error(f"Validation Error: API endpoint {key} references unknown or empty module '{comp}'")
                        sys.exit(2)

                for api in r.get("api_surface", []):
                    method = api.get("method", "").upper()
                    path = api.get("path", "")
                    key = f"{method} {path}"
                    if key not in seen_endpoints:
                         pass # It's possible for things to be in surface but not apis directly depending on extraction, but if checked we ensure consistency against knowns.

                # PHASE 2 MANDATORY VALIDATIONS
                # Validation 1: Auth sync mapping
                # Both arrays must match auth status precisely by endpoint.
                api_surface_auth = {f"{a.get('method', '').upper()} {a.get('path', '')}": bool(a.get("auth_required")) for a in r.get("api_surface", [])}
                for ep in r.get("api_contract", {}).get("endpoints", []):
                    key = f"{ep.get('method', '').upper()} {ep.get('path', '')}"
                    if key in api_surface_auth:
                        if bool(ep.get("auth_required")) != api_surface_auth[key]:
                            LOG.error(f"Validation Error: Auth desync on {key}")
                            sys.exit(2)

                # Validation 2: Component field presence & API structure
                for ep in r.get("api_contract", {}).get("endpoints", []):
                    method = ep.get('method')
                    path = ep.get('path')
                    key = f"{str(method).upper()} {path}"

                    if not method:
                        LOG.error("Validation Error: API endpoint missing method")
                        sys.exit(2)
                    if not path or "//" in path:
                        LOG.error("Validation Error: API endpoint missing path or unsanitized")
                        sys.exit(2)
                    if not ep.get("controller") and not ep.get("source", {}).get("controller"):
                        LOG.error(f"Validation Error: API endpoint missing controller for {key}")
                        sys.exit(2)
                    if not ep.get("component"):
                        LOG.error(f"Validation Error: Component field is empty for {key}")
                        sys.exit(2)

                # Validation 3: Dependency classification accuracy
                deps_class = r.get("dependency_classification", {})
                for pkg in deps_class.get("internal_modules", []):
                    if not pkg.startswith(("./", "../", "src/", "app/")):
                        LOG.error(f"Validation Error: internal_module {pkg} is not a valid relative/internal path.")
                        sys.exit(2)
                for pkg in deps_class.get("external_dependencies", []):
                    if pkg.startswith(("./", "../", "src/", "app/")):
                        LOG.error(f"Validation Error: external_dependency {pkg} looks internal.")
                        sys.exit(2)

                # 3. All entities have source files
                for entity in ev.get("entities", []):
                    name = entity.get("name")
                    src = entity.get("source_file")
                    if not src:
                        LOG.error(f"Validation Error: Entity '{name}' is missing a source_file")
                        sys.exit(2)

                # 4. Valid relationships
                valid_nodes = components.union(seen_endpoints)
                for ent in ev.get("entities", []):
                    if "name" in ent:
                        valid_nodes.add(ent["name"])
                for router in ev.get("routers", []):
                    if "name" in router:
                        valid_nodes.add(router["name"])
                for svc in ev.get("services", []):
                    if "name" in svc:
                        valid_nodes.add(svc["name"])

                for rel in ev.get("relationships", []):
                    # For EXPOSES_API, 'to' is 'METHOD /path'
                    rel_type = rel.get("type", "")
                    frm = rel.get("from")
                    to = rel.get("to")

                    if rel_type in ("EXPOSES_API", "CALLS_SERVICE", "USES_ENTITY"):
                        if frm not in valid_nodes:
                            LOG.error(f"Validation Error: Relationship '{rel_type}' has invalid 'from' node: {frm}")
                            sys.exit(2)
                        if to not in valid_nodes:
                            LOG.error(f"Validation Error: Relationship '{rel_type}' has invalid 'to' node: {to}")
                            sys.exit(2)
                    elif rel_type == "IMPORTS_MODULE":
                        if frm not in valid_nodes:
                            LOG.error(f"Validation Error: Relationship '{rel_type}' has invalid 'from' node: {frm}")
                            sys.exit(2)
                        if not str(to or "").strip():
                            LOG.error(f"Validation Error: Relationship '{rel_type}' has empty 'to' node")
                            sys.exit(2)

            LOG.info("Validating impact report integrity...")
            validate_impact_report(impact_report)

            # Write to impact_report.json
            output_path = os.path.join(os.path.dirname(__file__), "impact_report.json")
            dump_stable_file(output_path, impact_report, pretty=True)
            LOG.info(f"Report written to: {output_path}")

            # Print to stdout (for API to parse)
            print(json.dumps(impact_report, ensure_ascii=True, separators=(",", ":")))

        finally:
            git_mgr.cleanup()

    except Exception as e:
        LOG.error(f"Analysis failed: {str(e)}", exc_info=True)
        # Write error report
        error_report = {
            "schema_version": "epic1-impact/v3",
            "version": "1.0.0",
            "status": "error",
            "error": str(e),
            "meta": {"tool_version": "1.0.0", "generated_at": _stable_generated_at({}, "error")},
            "project_id": repo_input.split("/")[-1] if "/" in repo_input else "unknown",
            "branch": branch,
            "commit_sha": "error",
            "baseline_commit": None,
            "analysis_summary": {"total_files": 0, "highest_severity": "PATCH", "breaking_changes_detected": False},
            "breaking_changes": [],
            "statistics": {"total_changes": 0, "major": 0, "minor": 0, "patch": 0},
            "severity": "PATCH",
            "deterministic": True,
            "report": {
                "context": {},
                "analysis_summary": {},
                "changes": [],
                "repository_type": "library",
                "tech_stack": {"backend_framework": None, "frontend_framework": None, "database": None, "orm": None, "infra": [], "ci": []},
                "documentation_contract": {"requires_readme": True, "requires_api_reference": False, "requires_architecture_doc": False, "requires_adr": False},
                "architecture": {"pattern": "modular-monolith", "layers": [], "external_dependencies": []},
                "api_surface": [],
                "data_model": {"entities": [], "relationships": []},
                "doc_contract": {},
                "search_index": {"symbols": [], "references": [], "apis": [], "modules": []},
                "code_intelligence": {
                    "symbol_index": [],
                    "call_graph": {"nodes": [], "edges": []},
                    "dependency_graph": {
                        "modules": [],
                        "dependencies": [],
                        "cycle_detected": False,
                        "circular_dependencies": [],
                    },
                    "repository_graph": {"nodes": [], "edges": []},
                },
            },
        }
        output_path = os.path.join(os.path.dirname(__file__), "impact_report.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(error_report, f, indent=2, ensure_ascii=True)
        print(json.dumps(error_report, ensure_ascii=True, separators=(",", ":")))
        sys.exit(1)


if __name__ == "__main__":
    main()
