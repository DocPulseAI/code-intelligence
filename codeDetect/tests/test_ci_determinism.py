from tests.ci_helpers import canonical_json_bytes, materialize_git_fixture, run_analysis, sha256_bytes


def test_ci_determinism_hash_three_runs(tmp_path):
    repo = materialize_git_fixture(tmp_path, "nested_router")
    digests = []
    for _ in range(3):
        report = run_analysis(repo)
        digests.append(sha256_bytes(canonical_json_bytes(report)))
    assert digests[0] == digests[1] == digests[2]
