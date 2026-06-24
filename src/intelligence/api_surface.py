"""Canonical API surface model with deterministic schema hashing."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_schema(data: Any) -> str:
    if data is None:
        return ""
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def _normalize_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    raw = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", raw)
    # Keep original path variables but normalize duplicate slashes.
    while "//" in raw:
        raw = raw.replace("//", "/")
    return raw


def _controller_from_source(source: dict) -> str:
    # Prefer explicit controller key (set by EPIC-1 mount resolution)
    ctrl = str((source or {}).get("controller", "")).strip()
    if ctrl:
        return ctrl
    # Legacy fallback: derive from source file name
    file_name = str((source or {}).get("file", ""))
    return os.path.basename(file_name) if file_name else ""


def build_api_surface(endpoints: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        method = str(ep.get("method", "GET")).upper().strip() or "GET"
        path = _normalize_path(str(ep.get("path", "/")))

        auth = ep.get("auth", {}) or {}
        auth_required = bool(ep.get("auth_required")) or bool(auth.get("required") is True)

        req = ep.get("request", {}) or {}
        request_payload = {
            "path_params": req.get("path_params", []),
            "query_params": req.get("query_params", []),
            "body_schema": req.get("body_schema"),
        }
        response_payload = ep.get("responses", [])

        rows.append(
            {
                "method": method,
                "path": path,
                "auth_required": auth_required,
                "request_schema_hash": _hash_schema(request_payload),
                "response_schema_hash": _hash_schema(response_payload),
                "controller": _controller_from_source(ep.get("source", {}) or {}),
            }
        )

    merged: dict[tuple[str, str], dict] = {}
    for row in sorted(rows, key=lambda r: (r["method"], r["path"], r["controller"])):
        key = (row["method"], row["path"])
        if key not in merged:
            merged[key] = row
            continue

        current = merged[key]
        current["auth_required"] = bool(current["auth_required"] or row["auth_required"])
        if not current["request_schema_hash"] and row["request_schema_hash"]:
            current["request_schema_hash"] = row["request_schema_hash"]
        if not current["response_schema_hash"] and row["response_schema_hash"]:
            current["response_schema_hash"] = row["response_schema_hash"]
        if row["controller"] and (not current["controller"] or row["controller"] < current["controller"]):
            current["controller"] = row["controller"]

    return sorted(merged.values(), key=lambda r: (r["method"], r["path"]))
