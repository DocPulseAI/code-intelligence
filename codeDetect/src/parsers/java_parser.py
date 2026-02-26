from src.parsers.tree_sitter_engine import parse_code


class JavaParser:
    """Compatibility wrapper around Tree-sitter Java parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".java")
        return parsed.get("features", {}) or {}
