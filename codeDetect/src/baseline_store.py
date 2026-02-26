"""
Deterministic baseline persistence for commit-to-commit comparison.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned or "unknown"


class BaselineStore:
    """
    Filesystem-backed baseline store.

    Layout:
      <root>/<project_id>/<branch>/
        index.json
        <commit_sha>.json
    """

    def __init__(self, root_dir: Optional[str] = None):
        if root_dir:
            self.root_dir = root_dir
        else:
            self.root_dir = os.environ.get(
                "CODE_DETECT_BASELINE_DIR",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), ".baseline_store"),
            )
        os.makedirs(self.root_dir, exist_ok=True)

    def _branch_dir(self, project_id: str, branch: str) -> str:
        return os.path.join(self.root_dir, _safe_name(project_id), _safe_name(branch))

    def _index_path(self, project_id: str, branch: str) -> str:
        return os.path.join(self._branch_dir(project_id, branch), "index.json")

    def _commit_path(self, project_id: str, branch: str, commit_sha: str) -> str:
        return os.path.join(self._branch_dir(project_id, branch), f"{_safe_name(commit_sha)}.json")

    def _read_index(self, project_id: str, branch: str) -> list[str]:
        path = self._index_path(project_id, branch)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                return [str(item) for item in raw if str(item).strip()]
        except Exception:
            return []
        return []

    def _write_index(self, project_id: str, branch: str, commits: list[str]) -> None:
        branch_dir = self._branch_dir(project_id, branch)
        os.makedirs(branch_dir, exist_ok=True)
        path = self._index_path(project_id, branch)
        stable_commits = []
        seen = set()
        for commit in commits:
            if commit not in seen:
                seen.add(commit)
                stable_commits.append(commit)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stable_commits, f, ensure_ascii=True, separators=(",", ":"))

    def save_baseline(self, project_id: str, branch: str, commit_sha: str, report: dict) -> None:
        branch_dir = self._branch_dir(project_id, branch)
        os.makedirs(branch_dir, exist_ok=True)
        with open(self._commit_path(project_id, branch, commit_sha), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        commits = self._read_index(project_id, branch)
        commits.append(commit_sha)
        self._write_index(project_id, branch, commits)

    def load_baseline(self, project_id: str, branch: str, current_commit: str) -> Optional[dict]:
        """
        Load previous commit report for the same project/branch.
        Returns None when baseline is unavailable.
        """
        commits = self._read_index(project_id, branch)
        if not commits:
            return None

        previous_commit = None
        if current_commit in commits:
            idx = commits.index(current_commit)
            if idx > 0:
                previous_commit = commits[idx - 1]
        else:
            for candidate in reversed(commits):
                if candidate != current_commit:
                    previous_commit = candidate
                    break

        if not previous_commit:
            return None

        path = self._commit_path(project_id, branch, previous_commit)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
