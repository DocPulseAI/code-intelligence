"""Deterministic repository type classification."""

from __future__ import annotations

import os
from typing import Callable


REPO_TYPES = {
    "backend-service",
    "frontend-app",
    "fullstack",
    "library",
    "infra-only",
    "cli",
}


_INFRA_PATTERNS = (
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".github/workflows/",
    ".tf",
    "terraform",
)


def _is_infra_file(path: str) -> bool:
    lower = path.lower()
    return (
        lower.endswith("dockerfile")
        or "docker-compose" in lower
        or lower.startswith(".github/workflows/")
        or "/.github/workflows/" in lower
        or lower.endswith(".tf")
        or "terraform" in lower
    )


def _is_frontend_file(path: str) -> bool:
    lower = path.lower()
    return any(
        token in lower
        for token in ["/frontend/", "frontend/", "/client/", "client/", "/ui/", "ui/", "/web/", "web/"]
    ) or lower.endswith(
        (".tsx", ".jsx", "vite.config.ts", "vite.config.js", "next.config.js", "next.config.mjs")
    )


def _is_backend_file(path: str) -> bool:
    lower = path.lower()
    return any(
        token in lower
        for token in [
            "/backend/",
            "backend/",
            "/server/",
            "server/",
            "/api/",
            "api/",
            "/routes/",
            "routes/",
            "/controllers/",
            "controllers/",
            "/services/",
            "services/",
        ]
    )


def _contains_any(text: str, needles: list[str]) -> bool:
    low = text.lower()
    return any(n in low for n in needles)


def classify_repository_type(
    file_paths: list[str],
    read_file: Callable[[str], str | None],
    api_endpoint_count: int,
) -> str:
    """Classify repository type from structural evidence only."""
    paths = sorted(str(p) for p in file_paths if str(p).strip())

    if not paths:
        return "library"

    package_json_paths = [p for p in paths if os.path.basename(p).lower() == "package.json"]
    has_frontend_paths = any(_is_frontend_file(p) for p in paths)
    has_backend_paths = any(_is_backend_file(p) for p in paths)
    has_non_infra = any(not _is_infra_file(p) for p in paths)
    has_infra = any(_is_infra_file(p) for p in paths)

    has_express = False
    has_frontend_framework = False
    has_cli_markers = False
    has_exported_modules = False
    has_http_server_markers = api_endpoint_count > 0

    for pkg_path in package_json_paths:
        content = read_file(pkg_path) or ""
        low = content.lower()
        if '"express"' in low or '"koa"' in low or '"fastify"' in low or '"nestjs"' in low:
            has_express = True
            has_http_server_markers = True
        if '"react"' in low or '"next"' in low or '"vite"' in low or '"vue"' in low or '"svelte"' in low:
            has_frontend_framework = True
        if '"commander"' in low or '"yargs"' in low or '"oclif"' in low or '"bin"' in low:
            has_cli_markers = True

    for path in paths:
        lower = path.lower()
        if lower.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".py", ".java")):
            content = (read_file(path) or "")[:20000]
            low = content.lower()
            if _contains_any(low, ["module.exports", "export default", "export function", "export class", "__all__"]):
                has_exported_modules = True
            if _contains_any(low, ["express()", "app.listen(", "fastapi(", "@restcontroller", "router.get(", "router.post("]):
                has_http_server_markers = True
            if _contains_any(low, ["if __name__ == '__main__'", 'if __name__ == "__main__"', "argparse", "click.command", "def main("]):
                has_cli_markers = True

    if has_frontend_paths and (has_backend_paths or has_express or has_http_server_markers):
        return "fullstack"

    if has_frontend_framework and not (has_express or has_http_server_markers or has_backend_paths):
        return "frontend-app"

    if has_express or has_http_server_markers:
        return "backend-service"

    if has_infra and not has_non_infra:
        return "infra-only"

    if has_cli_markers and not has_http_server_markers:
        return "cli"

    if has_exported_modules and not has_http_server_markers:
        return "library"

    if has_frontend_framework:
        return "frontend-app"

    if has_backend_paths:
        return "backend-service"

    return "library"
