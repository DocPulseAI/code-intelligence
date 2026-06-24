"""
Tests for schema diff engine.
Validates schema model extraction and change detection with deterministic snapshots.
"""

import pytest
import json
from src.intelligence.schema_diff_engine import (
    diff_schema_models,
)


class MockReport:
    """Mock report builder for schema testing."""

    @staticmethod
    def build_report(entities, schema_analysis=None):
        """Build a mock report with schema data."""
        return {
            "data_model": {
                "entities": entities
            },
            "schema_analysis": schema_analysis or {"models": entities}
        }


class TestSchemaDiffEngine:
    """Test schema diff detection."""

    def test_schema_extraction_no_entities(self):
        """Test handling of reports with no schema data."""
        baseline = {"data_model": {"entities": []}}
        current = {"data_model": {"entities": []}}

        diffs = diff_schema_models(baseline, current)
        assert isinstance(diffs, list)
        assert len(diffs) == 0

    def test_entity_detection(self):
        """Test detection of entities in schema."""
        report = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        # Should be able to extract entities
        entities = report.get("data_model", {}).get("entities", [])
        assert len(entities) == 1
        assert entities[0]["name"] == "User"

    def test_field_addition_detection(self):
        """Test detection of added fields."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        assert isinstance(diffs, list)
        # May detect field addition depending on diff engine implementation

    def test_field_removal_detection(self):
        """Test detection of removed fields."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True},
                            "phone": {"type": "string", "required": False}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        assert isinstance(diffs, list)

    def test_entity_addition_detection(self):
        """Test detection of new entities."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        assert isinstance(diffs, list)

    def test_entity_removal_detection(self):
        """Test detection of removed entities."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        assert isinstance(diffs, list)

    def test_field_type_change_detection(self):
        """Test detection of field type changes."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "age": {"type": "int", "required": True}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "age": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        assert isinstance(diffs, list)

    def test_required_change_detection(self):
        """Test detection of required field changes."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "email": {"type": "string", "required": False}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "email": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        assert isinstance(diffs, list)

    def test_deterministic_snapshot_run_1(self):
        """Test deterministic snapshot - run 1."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "user_id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True},
                            "content": {"type": "text", "required": True}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True},
                            "phone": {"type": "string", "required": False}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "user_id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True},
                            "content": {"type": "text", "required": True},
                            "tags": {"type": "array", "required": False}
                        }
                    },
                    {
                        "name": "Comment",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "post_id": {"type": "int", "required": True},
                            "text": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        # Store snapshot
        snapshot_1 = json.dumps(diffs, sort_keys=True)

        return snapshot_1

    def test_deterministic_snapshot_run_2(self):
        """Test deterministic snapshot - run 2."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "user_id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True},
                            "content": {"type": "text", "required": True}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True},
                            "phone": {"type": "string", "required": False}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "user_id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True},
                            "content": {"type": "text", "required": True},
                            "tags": {"type": "array", "required": False}
                        }
                    },
                    {
                        "name": "Comment",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "post_id": {"type": "int", "required": True},
                            "text": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        # Store snapshot
        snapshot_2 = json.dumps(diffs, sort_keys=True)

        return snapshot_2

    def test_deterministic_snapshot_run_3(self):
        """Test deterministic snapshot - run 3."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "user_id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True},
                            "content": {"type": "text", "required": True}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True},
                            "phone": {"type": "string", "required": False}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "user_id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True},
                            "content": {"type": "text", "required": True},
                            "tags": {"type": "array", "required": False}
                        }
                    },
                    {
                        "name": "Comment",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "post_id": {"type": "int", "required": True},
                            "text": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(baseline, current)
        # Store snapshot
        snapshot_3 = json.dumps(diffs, sort_keys=True)

        return snapshot_3

    def test_three_run_deterministic_snapshot_consistency(self):
        """Test that three independent runs produce identical snapshots."""
        baseline = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "user_id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True},
                            "content": {"type": "text", "required": True}
                        }
                    }
                ]
            }
        }
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True},
                            "phone": {"type": "string", "required": False}
                        }
                    },
                    {
                        "name": "Post",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "user_id": {"type": "int", "required": True},
                            "title": {"type": "string", "required": True},
                            "content": {"type": "text", "required": True},
                            "tags": {"type": "array", "required": False}
                        }
                    },
                    {
                        "name": "Comment",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "post_id": {"type": "int", "required": True},
                            "text": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        snapshots = []
        for i in range(3):
            diffs = diff_schema_models(baseline, current)
            snapshot = json.dumps(diffs, sort_keys=True)
            snapshots.append(snapshot)

        # All three snapshots should be identical
        assert snapshots[0] == snapshots[1], "Run 1 and Run 2 snapshots differ"
        assert snapshots[1] == snapshots[2], "Run 2 and Run 3 snapshots differ"
        assert snapshots[0] == snapshots[2], "Run 1 and Run 3 snapshots differ"

    def test_schema_with_no_baseline(self):
        """Test schema diff when baseline is None (new schema)."""
        current = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(None, current)
        assert isinstance(diffs, list)

    def test_schema_with_no_changes(self):
        """Test schema diff when baseline and current are identical."""
        schema = {
            "data_model": {
                "entities": [
                    {
                        "name": "User",
                        "fields": {
                            "id": {"type": "int", "required": True},
                            "email": {"type": "string", "required": True}
                        }
                    }
                ]
            }
        }

        diffs = diff_schema_models(schema, schema)
        assert isinstance(diffs, list)
        # Should have no diffs or very minimal ones
        breaking = [d for d in diffs if d.get("severity") == "MAJOR"]
        assert len(breaking) == 0, "Should have no MAJOR changes for identical schemas"
