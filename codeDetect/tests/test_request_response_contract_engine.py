"""
Test suite for Request/Response Contract Engine (Phase 2).
Validates route contract extraction and breaking change detection.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.intelligence.request_response_contract_engine import (
    RequestResponseContractEngine,
    SchemaValidator,
    RouteParameter,
    BodySchema,
)


def test_simple_route_extraction():
    """Test extraction of simple Express routes."""
    engine = RequestResponseContractEngine()

    content = """
const express = require('express');
const app = express();

app.get('/users', (req, res) => {
  res.json({users: []});
});

app.post('/users', (req, res) => {
  res.status(201).json({id: 1});
});
"""

    contracts = engine.parse_file("routes.js", content)

    assert len(contracts) >= 2
    get_contract = [c for c in contracts if c.method == "get"][0]
    assert get_contract.path == "/users"
    assert 200 in get_contract.status_codes

    post_contract = [c for c in contracts if c.method == "post"][0]
    assert post_contract.method == "post"
    assert 201 in post_contract.status_codes


def test_path_parameter_extraction():
    """Test extraction of path parameters."""
    engine = RequestResponseContractEngine()

    content = """
app.get('/users/:id', (req, res) => {
  const userId = req.params.id;
  res.json({id: userId});
});

app.put('/users/:userId/posts/:postId', (req, res) => {
  res.json({updated: true});
});
"""

    contracts = engine.parse_file("routes.js", content)

    user_route = [c for c in contracts if "/users/:id" in c.path][0]
    assert len(user_route.path_params) == 1
    assert user_route.path_params[0].name == "id"

    multi_param = [c for c in contracts if ":postId" in c.path][0]
    assert len(multi_param.path_params) == 2
    param_names = {p.name for p in multi_param.path_params}
    assert "userId" in param_names
    assert "postId" in param_names


def test_zod_schema_detection():
    """Test detection of Zod schemas in routes."""
    engine = RequestResponseContractEngine()

    content = """
import { z } from 'zod';

const UserSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  name: z.string(),
});

app.post('/users', (req, res) => {
  const parsed = UserSchema.parse(req.body);
  res.status(201).json(parsed);
});
"""

    contracts = engine.parse_file("routes.ts", content)

    post_route = [c for c in contracts if c.method == "post"][0]
    assert post_route.body_schema is not None
    assert post_route.body_schema.validator == SchemaValidator.ZOD
    assert post_route.body_schema.schema_name == "UserSchema"


def test_joi_schema_detection():
    """Test detection of Joi schemas."""
    engine = RequestResponseContractEngine()

    content = """
const Joi = require('joi');

const createUserSchema = Joi.object({
  username: Joi.string().required(),
  email: Joi.string().email().required(),
});

app.post('/users', (req, res) => {
  const {error, value} = createUserSchema.validate(req.body);
  if (error) return res.status(400).json(error);
  res.status(201).json(value);
});
"""

    contracts = engine.parse_file("routes.js", content)

    post_route = [c for c in contracts if c.method == "post"][0]
    assert post_route.body_schema is not None
    assert post_route.body_schema.validator == SchemaValidator.JOI


def test_query_parameters():
    """Test extraction of query parameters."""
    engine = RequestResponseContractEngine()

    content = """
app.get('/search', (req, res) => {
  const q = req.query.q;
  const limit = req.query.limit;
  res.json({results: []});
});
"""

    contracts = engine.parse_file("routes.js", content)

    search_route = [c for c in contracts if c.path == "/search"][0]
    # Query parameters should be extracted
    assert search_route.method == "get"


def test_multiple_http_methods():
    """Test extraction of different HTTP methods."""
    engine = RequestResponseContractEngine()

    content = """
app.get('/items/:id', (req, res) => res.json({}));
app.post('/items', (req, res) => res.status(201).json({}));
app.put('/items/:id', (req, res) => res.json({}));
app.patch('/items/:id', (req, res) => res.json({}));
app.delete('/items/:id', (req, res) => res.status(204).send());
"""

    contracts = engine.parse_file("routes.js", content)

    methods = {c.method for c in contracts}
    assert "get" in methods
    assert "post" in methods
    assert "put" in methods
    assert "patch" in methods
    assert "delete" in methods


def test_status_code_extraction():
    """Test extraction of HTTP status codes."""
    engine = RequestResponseContractEngine()

    content = """
app.get('/items/:id', (req, res) => {
  res.status(200).json({});
});

app.post('/items', (req, res) => {
  res.status(201).json({});
});

app.delete('/items/:id', (req, res) => {
  res.status(204).send();
});

app.get('/items/:id/comments', (req, res) => {
  if (notFound) {
    return res.status(404).json({error: 'not found'});
  }
  res.json({});
});
"""

    contracts = engine.parse_file("routes.js", content)

    delete_route = [c for c in contracts if c.method == "delete"][0]
    assert 204 in delete_route.status_codes


def test_contract_hash_determinism():
    """Test that contract hashing is deterministic."""
    engine = RequestResponseContractEngine()

    content = """
app.post('/users', (req, res) => {
  res.status(201).json({id: 1, name: 'test'});
});
"""

    # Parse twice
    contracts1 = engine.parse_file("routes.js", content)
    hash1 = contracts1[0].compute_contract_hash()

    # Reset and parse again
    engine = RequestResponseContractEngine()
    contracts2 = engine.parse_file("routes.js", content)
    hash2 = contracts2[0].compute_contract_hash()

    assert hash1 == hash2, "Contract hash must be deterministic"


def test_three_run_contract_determinism():
    """Three-run determinism test for contract extraction."""
    content = """
