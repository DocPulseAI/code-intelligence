from src.parsers.tree_sitter_engine import parse_code
import re


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _extract_router_symbols(text: str) -> set[str]:
    symbols = {"app", "router"}
    for rx in (
        r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*express\.Router\s*\(",
        r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*Router\s*\(",
    ):
        for match in re.finditer(rx, text):
            symbols.add(match.group(1))
    return symbols


def _extract_api_endpoints(text: str) -> list[dict]:
    router_symbols = _extract_router_symbols(text)
    routes: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    route_re = re.compile(
        r"\b([A-Za-z_]\w*)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    for match in route_re.finditer(text):
        symbol = match.group(1)
        if symbol not in router_symbols and not symbol.lower().endswith("router"):
            continue
        verb = match.group(2).upper()
        route = match.group(3)
        line = _line_for_offset(text, match.start())
        key = (verb, route, line)
        if key in seen:
            continue
        seen.add(key)
        routes.append({"verb": verb, "route": route, "line": line})
    return sorted(routes, key=lambda r: (r["line"], r["verb"], r["route"]))


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

        if not features.get("functions"):
            funcs = set(re.findall(r"\b(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(", text))
            funcs.update(
                re.findall(
                    r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>",
                    text,
                )
            )
            funcs.update(
                re.findall(
                    r"\b([A-Za-z_]\w*)\s*=\s*(?:[A-Za-z_]\w*\s*\()?\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>",
                    text,
                )
            )
            funcs.update(
                re.findall(
                    r"\b([A-Za-z_]\w*)\s*=\s*asyncHandler\s*\(",
                    text,
                )
            )
            features["functions"] = sorted(funcs)
        if not features.get("classes"):
            features["classes"] = sorted(set(re.findall(r"\b(?:abstract\s+)?class\s+([A-Za-z_]\w*)\b", text)))
        if not features.get("exported_functions"):
            exported = set(re.findall(r"\bexport\s+(?:default\s+)?function\s+([A-Za-z_]\w*)\s*\(", text))
            exported.update(
                re.findall(
                    r"\bexport\s+(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>",
                    text,
                )
            )
            features["exported_functions"] = sorted(exported)
        if not features.get("exported_classes"):
            features["exported_classes"] = sorted(set(re.findall(r"\bexport\s+(?:default\s+)?class\s+([A-Za-z_]\w*)\b", text)))
        features.setdefault("api_endpoints", [])
        if not features["api_endpoints"]:
            features["api_endpoints"] = _extract_api_endpoints(text)
        if "dependencies" not in features:
            deps: list[str] = []
            deps.extend(re.findall(r"\bimport\s+(?:[\w*{}\s,]+)\s+from\s+['\"]([^'\"]+)['\"]", text))
            deps.extend(re.findall(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)", text))
            features["dependencies"] = sorted(set(deps))
        return features
