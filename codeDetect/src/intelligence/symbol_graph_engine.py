"""Deterministic semantic symbol graph extraction."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SymbolNode:
    id: str
    name: str
    kind: str
    file_path: str
    signature_hash: str
    visibility: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DependencyEdge:
    source_symbol_id: str
    target_symbol_id: str
    edge_type: str


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _symbol_id(file_path: str, normalized_signature: str) -> str:
    return _sha256(f"v1|{file_path}|{normalized_signature}")


def _signature_hash(kind: str, name: str, metadata: dict[str, Any]) -> str:
    return _sha256(_stable_json({"kind": kind, "name": name, "metadata": metadata}))


def _visibility(name: str) -> str:
    token = str(name or "")
    if token.startswith("_"):
        return "private"
    return "public"


def _add_node(nodes: dict[str, dict], node: SymbolNode) -> None:
    if node.id in nodes:
        return
    nodes[node.id] = {
        "id": node.id,
        "name": node.name,
        "kind": node.kind,
        "file_path": node.file_path,
        "signature_hash": node.signature_hash,
        "visibility": node.visibility,
        "metadata": node.metadata,
    }


def build_symbol_graph(report: dict) -> dict:
    """
    Build deterministic SymbolGraph from extracted report evidence.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_seen: set[tuple[str, str, str]] = set()

    changes = sorted(
        list(report.get("changes", [])),
        key=lambda c: (str(c.get("file", "")), str(c.get("language", ""))),
    )
    for change in changes:
        file_path = str(change.get("file", ""))
        language = str(change.get("language") or "")
        features = change.get("features", {}) or {}

        module_name = os.path.basename(file_path) or file_path or "module"
        module_sig = f"module:{file_path}"
        module_id = _symbol_id(file_path, module_sig)
        module_node = SymbolNode(
            id=module_id,
            name=f"{module_name}#module",
            kind="function",
            file_path=file_path,
            signature_hash=_signature_hash("function", f"{module_name}#module", {"language": language, "module": True}),
            visibility="public",
            metadata={"language": language, "module": True},
        )
        _add_node(nodes, module_node)

        for kind, key in [("class", "classes"), ("function", "functions"), ("method", "methods"), ("interface", "interfaces")]:
            values = features.get(key, [])
            if not isinstance(values, list):
                continue
            for raw_name in sorted(str(v) for v in values if str(v).strip()):
                meta = {"language": language, "declared_in": key}
                sig = f"{kind}:{raw_name}:{file_path}"
                sid = _symbol_id(file_path, sig)
                node = SymbolNode(
                    id=sid,
                    name=raw_name,
                    kind=kind,
                    file_path=file_path,
                    signature_hash=_signature_hash(kind, raw_name, meta),
                    visibility=_visibility(raw_name),
                    metadata=meta,
                )
                _add_node(nodes, node)

        import_strings = []
        for i in features.get("imports", []):
            if isinstance(i, dict):
                import_strings.append(i.get("source", ""))
            else:
                import_strings.append(str(i))
        for imported in sorted(set(imp for imp in import_strings if imp.strip())):
            target_sig = f"import:{imported}"
            target_id = _symbol_id(f"__import__/{imported}", target_sig)
            target_node = SymbolNode(
                id=target_id,
                name=imported,
                kind="interface",
                file_path=f"__import__/{imported}",
                signature_hash=_signature_hash("interface", imported, {"external": True}),
                visibility="public",
                metadata={"external": True},
            )
            _add_node(nodes, target_node)
            edge_key = (module_id, target_id, "imports")
            if edge_key not in edge_seen:
                edge_seen.add(edge_key)
                edges.append(
                    {
                        "source_symbol_id": module_id,
                        "target_symbol_id": target_id,
                        "edge_type": "imports",
                    }
                )

    for table in sorted(str(t) for t in (report.get("database_impact", {}) or {}).get("tables_affected", []) if str(t).strip()):
        file_path = "__schema__/database"
        sig = f"schema:{table}"
        sid = _symbol_id(file_path, sig)
        node = SymbolNode(
            id=sid,
            name=table,
            kind="schema",
            file_path=file_path,
            signature_hash=_signature_hash("schema", table, {"source": "database_impact"}),
            visibility="public",
            metadata={"source": "database_impact"},
        )
        _add_node(nodes, node)

    for endpoint in sorted(
        list((report.get("api_contract", {}) or {}).get("endpoints", [])),
        key=lambda e: (str(e.get("normalized_key", "")), str((e.get("source") or {}).get("file", "")), int((e.get("source") or {}).get("line_start", 0) or 0)),
    ):
        method = str(endpoint.get("method", "GET")).upper()
        path = str(endpoint.get("path", "/"))
        source = endpoint.get("source", {}) or {}
        source_file = str(source.get("file", "__route__/unknown"))
        normalized_key = str(endpoint.get("normalized_key", f"{method.lower()} {path.lower()}"))
        sig = f"route:{normalized_key}"
        sid = _symbol_id(source_file, sig)
        node = SymbolNode(
            id=sid,
            name=normalized_key,
            kind="route",
            file_path=source_file,
            signature_hash=_signature_hash("route", normalized_key, {"method": method, "path": path}),
            visibility="public",
            metadata={
                "method": method,
                "path": path,
                "normalized_key": normalized_key,
                "line_start": int(source.get("line_start", 0) or 0),
            },
        )
        _add_node(nodes, node)

    edges = sorted(
        edges,
        key=lambda e: (str(e.get("source_symbol_id", "")), str(e.get("target_symbol_id", "")), str(e.get("edge_type", ""))),
    )
    ordered_nodes = {k: nodes[k] for k in sorted(nodes.keys())}
    return {"nodes": ordered_nodes, "edges": edges}

