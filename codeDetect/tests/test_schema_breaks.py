from src.intelligence.breaking_change_engine import analyze_semantic_breaking_changes


def test_schema_removed_entity_is_breaking():
    baseline = {
        "changes": [],
        "database_impact": {"tables_affected": ["Project", "User"]},
        "api_contract": {"endpoints": []},
    }
    current = {
        "changes": [],
        "database_impact": {"tables_affected": ["User"]},
        "api_contract": {"endpoints": []},
    }

    out = analyze_semantic_breaking_changes(baseline, current)
    schema_changes = [x for x in out["breaking_changes"] if x["type"] == "SCHEMA_ENTITY_REMOVED"]
    assert len(schema_changes) == 1
    assert schema_changes[0]["entity"] == "Project"
    assert schema_changes[0]["severity"] in {"MAJOR", "MINOR", "PATCH"}
