from src.intelligence.code_intelligence_builder import build_code_intelligence
from tests.ci_helpers import materialize_git_fixture, run_analysis


def _reader(mapping: dict[str, str]):
    def _read(path: str):
        return mapping.get(path)

    return _read


def test_build_code_intelligence_deterministic_shapes():
    repository_evidence = {
        "modules": [
            {"name": "auth", "files": ["src/controllers/auth.controller.ts", "src/services/auth.service.ts"]},
            {"name": "users", "files": ["src/models/user.model.ts"]},
        ],
        "services": [
            {"name": "AuthService", "module": "auth", "file": "src/services/auth.service.ts"},
        ],
        "entities": [
            {"name": "User", "source_file": "src/models/user.model.ts"},
        ],
        "apis": [
            {
                "method": "POST",
                "path": "/api/auth/login",
                "controller": "AuthController.login",
                "module": "auth",
                "source_file": "src/controllers/auth.controller.ts",
                "line": 12,
            },
        ],
        "relationships": [
            {"type": "calls", "from": "AuthController", "to": "AuthService"},
            {"type": "uses", "from": "AuthService", "to": "User"},
        ],
        "file_evidence": {
            "src/controllers/auth.controller.ts": {
                "functions": ["login", "logout"],
                "classes": ["AuthController"],
                "methods": [],
            },
            "src/services/auth.service.ts": {
                "functions": ["authenticate"],
                "classes": ["AuthService"],
                "methods": [],
            },
            "src/models/user.model.ts": {
                "functions": [],
                "classes": [],
                "methods": [],
            },
        },
    }

    call_graph = [
        {"caller": "auth.login", "calls": ["AuthService.authenticate", "jwt.sign"]},
        {"caller": "auth.logout", "calls": ["AuthService.invalidate"]},
    ]

    dependency_graph = {
        "edges": [
            {"from": "auth", "to": "users", "type": "internal"},
            {"from": "users", "to": "auth", "type": "internal"},
            {"from": "auth", "to": "express", "type": "external"},
        ]
    }

    files = {
        "src/controllers/auth.controller.ts": "class AuthController { login() {} logout() {} }",
        "src/services/auth.service.ts": "class AuthService { authenticate() {} }",
        "src/models/user.model.ts": "const User = mongoose.model('User', userSchema);",
    }

    out = build_code_intelligence(repository_evidence, call_graph, dependency_graph, _reader(files))

    assert set(out.keys()) == {"symbol_index", "call_graph", "dependency_graph", "repository_graph"}

    symbol_index = out["symbol_index"]
    assert symbol_index == sorted(
        symbol_index,
        key=lambda r: (r.get("name", "").lower(), r.get("name", ""), r.get("type", ""), r.get("file", ""), int(r.get("line", 0))),
    )
    assert any(s["type"] == "route" and s["name"] == "POST /api/auth/login" for s in symbol_index)
    assert any(s["type"] == "controller" and s["name"] == "AuthController" for s in symbol_index)
    assert any(s["type"] == "service" and s["name"] == "AuthService" for s in symbol_index)
    assert any(s["type"] == "entity" and s["name"] == "User" for s in symbol_index)

    cg = out["call_graph"]
    assert cg["nodes"] == sorted(set(cg["nodes"]))
    assert cg["edges"] == sorted(cg["edges"], key=lambda e: (e["from"], e["to"]))
    assert {"from": "AuthController", "to": "AuthService"} in cg["edges"]

    dg = out["dependency_graph"]
    assert dg["modules"] == ["auth", "users"]
    assert dg["dependencies"] == [{"from": "auth", "to": "users"}, {"from": "users", "to": "auth"}]
    assert dg["cycle_detected"] is True
    assert dg["circular_dependencies"] == [["auth", "users"]]

    rg = out["repository_graph"]
    node_ids = {n["id"] for n in rg["nodes"]}
    assert {"auth", "users", "AuthController", "AuthService", "User", "POST /api/auth/login"}.issubset(node_ids)
    assert {"from": "AuthController", "to": "AuthService", "type": "calls"} in rg["edges"]
    assert {"from": "AuthService", "to": "User", "type": "uses"} in rg["edges"]
    assert {"from": "auth", "to": "users", "type": "depends_on"} in rg["edges"]


def test_report_contains_code_intelligence_section(tmp_path):
    repo = materialize_git_fixture(tmp_path, "express_small")
    report = run_analysis(repo)
    ci = report.get("report", {}).get("code_intelligence", {})
    assert set(ci.keys()) == {"symbol_index", "call_graph", "dependency_graph", "repository_graph"}
    assert isinstance(ci.get("symbol_index"), list)
    assert isinstance(ci.get("call_graph", {}).get("nodes"), list)
    assert isinstance(ci.get("call_graph", {}).get("edges"), list)
    assert isinstance(ci.get("dependency_graph", {}).get("modules"), list)
    assert isinstance(ci.get("repository_graph", {}).get("nodes"), list)
    assert isinstance(ci.get("repository_graph", {}).get("edges"), list)

