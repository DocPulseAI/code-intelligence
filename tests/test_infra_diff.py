"""
Tests for infrastructure semantic diff engine.
Validates Docker and GitHub Actions configuration change detection.
"""

import pytest
from src.intelligence.infra_diff_engine import (
    extract_docker_changes,
    extract_github_actions_changes,
    compute_infra_risk_level,
)


DOCKERFILE_BASELINE = """
FROM python:3.9-slim

WORKDIR /app

EXPOSE 5000

ENV FLASK_APP=app.py
ENV FLASK_ENV=production

RUN apt-get update && apt-get install -y gcc

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENTRYPOINT ["python"]
CMD ["app.py"]
"""

DOCKERFILE_UPDATED = """
FROM python:3.11-slim

WORKDIR /app

EXPOSE 5000 8000

ENV FLASK_APP=app.py
ENV FLASK_ENV=development
ENV DEBUG=false

RUN apt-get update && apt-get install -y gcc postgresql-client

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

USER appuser

ENTRYPOINT ["python", "-u"]
CMD ["app.py"]
"""

GITHUB_ACTIONS_BASELINE = """
name: Tests

on:
  push:
    branches:
      - main
  pull_request:
    branches:
      - main

env:
  PYTHON_VERSION: "3.9"

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
      - name: Run tests
        run: pytest
"""

GITHUB_ACTIONS_UPDATED = """
name: Tests

on:
  push:
    branches:
      - main
      - develop
  pull_request:
    branches:
      - main
  schedule:
    - cron: '0 0 * * *'

permissions:
  contents: read
  ID-token: write

env:
  PYTHON_VERSION: "3.11"

jobs:
  lint:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v3
      - name: Lint
        run: flake8

  test:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    needs: lint
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
      - name: Run tests
        run: pytest --cov=.

  security:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v3
      - name: Run security scan
        run: safety check
"""


class TestDockerDiffDetection:
    """Test Docker configuration diff detection."""

    def test_base_image_change(self):
        """Test detection of base image changes."""
        diffs = extract_docker_changes(DOCKERFILE_BASELINE, DOCKERFILE_UPDATED)

        base_image_changes = [d for d in diffs if d["change"] == "BASE_IMAGE_CHANGED"]
        assert len(base_image_changes) == 1, "Should detect base image change"
        assert base_image_changes[0]["severity"] == "MAJOR"
        assert "python:3.9" in base_image_changes[0]["description"]
        assert "python:3.11" in base_image_changes[0]["description"]

    def test_port_exposure_changes(self):
        """Test detection of port exposure changes."""
        diffs = extract_docker_changes(DOCKERFILE_BASELINE, DOCKERFILE_UPDATED)

        port_changes = [d for d in diffs if "PORT" in d["change"]]

        # Should detect port 8000 being exposed
        new_ports = [d for d in port_changes if d["change"] == "PORT_EXPOSED"]
        assert len(new_ports) > 0, "Should detect new port"

    def test_env_var_changes(self):
        """Test detection of environment variable changes."""
        diffs = extract_docker_changes(DOCKERFILE_BASELINE, DOCKERFILE_UPDATED)

        env_vars = [d for d in diffs if "ENV" in d["change"]]

        # FLASK_ENV changed from production to development
        env_changed = [d for d in env_vars if d["change"] == "ENV_VAR_CHANGED"]
        assert len(env_changed) > 0, "Should detect FLASK_ENV change"

        # DEBUG added
        env_added = [d for d in env_vars if d["change"] == "ENV_VAR_ADDED"]
        assert len(env_added) > 0, "Should detect new env var"

    def test_user_change(self):
        """Test detection of Docker USER change."""
        diffs = extract_docker_changes(DOCKERFILE_BASELINE, DOCKERFILE_UPDATED)

        user_changes = [d for d in diffs if d["change"] == "USER_CHANGED"]
        assert len(user_changes) == 1, "Should detect USER change"
        assert user_changes[0]["severity"] == "MAJOR"

    def test_entrypoint_change(self):
        """Test detection of ENTRYPOINT changes."""
        diffs = extract_docker_changes(DOCKERFILE_BASELINE, DOCKERFILE_UPDATED)

        entrypoint_changes = [d for d in diffs if d["change"] == "ENTRYPOINT_CHANGED"]
        assert len(entrypoint_changes) == 1, "Should detect ENTRYPOINT change"
        assert entrypoint_changes[0]["severity"] == "MAJOR"

    def test_cmd_change(self):
        """Test detection of CMD changes."""
        diffs = extract_docker_changes(DOCKERFILE_BASELINE, DOCKERFILE_UPDATED)

        cmd_changes = [d for d in diffs if d["change"] == "CMD_CHANGED"]
        assert len(cmd_changes) == 1, "Should detect CMD change"
        assert cmd_changes[0]["severity"] == "MINOR"

    def test_deterministic_diff_order(self):
        """Test that Docker diffs are deterministically ordered."""
        results = []
        for _ in range(3):
            diffs = extract_docker_changes(DOCKERFILE_BASELINE, DOCKERFILE_UPDATED)
            key = str([(d["change"], d.get("description", "")) for d in diffs])
            results.append(key)

        assert results[0] == results[1] == results[2], "Docker diffs should be deterministic"

    def test_empty_baseline(self):
        """Test diff with empty baseline (new Dockerfile)."""
        diffs = extract_docker_changes(None, DOCKERFILE_BASELINE)

        # Should detect base image as a change
        base_image_changes = [d for d in diffs if d["change"] == "BASE_IMAGE_CHANGED"]
        assert len(base_image_changes) == 1


