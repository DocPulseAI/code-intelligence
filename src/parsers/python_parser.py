from src.parsers.tree_sitter_engine import parse_code
import re


class PythonParser:
    """Compatibility wrapper around Tree-sitter Python parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".py")
        features = parsed.get("features", {}) or {}
        text = content or ""

        # Keep parser-native rich fields if available.
        out = dict(features)

        functions = list(features.get("functions", []))
        classes = list(features.get("classes", []))
        decorators = list(features.get("decorators", []))

        # Regex fallback/augmentation to preserve legacy completeness.
        regex_functions = re.findall(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text, flags=re.MULTILINE)
        regex_classes = re.findall(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b", text, flags=re.MULTILINE)
        regex_decorators = re.findall(r"^\s*@([A-Za-z_][A-Za-z0-9_\.]*(?:\([^)]*\))?)", text, flags=re.MULTILINE)
        functions = sorted(set(functions + regex_functions))
        classes = sorted(set(classes + regex_classes))
        decorators = sorted(set(decorators + regex_decorators))

        # Legacy decorator format excludes leading '@'.
        normalized_decorators = []
        for item in decorators:
            value = str(item).strip()
            if value.startswith("@"):
                value = value[1:]
            normalized_decorators.append(value)

        out["functions"] = functions
        out["classes"] = classes
        out["decorators"] = sorted(set(normalized_decorators))

        # Preserve API endpoints/routes and call graph cues for FastAPI/Flask repos.
        if "api_endpoints" not in out and isinstance(out.get("api_routes"), list):
            out["api_endpoints"] = [
                {
                    "verb": r.get("method") or r.get("verb") or "GET",
                    "route": r.get("route") or r.get("path") or "",
                    "line": r.get("line", 0),
                    "handler": r.get("handler", ""),
                    "router_symbol": r.get("router_symbol", "app"),
                }
                for r in out["api_routes"]
                if isinstance(r, dict)
            ]
        out.setdefault("api_endpoints", [])
        out.setdefault("calls", [])
        out.setdefault("imports", [])

        # Keep dependency extraction aligned with other language parsers.
        if "dependencies" not in out:
            deps: list[str] = []
            deps.extend(re.findall(r"^\s*import\s+([A-Za-z0-9_\.]+)", text, flags=re.MULTILINE))
            deps.extend(re.findall(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+", text, flags=re.MULTILINE))
            out["dependencies"] = sorted(set(d for d in deps if str(d).strip()))

        return out
