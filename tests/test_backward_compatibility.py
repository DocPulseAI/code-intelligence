import json
import subprocess
import sys
from pathlib import Path


def test_schema_and_endpoint_backward_compatibility(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name":"demo","dependencies":{"express":"^4.0.0"}}', encoding="utf-8")
    (repo / "server.js").write_text(
        """const express = require('express');
const app = express();
const router = express.Router();
app.use('/api', router);
router.get('/users/:id', auth, handler);
module.exports = app;
""",
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "main.py"), str(repo), "main", "--new-user"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    report = json.loads((root / "impact_report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "epic1-impact/v3"

    legacy_required = {
        "operation_id",
        "method",
        "path",
        "normalized_key",
        "summary",
        "description",
        "tags",
        "auth",
        "request",
        "responses",
        "example",
        "source",
        "confidence",
        "warnings",
    }
    eps = report["report"]["api_contract"]["endpoints"]
    assert eps
    assert legacy_required.issubset(set(eps[0].keys()))
    assert "endpoint_hash" in eps[0]

    search_index = report["report"].get("search_index")
    assert isinstance(search_index, dict)
    assert set(search_index.keys()) == {"symbols", "references", "apis", "modules"}
