"""Deterministic EPIC-1 code intelligence builders.

Builds additive, deterministic code_intelligence artifacts from existing
repository evidence and intelligence engines without changing legacy schemas.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from src.file_filter import FileFilter


def _normalize_controller_name(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    parts = [p for p in token.split(".") if p]
    if not parts:
        return token
    return parts[0]


def _leaf_symbol(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    token = token[:-2] if token.endswith("()") else token
    parts = [p for p in token.split(".") if p]
    return parts[-1] if parts else token


class _LineLocator:
    """Best-effort deterministic symbol line locator."""

    def __init__(self, read_file: Callable[[str], str | None] | None):
        self._read_file = read_file
        self._cache: dict[str, list[str] | None] = {}

    def _lines(self, path: str) -> list[str] | None:
        if not self._read_file or not path:
            return None
        if path not in self._cache:
            content = self._read_file(path) or ""
            self._cache[path] = content.splitlines() if content else None
        return self._cache[path]

    def locate(self, path: str, symbol_name: str, symbol_type: str) -> int:
        lines = self._lines(path)
        if not lines:
            return 0
        name = str(symbol_name or "").strip()
        if not name:
            return 0

        escaped = re.escape(name)
        patterns = {
            "function": [
                re.compile(rf"\bfunction\s+{escaped}\s*\("),
                re.compile(rf"\b(?:const|let|var)\s+{escaped}\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),
                re.compile(rf"\b(?:async\s+)?def\s+{escaped}\s*\("),
                re.compile(rf"\b{escaped}\s*\("),
            ],
            "class": [re.compile(rf"\bclass\s+{escaped}\b")],
            "method": [
                re.compile(rf"^\s*(?:public|private|protected|static|async|\s)*{escaped}\s*\(", re.IGNORECASE),
                re.compile(rf"\b{escaped}\s*\("),
            ],
            "controller": [re.compile(rf"\bclass\s+{escaped}\b"), re.compile(rf"\b{escaped}\b")],
            "service": [re.compile(rf"\bclass\s+{escaped}\b"), re.compile(rf"\b{escaped}\b")],
            "entity": [
                re.compile(rf"\bmodel\s*\(\s*['\"]{escaped}['\"]"),
                re.compile(rf"\bclass\s+{escaped}\b"),
                re.compile(rf"\bmodel\s+{escaped}\b", re.IGNORECASE),
                re.compile(rf"\b{escaped}\b"),
            ],
            "route": [],
        }
        checks = patterns.get(symbol_type, [re.compile(rf"\b{escaped}\b")])
        for idx, line in enumerate(lines, start=1):
            for pat in checks:
                if pat.search(line):
                    return idx
        return 0


def _build_symbol_index(
    repository_evidence: dict[str, Any],
    read_file: Callable[[str], str | None] | None,
) -> list[dict[str, Any]]:
    file_evidence = repository_evidence.get("file_evidence", {}) or {}
    apis = repository_evidence.get("apis", []) or []
    services = repository_evidence.get("services", []) or []
    entities = repository_evidence.get("entities", []) or []

    locator = _LineLocator(read_file)
    index: dict[tuple[str, str, str], dict[str, Any]] = {}

    def upsert(name: str, symbol_type: str, file_path: str, line: int = 0) -> None:
        clean_name = str(name or "").strip()
        clean_file = str(file_path or "").strip()
        if not clean_name or not clean_file:
            return
        if FileFilter.should_exclude_from_analysis(clean_file):
            return
        line_num = int(line or 0)
        if line_num <= 0:
            line_num = locator.locate(clean_file, clean_name, symbol_type)
        key = (clean_name, symbol_type, clean_file)
        if key not in index:
            index[key] = {
                "name": clean_name,
                "type": symbol_type,
                "file": clean_file,
                "line": line_num,
            }
            return
        old_line = int(index[key].get("line", 0) or 0)
        if old_line <= 0 and line_num > 0:
            index[key]["line"] = line_num
        elif line_num > 0 and old_line > 0:
            index[key]["line"] = min(old_line, line_num)

    # AST-derived functions/classes/methods by file.
    for file_path in sorted(file_evidence.keys()):
        if FileFilter.should_exclude_from_analysis(file_path):
            continue
        feats = file_evidence.get(file_path, {}) or {}
        for fn in sorted({str(x).strip() for x in feats.get("functions", []) if str(x).strip()}):
            upsert(fn, "function", file_path)
        for cls in sorted({str(x).strip() for x in feats.get("classes", []) if str(x).strip()}):
            upsert(cls, "class", file_path)
        for method in sorted({str(x).strip() for x in feats.get("methods", []) if str(x).strip()}):
            upsert(method, "method", file_path)

    # Controllers/routes from API evidence.
    for api in sorted(apis, key=lambda a: (str(a.get("method", "")), str(a.get("path", "")))):
        method = str(api.get("method", "GET")).upper().strip()
        path = str(api.get("path", "")).strip()
        source_file = str(api.get("source_file") or api.get("router_file") or "").strip()
        line = int(api.get("line", 0) or 0)
        if method and path and source_file:
            upsert(f"{method} {path}", "route", source_file, line)
        controller = _normalize_controller_name(str(api.get("controller", "")))
        if controller and source_file:
            upsert(controller, "controller", source_file, line)

    # Services from repository evidence.
    for svc in sorted(services, key=lambda s: str(s.get("name", ""))):
        name = str(svc.get("name", "")).strip()
        file_path = str(svc.get("file", "")).strip()
        if name and file_path:
            upsert(name, "service", file_path)

    # Entities from repository evidence.
    for ent in sorted(entities, key=lambda e: str(e.get("name", ""))):
        name = str(ent.get("name", "")).strip()
        file_path = str(ent.get("source_file", "")).strip()
        if name and file_path:
            upsert(name, "entity", file_path)

    rows = list(index.values())
    rows.sort(
        key=lambda r: (
            str(r.get("name", "")).lower(),
            str(r.get("name", "")),
            str(r.get("type", "")),
            str(r.get("file", "")),
            int(r.get("line", 0) or 0),
        )
    )
    return rows


def _build_call_graph_view(
    repository_evidence: dict[str, Any],
    call_graph: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()

    # Raw caller->callee entries from existing call graph engine.
    for entry in sorted(call_graph or [], key=lambda e: str(e.get("caller", ""))):
        caller = str(entry.get("caller", "")).strip()
        if caller:
            nodes.add(caller)
        for callee_raw in entry.get("calls", []) or []:
            callee = str(callee_raw).strip()
            if not callee:
                continue
            nodes.add(callee)
            if caller and caller != callee:
                edges.add((caller, callee))

    # Explicit controller/service calls from evidence relationships.
    for rel in sorted(
        repository_evidence.get("relationships", []) or [],
        key=lambda r: (str(r.get("type", "")), str(r.get("from", "")), str(r.get("to", ""))),
    ):
        rel_type = str(rel.get("type", "")).upper()
        if "CALL" not in rel_type:
            continue
        source = str(rel.get("from", "")).strip()
        target = str(rel.get("to", "")).strip()
        if not source or not target:
            continue
        nodes.add(source)
        nodes.add(target)
        if source != target:
            edges.add((source, target))

    return {
        "nodes": sorted(nodes),
        "edges": [{"from": src, "to": dst} for src, dst in sorted(edges)],
    }


def _tarjan_scc(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for src, dst in edges:
        if src in adj and dst in adj:
            adj[src].append(dst)
    for key in adj:
        adj[key] = sorted(set(adj[key]))

    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)

        for w in adj.get(v, []):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])

        if lowlink[v] == indices[v]:
            comp: list[str] = []
            while stack:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            comp_sorted = sorted(comp)
            if len(comp_sorted) > 1:
                sccs.append(comp_sorted)
            elif len(comp_sorted) == 1 and comp_sorted[0] in adj and comp_sorted[0] in adj[comp_sorted[0]]:
                sccs.append(comp_sorted)

    for node in nodes:
        if node not in indices:
            strongconnect(node)

    return sorted(sccs, key=lambda c: tuple(c))


def _build_dependency_graph_view(
    repository_evidence: dict[str, Any],
    dependency_graph: dict[str, Any],
) -> dict[str, Any]:
    module_names = sorted(
        {
            str(mod.get("name", "")).strip()
            for mod in (repository_evidence.get("modules", []) or [])
            if str(mod.get("name", "")).strip()
        }
    )
    module_set = set(module_names)

    deps: set[tuple[str, str]] = set()
    for edge in sorted(
        dependency_graph.get("edges", []) or [],
        key=lambda e: (str(e.get("from", "")), str(e.get("to", "")), str(e.get("type", ""))),
    ):
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        if not src or not dst or src == dst:
            continue
        if src in module_set and dst in module_set:
            deps.add((src, dst))

    dep_rows = [{"from": src, "to": dst} for src, dst in sorted(deps)]
    sccs = _tarjan_scc(module_names, sorted(deps))

    return {
        "modules": module_names,
        "dependencies": dep_rows,
        "cycle_detected": bool(sccs),
        "circular_dependencies": sccs,
    }


def _resolve_to_known_node(raw: str, known_nodes: set[str]) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    if token in known_nodes:
        return token

    # entity field refs: "Order.user" -> "Order"
    if "." in token:
        head = token.split(".", 1)[0].strip()
        if head in known_nodes:
            return head

    leaf = _leaf_symbol(token)
    if leaf in known_nodes:
        return leaf

    owner = ""
    parts = [p for p in token.split(".") if p]
    if len(parts) >= 2:
        owner = parts[-2]
    if owner in known_nodes:
        return owner

    return ""


def _build_repository_graph_view(
    repository_evidence: dict[str, Any],
    call_graph_view: dict[str, Any],
    dependency_graph_view: dict[str, Any],
) -> dict[str, Any]:
    nodes: dict[str, str] = {}
    edges: set[tuple[str, str, str]] = set()

    # Modules
    for mod in sorted(repository_evidence.get("modules", []) or [], key=lambda m: str(m.get("name", ""))):
        name = str(mod.get("name", "")).strip()
        if name:
            nodes[name] = "module"

    # Services
    for svc in sorted(repository_evidence.get("services", []) or [], key=lambda s: str(s.get("name", ""))):
        name = str(svc.get("name", "")).strip()
        if name:
            nodes[name] = "service"

    # Entities
    for ent in sorted(repository_evidence.get("entities", []) or [], key=lambda e: str(e.get("name", ""))):
        name = str(ent.get("name", "")).strip()
        if name:
            nodes[name] = "entity"

    # APIs + controllers (from repository evidence only)
    for api in sorted(repository_evidence.get("apis", []) or [], key=lambda a: (str(a.get("method", "")), str(a.get("path", "")))):
        method = str(api.get("method", "GET")).upper().strip()
        path = str(api.get("path", "")).strip()
        if method and path:
            api_id = f"{method} {path}"
            nodes[api_id] = "api"
            controller = _normalize_controller_name(str(api.get("controller", "")))
            if controller:
                nodes[controller] = "controller"
                edges.add((api_id, controller, "calls"))

    known = set(nodes.keys())

    # Calls/uses edges from repository evidence relationships.
    for rel in sorted(
        repository_evidence.get("relationships", []) or [],
        key=lambda r: (str(r.get("type", "")), str(r.get("from", "")), str(r.get("to", ""))),
    ):
        rel_type_raw = str(rel.get("type", "")).strip()
        rel_type_upper = rel_type_raw.upper()
        src = _resolve_to_known_node(str(rel.get("from", "")), known)
        dst = _resolve_to_known_node(str(rel.get("to", "")), known)
        if not src or not dst or src == dst:
            continue

        if "CALL" in rel_type_upper:
            edge_kind = "calls"
        elif rel_type_upper in {"USES", "USES_ENTITY", "PERSISTS", "ENTITY_RELATION"}:
            edge_kind = "uses"
        else:
            continue
        edges.add((src, dst, edge_kind))

    # Calls from call graph if both endpoints are known repository nodes.
    for row in sorted(call_graph_view.get("edges", []) or [], key=lambda e: (str(e.get("from", "")), str(e.get("to", "")))):
        src = _resolve_to_known_node(str(row.get("from", "")), known)
        dst = _resolve_to_known_node(str(row.get("to", "")), known)
        if src and dst and src != dst:
            edges.add((src, dst, "calls"))

    # Module dependencies.
    for dep in sorted(
        dependency_graph_view.get("dependencies", []) or [],
        key=lambda d: (str(d.get("from", "")), str(d.get("to", ""))),
    ):
        src = str(dep.get("from", "")).strip()
        dst = str(dep.get("to", "")).strip()
        if src in known and dst in known and src != dst:
            edges.add((src, dst, "depends_on"))

    node_rows = [{"id": node_id, "type": nodes[node_id]} for node_id in sorted(nodes.keys())]
    edge_rows = [{"from": src, "to": dst, "type": kind} for src, dst, kind in sorted(edges, key=lambda e: (e[2], e[0], e[1]))]
    return {"nodes": node_rows, "edges": edge_rows}


def build_code_intelligence(
    repository_evidence: dict[str, Any],
    call_graph: list[dict[str, Any]],
    dependency_graph: dict[str, Any],
    read_file: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Build deterministic additive code intelligence artifacts."""
    symbol_index = _build_symbol_index(repository_evidence, read_file)
    call_graph_view = _build_call_graph_view(repository_evidence, call_graph)
    dependency_graph_view = _build_dependency_graph_view(repository_evidence, dependency_graph)
    repository_graph_view = _build_repository_graph_view(
        repository_evidence,
        call_graph_view,
        dependency_graph_view,
    )
    return {
        "symbol_index": symbol_index,
        "call_graph": call_graph_view,
        "dependency_graph": dependency_graph_view,
        "repository_graph": repository_graph_view,
    }

