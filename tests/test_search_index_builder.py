"""Tests for semantic search index generation."""

import sys
from pathlib import Path

# Ensure codeDetect root is importable.
_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.intelligence.search_index_builder import build_search_index


def _make_reader(content_map: dict[str, str]):
    def _read(path: str):
        return content_map.get(path)
    return _read


def test_build_search_index_contains_expected_sections_and_links():
    files = {
        "src/auth/auth.controller.ts": (
            "export class AuthController {\n"
            "  async createUser() {\n"
            "    return this.authService.createUser();\n"
            "  }\n"
            "}\n"
        ),
        "src/auth/auth.service.ts": (
            "export class AuthService {\n"
            "  async createUser() {\n"
            "    return prisma.user.create({});\n"
            "  }\n"
            "}\n"
        ),
    }

    repository_evidence = {
        "modules": [
            {
                "name": "auth",
                "type": "backend_module",
                "files": ["src/auth/auth.controller.ts", "src/auth/auth.service.ts"],
            }
        ],
        "file_evidence": {
            "src/auth/auth.controller.ts": {
                "functions": ["createUser"],
                "classes": ["AuthController"],
            },
            "src/auth/auth.service.ts": {
                "functions": ["createUser"],
                "classes": ["AuthService"],
            },
        },
        "services": [
            {
                "name": "AuthService",
                "module": "auth",
                "file": "src/auth/auth.service.ts",
                "functions": ["createUser"],
            }
        ],
        "apis": [
            {
                "method": "POST",
                "path": "/users",
                "controller": "AuthController.createUser",
                "module": "auth",
                "source_file": "src/auth/auth.controller.ts",
                "line": 2,
            }
        ],
        "relationships": [
            {"type": "calls", "from": "AuthController", "to": "AuthService"}
        ],
    }

    architecture_reconstruction = {
        "components": [
            {"id": "AuthController", "type": "controller", "layer": "presentation"},
            {"id": "AuthService", "type": "service", "layer": "application"},
            {"id": "PrismaClient", "type": "database", "layer": "infrastructure"},
        ],
        "edges": [
            {"from": "AuthController", "to": "AuthService", "type": "calls"},
            {"from": "AuthService", "to": "PrismaClient", "type": "queries"},
        ],
    }

    dependency_graph = {
        "nodes": [{"id": "auth", "type": "internal_module"}, {"id": "bcrypt", "type": "external_package"}],
        "edges": [{"from": "auth", "to": "bcrypt", "type": "external"}],
    }

    call_graph = [
        {"caller": "auth.createUser", "calls": ["bcrypt.hash", "AuthService.createUser"]},
        {"caller": "AuthController.createUser", "calls": ["AuthService.createUser"]},
    ]

    out = build_search_index(
        repository_evidence,
        architecture_reconstruction,
        dependency_graph,
        call_graph,
        _make_reader(files),
    )

    index = out["search_index"]
    assert set(index.keys()) == {"symbols", "references", "apis", "modules"}

    symbols = {(s["name"], s["type"]) for s in index["symbols"]}
    assert ("createUser", "function") in symbols
    assert ("AuthController", "controller") in symbols
    assert ("AuthService", "service") in symbols

    api_entry = index["apis"][0]
    assert api_entry["endpoint"] == "POST /users"
    assert api_entry["controller"] == "AuthController"
    assert api_entry["service"] == "AuthService"

    refs = {r["symbol"]: r for r in index["references"]}
    assert "createUser" in refs
    assert "bcrypt.hash" in refs["createUser"]["calls"]
    assert "POST /users" in refs["AuthService"]["called_by"]

    modules = {m["name"]: m for m in index["modules"]}
    assert "auth" in modules
    assert "src/auth/auth.controller.ts" == modules["auth"]["file"]
    assert "bcrypt" in modules["auth"]["dependencies"]
    assert "PrismaClient" in modules["auth"]["dependencies"]


def test_search_index_builder_is_deterministic():
    repository_evidence = {"modules": [], "file_evidence": {}, "services": [], "apis": [], "relationships": []}
    architecture_reconstruction = {"components": [], "edges": []}
    dependency_graph = {"nodes": [], "edges": []}
    call_graph = []

    first = build_search_index(repository_evidence, architecture_reconstruction, dependency_graph, call_graph, None)
    second = build_search_index(repository_evidence, architecture_reconstruction, dependency_graph, call_graph, None)

    assert first == second
    assert first["search_index"] == {"symbols": [], "references": [], "apis": [], "modules": []}

