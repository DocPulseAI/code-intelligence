"""FastAPI adapter for static API route extraction with router include chains."""

from __future__ import annotations

import hashlib
import re
from typing import Callable, Optional

from src.intelligence.api_surface_adapter import ApiSurfaceAdapter, ResolvedRoute

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


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


def _hash_endpoint(method: str, path: str) -> str:
    return hashlib.sha256(f"v1|{method.upper()}|{_norm_path(path)}".encode("utf-8")).hexdigest()


def _to_pascal(token: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", " ", token).strip()
    return "".join(part[:1].upper() + part[1:] for part in clean.split()) or "Resource"


def _operation_id(method: str, full_path: str) -> str:
    segments = [s for s in _norm_path(full_path).split("/") if s and s.lower() not in {"api"} and not re.fullmatch(r"v\d+", s)]
    literals = [s for s in segments if not (s.startswith("{") and s.endswith("}"))]
    resource = literals[0] if literals else "resource"
    singular = resource[:-1] if resource.endswith("s") and len(resource) > 3 else resource
    has_param = any(s.startswith("{") and s.endswith("}") for s in segments)
    m = method.upper()
    if m == "GET" and not has_param:
        return f"get{_to_pascal(resource)}"
    if m == "GET" and has_param:
        return f"get{_to_pascal(singular)}ById"
    if m == "POST":
        return f"create{_to_pascal(singular)}"
    if m == "PUT":
        return f"replace{_to_pascal(singular)}"
    if m == "PATCH":
        return f"update{_to_pascal(singular)}"
    if m == "DELETE":
        return f"delete{_to_pascal(singular)}" + ("ById" if has_param else "")
    return f"{m.lower()}{_to_pascal(singular)}"


def _extract_kwarg_string(arglist: str, key: str) -> str:
    m = re.search(rf"{re.escape(key)}\s*=\s*['\"]([^'\"]+)['\"]", arglist)
    return m.group(1) if m else ""


def _extract_tokens(arglist: str) -> list[str]:
    return sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", arglist)))


class FastApiSurfaceAdapter(ApiSurfaceAdapter):
    name = "fastapi"

    def extract_candidates(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> list[dict]:
        router_prefix: dict[str, str] = {}
        app_symbols: set[str] = set()
        include_edges: list[tuple[str, str, str, list[str], str, int]] = []
        routes: list[dict] = []
        alias_to_router: dict[str, str] = {}

        for file_path in sorted(file_paths):
            if not file_path.endswith(".py"):
                continue
            text = read_file(file_path) or ""
            if "FastAPI" not in text and "APIRouter" not in text:
                continue
            lines = text.splitlines()
            for idx, line in enumerate(lines, start=1):
                m_alias = re.search(
                    r"\bfrom\s+[.\w]+\s+import\s+([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?",
                    line,
                )
                if m_alias:
                    src = m_alias.group(1)
                    dst = m_alias.group(2) or src
                    alias_to_router[dst] = src
                m_app = re.search(r"\b([A-Za-z_]\w*)\s*=\s*FastAPI\s*\(", line)
                if m_app:
                    app_symbols.add(m_app.group(1))
                m_router = re.search(r"\b([A-Za-z_]\w*)\s*=\s*APIRouter\s*\(([^)]*)\)", line)
                if m_router:
                    router_prefix[m_router.group(1)] = _extract_kwarg_string(m_router.group(2), "prefix")
                m_inc = re.search(r"\b([A-Za-z_]\w*)\.include_router\s*\(\s*([A-Za-z_]\w*)(?:\s*,\s*([^)]*))?\)", line)
                if m_inc:
                    parent = m_inc.group(1)
                    child = alias_to_router.get(m_inc.group(2), m_inc.group(2))
                    args = m_inc.group(3) or ""
                    include_edges.append((parent, child, _extract_kwarg_string(args, "prefix"), _extract_tokens(args), file_path, idx))
                m_route = re.search(r"@([A-Za-z_]\w*)\.(get|post|put|patch|delete)\s*\(([^)]*)\)", line, re.IGNORECASE)
                if m_route:
                    obj = m_route.group(1)
                    method = m_route.group(2).upper()
                    args = m_route.group(3)
                    path_match = re.search(r"['\"]([^'\"]+)['\"]", args)
                    path = path_match.group(1) if path_match else "/"
                    routes.append(
                        {
                            "router_symbol": obj,
                            "method": method,
                            "route_path": path,
                            "line_start": idx,
                            "source_file": file_path,
                            "tokens": _extract_tokens(args),
                        }
                    )

        # Build reachable prefix chain from FastAPI app roots.
        prefix_map: dict[str, list[tuple[str, tuple[str, ...]]]] = {sym: [("", tuple())] for sym in sorted(app_symbols)}
        changed = True
        for _ in range(10):
            if not changed:
                break
            changed = False
            for parent, child, pref, tokens, _, _ in sorted(include_edges):
                parent_ctx = prefix_map.get(parent, [])
                if not parent_ctx:
                    continue
                curr = set(prefix_map.get(child, []))
                for base_prefix, base_tokens in parent_ctx:
                    next_prefix = _norm_path(f"{base_prefix}/{pref}") if pref else _norm_path(base_prefix or "/")
                    next_tokens = tuple(sorted(set(list(base_tokens) + list(tokens))))
                    item = (next_prefix if next_prefix != "/" else "", next_tokens)
                    if item not in curr:
                        curr.add(item)
                        changed = True
                prefix_map[child] = sorted(list(curr), key=lambda t: (t[0], t[1]))

        out: list[dict] = []
        for row in routes:
            symbol = row["router_symbol"]
            contexts = prefix_map.get(symbol, [("", [])] if symbol in app_symbols else [])
            if not contexts:
                # not reachable from FastAPI app include chain
                continue
            for prefix, inherited_tokens in contexts:
                local_prefix = router_prefix.get(symbol, "")
                path = _norm_path(f"{prefix}/{local_prefix}/{row['route_path']}")
                out.append(
                    {
                        "method": row["method"],
                        "path": path,
                        "source_file": row["source_file"],
                        "line_start": row["line_start"],
                        "router_symbol": symbol,
                        "middleware_tokens": sorted(set(list(inherited_tokens) + list(row.get("tokens", [])))),
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
            tok = " ".join(str(x).lower() for x in c.get("middleware_tokens", []))
            has_jwt = any(x in tok for x in ["oauth2", "jwt", "bearer", "auth", "current_user"])
            has_rbac = any(x in tok for x in ["role", "rbac", "permission", "scope"])
            if has_jwt and has_rbac:
                auth_type = "JWT+RBAC"
            elif has_jwt:
                auth_type = "JWT"
            elif has_rbac:
                auth_type = "RBAC"
            else:
                auth_type = "Public"
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
