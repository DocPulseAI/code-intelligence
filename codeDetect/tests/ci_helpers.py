import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


def canonical_json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def materialize_git_fixture(tmp_path: Path, fixture_name: str) -> Path:
    src = ROOT / "tests" / "fixtures" / fixture_name
    dst = tmp_path / fixture_name
    shutil.copytree(src, dst)

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Deterministic Bot"
    env["GIT_AUTHOR_EMAIL"] = "deterministic@example.com"
    env["GIT_COMMITTER_NAME"] = "Deterministic Bot"
    env["GIT_COMMITTER_EMAIL"] = "deterministic@example.com"
    env["GIT_AUTHOR_DATE"] = "2024-01-01T00:00:00+0000"
    env["GIT_COMMITTER_DATE"] = "2024-01-01T00:00:00+0000"

    subprocess.run(["git", "init"], cwd=dst, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "deterministic@example.com"], cwd=dst, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "Deterministic Bot"], cwd=dst, check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=dst, check=True, env=env)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=dst, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return dst


def run_analysis(repo_path: Path) -> dict:
    subprocess.run(
        [sys.executable, str(MAIN), str(repo_path), "main", "--new-user"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    report_path = ROOT / "impact_report.json"
    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)
