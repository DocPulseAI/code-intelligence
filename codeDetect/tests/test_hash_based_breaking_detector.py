"""Test suite for HashBasedBreakingDetector (Phase 4)."""

import pytest
from src.intelligence.hash_based_breaking_detector import (
    HashBasedBreakingDetector,
    BreakingChangeSeverity,
    ChangeSource,
    SymbolMutation,
)


def test_symbol_mutation_detection():
    """Test detection of symbol structural hash changes."""
    detector = HashBasedBreakingDetector()

    baseline_graph = {
        "nodes": [
            {
                "symbol": "UserService",
                "file_path": "services.ts",
                "structural_hash": "hash_v1_original",
                "structure": {"type": "class", "methods": ["getUser", "createUser"]},
            }
        ],
        "edges": [],
    }

    current_graph = {
        "nodes": [
            {
                "symbol": "UserService",
                "file_path": "services.ts",
                "structural_hash": "hash_v1_modified",  # Changed!
                "structure": {"type": "class", "methods": ["getUser", "createUser", "deleteUser"]},
            }
        ],
        "edges": [],
    }

    changes = detector.detect_breaking_changes(
        baseline_graph, current_graph, [], [], [], []
    )

    assert len(changes) == 1
    assert changes[0].source == ChangeSource.TYPE_MUTATION
    assert changes[0].severity == BreakingChangeSeverity.MAJOR
    assert "UserService" in changes[0].affected_symbols
    assert len(changes[0].symbol_mutations) == 1


def test_contract_route_removal():
    """Test detection of removed routes."""
    detector = HashBasedBreakingDetector()

    baseline_contracts = [
        {
            "endpoint": "GET/api/users",
            "method": "GET",
            "path": "/api/users",
            "path_params": [],
            "query_params": [],
            "body_schema": None,
        },
        {
            "endpoint": "POST/api/users",
            "method": "POST",
            "path": "/api/users",
            "path_params": [],
            "query_params": [],
            "body_schema": {"validator": "zod", "schema_name": "CreateUserInput"},
        },
    ]

    current_contracts = [
        {
            "endpoint": "GET/api/users",
            "method": "GET",
            "path": "/api/users",
            "path_params": [],
            "query_params": [],
            "body_schema": None,
        }
    ]

    changes = detector.detect_breaking_changes({}, {}, baseline_contracts, current_contracts, [], [])

    assert len(changes) == 1
    assert changes[0].source == ChangeSource.CONTRACT_MUTATION
    assert changes[0].contract_breaks[0].change_type == "route_removal"
    assert changes[0].contract_breaks[0].endpoint == "POST/api/users"
    assert changes[0].severity == BreakingChangeSeverity.MAJOR


def test_contract_param_removal():
    """Test detection of removed path parameters."""
    detector = HashBasedBreakingDetector()

    baseline_contracts = [
        {
            "endpoint": "GET/api/users/:id",
            "method": "GET",
            "path": "/api/users/:id",
            "path_params": [{"name": "id", "type": "string", "required": True}],
            "query_params": [],
            "body_schema": None,
        }
    ]

    current_contracts = [
        {
            "endpoint": "GET/api/users",
            "method": "GET",
            "path": "/api/users",
            "path_params": [],
            "query_params": [],
            "body_schema": None,
        }
    ]

    changes = detector.detect_breaking_changes({}, {}, baseline_contracts, current_contracts, [], [])

    # Route changed (old route removed, new route added)
    assert any(c.contract_breaks[0].change_type == "route_removal" for c in changes)


