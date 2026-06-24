from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_failure_contract_duplicate():
    files = {"x.js": "const express=require('express'); const app=express(); const a=express.Router(); const b=express.Router(); app.use('/api',a); app.use('/api',b);"}
    candidates = [
        {"method": "GET", "path": "/users/:id", "source_file": "x.js", "line_start": 1, "router_symbol": "a", "middleware_tokens": []},
        {"method": "GET", "path": "/users/:id", "source_file": "x.js", "line_start": 1, "router_symbol": "b", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, ["x.js"], _reader(files), {"backend_framework": "express"})
    assert out == {"validation_status": "FAILED", "error": "Duplicate normalized route detected"}


def test_failure_contract_cycle():
    files = {"x.js": "const express=require('express'); const a=express.Router(); const b=express.Router(); a.use('/a',b); b.use('/b',a);"}
    candidates = [{"method": "GET", "path": "/x", "source_file": "x.js", "line_start": 1, "router_symbol": "a", "middleware_tokens": []}]
    out = resolve_route_candidates(candidates, ["x.js"], _reader(files), {"backend_framework": "express"})
    assert out == {"validation_status": "FAILED", "error": "Router mount cycle detected"}


def test_failure_contract_depth():
    lines = ["const express=require('express');", "const app=express();"]
    for i in range(12):
        lines.append(f"const r{i}=express.Router();")
    lines.append("app.use('/m0',r0);")
    for i in range(11):
        lines.append(f"r{i}.use('/m{i+1}',r{i+1});")
    files = {"x.js": "\n".join(lines)}
    candidates = [{"method": "GET", "path": "/x", "source_file": "x.js", "line_start": 1, "router_symbol": "r11", "middleware_tokens": []}]
    out = resolve_route_candidates(candidates, ["x.js"], _reader(files), {"backend_framework": "express"})
    assert out == {"validation_status": "FAILED", "error": "Router mount depth exceeded"}


def test_failure_contract_malformed_path():
    files = {"x.js": "const express=require('express'); const router=express.Router();"}
    candidates = [{"method": "GET", "path": "/users/{bad-param!}", "source_file": "x.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": []}]
    out = resolve_route_candidates(candidates, ["x.js"], _reader(files), {"backend_framework": "express"})
    assert out == {"validation_status": "FAILED", "error": "Malformed path detected"}
