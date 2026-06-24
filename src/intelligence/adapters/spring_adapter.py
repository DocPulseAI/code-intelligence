"""Spring Boot adapter for static API route extraction."""

from __future__ import annotations

import hashlib
import re
from typing import Callable, Optional

from src.intelligence.api_surface_adapter import ApiSurfaceAdapter, ResolvedRoute

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_API_PREFIX = re.compile(r"^v\d+$", re.IGNORECASE)


def _norm_path(path: str) -> str:
    p = str(path or "").strip()
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/{2,}", "/", p)
    p = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\s*:[^}]+\}", r"{\1}", p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def _to_param_path(path: str) -> str:
    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", r"{\1}", re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", _norm_path(path)))


def _hash_endpoint(method: str, path: str) -> str:
    return hashlib.sha256(f"v1|{method.upper()}|{_norm_path(path)}".encode("utf-8")).hexdigest()


def _to_pascal(token: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", " ", token).strip()
    return "".join(part[:1].upper() + part[1:] for part in clean.split()) or "Resource"


def _singular(token: str) -> str:
    t = token.lower()
    if t.endswith("ies") and len(t) > 3:
        return t[:-3] + "y"
    if t.endswith("s") and len(t) > 3:
        return t[:-1]
    return t


def _operation_id(method: str, full_path: str) -> str:
    segments = [s for s in _norm_path(full_path).split("/") if s and not _API_PREFIX.match(s) and s.lower() != "api"]
    literals = [s for s in segments if not (s.startswith("{") and s.endswith("}"))]
    resource = _singular(literals[0]) if literals else "resource"
    has_param = any(s.startswith("{") and s.endswith("}") for s in segments)
    m = method.upper()
    if m == "GET" and not has_param:
        return f"get{_to_pascal(resource + 's')}"
    if m == "GET" and has_param:
        return f"get{_to_pascal(resource)}ById"
    if m == "POST":
        return f"create{_to_pascal(resource)}"
    if m == "PUT":
        return f"replace{_to_pascal(resource)}"
    if m == "PATCH":
        return f"update{_to_pascal(resource)}"
    if m == "DELETE":
        return f"delete{_to_pascal(resource)}" + ("ById" if has_param else "")
    return f"{m.lower()}{_to_pascal(resource)}"


def _extract_path_from_annotation_args(args: str) -> str:
    if not args:
        return "/"
    m = re.search(r'(?:value|path)\s*=\s*"([^"]+)"', args)
    if m:
        return m.group(1)
    m = re.search(r'"([^"]+)"', args)
    if m:
        return m.group(1)
    return "/"


def _detect_context_path(file_paths: list[str], read_file: Callable[[str], str | None]) -> str:
    for path in sorted(file_paths):
        low = path.lower()
        if not (low.endswith("application.properties") or low.endswith("application.yml") or low.endswith("application.yaml")):
            continue
        text = read_file(path) or ""
        m = re.search(r"server\.servlet\.context-path\s*[:=]\s*([^\s#]+)", text)
        if m:
            return _norm_path(m.group(1).strip().strip('"').strip("'"))
    return ""


class SpringBootApiSurfaceAdapter(ApiSurfaceAdapter):
    name = "spring"

    def extract_candidates(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> list[dict]:
        context_path = _detect_context_path(file_paths, read_file)
        out: list[dict] = []
        for file_path in sorted(file_paths):
            if not file_path.endswith((".java", ".kt")):
                continue
            text = read_file(file_path) or ""
            if "@RestController" not in text:
                continue
            class_prefix = "/"
            class_match = re.search(r"@RequestMapping\s*\(([^)]*)\)\s*[\s\S]{0,300}?class\s+\w+", text)
            if class_match:
                class_prefix = _extract_path_from_annotation_args(class_match.group(1))
            lines = text.splitlines()
            for idx, line in enumerate(lines, start=1):
                m = re.search(r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*(?:\(([^)]*)\))?", line)
                if m:
                    method = m.group(1).replace("Mapping", "").upper()
                    method_path = _extract_path_from_annotation_args(m.group(2) or "")
                    full_path = _to_param_path(_norm_path(f"{context_path}/{class_prefix}/{method_path}"))
                    out.append(
                        {
                            "method": method,
                            "path": full_path,
                            "source_file": file_path,
                            "line_start": idx,
                            "router_symbol": "rest-controller",
                            "middleware_tokens": ["preauthorize"] if "@PreAuthorize" in line else [],
                        }
                    )
                rqm = re.search(r"@RequestMapping\s*\(([^)]*)\)", line)
                if rqm and "RequestMethod." in rqm.group(1):
                    method_m = re.search(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE)", rqm.group(1))
                    if not method_m:
                        continue
                    method = method_m.group(1).upper()
                    method_path = _extract_path_from_annotation_args(rqm.group(1))
                    full_path = _to_param_path(_norm_path(f"{context_path}/{class_prefix}/{method_path}"))
                    out.append(
                        {
                            "method": method,
                            "path": full_path,
                            "source_file": file_path,
                            "line_start": idx,
                            "router_symbol": "rest-controller",
                            "middleware_tokens": ["preauthorize"] if "@PreAuthorize" in line else [],
                        }
                    )
        return out

    def resolve_mounts(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> dict:
        return {"validation_status": "OK", "candidates": list(candidates)}

    def filter_reachable(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> list[dict]:
        return [c for c in candidates if str(c.get("path", "")).startswith("/")]

    def build_resolved_routes(self, candidates: list[dict]) -> list[ResolvedRoute]:
        out: list[ResolvedRoute] = []
        for c in candidates:
            method = str(c.get("method", "GET")).upper()
            path = _norm_path(str(c.get("path", "/")))
            auth_type = "RBAC" if any("preauthorize" in str(t).lower() for t in c.get("middleware_tokens", [])) else "Public"
            out.append(
                ResolvedRoute(
                    method=method,
                    full_path=path,
                    normalized_key=f"{method.lower()} {path.lower()}",
                    operation_id=_operation_id(method, path),
                    auth_type=auth_type,
                    endpoint_hash=_hash_endpoint(method, path),
                    source_file=str(c.get("source_file", "")),
                    line_start=int(c.get("line_start", 0) or 0),
                )
            )
        return out

