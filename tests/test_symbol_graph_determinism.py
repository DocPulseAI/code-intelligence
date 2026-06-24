import json

from src.intelligence.symbol_graph_engine import build_symbol_graph


def test_symbol_graph_deterministic_multirun():
    report = {
        "changes": [
            {
                "file": "src/users/service.js",
                "language": "javascript",
                "features": {
                    "functions": ["getUser", "updateUser"],
                    "imports": ["../models/User"],
                },
            },
            {
                "file": "src/users/controller.js",
                "language": "javascript",
                "features": {
                    "functions": ["getUserHandler"],
                    "imports": ["../services/userService"],
                },
            },
        ],
        "database_impact": {"tables_affected": ["User"]},
        "api_contract": {
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/api/users/{id}",
                    "normalized_key": "get /api/users/{id}",
                    "source": {"file": "src/users/routes.js", "line_start": 10},
                }
            ]
        },
    }

    g1 = build_symbol_graph(report)
    g2 = build_symbol_graph(report)
    assert json.dumps(g1, sort_keys=True) == json.dumps(g2, sort_keys=True)
    assert len(g1["nodes"]) > 0
    assert isinstance(g1["edges"], list)