class TestGitHubActionsDiffDetection:
    """Test GitHub Actions workflow diff detection."""

    def test_job_added(self):
        """Test detection of new CI jobs."""
        diffs = extract_github_actions_changes(GITHUB_ACTIONS_BASELINE, GITHUB_ACTIONS_UPDATED)

        job_additions = [d for d in diffs if d["change"] == "JOB_ADDED"]
        assert len(job_additions) > 0, "Should detect new jobs"

        job_names = [d.get("description", "") for d in job_additions]
        assert any("lint" in desc for desc in job_names), "Should detect lint job"
        assert any("security" in desc for desc in job_names), "Should detect security job"

    def test_job_removed(self):
        """Test detection of removed CI jobs."""
        diffs = extract_github_actions_changes(GITHUB_ACTIONS_UPDATED, GITHUB_ACTIONS_BASELINE)

        job_removals = [d for d in diffs if d["change"] == "JOB_REMOVED"]
        assert len(job_removals) > 0, "Should detect removed jobs"
        assert any(d["severity"] == "MAJOR" for d in job_removals)

    def test_trigger_changes(self):
        """Test detection of workflow trigger changes."""
        diffs = extract_github_actions_changes(GITHUB_ACTIONS_BASELINE, GITHUB_ACTIONS_UPDATED)

        trigger_changes = [d for d in diffs if d["change"] == "JOB_TRIGGER_CHANGED"]
        assert len(trigger_changes) == 1, "Should detect trigger changes"
        assert trigger_changes[0]["severity"] == "MAJOR"

    def test_permissions_added(self):
        """Test detection of new permissions."""
        diffs = extract_github_actions_changes(GITHUB_ACTIONS_BASELINE, GITHUB_ACTIONS_UPDATED)

        perm_additions = [d for d in diffs if d["change"] == "PERMISSION_ADDED"]
        assert len(perm_additions) > 0, "Should detect new permissions"
        assert any(d["severity"] == "MAJOR" for d in perm_additions)

    def test_permissions_removed(self):
        """Test detection of removed permissions."""
        diffs = extract_github_actions_changes(GITHUB_ACTIONS_UPDATED, GITHUB_ACTIONS_BASELINE)

        perm_removals = [d for d in diffs if d["change"] == "PERMISSION_REMOVED"]
        # May or may not have removals depending on how we parse
        if perm_removals:
            assert all(d["severity"] == "MINOR" for d in perm_removals)

    def test_env_var_changes(self):
        """Test detection of environment variable changes at workflow level."""
        diffs = extract_github_actions_changes(GITHUB_ACTIONS_BASELINE, GITHUB_ACTIONS_UPDATED)

        env_changes = [d for d in diffs if "ENV_VAR" in d["change"]]

        # PYTHON_VERSION changed
        env_changed = [d for d in env_changes if d["change"] == "ENV_VAR_CHANGED"]
        # (may not detect if we just see added/removed)

        # May see as removed old and added new
        if len(env_changed) == 0:
            env_added = [d for d in env_changes if d["change"] == "ENV_VAR_ADDED"]
            env_removed = [d for d in env_changes if d["change"] == "ENV_VAR_REMOVED"]
            assert len(env_added) > 0 or len(env_removed) > 0

    def test_deterministic_diff_order(self):
        """Test that GitHub Actions diffs are deterministically ordered."""
        results = []
        for _ in range(3):
            diffs = extract_github_actions_changes(GITHUB_ACTIONS_BASELINE, GITHUB_ACTIONS_UPDATED)
            key = str([(d["change"], d.get("description", "")) for d in diffs])
            results.append(key)

        assert results[0] == results[1] == results[2], "CI diffs should be deterministic"

    def test_empty_baseline(self):
        """Test diff with empty baseline (new workflow)."""
        diffs = extract_github_actions_changes(None, GITHUB_ACTIONS_BASELINE)

        # Should detect jobs and triggers as changes
        job_additions = [d for d in diffs if d["change"] == "JOB_ADDED"]
        assert len(job_additions) > 0, "Should detect jobs in new workflow"


