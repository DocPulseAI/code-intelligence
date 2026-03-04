from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_nested_mount_resolves_full_path():
    files = {
        "app.js": """
const express = require('express');
const app = express();
const routerA = require('./routerA');
app.use('/api', routerA);
""",
        "routerA.js": """
const express = require('express');
const routerA = express.Router();
const routerB = require('./routerB');
routerA.use('/projects', routerB);
module.exports = routerA;
""",
        "routerB.js": """
const express = require('express');
const routerB = express.Router();
routerB.get('/:id', handler);
module.exports = routerB;
""",
    }
    candidates = [
        {"method": "GET", "path": "/:id", "source_file": "routerB.js", "line_start": 4, "router_symbol": "routerB", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    row = out["candidates"][0]
    assert row["path"] == "/api/projects/{id}"
    assert row["normalized_key"] == "get /api/projects/{id}"
