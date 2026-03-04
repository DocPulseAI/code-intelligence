from src.parsers.tree_sitter_engine import parse_code
import re


class TSParser:
    """Compatibility wrapper around Tree-sitter TS/JS parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".ts")
        features = parsed.get("features", {}) or {}
        text = content or ""

        # Keep backward compatibility for callers expecting api_endpoints.
        if "api_endpoints" not in features and isinstance(features.get("api_routes"), list):
            features["api_endpoints"] = [
                {
                    "verb": r.get("method") or r.get("verb") or "GET",
                    "route": r.get("route") or r.get("path") or "",
                    "line": r.get("line", 0),
                }
                for r in features["api_routes"]
                if isinstance(r, dict)
            ]

        features.setdefault("functions", [])
        features.setdefault("classes", [])
        features.setdefault("exported_functions", [])
        features.setdefault("exported_classes", [])
        features.setdefault("api_endpoints", [])
        if "dependencies" not in features:
            deps: list[str] = []
            deps.extend(re.findall(r"\bimport\s+(?:[\w*{}\s,]+)\s+from\s+['\"]([^'\"]+)['\"]", text))
            deps.extend(re.findall(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)", text))
            features["dependencies"] = sorted(set(deps))
        return features
