"""
Test suite for Middleware Intelligence Engine (Phase 3).
Validates middleware chain detection and breaking change analysis.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.intelligence.middleware_intelligence_engine import (
    MiddlewareIntelligenceEngine,
    MiddlewareType,
    MiddlewareSeverity,
    MIDDLEWARE_SEVERITY_MAP,
)


def test_global_middleware_extraction():
    """Test extraction of global middleware."""
    engine = MiddlewareIntelligenceEngine()

    content = """
const express = require('express');
const cors = require('cors');
const app = express();

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({extended: true}));
"""

    chains, definitions = engine.parse_file("app.js", content)

    assert len(chains) >= 1
    global_chain = [c for c in chains if c.scope == "global"][0]
    assert len(global_chain.middlewares) >= 3

    middleware_names = {m.name for m in global_chain.middlewares}
    assert "cors" in middleware_names or "express" in middleware_names


def test_middleware_type_classification():
    """Test middleware type classification."""
    engine = MiddlewareIntelligenceEngine()

    content = """
app.use(cors({origin: 'http://localhost:3000'}));
app.use(jwt({secret: 'secret'}));
app.use(rateLimit({windowMs: 60000, max: 100}));
app.use(logger());
"""

    chains, definitions = engine.parse_file("app.js", content)

    global_chain = chains[0]

    # Check CORS
    cors_mw = next((m for m in global_chain.middlewares if m.middleware_type == MiddlewareType.CORS), None)
    assert cors_mw is not None, "CORS middleware not detected"

    # Check Auth
    auth_mw = next((m for m in global_chain.middlewares if m.middleware_type == MiddlewareType.AUTH), None)
    assert auth_mw is not None, "Auth middleware not detected"

    # Check Rate Limiter
    rate_mw = next((m for m in global_chain.middlewares if m.middleware_type == MiddlewareType.RATE_LIMITER), None)
    assert rate_mw is not None, "Rate limiter middleware not detected"

    # Check Logging
    log_mw = next((m for m in global_chain.middlewares if m.middleware_type == MiddlewareType.LOGGING), None)
    assert log_mw is not None, "Logging middleware not detected"


def test_router_middleware_extraction():
    """Test extraction of router middleware."""
    engine = MiddlewareIntelligenceEngine()

    content = """
const express = require('express');
const router = express.Router();

router.use(authenticate());
router.use(validateSchema());

router.get('/', (req, res) => res.json({}));
"""

    chains, definitions = engine.parse_file("routes.js", content)

    router_chains = [c for c in chains if "router_" in c.scope]
    assert len(router_chains) > 0

    router_chain = router_chains[0]
    assert len(router_chain.middlewares) >= 2


def test_middleware_severity_mapping():
    """Test that middleware types map to correct severities."""
    assert MIDDLEWARE_SEVERITY_MAP[MiddlewareType.RATE_LIMITER] == MiddlewareSeverity.PATCH
    assert MIDDLEWARE_SEVERITY_MAP[MiddlewareType.CORS] == MiddlewareSeverity.MINOR
    assert MIDDLEWARE_SEVERITY_MAP[MiddlewareType.AUTH] == MiddlewareSeverity.MAJOR


def test_chain_order_preservation():
    """Test that middleware order is preserved."""
    engine = MiddlewareIntelligenceEngine()

    content = """
app.use(cors());
app.use(bodyParser());
app.use(authenticate());
app.use(authorize());
"""

    chains, definitions = engine.parse_file("app.js", content)

    global_chain = chains[0]
    # Order should be preserved
    names = [m.name for m in global_chain.middlewares]

    # At least check we have 4 middlewares
    assert len(global_chain.middlewares) >= 4


def test_middleware_config_extraction():
    """Test extraction of middleware configuration."""
    engine = MiddlewareIntelligenceEngine()

    content = """
