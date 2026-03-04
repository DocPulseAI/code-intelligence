from pathlib import Path

from tests.ci_helpers import canonical_json_bytes, materialize_git_fixture, run_analysis


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CASES = [
    "express_small",
    "nested_router",
    "large_synthetic",
    "non_express_fastapi",
]


def test_golden_file_snapshots(tmp_path):
    for case in CASES:
        repo = materialize_git_fixture(tmp_path, case)
        report = run_analysis(repo)
        got = canonical_json_bytes(report)
        golden_path = GOLDEN_DIR / f"{case}_impact.json"
        assert golden_path.exists(), f"Missing golden file: {golden_path}"
        expected = golden_path.read_bytes()
        assert got == expected, f"Golden mismatch for {case}"
