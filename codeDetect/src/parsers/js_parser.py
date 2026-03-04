from src.parsers.tree_sitter_engine import parse_code
import re


class JSParser:
    """Compatibility wrapper around Tree-sitter JS parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".js")
        features = parsed.get("features", {}) or {}
        text = content or ""

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

        # Legacy-compatible defaults.
        features.setdefault("functions", [])
        features.setdefault("api_endpoints", [])

        # Dependency fallback for JS/TS import+require sources.
        if "dependencies" not in features:
            deps: list[str] = []
            deps.extend(re.findall(r"\bimport\s+(?:[\w*{}\s,]+)\s+from\s+['\"]([^'\"]+)['\"]", text))
            deps.extend(re.findall(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)", text))
            features["dependencies"] = sorted(set(deps))

        # React component presence marker used by legacy tests.
        if "react_components" not in features:
            has_jsx = bool(re.search(r"<[A-Za-z][A-Za-z0-9]*\b", text))
            features["react_components"] = ["REACT_COMPONENT"] if has_jsx else []

        # Hook extraction fallback.
        if "hooks" not in features:
            hooks = re.findall(r"\b(use[A-Z][A-Za-z0-9_]*)\s*\(", text)
            features["hooks"] = sorted(set(hooks))

        return features
