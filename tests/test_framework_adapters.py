from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_spring_adapter_extracts_class_and_method_mappings():
    files = {
        "src/main/java/com/acme/UserController.java": """
package com.acme;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/users")
public class UserController {
  @GetMapping("/{id}")
  public String getUser(@PathVariable String id) { return "ok"; }
  @PostMapping
  public String createUser() { return "ok"; }
}
""",
    }
    out = resolve_route_candidates([], sorted(files.keys()), _reader(files), {"backend_framework": "spring"})
    assert out["validation_status"] == "OK"
    keys = {f"{c['method']} {c['path']}" for c in out["candidates"]}
    assert "GET /api/users/{id}" in keys
    assert "POST /api/users" in keys


def test_fastapi_adapter_extracts_included_router_routes_only():
    files = {
        "app/main.py": """
from fastapi import FastAPI
from .users import router as users_router
app = FastAPI()
app.include_router(users_router, prefix="/api")
""",
        "app/users.py": """
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/users")
@router.get("/{id}", dependencies=[Depends(get_current_user)])
def get_user(id: str): return {}
@router.post("/")
def create_user(): return {}
""",
        "app/unmounted.py": """
from fastapi import APIRouter
hidden = APIRouter(prefix="/hidden")
@hidden.get("/x")
def x(): return {}
""",
    }
    out = resolve_route_candidates([], sorted(files.keys()), _reader(files), {"backend_framework": "fastapi"})
    assert out["validation_status"] == "OK"
    keys = {f"{c['method']} {c['path']}" for c in out["candidates"]}
    assert "GET /api/users/{id}" in keys
    assert "POST /api/users" in keys
    assert "GET /hidden/x" not in keys


def test_mixed_stack_merges_deterministically():
    files = {
        "server.js": """
const express=require('express');
const app=express();
const router=express.Router();
app.use('/api/projects', router);
router.get('/:id', h);
""",
        "app.py": """
from fastapi import FastAPI
app = FastAPI()
@app.get("/healthz")
def health(): return {}
""",
    }
    candidates = [
        {"method": "GET", "path": "/:id", "source_file": "server.js", "line_start": 6, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    rows = out["candidates"]
    assert rows == sorted(rows, key=lambda c: (c["normalized_key"], c["source_file"], int(c["line_start"])))