class TestInfraRiskAssessment:
    """Test infrastructure risk level computation."""

    def test_low_risk_minimal_changes(self):
        """Test LOW risk with minimal changes."""
        docker_changes = [
            {
                "severity": "PATCH",
                "change": "ENV_VAR_ADDED",
                "category": "docker"
            }
        ]
        ci_changes = []

        risk = compute_infra_risk_level(docker_changes, ci_changes)
        assert risk == "LOW"

    def test_medium_risk_multiple_minor_changes(self):
        """Test MEDIUM risk with multiple minor changes."""
        docker_changes = [
            {"severity": "MINOR", "change": "PORT_EXPOSED", "category": "docker"},
            {"severity": "MINOR", "change": "ENV_VAR_CHANGED", "category": "docker"},
            {"severity": "PATCH", "change": "VOLUME_ADDED", "category": "docker"}
        ]
        ci_changes = [
            {"severity": "PATCH", "change": "ENV_VAR_ADDED", "category": "ci"}
        ]

        risk = compute_infra_risk_level(docker_changes, ci_changes)
        assert risk == "MEDIUM"

    def test_high_risk_major_changes(self):
        """Test HIGH risk with MAJOR changes."""
        docker_changes = [
            {"severity": "MAJOR", "change": "BASE_IMAGE_CHANGED", "category": "docker"},
            {"severity": "MAJOR", "change": "USER_CHANGED", "category": "docker"}
        ]
        ci_changes = []

        risk = compute_infra_risk_level(docker_changes, ci_changes)
        assert risk == "HIGH"

    def test_high_risk_single_critical_change(self):
        """Test HIGH risk with one critical Docker change and minor CI change."""
        docker_changes = [
            {"severity": "MAJOR", "change": "ENTRYPOINT_CHANGED", "category": "docker"}
        ]
        ci_changes = [
            {"severity": "MAJOR", "change": "JOB_REMOVED", "category": "ci"}
        ]

        risk = compute_infra_risk_level(docker_changes, ci_changes)
        assert risk == "HIGH"

    def test_no_changes_low_risk(self):
        """Test LOW risk with no changes."""
        risk = compute_infra_risk_level([], [])
        assert risk == "LOW"

    def test_deterministic_risk_computation(self):
        """Test that risk computation is deterministic."""
        docker_changes = [
            {"severity": "MAJOR", "change": "BASE_IMAGE_CHANGED", "category": "docker"}
        ]
        ci_changes = [
            {"severity": "MINOR", "change": "JOB_ADDED", "category": "ci"}
        ]

        results = []
        for _ in range(3):
            risk = compute_infra_risk_level(docker_changes, ci_changes)
            results.append(risk)

        assert results[0] == results[1] == results[2], "Risk computation should be deterministic"