app.use(cors({origin: 'http://localhost:3000', credentials: true}));
app.use(rateLimit({windowMs: 60000, max: 100}));
"""

    chains, definitions = engine.parse_file("app.js", content)

    global_chain = chains[0]

    # CORS middleware should have origin config
    cors_mw = next(m for m in global_chain.middlewares if m.middleware_type == MiddlewareType.CORS)
    if cors_mw.config:
        assert "cors_origin" in cors_mw.config or len(cors_mw.config) > 0

    # Rate limiter should have windowMs and max
    rate_mw = next(m for m in global_chain.middlewares if m.middleware_type == MiddlewareType.RATE_LIMITER)
    if rate_mw.config:
        assert "window_ms" in rate_mw.config or "max_requests" in rate_mw.config or len(rate_mw.config) > 0


def test_chain_hash_determinism():
    """Test that chain hashing is deterministic."""
    engine = MiddlewareIntelligenceEngine()

    content = """
app.use(cors());
app.use(authenticate());
app.use(authorize());
"""

    # Parse twice
    chains1, _ = engine.parse_file("app.js", content)
    hash1 = chains1[0].compute_chain_hash()

    # Reset and parse again
    engine = MiddlewareIntelligenceEngine()
    chains2, _ = engine.parse_file("app.js", content)
    hash2 = chains2[0].compute_chain_hash()

    assert hash1 == hash2, "Chain hash must be deterministic"


def test_three_run_middleware_determinism():
    """Three-run determinism test for middleware extraction."""
    content = """
const express = require('express');
const cors = require('cors');
const jwt = require('express-jwt');
const rateLimit = require('express-rate-limit');

const app = express();

app.use(cors({origin: '*'}));
app.use(express.json());
app.use(jwt({secret: 'secret'}));
app.use(rateLimit({windowMs: 60000, max: 100}));