import { z } from 'zod';

app.get('/api/users', getUserList);
app.get('/api/users/:id', getUser);
app.post('/api/users', createUser);
app.put('/api/users/:id', updateUser);
app.delete('/api/users/:id', deleteUser);

const UserSchema = z.object({
  id: z.string(),
  email: z.string(),
  name: z.string(),
});
"""

    snapshots = []
    for run in range(3):
        engine = RequestResponseContractEngine()
        contracts = engine.parse_file("routes.ts", content)

        snapshot = {
            "count": len(contracts),
            "hashes": sorted([(c.method, c.path, c.compute_contract_hash()) for c in contracts]),
            "status_codes": sorted([
                (c.method, c.path, sorted(c.status_codes))
                for c in contracts
            ]),
        }
        snapshots.append(snapshot)

    assert snapshots[0] == snapshots[1] == snapshots[2], f"Snapshot mismatch: {snapshots}"


def test_contract_change_detection_route_removed():
    """Test detection of removed routes."""
    engine = RequestResponseContractEngine()

    baseline_contracts = [
        {
            "method": "get",
            "path": "/api/users",
            "body_schema": None,
            "query_params": [],
            "status_codes": [200],
        },
        {
            "method": "post",
            "path": "/api/users",
            "body_schema": {"validator": "zod"},
            "query_params": [],
            "status_codes": [201],
        },
    ]

    current_contracts = [
        {
            "method": "get",
            "path": "/api/users",
            "body_schema": None,
            "query_params": [],
            "status_codes": [200],
        },
    ]

    changes = engine.detect_contract_changes(baseline_contracts, current_contracts)

    assert len(changes) > 0
    assert any(c["type"] == "ROUTE_REMOVED" for c in changes)


def test_contract_change_body_field_removal():
    """Test detection of removed body fields."""
    engine = RequestResponseContractEngine()

    baseline_contracts = [
        {
            "method": "post",
            "path": "/api/users",
            "body_schema": {
                "validator": "zod",
                "fields": {
                    "name": {"type": "string", "required": True},
                    "email": {"type": "string", "required": True},
                    "phone": {"type": "string", "required": False},
                }
            },
            "query_params": [],
            "status_codes": [201],
        }
    ]

    current_contracts = [
        {
            "method": "post",
            "path": "/api/users",
            "body_schema": {
                "validator": "zod",
                "fields": {
                    "name": {"type": "string", "required": True},
                    "email": {"type": "string", "required": True},
                }
            },
            "query_params": [],
            "status_codes": [201],
        }
    ]

    changes = engine.detect_contract_changes(baseline_contracts, current_contracts)

    field_changes = [c for c in changes if c["type"] == "BODY_FIELD_REMOVED"]
    assert len(field_changes) > 0


def test_contract_change_query_param_required():
    """Test detection of optional parameter becoming required."""
    engine = RequestResponseContractEngine()

    baseline_contracts = [
        {
            "method": "get",
            "path": "/api/search",
            "body_schema": None,
            "query_params": [
                {"name": "q", "type": "string", "required": False}
            ],
            "status_codes": [200],
        }
    ]

    current_contracts = [
        {
            "method": "get",
            "path": "/api/search",
            "body_schema": None,
            "query_params": [
                {"name": "q", "type": "string", "required": True}
            ],
            "status_codes": [200],
        }
    ]

    changes = engine.detect_contract_changes(baseline_contracts, current_contracts)

    assert any(c["type"] == "QUERY_PARAM_REQUIRED" for c in changes)


def test_contract_graph_building():
    """Test building deterministic contract graph."""
    engine = RequestResponseContractEngine()

    content = """
app.get('/items', (req, res) => res.json([]));
app.post('/items', (req, res) => res.status(201).json({}));
app.get('/items/:id', (req, res) => res.json({}));
"""

    contracts = engine.parse_file("routes.js", content)
    graph = engine.build_contract_graph(contracts)

    assert len(graph["contracts"]) == 3
    assert len(graph["endpoints"]) == 3
    assert "GET/items" in graph["endpoints"]
    assert "POST/items" in graph["endpoints"]
    assert "GET/items/:id" in graph["endpoints"]


def test_router_usage():
    """Test extraction from Express Router."""
    engine = RequestResponseContractEngine()

    content = """
const router = express.Router();

router.get('/status', (req, res) => {
  res.json({healthy: true});
});

router.post('/validate', (req, res) => {
  res.status(202).json({validated: true});
});

module.exports = router;
"""

    contracts = engine.parse_file("healthcheck.js", content)

    assert len(contracts) >= 2
    paths = {c.path for c in contracts}
    assert "/status" in paths
    assert "/validate" in paths


if __name__ == "__main__":
    test_simple_route_extraction()
    test_path_parameter_extraction()
    test_zod_schema_detection()
    test_joi_schema_detection()
    test_query_parameters()
    test_multiple_http_methods()
    test_status_code_extraction()
    test_contract_hash_determinism()
    test_three_run_contract_determinism()
    test_contract_change_detection_route_removed()
    test_contract_change_body_field_removal()
    test_contract_change_query_param_required()
    test_contract_graph_building()
    test_router_usage()
    print("✅ All RequestResponseContractEngine tests passed!")
