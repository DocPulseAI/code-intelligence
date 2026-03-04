from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_duplicate_normalized_route_detected():
    files = {
        "app.js": """
const express = require('express');
const app = express();
const a = express.Router();
const b = express.Router();
app.use('/api', a);
app.use('/api', b);
""",
    }
    candidates = [
        {"method": "GET", "path": "/users/:id", "source_file": "app.js", "line_start": 6, "router_symbol": "a", "middleware_tokens": []},
        {"method": "GET", "path": "/users/:id", "source_file": "app.js", "line_start": 7, "router_symbol": "b", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "FAILED"
    assert out["error"] == "Duplicate normalized route detected"
