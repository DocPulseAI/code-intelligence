import json
import subprocess
import sys
from pathlib import Path

from src.intelligence.api_surface import build_api_surface
from src.intelligence.documentation_contract import build_documentation_contract
from src.intelligence.repository_classifier import classify_repository_type
from src.intelligence.tech_stack_model import build_tech_stack


def _reader(files: dict[str, str]):
    def _read(path: str):
        return files.get(path)

    return _read


def test_repository_type_fullstack():
    files = {
        "backend/package.json": '{"dependencies":{"express":"^4.0.0"}}',
        "frontend/package.json": '{"dependencies":{"react":"^18.0.0"}}',
    }
    repo_type = classify_repository_type(sorted(files.keys()), _reader(files), api_endpoint_count=2)
    assert repo_type == "fullstack"


def test_repository_type_infra_only():
    files = {
        "Dockerfile": "FROM python:3.12",
        ".github/workflows/ci.yml": "name: ci",
        "infra/main.tf": "resource \"aws_s3_bucket\" \"b\" {}",
    }
    repo_type = classify_repository_type(sorted(files.keys()), _reader(files), api_endpoint_count=0)
    assert repo_type == "infra-only"


def test_tech_stack_keys_and_sorted_arrays():
    files = {
        "package.json": '{"dependencies":{"express":"^4","mongoose":"^8","react":"^18"}}',
        "Dockerfile": "FROM node:20",
        ".github/workflows/ci.yml": "name: ci",
    }
    stack = build_tech_stack(sorted(files.keys()), _reader(files))
    assert set(stack.keys()) == {"backend_framework", "frontend_framework", "database", "orm", "infra", "ci"}
    assert stack["infra"] == sorted(stack["infra"])
    assert stack["ci"] == sorted(stack["ci"])


def test_documentation_contract_alignment():
    backend = build_documentation_contract("backend-service", True)
    frontend = build_documentation_contract("frontend-app", True)
    infra = build_documentation_contract("infra-only", True)

    assert backend["requires_api_reference"] is True
    assert backend["requires_adr"] is True
    assert frontend["requires_api_reference"] is False
    assert frontend["requires_adr"] is False
    assert infra["requires_architecture_doc"] is False


def test_api_surface_sorted_and_boolean_auth():
    endpoints = [
        {
            "method": "post",
            "path": "auth/login",
            "auth": {"required": True},
            "request": {"path_params": [], "query_params": [], "body_schema": {"a": 1}},
            "responses": [{"status": 200}],
            "source": {"file": "src/auth.controller.js"},
        },
        {
            "method": "GET",
            "path": "/health",
            "auth": {"required": None},
            "request": {},
            "responses": [],
            "source": {"file": "src/health.controller.js"},
        },
    ]
    surface = build_api_surface(endpoints)
    assert surface == sorted(surface, key=lambda e: (e["method"], e["path"]))
    assert all(isinstance(e["auth_required"], bool) for e in surface)
    assert all(e["request_schema_hash"] for e in surface)
    assert all(e["response_schema_hash"] is not None for e in surface)


def test_determinism_same_repo_twice(tmp_path: Path):
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name":"demo","dependencies":{"express":"^4.0.0"}}', encoding="utf-8")
    (repo / "server.js").write_text('const express=require("express"); const app=express(); app.get("/health",()=>{});', encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    project_root = Path(__file__).resolve().parents[1]
    main_py = project_root / "main.py"
    report_file = project_root / "impact_report.json"

    subprocess.run([sys.executable, str(main_py), str(repo), "main"], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first = report_file.read_bytes()

    subprocess.run([sys.executable, str(main_py), str(repo), "main"], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    second = report_file.read_bytes()

    assert first == second
    parsed = json.loads(first.decode("utf-8"))
    assert parsed["deterministic"] is True
