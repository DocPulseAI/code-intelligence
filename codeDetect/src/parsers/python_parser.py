from src.parsers.tree_sitter_engine import parse_code
import re


class PythonParser:
    """Compatibility wrapper around Tree-sitter Python parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".py")
        features = parsed.get("features", {}) or {}
        text = content or ""

        functions = list(features.get("functions", []))
        classes = list(features.get("classes", []))
        decorators = list(features.get("decorators", []))

        # Regex fallback if tree-sitter did not return keys.
        if not functions:
            functions = re.findall(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text, flags=re.MULTILINE)
        if not classes:
            classes = re.findall(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b", text, flags=re.MULTILINE)
        if not decorators:
            decorators = re.findall(r"^\s*@([A-Za-z_][A-Za-z0-9_\.]*(?:\([^)]*\))?)", text, flags=re.MULTILINE)

        # Legacy decorator format excludes leading '@'.
        normalized_decorators = []
        for item in decorators:
            value = str(item).strip()
            if value.startswith("@"):
                value = value[1:]
            normalized_decorators.append(value)

        return {
            "functions": sorted(set(functions)),
            "classes": sorted(set(classes)),
            "decorators": sorted(set(normalized_decorators)),
        }