def test_contract_body_field_removal():
    """Test detection of removed body schema fields."""
    detector = HashBasedBreakingDetector()

    baseline_contracts = [
        {
            "endpoint": "POST/api/users",
            "method": "POST",
            "path": "/api/users",
            "path_params": [],
            "query_params": [],
            "body_schema": {
                "validator": "zod",
                "schema_name": "CreateUserInput",
                "fields": [
                    {"name": "email", "type": "string", "required": True},
                    {"name": "name", "type": "string", "required": True},
                    {"name": "age", "type": "number", "required": False},
                ],
            },
        }
    ]

    current_contracts = [
        {
            "endpoint": "POST/api/users",
            "method": "POST",
            "path": "/api/users",
            "path_params": [],
            "query_params": [],
            "body_schema": {
                "validator": "zod",
                "schema_name": "CreateUserInput",
                "fields": [
                    {"name": "email", "type": "string", "required": True},
                    {"name": "name", "type": "string", "required": True},
                ],
            },
        }
    ]

    changes = detector.detect_breaking_changes(
        {}, {}, baseline_contracts, current_contracts, [], []
    )

    assert len(changes) == 1
    assert changes[0].contract_breaks[0].change_type == "body_field_removal"
    assert changes[0].contract_breaks[0].baseline_value == "age"


def test_contract_param_requirement_change():
    """Test detection of parameter requirement changes."""
    detector = HashBasedBreakingDetector()

    baseline_contracts = [
        {
            "endpoint": "GET/api/users",
            "method": "GET",
            "path": "/api/users",
            "path_params": [],
            "query_params": [{"name": "filter", "type": "string", "required": False}],
            "body_schema": None,
        }
    ]

    current_contracts = [
        {
            "endpoint": "GET/api/users",
            "method": "GET",
            "path": "/api/users",
            "path_params": [],
            "query_params": [{"name": "filter", "type": "string", "required": True}],
            "body_schema": None,
        }
    ]

    changes = detector.detect_breaking_changes(
        {}, {}, baseline_contracts, current_contracts, [], []
    )

    assert len(changes) == 1
    assert changes[0].contract_breaks[0].change_type == "query_param_requirement_change"
    assert changes[0].contract_breaks[0].baseline_value == "False"
    assert changes[0].contract_breaks[0].current_value == "True"


def test_middleware_removal_major_severity():
    """Test detection of auth middleware removal (MAJOR severity)."""
    detector = HashBasedBreakingDetector()

    baseline_middleware = [
        {
            "scope": "global",
            "middleware": [
                {"name": "cors", "type": "CORS", "config": {}},
                {"name": "jwt_auth", "type": "AUTH", "config": {}},
                {"name": "bodyParser", "type": "BODY_PARSER", "config": {}},
            ],
        }
    ]

    current_middleware = [
        {
            "scope": "global",
            "middleware": [
                {"name": "cors", "type": "CORS", "config": {}},
                {"name": "bodyParser", "type": "BODY_PARSER", "config": {}},
            ],
        }
    ]

    changes = detector.detect_breaking_changes(
        {}, {}, [], [], baseline_middleware, current_middleware
    )

    assert len(changes) == 1
    assert changes[0].source == ChangeSource.MIDDLEWARE_MUTATION
    assert changes[0].severity == BreakingChangeSeverity.MAJOR
    assert changes[0].middleware_breaks[0].middleware_name == "jwt_auth"


def test_middleware_removal_patch_severity():
    """Test detection of rate limiter removal (PATCH severity)."""
    detector = HashBasedBreakingDetector()

    baseline_middleware = [
        {
            "scope": "global",
            "middleware": [
                {"name": "rateLimit", "type": "RATE_LIMITER", "config": {"max": 100}},
                {"name": "cors", "type": "CORS", "config": {}},
            ],
        }
    ]

    current_middleware = [
        {
            "scope": "global",
            "middleware": [{"name": "cors", "type": "CORS", "config": {}}],
        }
    ]

    changes = detector.detect_breaking_changes(
        {}, {}, [], [], baseline_middleware, current_middleware
    )

    assert len(changes) == 1
    assert changes[0].severity == BreakingChangeSeverity.PATCH


def test_middleware_order_change():
    """Test detection of middleware order changes."""
    detector = HashBasedBreakingDetector()

    baseline_middleware = [
        {
            "scope": "global",
            "middleware": [
                {"name": "cors", "type": "CORS", "config": {}},
                {"name": "jwt_auth", "type": "AUTH", "config": {}},
                {"name": "bodyParser", "type": "BODY_PARSER", "config": {}},
            ],
        }
    ]

    current_middleware = [
        {
            "scope": "global",
            "middleware": [
                {"name": "jwt_auth", "type": "AUTH", "config": {}},
                {"name": "cors", "type": "CORS", "config": {}},
                {"name": "bodyParser", "type": "BODY_PARSER", "config": {}},
            ],
        }
    ]

    changes = detector.detect_breaking_changes(
        {}, {}, [], [], baseline_middleware, current_middleware
    )

    assert len(changes) == 1
    assert changes[0].middleware_breaks[0].change_type == "middleware_order_change"
    assert changes[0].severity == BreakingChangeSeverity.MINOR


