import json
import os
import subprocess
import sys
from pathlib import Path


def test_baseline_required_failure_when_missing(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name":"demo","dependencies":{"express":"^4.0.0"}}', encoding="utf-8")
    (repo / "server.js").write_text("const express=require('express'); const app=express();", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "server.js").write_text("const express=require('express'); const app=express(); app.get('/x',()=>{});", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["CODE_DETECT_BASELINE_DIR"] = str(tmp_path / "baseline_store")
    proc = subprocess.run(
        [sys.executable, str(root / "main.py"), str(repo), "main"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["stage"] == "analysis"
    assert "Baseline commit required for breaking change detection" in payload["details"]


def test_baseline_missing_explicitly_skipped_with_new_user(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name":"demo","dependencies":{"express":"^4.0.0"}}', encoding="utf-8")
    (repo / "server.js").write_text("const express=require('express'); const app=express(); app.get('/x',()=>{});", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["CODE_DETECT_BASELINE_DIR"] = str(tmp_path / "baseline_store")
    proc = subprocess.run(
        [sys.executable, str(root / "main.py"), str(repo), "main", "--new-user=true"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
