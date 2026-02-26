from src.parsers.tree_sitter_engine import parse_code


class JSParser:
    """Compatibility wrapper around Tree-sitter JS parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".js")
        features = parsed.get("features", {}) or {}

        # Backward compatibility with previous key name.
        if "dependencies" not in features and isinstance(features.get("imports"), list):
            features["dependencies"] = features["imports"]

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

        return features
