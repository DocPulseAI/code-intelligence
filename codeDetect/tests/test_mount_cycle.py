from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_mount_cycle_failure():
    files = {
        "routes.js": """
const express = require('express');
const a = express.Router();
const b = express.Router();
a.use('/a', b);
b.use('/b', a);
""",
    }
    candidates = [
        {"method": "GET", "path": "/x", "source_file": "routes.js", "line_start": 6, "router_symbol": "a", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "FAILED"
    assert out["error"] == "Router mount cycle detected"


def test_mount_depth_exceeded_failure():
    files = {}
    lines = ["const express = require('express');"]
    for i in range(12):
        lines.append(f"const r{i} = express.Router();")
    lines.append("const app = express();")
    lines.append("app.use('/m0', r0);")
    for i in range(11):
        lines.append(f"r{i}.use('/m{i+1}', r{i+1});")
    files["chain.js"] = "\n".join(lines)

    candidates = [
        {"method": "GET", "path": "/items", "source_file": "chain.js", "line_start": 30, "router_symbol": "r11", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, ["chain.js"], _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "FAILED"
    assert out["error"] == "Router mount depth exceeded"
