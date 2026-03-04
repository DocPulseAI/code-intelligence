from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_fastapi_bypass_preserves_paths():
    files = {"app.py": "from fastapi import FastAPI\napp = FastAPI()"}
    candidates = [
        {"method": "GET", "path": "/users/{id}", "source_file": "app.py", "line_start": 1, "router_symbol": "", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, ["app.py"], _reader(files), {"backend_framework": "fastapi"})
    assert out["validation_status"] == "OK"
    assert out["candidates"][0]["path"] == "/users/{id}"
    assert "normalized_key" not in out["candidates"][0]


def test_spring_bypass_preserves_paths():
    files = {"UserController.java": "@RestController public class UserController {}"}
    candidates = [
        {"method": "GET", "path": "/users/{id}", "source_file": "UserController.java", "line_start": 1, "router_symbol": "", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, ["UserController.java"], _reader(files), {"backend_framework": "spring"})
    assert out["validation_status"] == "OK"
    assert out["candidates"][0]["path"] == "/users/{id}"
    assert "normalized_key" not in out["candidates"][0]
