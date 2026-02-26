"""Syntax checking backed by Tree-sitter parsing."""

from typing import Optional

from src.parsers.tree_sitter_engine import parse_code


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
        return bool(result.get("syntax_error", False))

    @staticmethod
    def get_error_details(file_path: str, content: str) -> Optional[dict]:
        if not content or not content.strip():
            return None

        extension = "."
        if "." in file_path:
            extension = f".{file_path.rsplit('.', 1)[-1].lower()}"

        result = parse_code(content, extension)
        if not result.get("syntax_error", False):
            return None

        errors = result.get("error_nodes") or []
        if errors and isinstance(errors[0], dict):
            first = errors[0]
            return {
                "type": first.get("type", "SyntaxError"),
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
