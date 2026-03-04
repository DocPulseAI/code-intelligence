import json
import hashlib

from src.intelligence.route_resolution_engine import resolve_route_candidates


def _reader(files):
    def read(path):
        return files.get(path)

    return read


def test_multi_router_same_file():
    files = {
        "server.js": """
const express = require('express');
const app = express();
const r1 = express.Router();
const r2 = express.Router();
app.use('/projects', r1);
app.use('/users', r2);
r1.get('/:id', handler1);
r2.get('/:id', handler2);
""",
    }
    candidates = [
        {"method": "GET", "path": "/:id", "source_file": "server.js", "line_start": 7, "router_symbol": "r1", "middleware_tokens": []},
        {"method": "GET", "path": "/:id", "source_file": "server.js", "line_start": 8, "router_symbol": "r2", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    keys = [c["normalized_key"] for c in out["candidates"]]
    assert "get /projects/{id}" in keys
    assert "get /users/{id}" in keys


def test_nested_router_chain_resolution():
    files = {
        "app.js": """
const express = require('express');
const app = express();
const api = require('./api');
app.use('/v1', api);
""",
        "api.js": """
const express = require('express');
const router = express.Router();
const projects = require('./projects');
router.use('/projects', projects);
module.exports = router;
""",
        "projects.js": """
const express = require('express');
const router = express.Router();
router.delete('/:id', removeProject);
module.exports = router;
""",
    }
    candidates = [
        {"method": "DELETE", "path": "/:id", "source_file": "projects.js", "line_start": 4, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    assert out["candidates"][0]["path"] == "/v1/projects/{id}"


def test_mount_cycle_detection():
    files = {
        "a.js": """
const express = require('express');
const r1 = express.Router();
const r2 = express.Router();
r1.use('/x', r2);
r2.use('/y', r1);
""",
    }
    candidates = [
        {"method": "GET", "path": "/z", "source_file": "a.js", "line_start": 6, "router_symbol": "r1", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "FAILED"
    assert out["error"] == "Router mount cycle detected"


def test_duplicate_route_detection():
    files = {
        "app.js": """
const express = require('express');
const app = express();
const a = express.Router();
const b = express.Router();
app.use('/x', a);
app.use('/x', b);
""",
    }
    candidates = [
        {"method": "GET", "path": "/:id", "source_file": "app.js", "line_start": 6, "router_symbol": "a", "middleware_tokens": []},
        {"method": "GET", "path": "/:id", "source_file": "app.js", "line_start": 7, "router_symbol": "b", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "FAILED"
    assert out["error"] == "Duplicate normalized route detected"


def test_semantic_operation_id_and_param_conversion():
    files = {"routes.js": "const express=require('express'); const router=express.Router();"}
    candidates = [
        {"method": "GET", "path": "/projects/:id", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": []},
        {"method": "POST", "path": "/projects", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    rows = {c["method"] + " " + c["path"]: c for c in out["candidates"]}
    assert "GET /projects/{id}" in rows
    assert rows["GET /projects/{id}"]["operation_id"] == "getProjectById"
    assert rows["POST /projects"]["operation_id"] == "createProject"


def test_param_constraint_and_optional_marker_normalization():
    files = {"routes.js": "const express=require('express'); const router=express.Router();"}
    candidates = [
        {"method": "GET", "path": "/users/:id(\\\\d+)?", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": []},
        {"method": "GET", "path": "/teams/{teamId:[0-9]+}/", "source_file": "routes.js", "line_start": 2, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    paths = [c["path"] for c in out["candidates"]]
    keys = [c["normalized_key"] for c in out["candidates"]]
    assert "/users/{id}" in paths
    assert "/teams/{teamId}" in paths
    assert "get /users/{id}" in keys


def test_auth_precedence():
    files = {"routes.js": "const express=require('express'); const router=express.Router();"}
    candidates = [
        {"method": "GET", "path": "/a", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": ["jwt", "authorizeRole"]},
        {"method": "GET", "path": "/b", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": ["sessionAuth"]},
        {"method": "GET", "path": "/c", "source_file": "routes.js", "line_start": 1, "router_symbol": "router", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    rows = {c["path"]: c for c in out["candidates"]}
    assert rows["/a"]["resolved_auth_type"] == "JWT+RBAC"
    assert rows["/b"]["resolved_auth_type"] == "Session"
    assert rows["/c"]["resolved_auth_type"] == "Public"


def test_large_repo_simulation_10k_routes():
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
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    assert len(out["candidates"]) == 10000


def test_determinism_snapshot():
    files = {
        "server.js": """
const express = require('express');
const app = express();
const router = express.Router();
app.use('/api/projects', router);
router.get('/:id', auth, handler);
""",
    }
    candidates = [
        {"method": "GET", "path": "/:id", "source_file": "server.js", "line_start": 6, "router_symbol": "router", "middleware_tokens": ["auth"]},
    ]
    one = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    two = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    row = one["candidates"][0]
    expected = hashlib.sha256(f"v1|GET|/api/projects/{{id}}".encode("utf-8")).hexdigest()
    assert row["endpoint_hash"] == expected


def test_unmounted_router_routes_are_not_emitted():
    files = {
        "server.js": """
const express=require('express');
const app=express();
const mounted = express.Router();
const orphan = express.Router();
app.use('/api/mounted', mounted);
mounted.get('/x', h);
orphan.get('/y', h2);
""",
    }
    candidates = [
        {"method": "GET", "path": "/x", "source_file": "server.js", "line_start": 7, "router_symbol": "mounted", "middleware_tokens": []},
        {"method": "GET", "path": "/y", "source_file": "server.js", "line_start": 8, "router_symbol": "orphan", "middleware_tokens": []},
    ]
    out = resolve_route_candidates(candidates, sorted(files.keys()), _reader(files), {"backend_framework": "express"})
    assert out["validation_status"] == "OK"
    keys = {c["normalized_key"] for c in out["candidates"]}
    assert "get /api/mounted/x" in keys
    assert "get /y" not in keys
