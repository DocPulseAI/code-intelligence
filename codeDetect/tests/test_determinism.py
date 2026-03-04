import hashlib
import subprocess
import sys
from pathlib import Path


def test_analysis_is_byte_deterministic_across_three_runs(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name":"demo","dependencies":{"express":"^4.0.0"}}', encoding="utf-8")
    (repo / "server.js").write_text(
        "const express=require('express'); const app=express(); const r=express.Router(); app.use('/api',r); r.get('/items/:id',auth,handler);",
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    root = Path(__file__).resolve().parents[1]
    main_py = root / "main.py"
    report = root / "impact_report.json"

    digests = []
    for _ in range(3):
        subprocess.run([sys.executable, str(main_py), str(repo), "main", "--new-user"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        payload = report.read_bytes()
        digests.append(hashlib.sha256(payload).hexdigest())

    assert digests[0] == digests[1] == digests[2]
