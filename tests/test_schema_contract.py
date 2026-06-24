import json
from pathlib import Path

from jsonschema import validate

from tests.ci_helpers import materialize_git_fixture, run_analysis


def test_schema_contract_validation(tmp_path):
    repo = materialize_git_fixture(tmp_path, "express_small")
    report = run_analysis(repo)

    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "impact_report.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    validate(instance=report, schema=schema)
