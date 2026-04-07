"""
Spring Boot parity engine for deterministic API route and DTO extraction.
Extracts @RestController, method mappings, auth annotations, and DTO fields.
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


def _extract_annotation_value(line: str, param_key: str = "value") -> str:
    """Extract value from Spring annotation: @GetMapping("/path") or @GetMapping(value="/path")."""
    # Try named parameter
    m = re.search(rf'{param_key}\s*=\s*"([^"]*)"', line)
    if m:
        return m.group(1)
    # Try unnamed first positional
    m = re.search(r'\(\s*"([^"]*)"', line)
    if m:
        return m.group(1)
    return "/"


def _extract_request_body_annotation(line: str) -> bool:
    """Check if line contains @RequestBody annotation."""
    return "@RequestBody" in line


def _extract_path_variable_annotation(line: str) -> list[str]:
    """Extract @PathVariable parameter names from line."""
    params = []
    for match in re.finditer(r'@PathVariable\s*(?:\(\s*["\']([\w]+)["\'])?', line):
        if match.group(1):
            params.append(match.group(1))
    for match in re.finditer(
        r'@PathVariable(?:\s*\([^)]*\))?\s+[\w<>,.\[\]?]+\s+([A-Za-z_]\w*)',
        line,
    ):
        params.append(match.group(1))
    return params


def _extract_request_param_annotation(line: str) -> list[str]:
    """Extract @RequestParam parameter names from line."""
    params = []
    for match in re.finditer(r'@RequestParam\s*(?:\(\s*["\']([\w]+)["\'])?', line):
        if match.group(1):
            params.append(match.group(1))
    for match in re.finditer(
        r'@RequestParam(?:\s*\([^)]*\))?\s+[\w<>,.\[\]?]+\s+([A-Za-z_]\w*)',
        line,
    ):
        params.append(match.group(1))
    return params


def _extract_auth_type(lines: list[str], start_idx: int, context_lines: int = 15) -> str:
    """Determine auth type from @PreAuthorize, @RolesAllowed, @Secured annotations."""
    has_preauthorize = False
    has_roles = False
    for idx in range(max(0, start_idx - context_lines), min(len(lines), start_idx + context_lines)):
        line = lines[idx]
        if "@PreAuthorize" in line:
            has_preauthorize = True
        if "@RolesAllowed" in line or "@Secured" in line:
            has_roles = True

    if has_preauthorize and has_roles:
        return "JWT+RBAC"
    if has_preauthorize:
        return "JWT"
    if has_roles:
        return "RBAC"
    return "Public"


def _extract_dto_fields(content: str, class_name: str) -> list[dict]:
    """Extract fields from Spring DTO/entity class definition."""
    fields = []
    # Match class definition and body
    pattern = r"class\s+" + re.escape(class_name) + r"\s*(?:\{[^}]*\{[^}]*\})*\s*\{"
    m = re.search(pattern, content)
    if not m:
        return []

    start = m.end()
    # Find matching closing brace
    depth = 1
    end = start
    while depth > 0 and end < len(content):
        if content[end] == "{":
            depth += 1
        elif content[end] == "}":
            depth -= 1
        end += 1

    class_body = content[start:end-1]
    lines = class_body.split("\n")

    for line in lines:
        # Match field declarations: [private|public] Type fieldName [= default];
        m = re.match(r'\s*(?:private|public|protected)?\s+([\w<>,.?\[\]]+)\s+([A-Za-z_]\w*)\s*(?:=.*)?;', line)
        if m:
            type_str = m.group(1).strip()
            field_name = m.group(2).strip()
            required = "Optional" not in type_str and "?" not in type_str
            fields.append({
                "name": field_name,
                "type": type_str,
                "required": required,
            })

    return fields


def extract_spring_metadata(
    content: str,
    file_path: str,
    read_file: Callable[[str], str | None] = None,
) -> dict:
    """
    Extract Spring Boot metadata: routes, DTOs, auth annotations.
    Returns dict with keys: routes, dtos, context_path, errors.
    """
    routes = []
    dtos = []
    context_path = ""
    errors = []

    lines = content.splitlines()
    class_level_path = "/"
    class_level_auth = "Public"
    current_class = None
    pending_entity = False

    # Extract context path from application.properties or yml if available
    if read_file:
        for cfg_file in ["application.properties", "application.yml", "application.yaml"]:
            cfg_content = read_file(cfg_file)
            if cfg_content:
                m = re.search(r"server\.servlet\.context-path\s*[:=]\s*([^\s#\r\n]+)", cfg_content)
                if m:
                    context_path = _norm_path(m.group(1).strip().strip('"').strip("'"))
                    break

    for idx, line in enumerate(lines):
        class_decl = re.search(r"\bclass\s+([A-Za-z_]\w*)", line)
        if class_decl:
            current_class = class_decl.group(1)
            if pending_entity or current_class.endswith("DTO"):
                fields = _extract_dto_fields(content, current_class)
                if fields or pending_entity:
                    dtos.append({
                        "name": current_class,
                        "fields": fields,
                        "is_entity": pending_entity,
                    })
                pending_entity = False

        # Class-level annotations
        is_rest_controller = "@RestController" in line or "@Controller" in line
        if is_rest_controller:
            # Check next few lines for @RequestMapping
            for look_ahead in range(min(3, len(lines) - idx - 1)):
                next_line = lines[idx + look_ahead + 1]
                if "@RequestMapping" in next_line:
                    class_level_path = _norm_path(_extract_annotation_value(next_line))
                if "@PreAuthorize" in next_line:
                    class_level_auth = "JWT"
                if "@RolesAllowed" in next_line or "@Secured" in next_line:
                    class_level_auth = "RBAC"

        if "@Entity" in line or "@Embeddable" in line:
            pending_entity = True

        # Method-level routes
        method_match = re.search(
            r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)\s*(?:\(([^)]*)\))?",
            line
        )
        if method_match:
            annotation = method_match.group(1)
            args = method_match.group(2) or ""

            if annotation == "RequestMapping":
                method_m = re.search(r"method\s*=\s*RequestMethod\.(\w+)", args)
                if not method_m:
                    continue
                method = method_m.group(1)
            else:
                method = annotation.replace("Mapping", "").upper()

            path = _norm_path(_extract_annotation_value(line))
            full_path = _norm_path(f"{context_path}{class_level_path}{path}")

            has_jwt = False
            has_rbac = False
            for auth_idx in range(idx, min(idx + 5, len(lines))):
                auth_line = lines[auth_idx]
                if "@PreAuthorize" in auth_line:
                    has_jwt = True
                if "@RolesAllowed" in auth_line or "@Secured" in auth_line:
                    has_rbac = True
                if auth_line.strip().startswith(("public", "private", "protected")):
                    break

            if has_jwt and has_rbac:
                auth_type = "JWT+RBAC"
            elif has_jwt:
                auth_type = "JWT"
            elif has_rbac:
                auth_type = "RBAC"
            else:
                auth_type = class_level_auth

            # Collect parameters from next few lines
            path_params = []
            query_params = []
            has_body = False
            for param_idx in range(idx + 1, min(idx + 10, len(lines))):
                param_line = lines[param_idx]
                path_params.extend(_extract_path_variable_annotation(param_line))
                query_params.extend(_extract_request_param_annotation(param_line))
                has_body = has_body or _extract_request_body_annotation(param_line)
                if "{" in param_line and not param_line.strip().startswith("@"):
                    # Reached method body
                    break

            path_params = sorted(set(path_params))
            query_params = sorted(set(query_params))
            if not path_params:
                path_params = sorted(set(re.findall(r"\{([A-Za-z_]\w*)\}", full_path)))

            routes.append({
                "method": method,
                "path": full_path,
                "class": current_class or "UnknownController",
                "line": idx + 1,
                "auth_type": auth_type,
                "path_params": path_params,
                "query_params": query_params,
                "has_request_body": has_body,
            })

    return {
        "routes": routes,
        "dtos": dtos,
        "context_path": context_path,
        "errors": errors,
    }


def extract_spring_schema_diffs(
    baseline_metadata: dict | None,
    current_metadata: dict,
) -> list[dict]:
    """
    Compare Spring DTOs between baseline and current.
    Returns list of schema change descriptors.
    """
    changes = []

    baseline_dtos = {dto["name"]: dto for dto in (baseline_metadata or {}).get("dtos", [])}
    current_dtos = {dto["name"]: dto for dto in current_metadata.get("dtos", [])}

    # Added DTOs
    for name in current_dtos:
        if name not in baseline_dtos:
            changes.append({
                "type": "SCHEMA_BREAKING_CHANGE",
                "entity": name,
                "change": "ENTITY_ADDED",
                "description": f"New DTO/entity class '{name}' added",
                "severity": "MINOR",
                "classification_basis": "SCHEMA_DIFF",
                "id": _stable_id({"type": "SCHEMA_BREAKING_CHANGE", "entity": name, "change": "ENTITY_ADDED"}),
            })

    # Removed DTOs
    for name in baseline_dtos:
        if name not in current_dtos:
            changes.append({
                "type": "SCHEMA_BREAKING_CHANGE",
                "entity": name,
                "change": "ENTITY_REMOVED",
                "description": f"DTO/entity class '{name}' removed",
                "severity": "MAJOR",
                "classification_basis": "SCHEMA_DIFF",
                "id": _stable_id({"type": "SCHEMA_BREAKING_CHANGE", "entity": name, "change": "ENTITY_REMOVED"}),
            })

    # Modified DTOs - field changes
    for name in baseline_dtos:
        if name not in current_dtos:
            continue

        baseline_fields = {f["name"]: f for f in baseline_dtos[name].get("fields", [])}
        current_fields = {f["name"]: f for f in current_dtos[name].get("fields", [])}

        # Removed fields
        for field_name in baseline_fields:
            if field_name not in current_fields:
                changes.append({
                    "type": "SCHEMA_BREAKING_CHANGE",
                    "entity": name,
                    "field": field_name,
                    "change": "FIELD_REMOVED",
                    "description": f"Field '{field_name}' removed from DTO '{name}'",
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
                        "description": f"Required field '{field_name}' added to DTO '{name}'",
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


def extract_spring_route_diffs(
    baseline_metadata: dict | None,
    current_metadata: dict,
) -> list[dict]:
    """
    Compare Spring routes between baseline and current.
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
