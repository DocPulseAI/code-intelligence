"""
Deterministic structural breaking-change comparison.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


SEVERITY_ORDER = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_id(data: Any) -> str:
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()[:16]


def _path_to_openapi(path: str) -> str:
    out = []
    for segment in str(path).split("/"):
        if not segment:
            continue
        if segment.startswith(":"):
            out.append("{" + segment[1:] + "}")
        else:
            out.append(segment)
    return "/" + "/".join(out)


def _get_endpoints(report: dict) -> list[dict]:
    return list(report.get("api_contract", {}).get("endpoints", []))


def _get_endpoint_key(endpoint: dict) -> str:
    method = str(endpoint.get("method", "GET")).upper()
    path = _path_to_openapi(str(endpoint.get("path", "/")))
    return f"{method} {path}"


def _endpoint_source_key(endpoint: dict) -> str:
    src = endpoint.get("source", {}) or {}
    file_name = str(src.get("file", ""))
    handler = str(src.get("handler", ""))
    if not file_name:
        return ""
    return f"{file_name}::{handler}"


def _get_dto_fields(endpoint: dict) -> dict[str, str]:
    req = endpoint.get("request", {}) or {}
    body = req.get("body_schema")
    if not isinstance(body, dict):
        return {}
    props = body.get("properties", {}) or {}
    out = {}
    if isinstance(props, dict):
        for key, value in props.items():
            if isinstance(value, dict):
                out[str(key)] = str(value.get("type", "unknown"))
            else:
                out[str(key)] = type(value).__name__
    return out


def _make_descriptor(change_type: str, entity: str, file_name: str, severity: str, description: str) -> dict:
    stable = {
        "type": change_type,
        "entity": entity,
        "file": file_name,
        "severity": severity,
        "description": description,
    }
    stable["id"] = _hash_id(stable)
    return stable


def _detect_api_changes(baseline: dict, current: dict) -> list[dict]:
    descriptors: list[dict] = []
    base_eps = _get_endpoints(baseline)
    curr_eps = _get_endpoints(current)

    base_by_key = {_get_endpoint_key(ep): ep for ep in base_eps}
    curr_by_key = {_get_endpoint_key(ep): ep for ep in curr_eps}

    for key, ep in sorted(base_by_key.items()):
        if key not in curr_by_key:
            src = ep.get("source", {}) or {}
            descriptors.append(
                _make_descriptor(
                    "API_ENDPOINT_REMOVED",
                    key,
                    str(src.get("file", "")),
                    "MAJOR",
                    f"Endpoint removed: {key}",
                )
            )

    base_by_source = {}
    curr_by_source = {}
    for ep in base_eps:
        source_key = _endpoint_source_key(ep)
        if source_key:
            base_by_source[source_key] = ep
    for ep in curr_eps:
        source_key = _endpoint_source_key(ep)
        if source_key:
            curr_by_source[source_key] = ep

    for source_key in sorted(set(base_by_source.keys()) & set(curr_by_source.keys())):
        old_key = _get_endpoint_key(base_by_source[source_key])
        new_key = _get_endpoint_key(curr_by_source[source_key])
        if old_key == new_key:
            continue
        old_method, old_path = old_key.split(" ", 1)
        new_method, new_path = new_key.split(" ", 1)
        src_file = source_key.split("::", 1)[0]
        if old_method != new_method:
            descriptors.append(
                _make_descriptor(
                    "API_HTTP_VERB_CHANGED",
                    source_key,
                    src_file,
                    "MAJOR",
                    f"HTTP verb changed for {source_key}: {old_method} -> {new_method}",
                )
            )
        if old_path != new_path:
            descriptors.append(
                _make_descriptor(
                    "API_ROUTE_PATH_CHANGED",
                    source_key,
                    src_file,
                    "MAJOR",
                    f"Route path changed for {source_key}: {old_path} -> {new_path}",
                )
            )

    for key in sorted(set(base_by_key.keys()) & set(curr_by_key.keys())):
        old_fields = _get_dto_fields(base_by_key[key])
        new_fields = _get_dto_fields(curr_by_key[key])
        src = curr_by_key[key].get("source", {}) or {}
        src_file = str(src.get("file", ""))
        for field in sorted(old_fields.keys()):
            if field not in new_fields:
                descriptors.append(
                    _make_descriptor(
                        "API_DTO_FIELD_REMOVED",
                        f"{key}::{field}",
                        src_file,
                        "MAJOR",
                        f"Request DTO field removed in {key}: {field}",
                    )
                )
            elif old_fields[field] != new_fields[field]:
                descriptors.append(
                    _make_descriptor(
                        "API_DTO_FIELD_TYPE_CHANGED",
                        f"{key}::{field}",
                        src_file,
                        "MAJOR",
                        f"Request DTO field type changed in {key}: {field} ({old_fields[field]} -> {new_fields[field]})",
                    )
                )
    return descriptors


def _extract_models_from_changes(changes: list[dict], markers: tuple[str, ...]) -> set[str]:
    names = set()
    for change in changes:
        file_name = str(change.get("file", "")).lower()
        if not any(marker in file_name for marker in markers):
            continue
        base = file_name.replace("\\", "/").split("/")[-1]
        name = base.split(".")[0]
        if name:
            names.add(name)
    return names


def _detect_schema_changes(baseline: dict, current: dict) -> list[dict]:
    descriptors: list[dict] = []
    base_db = baseline.get("database_impact", {}) or {}
    curr_db = current.get("database_impact", {}) or {}
    base_tables = set(str(t) for t in base_db.get("tables_affected", []) if str(t).strip())
    curr_tables = set(str(t) for t in curr_db.get("tables_affected", []) if str(t).strip())

    for removed in sorted(base_tables - curr_tables):
        descriptors.append(
            _make_descriptor(
                "SCHEMA_ENTITY_REMOVED",
                removed,
                "",
                "MAJOR",
                f"Database entity removed from detected schema set: {removed}",
            )
        )

    base_changes = baseline.get("changes", []) or []
    curr_changes = current.get("changes", []) or []
    prisma_markers = ("schema.prisma",)
    mongoose_markers = ("mongoose", ".model.js", ".model.ts")
    jpa_markers = ("/entity/", "@entity", ".java")

    base_prisma = _extract_models_from_changes(base_changes, prisma_markers)
    curr_prisma = _extract_models_from_changes(curr_changes, prisma_markers)
    for name in sorted(base_prisma - curr_prisma):
        descriptors.append(
            _make_descriptor(
                "PRISMA_MODEL_REMOVED",
                name,
                "schema.prisma",
                "MAJOR",
                f"Prisma model removed: {name}",
            )
        )

    base_mongoose = _extract_models_from_changes(base_changes, mongoose_markers)
    curr_mongoose = _extract_models_from_changes(curr_changes, mongoose_markers)
    for name in sorted(base_mongoose - curr_mongoose):
        descriptors.append(
            _make_descriptor(
                "MONGOOSE_SCHEMA_MUTATION",
                name,
                "",
                "MAJOR",
                f"Mongoose model/schema appears removed or renamed: {name}",
            )
        )

    base_jpa = _extract_models_from_changes(base_changes, jpa_markers)
    curr_jpa = _extract_models_from_changes(curr_changes, jpa_markers)
    for name in sorted(base_jpa - curr_jpa):
        descriptors.append(
            _make_descriptor(
                "JPA_ENTITY_MUTATION",
                name,
                "",
                "MAJOR",
                f"JPA entity appears removed or renamed: {name}",
            )
        )
    return descriptors


def _detect_infra_changes(baseline: dict, current: dict) -> list[dict]:
    descriptors: list[dict] = []
    base_changes = baseline.get("changes", []) or []
    curr_changes = current.get("changes", []) or []
    base_files = {str(c.get("file", "")) for c in base_changes}
    curr_files = {str(c.get("file", "")) for c in curr_changes}

    for file_name in sorted(base_files - curr_files):
        lower = file_name.lower()
        if lower == "dockerfile" or lower.endswith("/dockerfile"):
            descriptors.append(
                _make_descriptor(
                    "DOCKERFILE_SEMANTIC_CHANGE",
                    "dockerfile",
                    file_name,
                    "MAJOR",
                    "Dockerfile removed or moved between baseline and current commit",
                )
            )
        if lower.startswith(".github/workflows/") and lower.endswith((".yml", ".yaml")):
            descriptors.append(
                _make_descriptor(
                    "GITHUB_ACTIONS_WORKFLOW_MUTATION",
                    file_name,
                    file_name,
                    "MINOR",
                    "GitHub Actions workflow removed or renamed",
                )
            )
    return descriptors


def compare_reports(baseline_report: dict | None, current_report: dict) -> list[dict]:
    """
    Compare normalized baseline/current reports and return deterministic descriptors.
    """
    if not baseline_report:
        return []
    descriptors = []
    descriptors.extend(_detect_api_changes(baseline_report, current_report))
    descriptors.extend(_detect_schema_changes(baseline_report, current_report))
    descriptors.extend(_detect_infra_changes(baseline_report, current_report))
    descriptors = sorted(
        descriptors,
        key=lambda d: (
            -SEVERITY_ORDER.get(str(d.get("severity", "PATCH")), 1),
            str(d.get("file", "")),
            str(d.get("entity", "")),
            str(d.get("type", "")),
        ),
    )
    return descriptors
