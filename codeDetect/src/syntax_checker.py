"""Syntax checking backed by Tree-sitter parsing with deterministic fallbacks."""

from __future__ import annotations

import json
import re
from typing import Optional

from src.parsers.tree_sitter_engine import parse_code


def _check_balanced(content: str, open_char: str, close_char: str) -> bool:
    cleaned = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', content)
    cleaned = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", cleaned)
    cleaned = re.sub(r'`[^`\\]*(?:\\.[^`\\]*)*`', '``', cleaned)
    count = 0
    for char in cleaned:
        if char == open_char:
            count += 1
        elif char == close_char:
            count -= 1
        if count < 0:
            return False
    return count == 0


def _python_error(content: str) -> Optional[dict]:
    try:
        compile(content, "<string>", "exec")
        return None
    except SyntaxError as exc:
        return {
            "type": "SyntaxError",
            "message": str(exc),
            "line": getattr(exc, "lineno", None),
            "column": getattr(exc, "offset", None),
        }


def _heuristic_error(extension: str, content: str) -> Optional[dict]:
    ext = extension.lower()
    if ext == ".py":
        return _python_error(content)

    if ext == ".json":
        try:
            json.loads(content)
            return None
        except Exception as exc:  # noqa: BLE001
            return {
                "type": "SyntaxError",
                "message": str(exc),
                "line": None,
                "column": None,
            }

    if ext in {".js", ".jsx", ".ts", ".tsx"}:
        has_error = (
            not _check_balanced(content, "{", "}")
            or not _check_balanced(content, "[", "]")
            or not _check_balanced(content, "(", ")")
            or (content.count("`") % 2 != 0)
        )
        if has_error:
            return {"type": "SyntaxError", "message": "Unbalanced JavaScript/TypeScript syntax", "line": None, "column": None}
        return None

    if ext in {".java", ".kt", ".cs", ".c", ".cpp", ".go", ".rs", ".php"}:
        has_error = not _check_balanced(content, "{", "}") or not _check_balanced(content, "(", ")")
        if has_error:
            return {"type": "SyntaxError", "message": "Unbalanced brace/parenthesis structure", "line": None, "column": None}
        return None

    return None


class SyntaxChecker:
    """Returns True when syntax is invalid, False otherwise."""

    @staticmethod
    def check(file_path: str, content: str) -> bool:
        if not content or not content.strip():
            return False

        extension = "."
        if "." in file_path:
            extension = f".{file_path.rsplit('.', 1)[-1].lower()}"

        result = parse_code(content, extension)
        if bool(result.get("syntax_error", False)):
            return True

        # Fallback when AST analysis is unavailable or parser could not load language.
        if not result.get("features"):
            return _heuristic_error(extension, content) is not None
        return False

    @staticmethod
    def get_error_details(file_path: str, content: str) -> Optional[dict]:
        if not content or not content.strip():
            return None

        extension = "."
        if "." in file_path:
            extension = f".{file_path.rsplit('.', 1)[-1].lower()}"

        result = parse_code(content, extension)
        if not result.get("syntax_error", False):
            return _heuristic_error(extension, content)

        errors = result.get("error_nodes") or []
        if errors and isinstance(errors[0], dict):
            first = errors[0]
            return {
                "type": "SyntaxError",
                "message": "Tree-sitter reported syntax errors",
                "line": first.get("start_line"),
                "column": first.get("start_col"),
            }

        return {
            "type": "SyntaxError",
            "message": "Tree-sitter reported syntax errors",
            "line": None,
            "column": None,
        }
