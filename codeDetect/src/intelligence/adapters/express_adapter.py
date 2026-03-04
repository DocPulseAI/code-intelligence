"""Express adapter for canonical route extraction."""

from __future__ import annotations

from typing import Callable, Optional

from src.intelligence.api_surface_adapter import ApiSurfaceAdapter, ResolvedRoute


class ExpressApiSurfaceAdapter(ApiSurfaceAdapter):
    name = "express"

    def __init__(self, express_resolver):
        self._express_resolver = express_resolver

    def extract_candidates(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> list[dict]:
        return list(candidates)

    def resolve_mounts(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> dict:
        return self._express_resolver(candidates, file_paths, read_file, tech_stack or {})

    def filter_reachable(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> list[dict]:
        return list(candidates)

    def build_resolved_routes(self, candidates: list[dict]) -> list[ResolvedRoute]:
        out: list[ResolvedRoute] = []
        for row in candidates:
            method = str(row.get("method", "GET")).upper()
            path = str(row.get("path", "/"))
            out.append(
                ResolvedRoute(
                    method=method,
                    full_path=path,
                    normalized_key=str(row.get("normalized_key", f"{method.lower()} {path.lower()}")),
                    operation_id=str(row.get("operation_id", "getResource")),
                    auth_type=str(row.get("resolved_auth_type", "Public")),
                    endpoint_hash=str(row.get("endpoint_hash", "")),
                    source_file=str(row.get("source_file", "")),
                    line_start=int(row.get("line_start", 0) or 0),
                )
            )
        return out