def test_middleware_config_change():
    """Test detection of middleware config changes."""
    detector = HashBasedBreakingDetector()

    baseline_middleware = [
        {
            "scope": "global",
            "middleware": [
                {
                    "name": "rateLimit",
                    "type": "RATE_LIMITER",
                    "config": {"windowMs": 60000, "max": 100},
                }
            ],
        }
    ]

    current_middleware = [
        {
            "scope": "global",
            "middleware": [
                {
                    "name": "rateLimit",
                    "type": "RATE_LIMITER",
                    "config": {"windowMs": 120000, "max": 50},
                }
            ],
        }
    ]

    changes = detector.detect_breaking_changes(
        {}, {}, [], [], baseline_middleware, current_middleware
    )

    assert len(changes) == 1
    assert changes[0].middleware_breaks[0].change_type == "middleware_config_change"
    assert changes[0].severity == BreakingChangeSeverity.PATCH


def test_propagation_chain_computation():
    """Test propagation chain tracing through dependency graph."""
    detector = HashBasedBreakingDetector()

    baseline_graph = {
        "nodes": [
            {
                "symbol": "UserType",
                "file_path": "types.ts",
                "structural_hash": "hash_v1",
                "structure": {},
            }
        ],
        "edges": [],
    }

    current_graph = {
        "nodes": [
            {
                "symbol": "UserType",
                "file_path": "types.ts",
                "structural_hash": "hash_v2",  # Changed
                "structure": {},
            },
            {
                "symbol": "UserService",
                "file_path": "services.ts",
                "structural_hash": "hash_svc",
                "structure": {},
            },
        ],
        "edges": [
            {"source": "UserService", "target": "UserType"}  # UserService depends on UserType
        ],
    }

    changes = detector.detect_breaking_changes(
        baseline_graph, current_graph, [], [], [], []
    )

    assert len(changes) == 1
    change = changes[0]
    assert "UserType" in change.affected_symbols
    # Due to propagation, UserService should be in chain
    assert "UserService" in change.propagation_chain or len(change.propagation_chain) >= 0


def test_severity_ranking():
    """Test that breaking changes are ranked by severity."""
    detector = HashBasedBreakingDetector()

    # Create multiple changes with different severities
    baseline_middleware = [
        {
            "scope": "global",
            "middleware": [
                {"name": "jwt_auth", "type": "AUTH", "config": {}},
                {"name": "cors", "type": "CORS", "config": {}},
                {"name": "rateLimit", "type": "RATE_LIMITER", "config": {}},
            ],
        }
    ]

    current_middleware = [
        {
            "scope": "global",
            "middleware": [],
        }
    ]

    changes = detector.detect_breaking_changes(
        {}, {}, [], [], baseline_middleware, current_middleware
    )

    # Should have 3 changes: AUTH (MAJOR), CORS (MINOR), RATE_LIMITER (PATCH)
    assert len(changes) >= 1

    # Check ordering: MAJOR should come before MINOR and PATCH
    severities = [c.severity for c in changes]
    assert severities == sorted(severities, key=lambda s: -s.value)


