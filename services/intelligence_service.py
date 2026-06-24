import logging
import hashlib
import re
from typing import Optional

# Intelligence imports
from src.intelligence.tech_stack_model import build_tech_stack
from src.intelligence.api_surface import build_api_surface
from src.intelligence.repository_classifier import classify_repository_type
from src.intelligence.architecture_model import build_architecture_model
from src.intelligence.data_model_graph import build_data_model
from src.intelligence.schema_diff_engine import extract_canonical_models
from src.intelligence.repository_evidence import build_repository_evidence
from src.intelligence.architecture_reconstructor import reconstruct_architecture
from src.intelligence.dependency_graph_engine import build_dependency_graph
from src.intelligence.call_graph_engine import build_call_graph
from src.intelligence.call_graph_analysis_engine import analyze_call_graph
from src.intelligence.code_intelligence_builder import build_code_intelligence
from src.intelligence.dependency_analysis_engine import analyze_dependencies
from src.intelligence.impact_analysis_engine import build_impact_analysis
from src.intelligence.search_index_builder import build_search_index
from src.intelligence.repository_intelligence_engine import analyze_repository_intelligence

LOG = logging.getLogger("epic1.cli")

_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
_JWT_HINTS = {"protect", "auth", "authenticate", "verifytoken", "jwt"}
_RBAC_HINTS = {"rbac", "role", "authorize"}

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
            if normalized_route != normalized_candidate and not normalized_candidate.endswith(normalized_route):
                continue
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


def _build_api_contract_endpoints(candidates: list[dict]) -> tuple[list[dict], list[str]]:
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


class IntelligenceService:
    def build_intelligence_layers(self, raw_analysis: dict) -> dict:
        """
        Coordinates the high-level AST layer constructions:
        API surface, schema, search index, call graph, dependency graph.
        """
        all_file_paths = raw_analysis["all_file_paths"]
        read_file = raw_analysis["read_file"]
        changes = raw_analysis["changes"]
        all_endpoints = raw_analysis["all_endpoints"]
        all_packages = raw_analysis["all_packages"]
        severity_counts = raw_analysis["severity_counts"]
        database_models = raw_analysis["database_models"]
        file_features = raw_analysis["file_features"]
        file_schema_tags = raw_analysis["file_schema_tags"]
        repo_name = raw_analysis["repo_name"]
        commit_sha = raw_analysis["commit_sha"]

        LOG.info("Building tech stack analysis...")
        tech_stack = build_tech_stack(all_file_paths, read_file)

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
            {"endpoints": repository_evidence.get('apis', [])},
            dependency_graph,
            call_graph,
            architecture_reconstruction,
            changes,
            database_models,
            dependency_analysis
        )

        # Override api_surface and api_contract with resolved endpoints
        ev_apis = repository_evidence.get("apis", [])
        if ev_apis:
            LOG.info(f"Overriding api_surface with {len(ev_apis)} resolved endpoints from repository_evidence")
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
            all_endpoints = sorted(
                resolved_endpoints_for_surface,
                key=lambda ep: (ep.get("method", ""), ep.get("path", ""))
            )

        LOG.info("Classifying repository type...")
        repository_type = classify_repository_type(all_file_paths, read_file, len(all_endpoints))

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

        internal_modules_list = []
        external_dependencies_list = []
        for pkg in sorted(all_packages):
            if pkg.startswith(("./", "../", "src/", "app/")):
                internal_modules_list.append(pkg)
            else:
                external_dependencies_list.append(pkg)

        return {
            "current_normalized_report": current_normalized_report,
            "tech_stack": tech_stack,
            "api_surface": api_surface,
            "all_endpoints": all_endpoints,
            "architecture": architecture,
            "data_model": data_model,
            "schema_models": schema_models,
            "schema_relationships": schema_relationships,
            "repository_evidence": repository_evidence,
            "architecture_reconstruction": architecture_reconstruction,
            "dependency_graph": dependency_graph,
            "call_graph": call_graph,
            "call_graph_analysis": call_graph_analysis,
            "code_intelligence": code_intelligence,
            "dependency_analysis": dependency_analysis,
            "impact_analysis": impact_analysis,
            "search_index": search_index,
            "repo_intel": repo_intel,
            "repository_type": repository_type,
            "highest_severity": highest_severity,
            "quality_warnings": quality_warnings,
            "internal_modules_list": internal_modules_list,
            "external_dependencies_list": external_dependencies_list,
        }
