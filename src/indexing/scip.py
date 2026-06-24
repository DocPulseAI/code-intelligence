"""SCIP-style deterministic symbol identifiers for parsed features."""

from __future__ import annotations

from typing import Any
import re


def _sanitize_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_./-]", "_", value.strip())
    return token or "unknown"


def build_symbol_id(language: str, file_path: str, kind: str, name: str) -> str:
    lang = _sanitize_token(language or "unknown")
    path = _sanitize_token(file_path)
    k = _sanitize_token(kind)
    n = _sanitize_token(name)
    return f"scip-local://{lang}/{path}#{k}:{n}"


def extract_scip_symbols(file_path: str, language: str | None, features: dict[str, Any]) -> list[dict[str, str]]:
    """Map parsed features to deterministic SCIP-like symbol entries."""
    lang = language or "unknown"
    out: list[dict[str, str]] = []

    for kind, key in [
        ("function", "functions"),
        ("class", "classes"),
        ("method", "methods"),
        ("exported_function", "exported_functions"),
        ("exported_class", "exported_classes"),
    ]:
        values = features.get(key, [])
        if not isinstance(values, list):
            continue
        for name in values:
            if not isinstance(name, str) or not name.strip():
                continue
            out.append(
                {
                    "symbol_id": build_symbol_id(lang, file_path, kind, name),
                    "kind": kind,
                    "name": name,
                }
            )

    dedup: dict[str, dict[str, str]] = {}
    for item in out:
        dedup[item["symbol_id"]] = item
    return list(dedup.values())
