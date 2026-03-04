from src.intelligence.breaking_change_engine import analyze_semantic_breaking_changes


def test_risk_scoring_ordering_and_severity_mapping():
    baseline = {
        "changes": [
            {"file": "src/a.js", "language": "javascript", "features": {"functions": ["f1", "f2"]}},
        ],
        "database_impact": {"tables_affected": ["A"]},
        "api_contract": {
            "endpoints": [
                {"method": "GET", "path": "/x", "normalized_key": "get /x", "source": {"file": "src/routes.js", "line_start": 1}},
            ]
        },
    }
    current = {
        "changes": [
            {"file": "src/a.js", "language": "javascript", "features": {"functions": []}},
        ],
        "database_impact": {"tables_affected": []},
        "api_contract": {"endpoints": []},
    }

    out = analyze_semantic_breaking_changes(baseline, current)
    findings = out["breaking_changes"]
    assert findings == sorted(findings, key=lambda d: (-float(d.get("risk_score", 0.0)), str(d.get("symbol_id", "")), str(d.get("id", ""))))
    for item in findings:
        score = float(item["risk_score"])
        if score >= 8:
            assert item["severity"] == "MAJOR"
        elif score >= 4:
            assert item["severity"] == "MINOR"
        else:
            assert item["severity"] == "PATCH"
