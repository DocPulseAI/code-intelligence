"""
Infrastructure semantic diff engine for Docker and GitHub Actions.
Analyzes container configuration and CI/CD workflow changes with deterministic severity mapping.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional


def _canonical(data: Any) -> str:
    """Canonical JSON encoding for deterministic hashing."""
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _stable_id(payload: dict) -> str:
    """Generate deterministic id for a payload."""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


# Severity mapping for Docker changes
_DOCKER_SEVERITY = {
    "BASE_IMAGE_CHANGED": "MAJOR",
    "BASE_IMAGE_TAG_CHANGED": "MAJOR",
    "PORT_EXPOSED": "MINOR",
    "PORT_REMOVED": "MAJOR",
    "ENV_VAR_ADDED": "MINOR",
    "ENV_VAR_REMOVED": "PATCH",
    "ENV_VAR_CHANGED": "MINOR",
    "USER_CHANGED": "MAJOR",
    "ENTRYPOINT_CHANGED": "MAJOR",
    "CMD_CHANGED": "MINOR",
    "VOLUME_ADDED": "MINOR",
    "VOLUME_REMOVED": "PATCH",
    "WORKDIR_CHANGED": "PATCH",
    "RUN_COMMAND_CHANGED": "MINOR",
}

# Severity mapping for GitHub Actions changes
_CI_SEVERITY = {
    "JOB_ADDED": "MINOR",
    "JOB_REMOVED": "MAJOR",
    "JOB_TRIGGER_CHANGED": "MAJOR",
    "STEP_ADDED": "PATCH",
    "STEP_REMOVED": "PATCH",
    "PERMISSION_ADDED": "MAJOR",
    "PERMISSION_REMOVED": "MINOR",
    "PERMISSION_CHANGED": "MAJOR",
    "ENV_VAR_ADDED": "PATCH",
    "ENV_VAR_REMOVED": "PATCH",
    "TIMEOUT_CHANGED": "PATCH",
    "CONCURRENCY_CHANGED": "PATCH",
}


def _parse_dockerfile(content: str) -> dict[str, Any]:
    """Parse Dockerfile and extract key configuration elements."""
    config = {
        "base_image": None,
        "ports": [],
        "env_vars": {},
        "user": None,
        "entrypoint": None,
        "cmd": None,
        "volumes": [],
        "workdir": None,
        "run_commands": [],
    }

    lines = content.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        if not parts:
            continue

        instruction = parts[0].upper()
        args = parts[1] if len(parts) > 1 else ""

        if instruction == "FROM":
            config["base_image"] = args.strip()
        elif instruction == "EXPOSE":
            config["ports"].extend([p.strip() for p in args.split()])
        elif instruction == "ENV":
            # Parse ENV key=value or ENV key value
            if "=" in args:
                key, val = args.split("=", 1)
                config["env_vars"][key.strip()] = val.strip()
            else:
                parts = args.split(None, 1)
                if len(parts) == 2:
                    config["env_vars"][parts[0]] = parts[1]
        elif instruction == "USER":
            config["user"] = args.strip()
        elif instruction == "ENTRYPOINT":
            config["entrypoint"] = args.strip()
        elif instruction == "CMD":
            config["cmd"] = args.strip()
        elif instruction == "VOLUME":
            config["volumes"].extend([v.strip() for v in args.split()])
        elif instruction == "WORKDIR":
            config["workdir"] = args.strip()
        elif instruction == "RUN":
            config["run_commands"].append(args.strip())

    return config


def _parse_github_actions(content: str) -> dict[str, Any]:
    """Parse GitHub Actions workflow YAML (simplified - no full YAML parser)."""
    workflow = {
        "jobs": [],
        "on": [],
        "env": {},
        "permissions": {},
        "concurrency": None,
        "timeout": None,
    }

    lines = content.strip().split("\n")
    current_job = None
    current_section = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Detect top-level sections
        if not line.startswith(" "):
            if stripped.startswith("jobs:"):
                current_section = "jobs"
            elif stripped.startswith("on:"):
                current_section = "on"
            elif stripped.startswith("env:"):
                current_section = "env"
            elif stripped.startswith("permissions:"):
                current_section = "permissions"
            elif stripped.startswith("timeout-minutes:"):
                m = re.search(r'timeout-minutes:\s*(\d+)', stripped)
                if m:
                    workflow["timeout"] = int(m.group(1))
            elif stripped.startswith("concurrency:"):
                m = re.search(r'concurrency:\s*(.+)', stripped)
                if m:
                    workflow["concurrency"] = m.group(1).strip()
            continue

        # Parse section content
        if current_section == "jobs":
            if not stripped.startswith("-"):
                # Job definition: "  job_name:"
                m = re.match(r'(\w+):', stripped)
                if m:
                    current_job = m.group(1)
                    workflow["jobs"].append(current_job)
        elif current_section == "on":
            # Event triggers
            m = re.match(r'(\w+):', stripped)
            if m:
                workflow["on"].append(m.group(1))
        elif current_section == "env":
            # Environment variables
            m = re.match(r'(\w+):\s*(.+)', stripped)
            if m:
                workflow["env"][m.group(1)] = m.group(2).strip()
        elif current_section == "permissions":
            # Permissions
            m = re.match(r'(\w+):\s*(.+)', stripped)
            if m:
                workflow["permissions"][m.group(1)] = m.group(2).strip()

    return workflow


def extract_docker_changes(
    baseline_content: str | None,
    current_content: str,
) -> list[dict]:
    """
    Detect Docker configuration changes between baseline and current.
    Returns list of change descriptors with severity.
    """
    changes = []

    baseline_config = _parse_dockerfile(baseline_content or "")
    current_config = _parse_dockerfile(current_content)

    # Base image change
    if baseline_config.get("base_image") != current_config.get("base_image"):
        severity = _DOCKER_SEVERITY.get("BASE_IMAGE_CHANGED", "MAJOR")
        changes.append({
            "type": "INFRA_CHANGE",
            "category": "docker",
            "change": "BASE_IMAGE_CHANGED",
            "description": f"Base image changed from '{baseline_config.get('base_image')}' to '{current_config.get('base_image')}'",
            "severity": severity,
            "classification_basis": "INFRA_DIFF",
            "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "BASE_IMAGE_CHANGED"}),
        })

    # Exposed ports
    baseline_ports = set(baseline_config.get("ports", []))
    current_ports = set(current_config.get("ports", []))

    for port in current_ports - baseline_ports:
        changes.append({
            "type": "INFRA_CHANGE",
            "category": "docker",
            "change": "PORT_EXPOSED",
            "description": f"New port exposed: {port}",
            "severity": _DOCKER_SEVERITY.get("PORT_EXPOSED", "MINOR"),
            "classification_basis": "INFRA_DIFF",
            "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "PORT_EXPOSED", "port": port}),
        })

    for port in baseline_ports - current_ports:
        changes.append({
            "type": "INFRA_CHANGE",
            "category": "docker",
            "change": "PORT_REMOVED",
            "description": f"Port removed: {port}",
            "severity": _DOCKER_SEVERITY.get("PORT_REMOVED", "MAJOR"),
            "classification_basis": "INFRA_DIFF",
            "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "PORT_REMOVED", "port": port}),
        })

    # Environment variables
    baseline_env = baseline_config.get("env_vars", {})
    current_env = current_config.get("env_vars", {})

    for key in current_env:
        if key not in baseline_env:
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "docker",
                "change": "ENV_VAR_ADDED",
                "description": f"Environment variable added: {key}",
                "severity": _DOCKER_SEVERITY.get("ENV_VAR_ADDED", "MINOR"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "ENV_VAR_ADDED", "key": key}),
            })
        elif baseline_env.get(key) != current_env.get(key):
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "docker",
                "change": "ENV_VAR_CHANGED",
                "description": f"Environment variable changed: {key}",
                "severity": _DOCKER_SEVERITY.get("ENV_VAR_CHANGED", "MINOR"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "ENV_VAR_CHANGED", "key": key}),
            })

    for key in baseline_env:
        if key not in current_env:
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "docker",
                "change": "ENV_VAR_REMOVED",
                "description": f"Environment variable removed: {key}",
                "severity": _DOCKER_SEVERITY.get("ENV_VAR_REMOVED", "PATCH"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "ENV_VAR_REMOVED", "key": key}),
            })

    # User change
    if baseline_config.get("user") != current_config.get("user"):
        if baseline_config.get("user") or current_config.get("user"):
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "docker",
                "change": "USER_CHANGED",
                "description": f"Docker user changed from '{baseline_config.get('user')}' to '{current_config.get('user')}'",
                "severity": _DOCKER_SEVERITY.get("USER_CHANGED", "MAJOR"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "USER_CHANGED"}),
            })

    # Entrypoint change
    if baseline_config.get("entrypoint") != current_config.get("entrypoint"):
        if baseline_config.get("entrypoint") or current_config.get("entrypoint"):
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "docker",
                "change": "ENTRYPOINT_CHANGED",
                "description": f"Docker entrypoint changed",
                "severity": _DOCKER_SEVERITY.get("ENTRYPOINT_CHANGED", "MAJOR"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "ENTRYPOINT_CHANGED"}),
            })

    # CMD change
    if baseline_config.get("cmd") != current_config.get("cmd"):
        if baseline_config.get("cmd") or current_config.get("cmd"):
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "docker",
                "change": "CMD_CHANGED",
                "description": f"Docker CMD changed",
                "severity": _DOCKER_SEVERITY.get("CMD_CHANGED", "MINOR"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "CMD_CHANGED"}),
            })

    # Volumes
    baseline_volumes = set(baseline_config.get("volumes", []))
    current_volumes = set(current_config.get("volumes", []))

    for vol in current_volumes - baseline_volumes:
        changes.append({
            "type": "INFRA_CHANGE",
            "category": "docker",
            "change": "VOLUME_ADDED",
            "description": f"Volume added: {vol}",
            "severity": _DOCKER_SEVERITY.get("VOLUME_ADDED", "MINOR"),
            "classification_basis": "INFRA_DIFF",
            "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "VOLUME_ADDED", "volume": vol}),
        })

    for vol in baseline_volumes - current_volumes:
        changes.append({
            "type": "INFRA_CHANGE",
            "category": "docker",
            "change": "VOLUME_REMOVED",
            "description": f"Volume removed: {vol}",
            "severity": _DOCKER_SEVERITY.get("VOLUME_REMOVED", "PATCH"),
            "classification_basis": "INFRA_DIFF",
            "id": _stable_id({"type": "INFRA_CHANGE", "category": "docker", "change": "VOLUME_REMOVED", "volume": vol}),
        })

    return sorted(changes, key=lambda c: (c["change"], c.get("description", "")))


def extract_github_actions_changes(
    baseline_content: str | None,
    current_content: str,
) -> list[dict]:
    """
    Detect GitHub Actions workflow changes between baseline and current.
    Returns list of change descriptors with severity.
    """
    changes = []

    baseline_workflow = _parse_github_actions(baseline_content or "")
    current_workflow = _parse_github_actions(current_content)

    # Job changes
    baseline_jobs = set(baseline_workflow.get("jobs", []))
    current_jobs = set(current_workflow.get("jobs", []))

    for job in current_jobs - baseline_jobs:
        changes.append({
            "type": "INFRA_CHANGE",
            "category": "ci",
            "change": "JOB_ADDED",
            "description": f"CI job added: {job}",
            "severity": _CI_SEVERITY.get("JOB_ADDED", "MINOR"),
            "classification_basis": "INFRA_DIFF",
            "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "JOB_ADDED", "job": job}),
        })

    for job in baseline_jobs - current_jobs:
        changes.append({
            "type": "INFRA_CHANGE",
            "category": "ci",
            "change": "JOB_REMOVED",
            "description": f"CI job removed: {job}",
            "severity": _CI_SEVERITY.get("JOB_REMOVED", "MAJOR"),
            "classification_basis": "INFRA_DIFF",
            "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "JOB_REMOVED", "job": job}),
        })

    # Event trigger changes
    baseline_events = set(baseline_workflow.get("on", []))
    current_events = set(current_workflow.get("on", []))

    if baseline_events != current_events:
        changes.append({
            "type": "INFRA_CHANGE",
            "category": "ci",
            "change": "JOB_TRIGGER_CHANGED",
            "description": f"Workflow triggers changed from {sorted(baseline_events)} to {sorted(current_events)}",
            "severity": _CI_SEVERITY.get("JOB_TRIGGER_CHANGED", "MAJOR"),
            "classification_basis": "INFRA_DIFF",
            "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "JOB_TRIGGER_CHANGED"}),
        })

    # Permissions changes
    baseline_perms = baseline_workflow.get("permissions", {})
    current_perms = current_workflow.get("permissions", {})

    for perm_key in current_perms:
        if perm_key not in baseline_perms:
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "ci",
                "change": "PERMISSION_ADDED",
                "description": f"CI permission added: {perm_key}",
                "severity": _CI_SEVERITY.get("PERMISSION_ADDED", "MAJOR"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "PERMISSION_ADDED", "permission": perm_key}),
            })
        elif baseline_perms.get(perm_key) != current_perms.get(perm_key):
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "ci",
                "change": "PERMISSION_CHANGED",
                "description": f"CI permission changed: {perm_key}",
                "severity": _CI_SEVERITY.get("PERMISSION_CHANGED", "MAJOR"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "PERMISSION_CHANGED", "permission": perm_key}),
            })

    for perm_key in baseline_perms:
        if perm_key not in current_perms:
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "ci",
                "change": "PERMISSION_REMOVED",
                "description": f"CI permission removed: {perm_key}",
                "severity": _CI_SEVERITY.get("PERMISSION_REMOVED", "MINOR"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "PERMISSION_REMOVED", "permission": perm_key}),
            })

    # Environment variable changes (CI level)
    baseline_env = baseline_workflow.get("env", {})
    current_env = current_workflow.get("env", {})

    for key in current_env:
        if key not in baseline_env:
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "ci",
                "change": "ENV_VAR_ADDED",
                "description": f"CI environment variable added: {key}",
                "severity": _CI_SEVERITY.get("ENV_VAR_ADDED", "PATCH"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "ENV_VAR_ADDED", "key": key}),
            })

    for key in baseline_env:
        if key not in current_env:
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "ci",
                "change": "ENV_VAR_REMOVED",
                "description": f"CI environment variable removed: {key}",
                "severity": _CI_SEVERITY.get("ENV_VAR_REMOVED", "PATCH"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "ENV_VAR_REMOVED", "key": key}),
            })

    # Timeout changes
    if baseline_workflow.get("timeout") != current_workflow.get("timeout"):
        if baseline_workflow.get("timeout") or current_workflow.get("timeout"):
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "ci",
                "change": "TIMEOUT_CHANGED",
                "description": f"Workflow timeout changed",
                "severity": _CI_SEVERITY.get("TIMEOUT_CHANGED", "PATCH"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "TIMEOUT_CHANGED"}),
            })

    # Concurrency changes
    if baseline_workflow.get("concurrency") != current_workflow.get("concurrency"):
        if baseline_workflow.get("concurrency") or current_workflow.get("concurrency"):
            changes.append({
                "type": "INFRA_CHANGE",
                "category": "ci",
                "change": "CONCURRENCY_CHANGED",
                "description": f"Workflow concurrency changed",
                "severity": _CI_SEVERITY.get("CONCURRENCY_CHANGED", "PATCH"),
                "classification_basis": "INFRA_DIFF",
                "id": _stable_id({"type": "INFRA_CHANGE", "category": "ci", "change": "CONCURRENCY_CHANGED"}),
            })

    return sorted(changes, key=lambda c: (c["change"], c.get("description", "")))


def compute_infra_risk_level(docker_changes: list[dict], ci_changes: list[dict]) -> str:
    """
    Compute overall infrastructure risk level based on Docker and CI changes.
    Returns one of: LOW, MEDIUM, HIGH
    """
    all_changes = docker_changes + ci_changes

    if not all_changes:
        return "LOW"

    # Count by severity
    major_count = sum(1 for c in all_changes if c.get("severity") == "MAJOR")
    minor_count = sum(1 for c in all_changes if c.get("severity") == "MINOR")

    # Risk assessment logic
    if major_count >= 2 or (major_count >= 1 and any(c.get("change") in {"USER_CHANGED", "ENTRYPOINT_CHANGED", "BASE_IMAGE_CHANGED", "JOB_REMOVED"} for c in all_changes)):
        return "HIGH"
    if major_count >= 1 or minor_count >= 3:
        return "MEDIUM"
    return "LOW"
