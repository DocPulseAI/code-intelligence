from src.parsers.tree_sitter_engine import parse_code


class TSParser:
    """Compatibility wrapper around Tree-sitter TS/JS parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".ts")
        features = parsed.get("features", {}) or {}

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

        return features
