from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_operation_id_grammar_matrix():
    files = {"routes.js": "const express=require('express'); const router=express.Router();"}
    candidates = [
        {"method": "GET", "path": "/resources", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": []},
        {"method": "POST", "path": "/resources", "source_file": "routes.js", "line_start": 2, "router_symbol": "router", "middleware_tokens": []},
        {"method": "GET", "path": "/resources/{id}", "source_file": "routes.js", "line_start": 3, "router_symbol": "router", "middleware_tokens": []},
        {"method": "PATCH", "path": "/resources/{id}", "source_file": "routes.js", "line_start": 4, "router_symbol": "router", "middleware_tokens": []},
        {"method": "DELETE", "path": "/resources/{id}", "source_file": "routes.js", "line_start": 5, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, ["routes.js"], _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    by_key = {f"{c['method']} {c['path']}": c["operation_id"] for c in out["candidates"]}
    assert by_key["GET /resources"] == "getResources"
    assert by_key["POST /resources"] == "createResource"
    assert by_key["GET /resources/{id}"] == "getResourceById"
    assert by_key["PATCH /resources/{id}"] == "updateResource"
    assert by_key["DELETE /resources/{id}"] == "deleteResourceById"


def test_operation_id_semantic_cases():
    files = {"routes.js": "const express=require('express'); const router=express.Router();"}
    candidates = [
        {"method": "GET", "path": "/activities", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": []},
        {"method": "GET", "path": "/dashboard/overview", "source_file": "routes.js", "line_start": 2, "router_symbol": "router", "middleware_tokens": []},
        {"method": "GET", "path": "/tasks/board/{projectId}", "source_file": "routes.js", "line_start": 3, "router_symbol": "router", "middleware_tokens": []},
        {"method": "DELETE", "path": "/comments/{id}/reactions/{emoji}", "source_file": "routes.js", "line_start": 4, "router_symbol": "router", "middleware_tokens": []},
        {"method": "GET", "path": "/search", "source_file": "routes.js", "line_start": 5, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, ["routes.js"], _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    by_key = {f"{c['method']} {c['path']}": c["operation_id"] for c in out["candidates"]}
    assert by_key["GET /activities"] == "getActivities"
    assert by_key["GET /dashboard/overview"] == "getDashboardOverview"
    assert by_key["GET /tasks/board/{projectId}"] == "getTaskBoardByProjectId"
    assert by_key["DELETE /comments/{id}/reactions/{emoji}"] == "deleteCommentReaction"
    assert by_key["GET /search"] == "searchResources"


def test_operation_id_duplicate_suffix_is_deterministic():
    files = {"routes.js": "const express=require('express'); const router=express.Router();"}
    candidates = [
        {"method": "GET", "path": "/projects", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": []},
        {"method": "GET", "path": "/project", "source_file": "routes.js", "line_start": 2, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, ["routes.js"], _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    ops = [c["operation_id"] for c in out["candidates"]]
    assert ops == ["getProjects", "getProjects_2"]
