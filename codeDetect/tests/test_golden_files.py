import json
from pathlib import Path

from tests.ci_helpers import canonical_json_bytes, materialize_git_fixture, run_analysis


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CASES = [
    "express_small",
    "nested_router",
    "large_synthetic",
    "non_express_fastapi",
]


def _normalize_report_for_snapshot(report: dict) -> dict:
    normalized = json.loads(json.dumps(report))
    report_body = normalized.get("report", {})
    if isinstance(report_body, dict):
        report_body.pop("search_index", None)
        report_body.pop("code_intelligence", None)
    return normalized


def test_golden_file_snapshots(tmp_path):
    for case in CASES:
        repo = materialize_git_fixture(tmp_path, case)
        report = run_analysis(repo)
        got = canonical_json_bytes(_normalize_report_for_snapshot(report))
        golden_path = GOLDEN_DIR / f"{case}_impact.json"
        assert golden_path.exists(), f"Missing golden file: {golden_path}"
        expected = canonical_json_bytes(_normalize_report_for_snapshot(json.loads(golden_path.read_text(encoding="utf-8"))))
        assert got == expected, f"Golden mismatch for {case}"

        search_index = report.get("report", {}).get("search_index")
        assert isinstance(search_index, dict), f"Missing search_index for {case}"
        assert set(search_index.keys()) == {"symbols", "references", "apis", "modules"}