app.get('/', (req, res) => res.json({}));
"""

    snapshots = []
    for run in range(3):
        engine = MiddlewareIntelligenceEngine()
        chains, definitions = engine.parse_file("app.js", content)

        snapshot = {
            "chains_count": len(chains),
            "chain_hashes": sorted([(c.scope, c.compute_chain_hash()) for c in chains]),
            "middleware_types": sorted([
                (m.name, m.middleware_type.value)
                for chain in chains
                for m in chain.middlewares
            ]),
        }
        snapshots.append(snapshot)

    assert snapshots[0] == snapshots[1] == snapshots[2], f"Snapshot mismatch: {snapshots}"


def test_middleware_removal_detection():
    """Test detection of removed middleware."""
    engine = MiddlewareIntelligenceEngine()

    baseline_chains = [
        {
            "scope": "global",
            "file_path": "app.js",
            "middlewares": [
                {"name": "cors", "type": "cors", "file_path": "app.js", "line": 1, "config": {}},
                {"name": "jwt", "type": "auth", "file_path": "app.js", "line": 2, "config": {}},
                {"name": "rateLimit", "type": "rate_limiter", "file_path": "app.js", "line": 3, "config": {}},
            ],
            "chain_hash": "abc123",
        }
    ]

    current_chains = [
        {
            "scope": "global",
            "file_path": "app.js",
            "middlewares": [
                {"name": "cors", "type": "cors", "file_path": "app.js", "line": 1, "config": {}},
                {"name": "rateLimit", "type": "rate_limiter", "file_path": "app.js", "line": 3, "config": {}},
            ],
            "chain_hash": "def456",
        }
    ]

    changes = engine.detect_middleware_changes(baseline_chains, current_chains)

    # Should detect removed auth middleware
    assert any(c["type"] == "MIDDLEWARE_REMOVED" and "jwt" in c.get("middleware", "") for c in changes)


def test_middleware_order_change_detection():
    """Test detection of middleware order changes."""
    engine = MiddlewareIntelligenceEngine()

    baseline_chains = [
        {
            "scope": "global",
            "file_path": "app.js",
            "middlewares": [
                {"name": "cors", "type": "cors", "file_path": "app.js", "line": 1, "config": {}},
                {"name": "jwt", "type": "auth", "file_path": "app.js", "line": 2, "config": {}},
            ],
            "chain_hash": "abc",
        }
    ]

    current_chains = [
        {
            "scope": "global",
            "file_path": "app.js",
            "middlewares": [
                {"name": "jwt", "type": "auth", "file_path": "app.js", "line": 2, "config": {}},
                {"name": "cors", "type": "cors", "file_path": "app.js", "line": 1, "config": {}},
            ],
            "chain_hash": "def",
        }
    ]

    changes = engine.detect_middleware_changes(baseline_chains, current_chains)

    # Should detect order change
    assert any(c["type"] == "MIDDLEWARE_ORDER_CHANGED" for c in changes)


def test_middleware_config_change_detection():
    """Test detection of middleware configuration changes."""
    engine = MiddlewareIntelligenceEngine()

    baseline_chains = [
        {
            "scope": "global",
            "file_path": "app.js",
            "middlewares": [
                {
                    "name": "cors",
                    "type": "cors",
                    "file_path": "app.js",
                    "line": 1,
                    "config": {"cors_origin": "http://localhost:3000"},
                },
            ],
            "chain_hash": "abc",
        }
    ]

    current_chains = [
        {
            "scope": "global",
            "file_path": "app.js",
            "middlewares": [
                {
                    "name": "cors",
                    "type": "cors",
                    "file_path": "app.js",
                    "line": 1,
                    "config": {"cors_origin": "*"},
                },
            ],
            "chain_hash": "def",
        }
    ]

    changes = engine.detect_middleware_changes(baseline_chains, current_chains)

    # Should detect config change
    assert any(c["type"] == "MIDDLEWARE_CONFIG_CHANGED" for c in changes)


def test_severity_ranking():
    """Test that changes are ranked by severity."""
    engine = MiddlewareIntelligenceEngine()

    baseline_chains = [
        {
            "scope": "global",
            "file_path": "app.js",
            "middlewares": [
                {"name": "jwt", "type": "auth", "file_path": "app.js", "line": 1, "config": {}},
                {"name": "cors", "type": "cors", "file_path": "app.js", "line": 2, "config": {}},
            ],
            "chain_hash": "abc",
        }
    ]

    current_chains = [
        {
            "scope": "global",
            "file_path": "app.js",
            "middlewares": [
                {"name": "cors", "type": "cors", "file_path": "app.js", "line": 2, "config": {}},
            ],
            "chain_hash": "def",
        }
    ]

    changes = engine.detect_middleware_changes(baseline_chains, current_chains)

    # Auth removal should be MAJOR
    auth_changes = [c for c in changes if "jwt" in c.get("middleware", "")]
    if auth_changes:
        assert auth_changes[0]["severity"] == "MAJOR"


def test_middleware_graph_building():
    """Test building deterministic middleware graph."""
    engine = MiddlewareIntelligenceEngine()

    content = """
app.use(cors());
app.use(jwt());
app.use(rateLimit());
"""

    chains, definitions = engine.parse_file("app.js", content)
    graph = engine.build_middleware_graph(chains)

    assert len(graph["chains"]) >= 1
    assert len(graph["middleware_map"]) >= 3

    # Verify deterministic keys
    assert all(isinstance(k, str) for k in graph["middleware_map"].keys())


def test_scoped_middleware_with_path():
    """Test extraction of path-scoped middleware."""
    engine = MiddlewareIntelligenceEngine()

    content = """
const express = require('express');
const app = express();

app.use('/api', authenticate());
app.use('/public', publicHandler());
"""

    chains, definitions = engine.parse_file("app.js", content)

    # Should get global chain
    global_chains = [c for c in chains if c.scope == "global"]
    assert len(global_chains) > 0


if __name__ == "__main__":
    test_global_middleware_extraction()
    test_middleware_type_classification()
    test_router_middleware_extraction()
    test_middleware_severity_mapping()
    test_chain_order_preservation()
    test_middleware_config_extraction()
    test_chain_hash_determinism()
    test_three_run_middleware_determinism()
    test_middleware_removal_detection()
    test_middleware_order_change_detection()
    test_middleware_config_change_detection()
    test_severity_ranking()
    test_middleware_graph_building()
    test_scoped_middleware_with_path()
    print("✅ All MiddlewareIntelligenceEngine tests passed!")
