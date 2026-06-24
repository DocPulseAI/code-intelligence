"""Documentation contract policy by repository type."""

from __future__ import annotations


def build_documentation_contract(repository_type: str, breaking_change_detected: bool) -> dict:
    repo_type = repository_type or "library"

    if repo_type == "backend-service":
        return {
            "requires_readme": True,
            "requires_api_reference": True,
            "requires_architecture_doc": True,
            "requires_adr": bool(breaking_change_detected),
        }

    if repo_type == "frontend-app":
        return {
            "requires_readme": True,
            "requires_api_reference": False,
            "requires_architecture_doc": True,
            "requires_adr": False,
        }

    if repo_type == "infra-only":
        return {
            "requires_readme": True,
            "requires_api_reference": False,
            "requires_architecture_doc": False,
            "requires_adr": False,
        }

    if repo_type == "fullstack":
        return {
            "requires_readme": True,
            "requires_api_reference": True,
            "requires_architecture_doc": True,
            "requires_adr": bool(breaking_change_detected),
        }

    if repo_type == "cli":
        return {
            "requires_readme": True,
            "requires_api_reference": False,
            "requires_architecture_doc": True,
            "requires_adr": False,
        }

    return {
        "requires_readme": True,
        "requires_api_reference": False,
        "requires_architecture_doc": True,
        "requires_adr": bool(breaking_change_detected),
    }
