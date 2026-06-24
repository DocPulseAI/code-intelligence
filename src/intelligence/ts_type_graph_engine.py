"""
TypeScript/JavaScript Type Graph Engine — Phase 1.
Parses types, resolves imports, builds deterministic symbol graph.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Optional


def _canonical(data: Any) -> str:
    """Canonical JSON encoding for deterministic hashing."""
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _stable_hash(payload: dict) -> str:
    """Generate deterministic hash for structure."""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def _extract_generic_names(generics_str: str) -> list[str]:
    """Extract just the parameter names from generic declaration string."""
    if not generics_str:
        return []

    names = []
    for g in generics_str.split(","):
        g = g.strip()
        # Extract just the name before "extends" or "="
        param_name = re.split(r'\s+(?:extends|=)', g)[0].strip()
        if param_name:
            names.append(param_name)
    return names


class TypeNode:
    """Represents a TypeScript type definition."""

    def __init__(
        self,
        symbol_id: str,
        kind: str,
        name: str,
        file_path: str,
        line: int,
        exported: bool = False,
        generic_params: list[str] | None = None,
        extends: list[str] | None = None,
        implements: list[str] | None = None,
        fields: dict[str, dict] | None = None,
        union_types: list[str] | None = None,
    ):
        self.symbol_id = symbol_id
        self.kind = kind  # interface, type, enum, class, etc.
        self.name = name
        self.file_path = file_path
        self.line = line
        self.exported = exported
        self.generic_params = generic_params or []
        self.extends = extends or []
        self.implements = implements or []
        self.fields = fields or {}
        self.union_types = union_types or []
        self.imported_by: set[str] = set()
        self.dependencies: set[str] = set()

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "symbol_id": self.symbol_id,
            "kind": self.kind,
            "name": self.name,
            "file_path": self.file_path,
            "line": self.line,
            "exported": self.exported,
            "generic_params": sorted(self.generic_params),
            "extends": sorted(self.extends),
            "implements": sorted(self.implements),
            "fields": self.fields,
            "union_types": sorted(self.union_types),
            "imported_by": sorted(self.imported_by),
            "dependencies": sorted(self.dependencies),
        }

    def compute_structural_hash(self) -> str:
        """Compute deterministic structural hash (excludes formatting, line numbers)."""
        payload = {
            "kind": self.kind,
            "name": self.name,
            "generic_params": sorted(self.generic_params),
            "extends": sorted(self.extends),
            "implements": sorted(self.implements),
            "fields": self.fields,
            "union_types": sorted(self.union_types),
        }
        return _stable_hash(payload)


class TypeGraphEngine:
    """Parses and resolves TypeScript/JavaScript types."""

    # Type definition patterns
    INTERFACE_PATTERN = re.compile(
        r"(?:export\s+)?interface\s+([A-Za-z_]\w*)\s*(?:<([^>]+)>)?\s*(?:extends\s+([^{]+?))?\{",
        re.MULTILINE
    )
    TYPE_ALIAS_PATTERN = re.compile(
        r"(?:export\s+)?type\s+([A-Za-z_]\w*)\s*(?:<([^>]+)>)?\s*=\s*",
        re.MULTILINE
    )
    ENUM_PATTERN = re.compile(
        r"(?:export\s+)?enum\s+([A-Za-z_]\w*)\s*\{",
        re.MULTILINE
    )
    CLASS_PATTERN = re.compile(
        r"(?:export\s+)?class\s+([A-Za-z_]\w*)\s*(?:<([^>]+)>)?\s*(?:extends\s+([A-Za-z_.\w]+))?\s*(?:implements\s+([^{]+?))?\{",
        re.MULTILINE
    )
    EXPORT_PATTERN = re.compile(
        r"export\s+(?:default\s+)?(?:interface|type|enum|class|const|function)\s+([A-Za-z_]\w*)",
        re.MULTILINE
    )

    def __init__(self):
        self.nodes: dict[str, TypeNode] = {}
        self.imports: dict[str, list[tuple[str, str]]] = {}
        self.exports: dict[str, set[str]] = {}
        self.barrel_exports: dict[str, list[str]] = {}

    def parse_file(self, file_path: str, content: str) -> list[TypeNode]:
        """Parse a TypeScript/JavaScript file for type definitions."""
        nodes = []

        # Extract exports
        self.exports[file_path] = set()
        for match in self.EXPORT_PATTERN.finditer(content):
            self.exports[file_path].add(match.group(1))

        # Extract imports
        self.imports[file_path] = self._extract_imports(content)

        # Parse interfaces
        for match in self.INTERFACE_PATTERN.finditer(content):
            name = match.group(1)
            generic_params = _extract_generic_names(match.group(2) or "")
            extends_str = match.group(3) or ""

            symbol_id = f"{file_path}#{name}"
            node = TypeNode(
                symbol_id=symbol_id,
                kind="interface",
                name=name,
                file_path=file_path,
                line=content[:match.start()].count("\n") + 1,
                exported=name in self.exports.get(file_path, set()),
                generic_params=generic_params,
                extends=[e.strip() for e in extends_str.split(",") if e.strip()],
            )
            node.fields = self._extract_fields(content, match.end())
            nodes.append(node)
            self.nodes[symbol_id] = node

        # Parse type aliases
        for match in self.TYPE_ALIAS_PATTERN.finditer(content):
            name = match.group(1)
            generic_params = _extract_generic_names(match.group(2) or "")

            # Extract type definition
            start = match.end()
            end = content.find(";", start)
            if end == -1:
                end = content.find("\n", start)
            type_def = content[start:end].strip()

            union_types = []
            if "|" in type_def:
                union_types = [t.strip() for t in type_def.split("|")]

            symbol_id = f"{file_path}#{name}"
            node = TypeNode(
                symbol_id=symbol_id,
                kind="type",
                name=name,
                file_path=file_path,
                line=content[:match.start()].count("\n") + 1,
                exported=name in self.exports.get(file_path, set()),
                generic_params=generic_params,
                union_types=union_types,
            )
            nodes.append(node)
            self.nodes[symbol_id] = node

        # Parse enums
        for match in self.ENUM_PATTERN.finditer(content):
            name = match.group(1)

            symbol_id = f"{file_path}#{name}"
            node = TypeNode(
                symbol_id=symbol_id,
                kind="enum",
                name=name,
                file_path=file_path,
                line=content[:match.start()].count("\n") + 1,
                exported=name in self.exports.get(file_path, set()),
            )
            nodes.append(node)
            self.nodes[symbol_id] = node

        # Parse classes
        for match in self.CLASS_PATTERN.finditer(content):
            name = match.group(1)
            generic_params = _extract_generic_names(match.group(2) or "")
            extends_str = match.group(3) or ""
            implements_str = match.group(4) or ""

            symbol_id = f"{file_path}#{name}"
            node = TypeNode(
                symbol_id=symbol_id,
                kind="class",
                name=name,
                file_path=file_path,
                line=content[:match.start()].count("\n") + 1,
                exported=name in self.exports.get(file_path, set()),
                generic_params=generic_params,
                extends=[extends_str.strip()] if extends_str.strip() else [],
                implements=[i.strip() for i in implements_str.split(",") if i.strip()],
            )
            node.fields = self._extract_fields(content, match.end())
            nodes.append(node)
            self.nodes[symbol_id] = node

        return nodes

    def _extract_imports(self, content: str) -> list[tuple[str, str]]:
        """Extract all imports from content."""
        imports = []

        # Pattern 1: import { a, b } from "module"
        named_import_pattern = re.compile(
            r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]"
        )
        for match in named_import_pattern.finditer(content):
            for symbol in match.group(1).split(","):
                sym_name = symbol.strip().split(" as ")[-1].strip()
                if sym_name:
                    imports.append((sym_name, match.group(2)))

        # Pattern 2: import type { a } from "module"
        type_import_pattern = re.compile(
            r"import\s+type\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]"
        )
        for match in type_import_pattern.finditer(content):
            for symbol in match.group(1).split(","):
                sym_name = symbol.strip().split(" as ")[-1].strip()
                if sym_name:
                    imports.append((sym_name, match.group(2)))

        # Pattern 3: import * as x from "module"
        namespace_import_pattern = re.compile(
            r"import\s+\*\s+as\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]"
        )
        for match in namespace_import_pattern.finditer(content):
            imports.append((match.group(1), match.group(2)))

        # Pattern 4: import default from "module"
        default_import_pattern = re.compile(
            r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]"
        )
        for match in default_import_pattern.finditer(content):
            imports.append((match.group(1), match.group(2)))

        return imports

    def _extract_fields(self, content: str, start_pos: int) -> dict[str, dict]:
        """Extract fields from interface/class body."""
        fields = {}
        depth = 1
        pos = start_pos
        body_end = start_pos

        # Find matching closing brace
        while depth > 0 and pos < len(content):
            if content[pos] == "{":
                depth += 1
            elif content[pos] == "}":
                depth -= 1
                if depth == 0:
                    body_end = pos
            pos += 1

        body = content[start_pos:body_end]

        # Parse field definitions (both required and optional)
        field_pattern = re.compile(
            r"^\s*([A-Za-z_]\w*)\s*(\?)?\s*:\s*([^;{]+?)(?:;|$)",
            re.MULTILINE
        )

        for match in field_pattern.finditer(body):
            field_name = match.group(1)
            optional_marker = match.group(2)
            field_type = match.group(3).strip()
            is_optional = optional_marker == "?"

            fields[field_name] = {
                "type": field_type,
                "required": not is_optional,
            }

        return fields

    def resolve_symbol(self, symbol_name: str, from_file: str, file_getter: Callable[[str], str | None] | None = None) -> TypeNode | None:
        """Resolve a symbol from imports/exports."""
        # Direct lookup
        symbol_id = f"{from_file}#{symbol_name}"
        if symbol_id in self.nodes:
            return self.nodes[symbol_id]

        # Check imports in from_file
        if from_file in self.imports:
            for imported_symbol, from_path in self.imports[from_file]:
                if imported_symbol == symbol_name:
                    resolved_file = self._resolve_import_path(from_path, from_file, file_getter)
                    if resolved_file:
                        return self.resolve_symbol(symbol_name, resolved_file, file_getter)

        # Check barrel exports (index.ts)
        index_path = from_file.rsplit("/", 1)[0] + "/index.ts"
        if index_path in self.exports and symbol_name in self.exports[index_path]:
            return self.resolve_symbol(symbol_name, index_path, file_getter)

        return None

    def _resolve_import_path(self, import_path: str, from_file: str, file_getter: Callable[[str], str | None] | None = None) -> str | None:
        """Resolve import path to actual file."""
        # Relative import
        if import_path.startswith("."):
            base_dir = from_file.rsplit("/", 1)[0]
            resolved = f"{base_dir}/{import_path}".replace("//", "/")
            # Try with various extensions
            for ext in [".ts", ".tsx", ".js", ".jsx", "/index.ts"]:
                candidate = resolved if resolved.endswith(ext) else resolved + ext
                if file_getter and file_getter(candidate):
                    return candidate
            return resolved

        # Absolute/node_modules import (not resolved)
        return import_path

    def build_symbol_graph(self) -> dict[str, Any]:
        """Build symbol graph with dependencies."""
        graph = {
            "nodes": {},
            "edges": [],
        }

        for symbol_id, node in self.nodes.items():
            structural_hash = node.compute_structural_hash()

            graph["nodes"][symbol_id] = {
                **node.to_dict(),
                "structural_hash": structural_hash,
            }

        # Build edges
        for symbol_id, node in self.nodes.items():
            for dep in node.dependencies:
                if dep in self.nodes:
                    graph["edges"].append({
                        "source": symbol_id,
                        "target": dep,
                        "type": "depends_on",
                    })

            for imp in node.imported_by:
                if imp in self.nodes:
                    graph["edges"].append({
                        "source": imp,
                        "target": symbol_id,
                        "type": "imports",
                    })

        # Sort for determinism
        graph["nodes"] = dict(sorted(graph["nodes"].items()))
        graph["edges"] = sorted(graph["edges"], key=lambda e: (e["source"], e["target"]))

        return graph

    def detect_type_changes(self, baseline_graph: dict | None, current_graph: dict) -> list[dict]:
        """Detect structural type changes between baseline and current."""
        changes = []

        if not baseline_graph:
            return changes

        baseline_nodes = baseline_graph.get("nodes", {})
        current_nodes = current_graph.get("nodes", {})

        # Compare hashes for existing types
        for symbol_id in baseline_nodes:
            if symbol_id in current_nodes:
                baseline_hash = baseline_nodes[symbol_id].get("structural_hash", "")
                current_hash = current_nodes[symbol_id].get("structural_hash", "")

                if baseline_hash != current_hash:
                    current_node = current_nodes[symbol_id]

                    changes.append({
                        "type": "TYPE_STRUCTURE_CHANGE",
                        "symbol_id": symbol_id,
                        "symbol_name": current_node.get("name", ""),
                        "kind": current_node.get("kind", ""),
                        "description": f"Type structure changed for {current_node.get('name')}",
                        "severity": "MAJOR",
                        "file": current_node.get("file_path", ""),
                        "classification_basis": "TYPE_CHANGE",
                        "id": _stable_hash({"symbol_id": symbol_id, "type": "TYPE_STRUCTURE_CHANGE"}),
                    })
            else:
                changes.append({
                    "type": "TYPE_REMOVED",
                    "symbol_id": symbol_id,
                    "symbol_name": baseline_nodes[symbol_id].get("name", ""),
                    "kind": baseline_nodes[symbol_id].get("kind", ""),
                    "description": f"Type {baseline_nodes[symbol_id].get('name')} removed",
                    "severity": "MAJOR",
                    "file": baseline_nodes[symbol_id].get("file_path", ""),
                    "classification_basis": "TYPE_CHANGE",
                    "id": _stable_hash({"symbol_id": symbol_id, "type": "TYPE_REMOVED"}),
                })

        # Added types
        for symbol_id in current_nodes:
            if symbol_id not in baseline_nodes:
                if current_nodes[symbol_id].get("exported"):
                    changes.append({
                        "type": "TYPE_ADDED",
                        "symbol_id": symbol_id,
                        "symbol_name": current_nodes[symbol_id].get("name", ""),
                        "kind": current_nodes[symbol_id].get("kind", ""),
                        "description": f"New exported type {current_nodes[symbol_id].get('name')}",
                        "severity": "MINOR",
                        "file": current_nodes[symbol_id].get("file_path", ""),
                        "classification_basis": "TYPE_CHANGE",
                        "id": _stable_hash({"symbol_id": symbol_id, "type": "TYPE_ADDED"}),
                    })

        return sorted(changes, key=lambda c: (c["symbol_id"], c["type"]))
