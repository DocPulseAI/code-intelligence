import tracemalloc

from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_memory_budget_10k_routes_under_40mb():
    files = {"routes.js": "const express=require('express'); const router=express.Router();"}
    candidates = [
        {
            "method": "GET",
            "path": f"/items/{i}",
            "source_file": "routes.js",
            "line_start": i + 1,
            "router_symbol": "router",
            "middleware_tokens": [],
        }
        for i in range(10000)
    ]

    tracemalloc.start()
    out = resolve_route_candidates(candidates, ["routes.js"], _reader(files), {"backend_framework": "express"})
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert out["validation_status"] == "OK"
    assert peak < 40 * 1024 * 1024
