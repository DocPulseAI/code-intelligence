"""Search intelligence index builder for semantic developer-portal queries."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Callable


def _empty_index() -> dict[str, list]:
    return {
        "symbols": [],
        "references": [],
        "apis": [],
        "modules": [],
    }


def _symbol_leaf(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    token = token[:-2] if token.endswith("()") else token
    parts = [p for p in token.split(".") if p]
    return parts[-1] if parts else token


def _symbol_owner(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    parts = [p for p in token.split(".") if p]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else ""


def _normalize_controller_name(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    parts = [p for p in token.split(".") if p]
    if not parts:
        return token
    if len(parts) >= 2 and parts[0].lower() not in {"this", "self"}:
        return parts[0]
    return parts[-1]


class _LineLocator:
    """Best-effort symbol line resolver that avoids reparsing AST."""

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

    def locate(self, path: str, symbol: str, symbol_type: str) -> int:
        lines = self._lines(path)
        if not lines or not symbol:
            return 0
        escaped = re.escape(symbol)
        patterns = [
            re.compile(rf"\bclass\s+{escaped}\b"),
            re.compile(rf"\b(?:async\s+)?def\s+{escaped}\s*\("),
            re.compile(rf"\bfunction\s+{escaped}\s*\("),
            re.compile(rf"\b(?:const|let|var)\s+{escaped}\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),
            re.compile(rf"\b{escaped}\s*\([^)]*\)\s*{{"),
            re.compile(rf"\b{escaped}\s*\("),
        ]
        # Give class pattern priority for class/controller/service symbols.
        if symbol_type in {"class", "controller", "service"}:
            patterns = [patterns[0]] + patterns[1:]
        for idx, line in enumerate(lines, start=1):
            for pat in patterns:
                if pat.search(line):
                    return idx
        return 0


def _build_symbols(
    repository_evidence: dict[str, Any],
    read_file: Callable[[str], str | None] | None,
) -> list[dict[str, Any]]:
    file_to_module: dict[str, str] = {}
    for module in repository_evidence.get("modules", []):
        for file_path in module.get("files", []):
            file_to_module[str(file_path)] = str(module.get("name", ""))

    locator = _LineLocator(read_file)
    entries: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def upsert(name: str, symbol_type: str, module_name: str, file_path: str, line_hint: int) -> None:
        clean_name = str(name or "").strip()
        if not clean_name:
            return
        module_value = str(module_name or "")
        file_value = str(file_path or "")
        line_value = int(line_hint or 0)
        key = (clean_name, symbol_type, module_value, file_value)
        current = entries.get(key)
        if not current:
            entries[key] = {
                "name": clean_name,
                "type": symbol_type,
                "module": module_value,
                "file": file_value,
                "line": line_value,
            }
            return
        current_line = int(current.get("line", 0))
        if current_line <= 0 and line_value > 0:
            current["line"] = line_value
        elif line_value > 0 and current_line > 0:
            current["line"] = min(current_line, line_value)

    file_evidence = repository_evidence.get("file_evidence", {})
    for path in sorted(file_evidence.keys()):
        features = file_evidence.get(path, {}) or {}
        module_name = file_to_module.get(path, "")
        functions = sorted({str(f).strip() for f in features.get("functions", []) if str(f).strip()})
        classes = sorted({str(c).strip() for c in features.get("classes", []) if str(c).strip()})

        for func_name in functions:
            line = locator.locate(path, func_name, "function")
            upsert(func_name, "function", module_name, path, line)

        for class_name in classes:
            lower = class_name.lower()
            if lower.endswith("controller"):
                class_type = "controller"
            elif lower.endswith("service"):
                class_type = "service"
            else:
                class_type = "class"
            line = locator.locate(path, class_name, class_type)
            upsert(class_name, class_type, module_name, path, line)

    for service in sorted(repository_evidence.get("services", []), key=lambda s: str(s.get("name", ""))):
        name = str(service.get("name", "")).strip()
        file_path = str(service.get("file", "")).strip()
        module_name = str(service.get("module", "")).strip() or file_to_module.get(file_path, "")
        line = locator.locate(file_path, name, "service")
        upsert(name, "service", module_name, file_path, line)

    for api in sorted(repository_evidence.get("apis", []), key=lambda a: (str(a.get("method", "")), str(a.get("path", "")))):
        raw_controller = str(api.get("controller", "")).strip()
        controller = _normalize_controller_name(raw_controller)
        if not controller:
            continue
        file_path = str(api.get("source_file", "") or api.get("router_file", "")).strip()
        module_name = str(api.get("module", "")).strip() or file_to_module.get(file_path, "")
        line = int(api.get("line") or 0) or locator.locate(file_path, controller, "controller")
        upsert(controller, "controller", module_name, file_path, line)

    return sorted(
        entries.values(),
        key=lambda s: (
            str(s.get("name", "")),
            str(s.get("type", "")),
            str(s.get("module", "")),
            str(s.get("file", "")),
            int(s.get("line", 0)),
        ),
    )


def _build_api_index(
    repository_evidence: dict[str, Any],
    call_graph: list[dict[str, Any]],
    symbol_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    service_names = {
        str(service.get("name", "")).strip()
        for service in repository_evidence.get("services", [])
        if str(service.get("name", "")).strip()
    }
    service_names.update({
        str(symbol.get("name", "")).strip()
        for symbol in symbol_entries
        if str(symbol.get("type", "")).strip() == "service"
    })

    controller_to_services: dict[str, set[str]] = defaultdict(set)
    for rel in repository_evidence.get("relationships", []):
        rel_type = str(rel.get("type", "")).upper()
        if "CALL" not in rel_type:
            continue
        source = str(rel.get("from", "")).strip()
        target = _symbol_leaf(str(rel.get("to", "")))
        if target and target in service_names:
            source_leaf = _symbol_leaf(source)
            if source_leaf:
                controller_to_services[source_leaf].add(target)
            if source:
                controller_to_services[source].add(target)

    for entry in call_graph:
        caller_raw = str(entry.get("caller", "")).strip()
        caller_leaf = _symbol_leaf(caller_raw)
        caller_owner = _symbol_owner(caller_raw)
        for callee in entry.get("calls", []):
            callee_leaf = _symbol_leaf(str(callee))
            if callee_leaf and callee_leaf in service_names:
                if caller_leaf:
                    controller_to_services[caller_leaf].add(callee_leaf)
                if caller_owner:
                    controller_to_services[caller_owner].add(callee_leaf)
                if caller_raw:
                    controller_to_services[caller_raw].add(callee_leaf)

    items: dict[str, dict[str, str]] = {}
    for api in sorted(repository_evidence.get("apis", []), key=lambda a: (str(a.get("method", "")), str(a.get("path", "")))):
        method = str(api.get("method", "GET")).upper()
        path = str(api.get("path", "")).strip()
        if not path:
            continue
        endpoint = f"{method} {path}"
        raw_controller = str(api.get("controller", "")).strip()
        controller = _normalize_controller_name(raw_controller) or _symbol_owner(raw_controller) or raw_controller
        handler_leaf = _symbol_leaf(raw_controller)

        service_candidates: set[str] = set()
        for key in (controller, handler_leaf, raw_controller):
            if key:
                service_candidates.update(controller_to_services.get(key, set()))
        if not service_candidates and handler_leaf in service_names:
            service_candidates.add(handler_leaf)

        items[endpoint] = {
            "endpoint": endpoint,
            "method": method,
            "controller": controller,
            "service": sorted(service_candidates)[0] if service_candidates else "",
        }

    return [items[key] for key in sorted(items.keys())]


def _build_references(
    call_graph: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    api_index: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"called_by": set(), "calls": set()})

    for entry in sorted(call_graph, key=lambda e: str(e.get("caller", ""))):
        caller_raw = str(entry.get("caller", "")).strip()
        caller_symbol = _symbol_leaf(caller_raw)
        if not caller_symbol:
            continue
        for callee in entry.get("calls", []):
            callee_raw = str(callee).strip()
            if not callee_raw:
                continue
            refs[caller_symbol]["calls"].add(callee_raw)
            callee_symbol = _symbol_leaf(callee_raw)
            if callee_symbol:
                refs[callee_symbol]["called_by"].add(caller_raw)

    for rel in relationships:
        rel_type = str(rel.get("type", "")).upper()
        if "CALL" not in rel_type:
            continue
        source = _symbol_leaf(str(rel.get("from", "")))
        target = _symbol_leaf(str(rel.get("to", "")))
        if source and target:
            refs[source]["calls"].add(target)
            refs[target]["called_by"].add(source)

    for api in api_index:
        endpoint = str(api.get("endpoint", "")).strip()
        if not endpoint:
            continue
        controller = str(api.get("controller", "")).strip()
        service = str(api.get("service", "")).strip()
        if controller:
            refs[controller]["called_by"].add(endpoint)
        if service:
            refs[service]["called_by"].add(endpoint)

    out: list[dict[str, Any]] = []
    for symbol in sorted(refs.keys()):
        called_by = sorted(refs[symbol]["called_by"])
        calls = sorted(refs[symbol]["calls"])
        if not called_by and not calls:
            continue
        out.append({
            "symbol": symbol,
            "called_by": called_by,
            "calls": calls,
        })
    return out


def _module_type(
    module_name: str,
    module_obj: dict[str, Any],
    services: list[dict[str, Any]],
    apis: list[dict[str, Any]],
) -> str:
    has_service = any(str(s.get("module", "")) == module_name for s in services)
    has_api = any(str(a.get("module", "")) == module_name for a in apis)
    if has_service and not has_api:
        return "service"
    if has_api and not has_service:
        return "controller"
    raw = str(module_obj.get("type", "")).strip()
    if raw.endswith("_module"):
        raw = raw[:-7]
    return raw or "module"


def _build_module_index(
    repository_evidence: dict[str, Any],
    architecture_reconstruction: dict[str, Any],
    dependency_graph: dict[str, Any],
) -> list[dict[str, Any]]:
    services = repository_evidence.get("services", [])
    apis = repository_evidence.get("apis", [])
    modules = repository_evidence.get("modules", [])

    dep_map: dict[str, set[str]] = defaultdict(set)
    for edge in dependency_graph.get("edges", []):
        source = str(edge.get("from", "")).strip()
        target = str(edge.get("to", "")).strip()
        if source and target and source != target:
            dep_map[source].add(target)

    service_to_module = {
        str(s.get("name", "")).strip(): str(s.get("module", "")).strip()
        for s in services
        if str(s.get("name", "")).strip() and str(s.get("module", "")).strip()
    }
    controller_to_module: dict[str, str] = {}
    for api in apis:
        module_name = str(api.get("module", "")).strip()
        raw_controller = str(api.get("controller", "")).strip()
        normalized = _normalize_controller_name(raw_controller)
        if module_name and normalized:
            controller_to_module[normalized] = module_name
        if module_name and raw_controller:
            controller_to_module[raw_controller] = module_name

    for edge in architecture_reconstruction.get("edges", []):
        source = str(edge.get("from", "")).strip()
        target = str(edge.get("to", "")).strip()
        if not source or not target:
            continue

        source_module = (
            service_to_module.get(source)
            or controller_to_module.get(source)
            or controller_to_module.get(_symbol_leaf(source))
        )
        target_module = (
            service_to_module.get(target)
            or controller_to_module.get(target)
            or controller_to_module.get(_symbol_leaf(target))
        )

        if source_module and target_module and source_module != target_module:
            dep_map[source_module].add(target_module)
        elif source_module and not target_module:
            dep_map[source_module].add(target)

    out: list[dict[str, Any]] = []
    for module in sorted(modules, key=lambda m: str(m.get("name", ""))):
        name = str(module.get("name", "")).strip()
        if not name:
            continue
        files = sorted(str(f) for f in module.get("files", []) if str(f).strip())
        out.append({
            "name": name,
            "type": _module_type(name, module, services, apis),
            "file": files[0] if files else "",
            "dependencies": sorted(dep_map.get(name, set())),
        })
    return out


def build_search_index(
    repository_evidence: dict[str, Any],
    architecture_reconstruction: dict[str, Any],
    dependency_graph: dict[str, Any],
    call_graph: list[dict[str, Any]],
    read_file: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Build semantic-search index from existing EPIC-1 analysis outputs."""
    if not isinstance(repository_evidence, dict):
        return {"search_index": _empty_index()}

    symbols = _build_symbols(repository_evidence, read_file)
    api_index = _build_api_index(repository_evidence, call_graph, symbols)
    references = _build_references(call_graph, repository_evidence.get("relationships", []), api_index)
    module_index = _build_module_index(repository_evidence, architecture_reconstruction, dependency_graph)

    return {
        "search_index": {
            "symbols": symbols,
            "references": references,
            "apis": api_index,
            "modules": module_index,
        }
    }
