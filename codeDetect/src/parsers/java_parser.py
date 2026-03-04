from src.parsers.tree_sitter_engine import parse_code
import re


class JavaParser:
    """Compatibility wrapper around Tree-sitter Java parsing."""

    @staticmethod
    def analyze(content: str) -> dict:
        parsed = parse_code(content, ".java")
        features = parsed.get("features", {}) or {}
        text = content or ""

        classes = list(features.get("classes", []))
        annotations = list(features.get("annotations", []))

        # JavaParser compatibility: only public methods.
        methods = re.findall(
            r"^\s*public\s+[A-Za-z0-9_<>,\[\]\s]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            text,
            flags=re.MULTILINE,
        )
        methods = sorted(set(methods))

        if not classes:
            classes = re.findall(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b", text)
        classes = sorted(set(classes))

        if not annotations:
            annotations = [f"@{name}" for name in re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)", text)]
        annotations = sorted(set(annotations))

        # Build API endpoints by combining class-level RequestMapping + method mappings.
        base_path = ""
        class_map = re.search(r"@RequestMapping\s*\(\s*\"([^\"]+)\"\s*\)\s*[\s\S]*?class\s+[A-Za-z_]", text)
        if class_map:
            base_path = class_map.group(1)

        api_endpoints: list[dict] = []
        for m in re.finditer(r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*\(\s*\"([^\"]+)\"\s*\)", text):
            verb = m.group(1).replace("Mapping", "").upper()
            path = m.group(2)
            route = f"{base_path.rstrip('/')}/{path.lstrip('/')}" if base_path else path
            route = re.sub(r"/{2,}", "/", route)
            if not route.startswith("/"):
                route = "/" + route
            api_endpoints.append({"verb": verb, "route": route, "line": text[: m.start()].count("\n") + 1})

        return {
            "classes": classes,
            "methods": methods,
            "annotations": annotations,
            "api_endpoints": api_endpoints,
        }
