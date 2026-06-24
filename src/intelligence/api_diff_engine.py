"""Deterministic API surface diff engine."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_SEVERITY_ORDER = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}
_IGNORE_ROUTE_PREFIXES = (
    "/",
    "/health",
    "/healthz",
    "/ready",
    "/live",
    "/swagger",
    "/openapi",
    "/docs",
    "/static",
    "/assets",
)


def _canonical(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _stable_id(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def _norm_path(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        return "/"
    if not value.startswith("/"):
        value = "/" + value
    value = re.sub(r"/{2,}", "/", value)
    return value[:-1] if len(value) > 1 and value.endswith("/") else value


def _is_business_endpoint(path: str) -> bool:
    p = _norm_path(path).lower()
    if p == "/":
        return False
    for prefix in _IGNORE_ROUTE_PREFIXES[1:]:
        if p == prefix or p.startswith(prefix + "/"):
            return False
    return True


def _extract_path_params(path: str) -> set[str]:
    return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", _norm_path(path)))


def _required_request_fields(endpoint: dict) -> set[str]:
    req = endpoint.get("request", {}) if isinstance(endpoint.get("request"), dict) else {}
    body = req.get("body_schema") if isinstance(req.get("body_schema"), dict) else {}
    required = body.get("required") if isinstance(body.get("required"), list) else []
    return set(str(x) for x in required if str(x).strip())


def _request_field_types(endpoint: dict) -> dict[str, str]:
    req = endpoint.get("request", {}) if isinstance(endpoint.get("request"), dict) else {}
    body = req.get("body_schema") if isinstance(req.get("body_schema"), dict) else {}
    props = body.get("properties") if isinstance(body.get("properties"), dict) else {}
    out: dict[str, str] = {}
    for key, val in props.items():
        if isinstance(val, dict):
            out[str(key)] = str(val.get("type", "unknown"))
    return out


def _response_schema_signature(endpoint: dict) -> str:
    responses = endpoint.get("responses")
    return hashlib.sha256(_canonical(responses).encode("utf-8")).hexdigest()


def _auth_type(endpoint: dict) -> str:
    auth = endpoint.get("auth") if isinstance(endpoint.get("auth"), dict) else {}
    typ = str(auth.get("type", "Public"))
    return typ if typ in {"JWT", "RBAC", "JWT+RBAC", "Session", "Public"} else "Public"


def _auth_required(endpoint: dict) -> bool:
    auth = endpoint.get("auth") if isinstance(endpoint.get("auth"), dict) else {}
    return bool(auth.get("required") is True)


def _endpoint_key(endpoint: dict) -> str:
    method = str(endpoint.get("method", "GET")).upper()
    path = _norm_path(str(endpoint.get("path", "/")))
    return f"{method} {path}"


def _operation_id(endpoint: dict) -> str:
    return str(endpoint.get("operation_id", "")).strip()


def _request_properties(endpoint: dict) -> dict[str, dict]:
    req = endpoint.get("request", {}) if isinstance(endpoint.get("request"), dict) else {}
    body = req.get("body_schema") if isinstance(req.get("body_schema"), dict) else {}
    props = body.get("properties") if isinstance(body.get("properties"), dict) else {}
    out: dict[str, dict] = {}
    for key, value in props.items():
        out[str(key)] = value if isinstance(value, dict) else {"type": str(type(value).__name__)}
    return out


def _responses_by_status(endpoint: dict) -> dict[str, dict]:
    responses = endpoint.get("responses")
    out: dict[str, dict] = {}
    if not isinstance(responses, list):
        return out
    for item in responses:
        if not isinstance(item, dict):
            continue
        if "status" not in item:
            continue
        key = str(item.get("status"))
        out[key] = item
    return out


def _unique_operation_map(endpoints: list[dict]) -> dict[str, dict]:
    counts: dict[str, int] = {}
    for ep in endpoints:
        op = _operation_id(ep)
        if op:
            counts[op] = counts.get(op, 0) + 1
    return {
        op: ep
        for ep in endpoints
        if (op := _operation_id(ep)) and counts.get(op, 0) == 1
    }


def _findings_sort_key(item: dict) -> tuple:
    return (
        -_SEVERITY_ORDER.get(str(item.get("severity", "PATCH")), 1),
        str(item.get("endpoint", "")),
        str(item.get("type", "")),
        str(item.get("id", "")),
    )


def _descriptor(change_type: str, endpoint: str, description: str, severity: str, basis: str) -> dict:
    row = {
        "type": change_type,
        "endpoint": endpoint,
        "entity": endpoint,
        "file": "",
        "description": description,
        "severity": severity,
        "classification_basis": basis,
    }
    row["id"] = _stable_id(row)
    return row


def diff_api_surfaces(baseline_report: dict | None, current_report: dict) -> list[dict]:
    baseline_endpoints = list(((baseline_report or {}).get("api_contract", {}) or {}).get("endpoints", []))
    current_endpoints = list((current_report.get("api_contract", {}) or {}).get("endpoints", []))

    base_map = {_endpoint_key(ep): ep for ep in baseline_endpoints if _is_business_endpoint(str(ep.get("path", "/")))}
    curr_map = {_endpoint_key(ep): ep for ep in current_endpoints if _is_business_endpoint(str(ep.get("path", "/")))}

    findings: list[dict] = []

    # A. Endpoint removed
    for key in sorted(set(base_map.keys()) - set(curr_map.keys())):
        findings.append(
            _descriptor(
                "API_REMOVAL",
                key,
                f"Endpoint removed: {key}",
                "MAJOR",
                "ROUTE_REMOVAL",
            )
        )

    # B/C/D strict method/path changes matched only by deterministic operation_id.
    base_by_operation = _unique_operation_map(list(base_map.values()))
    curr_by_operation = _unique_operation_map(list(curr_map.values()))
    for op_id in sorted(set(base_by_operation.keys()) & set(curr_by_operation.keys())):
        old = base_by_operation[op_id]
        new = curr_by_operation[op_id]
        old_method = str(old.get("method", "GET")).upper()
        new_method = str(new.get("method", "GET")).upper()
        old_path = _norm_path(str(old.get("path", "/")))
        new_path = _norm_path(str(new.get("path", "/")))
        old_key = f"{old_method} {old_path}"
        new_key = f"{new_method} {new_path}"
        if old_method != new_method:
            findings.append(
                _descriptor(
                    "API_SIGNATURE_CHANGE",
                    new_key,
                    f"HTTP method changed: {old_key} -> {new_key}",
                    "MAJOR",
                    "STRUCTURAL_DIFF",
                )
            )
        if old_path != new_path:
            findings.append(
                _descriptor(
                    "API_SIGNATURE_CHANGE",
                    new_key,
                    f"Path changed: {old_path} -> {new_path}",
                    "MAJOR",
                    "STRUCTURAL_DIFF",
                )
            )
            old_params = _extract_path_params(old_path)
            new_params = _extract_path_params(new_path)
            if not old_params.issubset(new_params):
                findings.append(
                    _descriptor(
                        "API_SIGNATURE_CHANGE",
                        new_key,
                        f"Path parameter removed: {', '.join(sorted(old_params - new_params))}",
                        "MAJOR",
                        "STRUCTURAL_DIFF",
                )
            )

    # E/F/G/H compare shared endpoint keys.
    for key in sorted(set(base_map.keys()) & set(curr_map.keys())):
        old = base_map[key]
        new = curr_map[key]

        old_required = _required_request_fields(old)
        new_required = _required_request_fields(new)
        added_required = sorted(new_required - old_required)
        if added_required:
            findings.append(
                _descriptor(
                    "API_SIGNATURE_CHANGE",
                    key,
                    f"Required request field added: {', '.join(added_required)}",
                    "MAJOR",
                    "STRUCTURAL_DIFF",
                )
            )

        old_props = _request_properties(old)
        new_props = _request_properties(new)
        optional_added = sorted(
            field
            for field in (set(new_props.keys()) - set(old_props.keys()))
            if field not in set(added_required)
        )
        if optional_added:
            findings.append(
                _descriptor(
                    "API_REQUEST_EXPANSION",
                    key,
                    f"Optional request field added: {', '.join(optional_added)}",
                    "MINOR",
                    "STRUCTURAL_DIFF",
                )
            )

        old_types = _request_field_types(old)
        new_types = _request_field_types(new)
        for field in sorted(set(old_types.keys()) & set(new_types.keys())):
            if old_types[field] != new_types[field]:
                findings.append(
                    _descriptor(
                        "API_SIGNATURE_CHANGE",
                        key,
                        f"Type mutation: {field} ({old_types[field]} -> {new_types[field]})",
                        "MAJOR",
                        "STRUCTURAL_DIFF",
                    )
                )

        old_responses = _responses_by_status(old)
        new_responses = _responses_by_status(new)
        removed_status = sorted(set(old_responses.keys()) - set(new_responses.keys()))
        if removed_status:
            findings.append(
                _descriptor(
                    "API_SIGNATURE_CHANGE",
                    key,
                    f"Status code removed: {', '.join(removed_status)}",
                    "MAJOR",
                    "STRUCTURAL_DIFF",
                )
            )
        for status in sorted(set(old_responses.keys()) & set(new_responses.keys())):
            if _canonical(old_responses[status]) != _canonical(new_responses[status]):
                findings.append(
                    _descriptor(
                        "API_SIGNATURE_CHANGE",
                        key,
                        f"Response schema incompatible change at status {status}",
                        "MAJOR",
                        "STRUCTURAL_DIFF",
                    )
                )

        # Auth tightening (Public/open -> protected).
        old_type = _auth_type(old)
        new_type = _auth_type(new)
        if (old_type == "Public" and new_type != "Public") or (not _auth_required(old) and _auth_required(new)):
            findings.append(
                _descriptor(
                    "AUTH_TIGHTENING",
                    key,
                    f"Auth tightened: {old_type} -> {new_type}",
                    "MAJOR",
                    "AUTH_CHANGE",
                )
            )

    # MINOR non-breaking changes
    for key in sorted(set(curr_map.keys()) - set(base_map.keys())):
        findings.append(
            _descriptor(
                "API_ADDITION",
                key,
                f"Endpoint added: {key}",
                "MINOR",
                "STRUCTURAL_DIFF",
            )
        )

    dedup: dict[str, dict] = {}
    for item in findings:
        dedup[item["id"]] = item
    return sorted(dedup.values(), key=_findings_sort_key)
