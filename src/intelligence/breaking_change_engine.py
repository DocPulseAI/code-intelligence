"""Deterministic semantic breaking-change diff over symbol graphs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.intelligence.symbol_graph_engine import build_symbol_graph


SEVERITY_WEIGHT = {"MAJOR": 6.0, "MINOR": 3.0, "PATCH": 1.0}


def _stable_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _severity_from_score(score: float) -> str:
    if score >= 8:
        return "MAJOR"
    if score >= 4:
        return "MINOR"
    return "PATCH"


def _incoming_outgoing(edges: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    incoming: dict[str, int] = {}
    outgoing: dict[str, int] = {}
    for edge in edges:
        src = str(edge.get("source_symbol_id", ""))
        dst = str(edge.get("target_symbol_id", ""))
        if src:
            outgoing[src] = outgoing.get(src, 0) + 1
        if dst:
            incoming[dst] = incoming.get(dst, 0) + 1
    return incoming, outgoing


def _base_descriptor(symbol_id: str, change_type: str, file_path: str, entity: str, description: str, score: float, dependents: int) -> dict:
    severity = _severity_from_score(score)
    descriptor = {
        "type": change_type,
        "entity": entity,
        "file": file_path,
        "severity": severity,
        "description": description,
        "symbol_id": symbol_id,
        "affected_dependents_count": int(dependents),
        "risk_score": round(score, 3),
    }
    descriptor["id"] = _stable_hash(descriptor)
    return descriptor


def _key_index(nodes: dict[str, dict]) -> dict[tuple[str, str, str], dict]:
    out: dict[tuple[str, str, str], dict] = {}
    for node in nodes.values():
        key = (str(node.get("file_path", "")), str(node.get("name", "")), str(node.get("kind", "")))
        out[key] = node
    return out


def _route_map(report_payload: dict) -> dict[str, dict]:
    endpoints = list((report_payload.get("api_contract", {}) or {}).get("endpoints", [])
    )
    out = {}
    for ep in endpoints:
        key = str(ep.get("normalized_key", ""))
        if key:
            out[key] = ep
    return out


def analyze_semantic_breaking_changes(baseline_payload: dict | None, current_payload: dict) -> dict:
    current_graph = current_payload.get("symbol_graph")
    if not isinstance(current_graph, dict):
        current_graph = build_symbol_graph(current_payload)

    baseline_graph = {}
    if isinstance(baseline_payload, dict):
        baseline_graph = baseline_payload.get("symbol_graph", {})
        if not isinstance(baseline_graph, dict):
            baseline_graph = {}
    if not baseline_graph:
        baseline_graph = build_symbol_graph(baseline_payload or {})

    curr_nodes = current_graph.get("nodes", {}) if isinstance(current_graph.get("nodes", {}), dict) else {}
    base_nodes = baseline_graph.get("nodes", {}) if isinstance(baseline_graph.get("nodes", {}), dict) else {}
    curr_edges = list(current_graph.get("edges", [])) if isinstance(current_graph.get("edges", []), list) else []

    incoming, outgoing = _incoming_outgoing(curr_edges)
    curr_index = _key_index(curr_nodes)
    base_index = _key_index(base_nodes)
    findings: list[dict] = []

    for key, old_node in sorted(base_index.items()):
        if key in curr_index:
            continue
        sid = str(old_node.get("id", ""))
        dep = incoming.get(sid, 0)
        score = SEVERITY_WEIGHT["MAJOR"] + (outgoing.get(sid, 0) * 0.5) + (2 if old_node.get("visibility") == "public" else 0)
        findings.append(
            _base_descriptor(
                sid,
                "SYMBOL_REMOVED",
                str(old_node.get("file_path", "")),
                str(old_node.get("name", "")),
                f"Symbol removed: {old_node.get('name', '')}",
                score,
                dep,
            )
        )

    for key, old_node in sorted(base_index.items()):
        new_node = curr_index.get(key)
        if not new_node:
            continue
        sid = str(new_node.get("id", ""))
        dep = incoming.get(sid, 0)
        if str(old_node.get("signature_hash", "")) != str(new_node.get("signature_hash", "")):
            score = SEVERITY_WEIGHT["MAJOR"] + (outgoing.get(sid, 0) * 0.5) + (2 if new_node.get("visibility") == "public" else 0)
            findings.append(
                _base_descriptor(
                    sid,
                    "SYMBOL_SIGNATURE_CHANGED",
                    str(new_node.get("file_path", "")),
                    str(new_node.get("name", "")),
                    f"Symbol signature changed: {new_node.get('name', '')}",
                    score,
                    dep,
                )
            )
        if str(old_node.get("visibility", "public")) == "public" and str(new_node.get("visibility", "public")) != "public":
            score = SEVERITY_WEIGHT["MAJOR"] + (outgoing.get(sid, 0) * 0.5) + 2
            findings.append(
                _base_descriptor(
                    sid,
                    "SYMBOL_VISIBILITY_DOWNGRADE",
                    str(new_node.get("file_path", "")),
                    str(new_node.get("name", "")),
                    f"Symbol visibility downgraded: {new_node.get('name', '')}",
                    score,
                    dep,
                )
            )

    base_tables = set(str(t) for t in ((baseline_payload or {}).get("database_impact", {}) or {}).get("tables_affected", []) if str(t).strip())
    curr_tables = set(str(t) for t in (current_payload.get("database_impact", {}) or {}).get("tables_affected", []) if str(t).strip())
    for removed in sorted(base_tables - curr_tables):
        sid = hashlib.sha256(f"schema|{removed}".encode("utf-8")).hexdigest()
        findings.append(
            _base_descriptor(
                sid,
                "SCHEMA_ENTITY_REMOVED",
                "__schema__/database",
                removed,
                f"Schema entity removed: {removed}",
                SEVERITY_WEIGHT["MAJOR"] + 3,
                0,
            )
        )

    base_routes = _route_map(baseline_payload or {})
    curr_routes = _route_map(current_payload)
    for key in sorted(set(base_routes.keys()) - set(curr_routes.keys())):
        sid = hashlib.sha256(f"route|{key}".encode("utf-8")).hexdigest()
        removed_file = str(((base_routes.get(key, {}) or {}).get("source", {}) or {}).get("file", ""))
        findings.append(
            _base_descriptor(
                sid,
                "ROUTE_REMOVED",
                removed_file,
                key,
                f"Route removed: {key}",
                SEVERITY_WEIGHT["MAJOR"],
                0,
            )
        )

    base_path_methods: dict[str, set[str]] = {}
    curr_path_methods: dict[str, set[str]] = {}
    for key in base_routes.keys():
        method, path = key.split(" ", 1)
        base_path_methods.setdefault(path, set()).add(method)
    for key in curr_routes.keys():
        method, path = key.split(" ", 1)
        curr_path_methods.setdefault(path, set()).add(method)
    for path in sorted(set(base_path_methods.keys()) & set(curr_path_methods.keys())):
        if base_path_methods[path] == curr_path_methods[path]:
            continue
        sid = hashlib.sha256(f"route-method|{path}".encode("utf-8")).hexdigest()
        matching_keys = sorted([k for k in curr_routes if k.endswith(f" {path}")])
        sample_key = matching_keys[0] if matching_keys else ""
        changed_file = str(((curr_routes.get(sample_key, {}) or {}).get("source", {}) or {}).get("file", ""))
        findings.append(
            _base_descriptor(
                sid,
                "ROUTE_METHOD_CHANGED",
                changed_file,
                path,
                f"Route method set changed for path: {path}",
                SEVERITY_WEIGHT["MAJOR"],
                0,
            )
        )

    findings = sorted(
        findings,
        key=lambda d: (-float(d.get("risk_score", 0.0)), str(d.get("symbol_id", "")), str(d.get("id", ""))),
    )

    summary = {
        "total_symbols": len(curr_nodes),
        "total_edges": len(curr_edges),
        "breaking_symbols": len(findings),
        "risk_score_total": round(sum(float(item.get("risk_score", 0.0)) for item in findings), 3),
    }

    return {
        "symbol_graph": current_graph,
        "symbol_summary": summary,
        "breaking_changes": findings,
    }