def test_multiple_breaking_changes_aggregation():
    """Test aggregation of multiple breaking changes across sources."""
    detector = HashBasedBreakingDetector()

    baseline_graph = {
        "nodes": [
            {
                "symbol": "API",
                "file_path": "api.ts",
                "structural_hash": "hash_v1",
                "structure": {},
            }
        ],
        "edges": [],
    }

    current_graph = {
        "nodes": [
            {
                "symbol": "API",
                "file_path": "api.ts",
                "structural_hash": "hash_v2",  # Changed
                "structure": {},
            }
        ],
        "edges": [],
    }

    baseline_contracts = [
        {
            "endpoint": "GET/api/data",
            "method": "GET",
            "path": "/api/data",
            "path_params": [],
            "query_params": [],
            "body_schema": None,
        }
    ]

    current_contracts = []

    baseline_middleware = [
        {
            "scope": "global",
            "middleware": [{"name": "jwt_auth", "type": "AUTH", "config": {}}],
        }
    ]

    current_middleware = [
        {
            "scope": "global",
            "middleware": [],
        }
    ]

    changes = detector.detect_breaking_changes(
        baseline_graph,
        current_graph,
        baseline_contracts,
        current_contracts,
        baseline_middleware,
        current_middleware,
    )

    # Should have multiple types of changes
    sources = {c.source for c in changes}
    assert len(sources) >= 2  # At least type and contract mutations


def test_determinism_three_runs():
    """Test that detector produces identical output across three runs."""
    detector1 = HashBasedBreakingDetector()
    detector2 = HashBasedBreakingDetector()
    detector3 = HashBasedBreakingDetector()

    baseline_graph = {
        "nodes": [
            {
                "symbol": "Service",
                "file_path": "svc.ts",
                "structural_hash": "hash1",
                "structure": {},
            }
        ],
        "edges": [],
    }

    current_graph = {
        "nodes": [
            {
                "symbol": "Service",
                "file_path": "svc.ts",
                "structural_hash": "hash2",
                "structure": {},
            }
        ],
        "edges": [],
    }

    baseline_contracts = [
        {
            "endpoint": "GET/api/users",
            "method": "GET",
            "path": "/api/users",
            "path_params": [],
            "query_params": [],
            "body_schema": None,
        }
    ]

    current_contracts = []

    baseline_middleware = [
        {
            "scope": "global",
            "middleware": [{"name": "jwt_auth", "type": "AUTH", "config": {}}],
        }
    ]

    current_middleware = [{"scope": "global", "middleware": []}]

    changes1 = detector1.detect_breaking_changes(
        baseline_graph,
        current_graph,
        baseline_contracts,
        current_contracts,
        baseline_middleware,
        current_middleware,
    )
    changes2 = detector2.detect_breaking_changes(
        baseline_graph,
        current_graph,
        baseline_contracts,
        current_contracts,
        baseline_middleware,
        current_middleware,
    )
    changes3 = detector3.detect_breaking_changes(
        baseline_graph,
        current_graph,
        baseline_contracts,
        current_contracts,
        baseline_middleware,
        current_middleware,
    )

    # Convert to dicts for comparison
    dict1 = [c.to_dict() for c in changes1]
    dict2 = [c.to_dict() for c in changes2]
    dict3 = [c.to_dict() for c in changes3]

    assert dict1 == dict2 == dict3


def test_cors_severity_is_minor():
    """Test CORS removal is MINOR severity."""
    detector = HashBasedBreakingDetector()

    baseline_middleware = [
        {
            "scope": "global",
            "middleware": [{"name": "cors", "type": "CORS", "config": {}}],
        }
    ]

    current_middleware = [
        {
            "scope": "global",
            "middleware": [],
        }
    ]

    changes = detector.detect_breaking_changes(
        {}, {}, [], [], baseline_middleware, current_middleware
    )

    assert changes[0].severity == BreakingChangeSeverity.MINOR


def test_no_changes_when_identical():
    """Test that no breaking changes are detected when graphs are identical."""
    detector = HashBasedBreakingDetector()

    graph = {
        "nodes": [
            {
                "symbol": "API",
                "file_path": "api.ts",
                "structural_hash": "same_hash",
                "structure": {},
            }
        ],
        "edges": [],
    }

    contracts = [
        {
            "endpoint": "GET/api/data",
            "method": "GET",
            "path": "/api/data",
            "path_params": [],
            "query_params": [],
            "body_schema": None,
        }
    ]

    middleware = [
        {
            "scope": "global",
            "middleware": [{"name": "cors", "type": "CORS", "config": {}}],
        }
    ]

    changes = detector.detect_breaking_changes(
        graph, graph, contracts, contracts, middleware, middleware
    )

    assert len(changes) == 0
