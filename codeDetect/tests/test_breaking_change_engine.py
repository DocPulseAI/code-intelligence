from src.intelligence.breaking_change_engine import analyze_semantic_breaking_changes


def test_semantic_breaking_removed_symbol_and_route():
    baseline = {
        "changes": [
            {"file": "src/svc.js", "language": "javascript", "features": {"functions": ["getData"]}},
        ],
        "database_impact": {"tables_affected": ["User"]},
        "api_contract": {
            "endpoints": [
                {"method": "GET", "path": "/api/users/{id}", "normalized_key": "get /api/users/{id}", "source": {"file": "src/routes.js", "line_start": 1}},
            ]
        },
    }
    current = {
        "changes": [
            {"file": "src/svc.js", "language": "javascript", "features": {"functions": []}},
        ],
        "database_impact": {"tables_affected": []},
        "api_contract": {"endpoints": []},
    }

    out = analyze_semantic_breaking_changes(baseline, current)
    findings = out["breaking_changes"]
    types = {f["type"] for f in findings}
    assert "SYMBOL_REMOVED" in types
    assert "ROUTE_REMOVED" in types
    assert "SCHEMA_ENTITY_REMOVED" in types
    assert out["symbol_summary"]["breaking_symbols"] == len(findings)
