"""
FastAPI parity engine for deterministic API route and Pydantic model extraction.
Extracts routes, prefixes, auth decorators, and model fields.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Optional


def _canonical(data: Any) -> str:
    """Canonical JSON encoding for deterministic hashing."""
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _stable_id(payload: dict) -> str:
    """Generate deterministic id for a payload."""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def _norm_path(path: str) -> str:
    """Normalize path: ensure leading slash, collapse double slashes, trim trailing slash."""
    p = str(path or "").strip()
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/{2,}", "/", p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def _extract_string_value(text: str, key: str = "prefix") -> str:
    """Extract string value from keyword argument: prefix="/v1" or prefix='/api'."""
    pattern = rf'{key}\s*=\s*["\']([^"\']*)["\']'
    m = re.search(pattern, text)
    return m.group(1) if m else ""


def _extract_pydantic_model_fields(content: str, class_name: str) -> list[dict]:
    """Extract fields from Pydantic BaseModel class definition."""
    fields = []
    # Match class definition
    pattern = rf"class\s+{re.escape(class_name)}\s*\([^)]*BaseModel[^)]*\)\s*:"
    m = re.search(pattern, content)
    if not m:
        return []

    # Extract class body
    start = m.end()
    lines = content[start:].split("\n")

    for line in lines[:30]:  # Limit to first 30 lines of class
        if line and not line[0].isspace():
            break  # Reached end of class

        # Match field annotation: field_name: Type [= default]
        m = re.match(r'\s+([A-Za-z_]\w*)\s*:\s*([\w\[\],\.\|?\s]+?)(?:\s*=\s*.*)?$', line)
        if m:
            field_name = m.group(1).strip()
            type_annotation = m.group(2).strip()
            required = "Optional" not in type_annotation and "| None" not in type_annotation
            fields.append({
                "name": field_name,
                "type": type_annotation,
                "required": required,
            })

    return fields


def _extract_auth_type_from_depends(line: str) -> str:
    """Determine auth type from Depends(...) parameters."""
    # Check for OAuth2PasswordBearer
    if "OAuth2PasswordBearer" in line or "oauth" in line.lower():
        return "JWT"
    # Check for role/permission checks
    if "role" in line.lower() or "permission" in line.lower():
        return "RBAC"
    # Generic auth dependency
    if "Depends" in line and "auth" in line.lower():
        return "JWT"
    return "Public"


def _resolve_router_symbol(alias: str, alias_map: dict[str, str]) -> str:
    """Resolve import alias to actual symbol name."""
    return alias_map.get(alias, alias)


def extract_fastapi_metadata(
    content: str,
    file_path: str,
) -> dict:
    """
    Extract FastAPI metadata: routes, routers, models, auth.
    Returns dict with keys: routes, routers, models, errors.
    """
    routes = []
    routers = {}
    models = []
    errors = []

    lines = content.splitlines()
    alias_map = {}
    router_prefixes = {}
    include_edges = []

    # Phase 1: Map imports and collect router definitions
    for idx, line in enumerate(lines):
        # Track imports with aliases: from X import Y as Z
        import_match = re.search(r"from\s+[\w.]+\s+import\s+([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?", line)
        if import_match:
            src = import_match.group(1)
            dst = import_match.group(2) or src
            alias_map[dst] = src

        # Collect FastAPI app instantiation
        app_match = re.search(r"([A-Za-z_]\w*)\s*=\s*FastAPI\s*\(", line)
        if app_match:
            app_symbol = app_match.group(1)
            routers[app_symbol] = {"prefix": "", "children": []}

        # Collect APIRouter instantiation with prefix
        router_match = re.search(r"([A-Za-z_]\w*)\s*=\s*APIRouter\s*\(([^)]*)\)", line)
        if router_match:
            router_symbol = router_match.group(1)
            args = router_match.group(2)
            prefix = _extract_string_value(args, "prefix")
            routers[router_symbol] = {"prefix": prefix or "", "children": []}

        # Track include_router calls: app.include_router(router, prefix="/api")
        include_match = re.search(r"([A-Za-z_]\w*)\.include_router\s*\(\s*([A-Za-z_]\w*)(?:\s*,\s*([^)]*))?\)", line)
        if include_match:
            parent = include_match.group(1)
            child = _resolve_router_symbol(include_match.group(2), alias_map)
            args = include_match.group(3) or ""
            prefix = _extract_string_value(args, "prefix")
            include_edges.append((parent, child, prefix, idx + 1))

    # Phase 2: Resolve router prefix chains (depth guard=10)
    resolved_prefixes = {}
    for router_symbol, router_data in routers.items():
        resolved_prefixes[router_symbol] = [("", tuple())]

    for iteration in range(10):
        changed = False
        for parent, child, prefix, _ in include_edges:
            if parent not in resolved_prefixes or child not in resolved_prefixes:
                continue

            parent_contexts = resolved_prefixes[parent]
            child_contexts = resolved_prefixes[child]
            added = set()

            for base_prefix, _ in parent_contexts:
                new_prefix = _norm_path(f"{base_prefix}/{prefix}") if prefix else _norm_path(base_prefix or "/")
                item = (new_prefix if new_prefix != "/" else "", tuple())
                if item not in added:
                    added.add(item)
                    child_contexts.append(item)
                    changed = True

            resolved_prefixes[child] = sorted(list(set(child_contexts)), key=lambda x: (x[0], x[1]))

        if not changed:
            break

    # Phase 3: Extract routes with resolved prefixes
    for idx, line in enumerate(lines):
        route_match = re.search(
            r"@([A-Za-z_]\w*)\.(?:get|post|put|patch|delete)\s*\((?:['\"]([^'\"]*)['\"])?\s*(?:[^)]*)\)",
            line,
            re.IGNORECASE
        )
        if route_match:
            router_symbol = route_match.group(1)
            route_path = route_match.group(2) or "/"
            method = line[line.find("@") + 1:].split(".")[1].split("(")[0].upper()

            # Get all possible full paths for this route from resolved prefixes
            prefixes = resolved_prefixes.get(router_symbol, [("", tuple())])
            for base_prefix, _ in prefixes:
                full_path = _norm_path(f"{base_prefix}/{route_path}")

                # Extract auth type from decorator or next few lines
                auth_type = "Public"
                for check_idx in range(idx, min(idx + 5, len(lines))):
                    check_line = lines[check_idx]
                    if "Depends" in check_line:
                        auth_type = _extract_auth_type_from_depends(check_line)
                        break

                routes.append({
                    "method": method,
                    "path": full_path,
                    "router": router_symbol,
                    "line": idx + 1,
                    "auth_type": auth_type,
                    "raw_path": route_path,
                })

    # Phase 4: Extract Pydantic models
    for idx, line in enumerate(lines):
        model_match = re.search(r"class\s+([A-Za-z_]\w*)\s*\([^)]*BaseModel[^)]*\)\s*:", line)
        if model_match:
            model_name = model_match.group(1)
            fields = _extract_pydantic_model_fields("\n".join(lines[idx:]), model_name)
            if fields or "BaseModel" in line:
                models.append({
                    "name": model_name,
                    "fields": fields,
                    "is_request": "Request" in model_name or "Input" in model_name,
                    "is_response": "Response" in model_name or "Output" in model_name,
                })

    return {
        "routes": routes,
        "routers": routers,
        "models": models,
        "resolved_prefixes": resolved_prefixes,
        "include_edges": include_edges,
        "errors": errors,
    }


def extract_fastapi_schema_diffs(
    baseline_metadata: dict | None,
    current_metadata: dict,
) -> list[dict]:
    """
    Compare Pydantic models between baseline and current.
    Returns list of schema change descriptors.
    """
    changes = []

    baseline_models = {m["name"]: m for m in (baseline_metadata or {}).get("models", [])}
    current_models = {m["name"]: m for m in current_metadata.get("models", [])}

    # Added models
    for name in current_models:
        if name not in baseline_models:
            changes.append({
                "type": "SCHEMA_BREAKING_CHANGE",
                "entity": name,
                "change": "ENTITY_ADDED",
                "description": f"New Pydantic model '{name}' added",
                "severity": "MINOR",
                "classification_basis": "SCHEMA_DIFF",
                "id": _stable_id({"type": "SCHEMA_BREAKING_CHANGE", "entity": name, "change": "ENTITY_ADDED"}),
            })

    # Removed models
    for name in baseline_models:
        if name not in current_models:
            changes.append({
                "type": "SCHEMA_BREAKING_CHANGE",
                "entity": name,
                "change": "ENTITY_REMOVED",
                "description": f"Pydantic model '{name}' removed",
                "severity": "MAJOR",
                "classification_basis": "SCHEMA_DIFF",
                "id": _stable_id({"type": "SCHEMA_BREAKING_CHANGE", "entity": name, "change": "ENTITY_REMOVED"}),
            })

    # Modified models - field changes
    for name in baseline_models:
        if name not in current_models:
            continue

        baseline_fields = {f["name"]: f for f in baseline_models[name].get("fields", [])}
        current_fields = {f["name"]: f for f in current_models[name].get("fields", [])}

        # Removed fields
        for field_name in baseline_fields:
            if field_name not in current_fields:
                changes.append({
                    "type": "SCHEMA_BREAKING_CHANGE",
                    "entity": name,
                    "field": field_name,
                    "change": "FIELD_REMOVED",
                    "description": f"Field '{field_name}' removed from model '{name}'",
                    "severity": "MAJOR",
                    "classification_basis": "SCHEMA_DIFF",
                    "id": _stable_id({"entity": name, "field": field_name, "change": "FIELD_REMOVED"}),
                })

        # Added required fields
        for field_name in current_fields:
            if field_name not in baseline_fields:
                field = current_fields[field_name]
                if field.get("required"):
                    changes.append({
                        "type": "SCHEMA_BREAKING_CHANGE",
                        "entity": name,
                        "field": field_name,
                        "change": "REQUIRED_FIELD_ADDED",
                        "description": f"Required field '{field_name}' added to model '{name}'",
                        "severity": "MINOR",
                        "classification_basis": "SCHEMA_DIFF",
                        "id": _stable_id({"entity": name, "field": field_name, "change": "REQUIRED_FIELD_ADDED"}),
                    })

        # Modified field types
        for field_name in baseline_fields:
            if field_name in current_fields:
                baseline_type = baseline_fields[field_name].get("type", "")
                current_type = current_fields[field_name].get("type", "")
                if baseline_type != current_type:
                    changes.append({
                        "type": "SCHEMA_BREAKING_CHANGE",
                        "entity": name,
                        "field": field_name,
                        "change": "FIELD_TYPE_CHANGED",
                        "description": f"Field '{field_name}' type changed in '{name}' from '{baseline_type}' to '{current_type}'",
                        "severity": "MAJOR",
                        "classification_basis": "SCHEMA_DIFF",
                        "id": _stable_id({"entity": name, "field": field_name, "change": "FIELD_TYPE_CHANGED", "old": baseline_type, "new": current_type}),
                    })

    return sorted(changes, key=lambda c: (c["entity"], c.get("field", ""), c["change"]))


def extract_fastapi_route_diffs(
    baseline_metadata: dict | None,
    current_metadata: dict,
) -> list[dict]:
    """
    Compare FastAPI routes between baseline and current.
    Returns list of route change descriptors.
    """
    changes = []

    baseline_routes = {(r["method"], r["path"]): r for r in (baseline_metadata or {}).get("routes", [])}
    current_routes = {(r["method"], r["path"]): r for r in current_metadata.get("routes", [])}

    # Added routes
    for (method, path) in current_routes:
        if (method, path) not in baseline_routes:
            changes.append({
                "type": "API_ENDPOINT_CHANGE",
                "endpoint": f"{method} {path}",
                "change": "ROUTE_ADDED",
                "description": f"New route: {method} {path}",
                "severity": "MINOR",
                "classification_basis": "STRUCTURAL_DIFF",
                "id": _stable_id({"type": "API_ENDPOINT_CHANGE", "endpoint": f"{method} {path}", "change": "ROUTE_ADDED"}),
            })

    # Removed routes
    for (method, path) in baseline_routes:
        if (method, path) not in current_routes:
            changes.append({
                "type": "API_ENDPOINT_CHANGE",
                "endpoint": f"{method} {path}",
                "change": "ROUTE_REMOVED",
                "description": f"Route removed: {method} {path}",
                "severity": "MAJOR",
                "classification_basis": "STRUCTURAL_DIFF",
                "id": _stable_id({"type": "API_ENDPOINT_CHANGE", "endpoint": f"{method} {path}", "change": "ROUTE_REMOVED"}),
            })

    # Modified auth
    for (method, path) in baseline_routes:
        if (method, path) not in current_routes:
            continue
        baseline_auth = baseline_routes[(method, path)].get("auth_type", "Public")
        current_auth = current_routes[(method, path)].get("auth_type", "Public")
        if baseline_auth != current_auth:
            severity = "MAJOR" if baseline_auth == "Public" and current_auth != "Public" else "MINOR"
            changes.append({
                "type": "AUTH_CHANGE",
                "endpoint": f"{method} {path}",
                "change": "AUTH_TYPE_CHANGED",
                "description": f"Auth type changed for {method} {path} from '{baseline_auth}' to '{current_auth}'",
                "severity": severity,
                "classification_basis": "AUTH_CHANGE",
                "id": _stable_id({"endpoint": f"{method} {path}", "change": "AUTH_TYPE_CHANGED", "old_auth": baseline_auth, "new_auth": current_auth}),
            })

    return sorted(changes, key=lambda c: (c["endpoint"], c["change"]))
