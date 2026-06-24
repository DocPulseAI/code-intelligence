"""Framework adapter coordinator for canonical API route extraction."""

from __future__ import annotations

import re
from typing import Callable

from src.intelligence.adapters.express_adapter import ExpressApiSurfaceAdapter
from src.intelligence.adapters.fastapi_adapter import FastApiSurfaceAdapter
from src.intelligence.adapters.spring_adapter import SpringBootApiSurfaceAdapter
from src.intelligence.api_surface_adapter import ApiSurfaceAdapter, ResolvedRoute

_VALID_AUTH = {"JWT", "Session", "RBAC", "JWT+RBAC", "Public"}
_OP_ID_RE = re.compile(r"^[a-z][A-Za-z0-9]*(?:_[0-9]+)?$")


def _detect_frameworks(file_paths: list[str], read_file: Callable[[str], str | None], tech_stack: dict) -> list[str]:
    frameworks: set[str] = set()
    backend = str((tech_stack or {}).get("backend_framework", "")).lower()
    if "express" in backend:
        frameworks.add("express")
    if "spring" in backend:
        frameworks.add("spring")
    if "fastapi" in backend:
        frameworks.add("fastapi")

    for path in sorted(file_paths):
        if path.endswith(("package.json", ".js", ".ts", ".jsx", ".tsx")):
            txt = (read_file(path) or "").lower()
            if '"express"' in txt or "express.router(" in txt:
                frameworks.add("express")
        if path.endswith((".java", ".kt")):
            txt = read_file(path) or ""
            if "@RestController" in txt or "@RequestMapping" in txt:
                frameworks.add("spring")
        if path.endswith(".py"):
            txt = read_file(path) or ""
            if "FastAPI(" in txt or "APIRouter(" in txt or "include_router(" in txt:
                frameworks.add("fastapi")

    # Preserve stable adapter order.
    ordered = [name for name in ["express", "spring", "fastapi"] if name in frameworks]
    return ordered


def _routes_to_candidates(routes: list[ResolvedRoute]) -> list[dict]:
    out: list[dict] = []
    for r in routes:
        out.append(
            {
                "method": r.method,
                "path": r.full_path,
                "normalized_key": r.normalized_key,
                "operation_id": r.operation_id,
                "resolved_auth_type": r.auth_type,
                "endpoint_hash": r.endpoint_hash,
                "source_file": r.source_file,
                "line_start": r.line_start,
                "router_symbol": "",
                "middleware_tokens": [],
            }
        )
    return out


def _validate_routes(rows: list[ResolvedRoute]) -> tuple[bool, str]:
    seen_keys: set[str] = set()
    seen_hashes: set[str] = set()
    for row in rows:
        if row.normalized_key in seen_keys:
            return False, "Duplicate normalized route detected"
        if row.endpoint_hash in seen_hashes:
            return False, "Duplicate endpoint hash detected"
        if row.auth_type not in _VALID_AUTH:
            return False, "Invalid auth classification detected"
        if not _OP_ID_RE.fullmatch(row.operation_id):
            return False, "Malformed operation_id detected"
        seen_keys.add(row.normalized_key)
        seen_hashes.add(row.endpoint_hash)
    return True, ""


def _build_adapter(framework: str, express_resolver) -> ApiSurfaceAdapter:
    if framework == "express":
        return ExpressApiSurfaceAdapter(express_resolver)
    if framework == "spring":
        return SpringBootApiSurfaceAdapter()
    if framework == "fastapi":
        return FastApiSurfaceAdapter()
    raise ValueError(f"Unsupported adapter: {framework}")


def resolve_with_framework_adapters(
    candidates: list[dict],
    file_paths: list[str],
    read_file: Callable[[str], str | None],
    tech_stack: dict,
    express_resolver,
) -> dict:
    frameworks = _detect_frameworks(file_paths, read_file, tech_stack or {})
    if not frameworks:
        return {"validation_status": "OK", "candidates": list(candidates)}

    all_routes: list[ResolvedRoute] = []
    coverage_metrics = {}  # Preserve coverage metrics from first framework
    passthrough_candidates: list[dict] = []

    for name in frameworks:
        adapter = _build_adapter(name, express_resolver)
        extracted = adapter.extract_candidates(candidates, file_paths, read_file, tech_stack or {})
        # Backward compatibility: preserve caller-supplied candidates for non-Express
        # frameworks when adapter extraction yields no static matches.
        if not extracted and candidates and name in {"fastapi", "spring"}:
            passthrough_candidates = sorted(
                [dict(c) for c in candidates],
                key=lambda c: (
                    str(c.get("method", "")).upper(),
                    str(c.get("path", "")),
                    str(c.get("source_file", "")),
                    int(c.get("line_start", 0) or 0),
                ),
            )
            continue
        resolved = adapter.resolve_mounts(extracted, file_paths, read_file, tech_stack or {})
        if resolved.get("validation_status") == "FAILED":
            return resolved

        # Preserve coverage_metrics from the resolver if available
        if not coverage_metrics and resolved.get("coverage_metrics"):
            coverage_metrics = resolved.get("coverage_metrics", {})

        reachable = adapter.filter_reachable(list(resolved.get("candidates", [])), file_paths, read_file, tech_stack or {})
        all_routes.extend(adapter.build_resolved_routes(reachable))

    if not all_routes and passthrough_candidates:
        result = {"validation_status": "OK", "candidates": passthrough_candidates}
        if coverage_metrics:
            result["coverage_metrics"] = coverage_metrics
        return result

    ordered_routes = sorted(
        all_routes,
        key=lambda r: (
            str(r.normalized_key),
            str(r.source_file),
            int(r.line_start or 0),
            str(r.operation_id),
        ),
    )
    ok, err = _validate_routes(ordered_routes)
    if not ok:
        return {"validation_status": "FAILED", "error": err}

    result = {"validation_status": "OK", "candidates": _routes_to_candidates(ordered_routes)}

    # Include coverage_metrics in the result if available
    if coverage_metrics:
        result["coverage_metrics"] = coverage_metrics

    return result
