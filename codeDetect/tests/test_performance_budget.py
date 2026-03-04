import time

from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def _run(size: int) -> float:
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
        for i in range(size)
    ]
    start = time.perf_counter()
    out = resolve_route_candidates(candidates, ["routes.js"], _reader(files), {"backend_framework": "express"})
    elapsed = time.perf_counter() - start
    assert out["validation_status"] == "OK"
    assert len(out["candidates"]) == size
    return elapsed


def test_performance_budget_10k():
    assert _run(10000) < 1.5


def test_performance_budget_50k():
    assert _run(50000) < 4.0


def test_performance_budget_100k():
    assert _run(100000) < 8.0
