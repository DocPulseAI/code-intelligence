"""Structured tech stack extraction."""

from __future__ import annotations

import os
import re
from typing import Callable


def _read_json_like(read_file: Callable[[str], str | None], path: str) -> str:
    return (read_file(path) or "").lower()


def _pick_first(candidates: list[str]) -> str | None:
    for item in candidates:
        if item:
            return item
    return None


def build_tech_stack(file_paths: list[str], read_file: Callable[[str], str | None]) -> dict:
    paths = sorted(str(p) for p in file_paths if str(p).strip())
    backend_framework: str | None = None
    frontend_framework: str | None = None
    database: str | None = None
    orm: str | None = None
    infra: set[str] = set()
    ci: set[str] = set()

    package_jsons = [p for p in paths if os.path.basename(p).lower() == "package.json"]
    for pkg in package_jsons:
        low = _read_json_like(read_file, pkg)
        if backend_framework is None:
            backend_framework = _pick_first([
                "nestjs" if '"nestjs"' in low or "@nestjs" in low else "",
                "express" if '"express"' in low else "",
                "fastify" if '"fastify"' in low else "",
                "koa" if '"koa"' in low else "",
            ])
        if frontend_framework is None:
            frontend_framework = _pick_first([
                "nextjs" if '"next"' in low else "",
                "react" if '"react"' in low else "",
                "vite" if '"vite"' in low else "",
                "vue" if '"vue"' in low else "",
                "svelte" if '"svelte"' in low else "",
            ])
        if orm is None:
            orm = _pick_first([
                "prisma" if '"prisma"' in low else "",
                "mongoose" if '"mongoose"' in low else "",
                "typeorm" if '"typeorm"' in low else "",
                "sequelize" if '"sequelize"' in low else "",
            ])
        if database is None:
            database = _pick_first([
                "postgresql" if '"postgres"' in low else "",
                "mysql" if '"mysql"' in low else "",
                "mongodb" if '"mongodb"' in low or '"mongoose"' in low else "",
                "sqlite" if '"sqlite"' in low else "",
                "redis" if '"redis"' in low else "",
            ])

    for path in paths:
        lower = path.lower()
        if lower == "dockerfile" or lower.endswith("/dockerfile") or "docker-compose" in lower:
            infra.add("docker")
        if lower.endswith(".tf") or "terraform" in lower:
            infra.add("terraform")
        if lower.startswith(".github/workflows/") or "/.github/workflows/" in lower:
            ci.add("github-actions")

        if lower.endswith("schema.prisma"):
            orm = orm or "prisma"
            content = (read_file(path) or "").lower()
            if re.search(r"\bprovider\s*=\s*\"postgresql\"", content):
                database = database or "postgresql"
            elif re.search(r"\bprovider\s*=\s*\"mysql\"", content):
                database = database or "mysql"
            elif re.search(r"\bprovider\s*=\s*\"sqlite\"", content):
                database = database or "sqlite"
            elif re.search(r"\bprovider\s*=\s*\"mongodb\"", content):
                database = database or "mongodb"

        if lower.endswith(".java"):
            content = (read_file(path) or "")
            if backend_framework is None and "@RestController" in content:
                backend_framework = "spring"
            if orm is None and "@Entity" in content:
                orm = "jpa"

        if lower.endswith("requirements.txt") or lower.endswith("pyproject.toml"):
            content = (read_file(path) or "").lower()
            if backend_framework is None:
                backend_framework = _pick_first([
                    "fastapi" if "fastapi" in content else "",
                    "flask" if "flask" in content else "",
                    "django" if "django" in content else "",
                ])
            if orm is None:
                orm = _pick_first([
                    "django-orm" if "django" in content else "",
                    "sqlalchemy" if "sqlalchemy" in content else "",
                ])
            if database is None:
                database = _pick_first([
                    "postgresql" if "psycopg" in content or "postgres" in content else "",
                    "mysql" if "pymysql" in content or "mysql" in content else "",
                ])

    return {
        "backend_framework": backend_framework,
        "frontend_framework": frontend_framework,
        "database": database,
        "orm": orm,
        "infra": sorted(infra),
        "ci": sorted(ci),
    }
