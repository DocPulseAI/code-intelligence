"""
Deterministic risk scoring utilities.
"""

from __future__ import annotations

from typing import Any


SEVERITY_ORDER = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}


def _count_auth_sensitive_endpoints(endpoints: list[dict]) -> int:
    total = 0
    for endpoint in endpoints:
        if endpoint.get("auth_required") is True:
            total += 1
            continue
        auth = (endpoint.get("auth") or {})
        if auth.get("required") is True:
            total += 1
    return total


def _dependency_fanout(affected_packages: list[str]) -> int:
    return len(set(str(p) for p in affected_packages if str(p).strip()))


def score_report_risk(report: dict, breaking_changes: list[dict]) -> dict[str, Any]:
    """
    Deterministic risk model with severity buckets and score.
    """
    major = sum(1 for item in breaking_changes if item.get("severity") == "MAJOR")
    minor = sum(1 for item in breaking_changes if item.get("severity") == "MINOR")
    patch = sum(1 for item in breaking_changes if item.get("severity") == "PATCH")

    api_summary = report.get("api_summary", {}) or {}
    api_impact = int(api_summary.get("added", 0)) + int(api_summary.get("modified", 0)) + int(api_summary.get("removed", 0))

    schema_weight = 4 if (report.get("database_impact", {}) or {}).get("schema_changed") else 0
    auth_endpoints = list((report.get("api_surface", []) or []))
    if not auth_endpoints:
        auth_endpoints = list((report.get("api_contract", {}) or {}).get("endpoints", [])
        )
    auth_weight = _count_auth_sensitive_endpoints(auth_endpoints)
    fanout_weight = _dependency_fanout(list(report.get("affected_packages", [])))

    score = (
        major * 7
        + minor * 3
        + patch
        + min(20, api_impact)
        + schema_weight
        + min(12, fanout_weight // 10)
        + min(12, auth_weight // 5)
    )

    severity = "PATCH"
    if major > 0:
        severity = "MAJOR"
    elif minor > 0:
        severity = "MINOR"

    return {
        "score": int(score),
        "severity": severity,
        "statistics": {
            "total_changes": len(breaking_changes),
            "major": major,
            "minor": minor,
            "patch": patch,
        },
        "deterministic": True,
    }
