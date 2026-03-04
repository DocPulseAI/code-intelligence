from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_auth_closed_set_and_precedence():
    files = {"routes.js": "const express=require('express'); const router=express.Router();"}
    candidates = [
        {"method": "GET", "path": "/a", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": ["jwt", "authorizeRole"]},
        {"method": "GET", "path": "/b", "source_file": "routes.js", "line_start": 2, "router_symbol": "router", "middleware_tokens": ["jwtGuard"]},
        {"method": "GET", "path": "/c", "source_file": "routes.js", "line_start": 3, "router_symbol": "router", "middleware_tokens": ["sessionAuth"]},
        {"method": "GET", "path": "/d", "source_file": "routes.js", "line_start": 4, "router_symbol": "router", "middleware_tokens": ["rbac"]},
        {"method": "GET", "path": "/e", "source_file": "routes.js", "line_start": 5, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, ["routes.js"], _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    allowed = {"JWT", "Session", "RBAC", "JWT+RBAC", "Public"}
    values = {c["path"]: c["resolved_auth_type"] for c in out["candidates"]}
    assert set(values.values()).issubset(allowed)
    assert "Unknown" not in set(values.values())
    assert values["/a"] == "JWT+RBAC"
    assert values["/b"] == "JWT"
    assert values["/c"] == "Session"
    assert values["/d"] == "RBAC"
    assert values["/e"] == "Public"


def test_auth_inheritance_from_mount_chain():
    files = {
        "app.js": """
const express = require('express');
const app = express();
const api = require('./api');
app.use('/api', authenticate, authorizeRole, api);
""",
        "api.js": """
const express = require('express');
const router = express.Router();
router.get('/projects/:id', handler);
module.exports = router;
""",
    }
    candidates = [
        {"method": "GET", "path": "/projects/:id", "source_file": "api.js", "line_start": 3, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    row = out["candidates"][0]
    assert row["path"] == "/api/projects/{id}"
    assert row["resolved_auth_type"] == "JWT+RBAC"
