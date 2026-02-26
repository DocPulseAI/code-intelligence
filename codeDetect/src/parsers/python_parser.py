from src.parsers.tree_sitter_engine import parse_code


class PythonParser:
    """Compatibility wrapper around Tree-sitter Python parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".py")
        return parsed.get("features", {}) or {}
