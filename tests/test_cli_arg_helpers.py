from main import _looks_like_github_token


def test_detects_supported_github_token_prefixes():
    assert _looks_like_github_token("ghp_example")
    assert _looks_like_github_token("github_pat_example")
    assert _looks_like_github_token("gho_example")
    assert _looks_like_github_token("ghu_example")
    assert _looks_like_github_token("ghs_example")
    assert _looks_like_github_token("ghr_example")


def test_does_not_treat_branch_as_token():
    assert not _looks_like_github_token("main")
    assert not _looks_like_github_token("master")
    assert not _looks_like_github_token("feature/docpulse")
