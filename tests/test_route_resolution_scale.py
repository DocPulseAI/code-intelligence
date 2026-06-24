import time
import tracemalloc

from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


import sys
import pytest

def test_scale_10k_routes_time_and_memory():
    if sys.gettrace() is not None:
        pytest.skip("Skipping performance budget test under coverage instrumentation")
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
    start = time.perf_counter()
    out = resolve_route_candidates(candidates, ["routes.js"], _reader(files), {"backend_framework": "express"})
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert out["validation_status"] == "OK"
    assert len(out["candidates"]) == 10000
    assert elapsed < 1.5
    assert peak < 30 * 1024 * 1024
