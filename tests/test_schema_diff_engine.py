from src.intelligence.schema_diff_engine import diff_schema_models


def test_schema_field_removed_and_type_changed():
    baseline = {
        "data_model": {
            "entities": [
                {
                    "name": "User",
                    "fields": [
                        {"name": "email", "type": "string", "required": True},
                        {"name": "age", "type": "number", "required": False},
                    ],
                }
            ]
        }
    }
    current = {
        "data_model": {
            "entities": [
                {
                    "name": "User",
                    "fields": [
                        {"name": "email", "type": "number", "required": True},
                    ],
                }
            ]
        }
    }
    findings = diff_schema_models(baseline, current)
    changes = {(f["change"], f["field"]) for f in findings}
    assert ("FIELD_REMOVED", "age") in changes
    assert ("TYPE_CHANGED", "email") in changes


def test_schema_required_add_without_default():
    baseline = {"data_model": {"entities": [{"name": "User", "fields": []}]}}
    current = {
        "data_model": {
            "entities": [
                {"name": "User", "fields": [{"name": "username", "type": "string", "required": True}]}
            ]
        }
    }
    findings = diff_schema_models(baseline, current)
    assert any(f["change"] == "REQUIRED_FIELD_ADDED_NO_DEFAULT" for f in findings)
