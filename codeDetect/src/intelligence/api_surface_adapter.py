"""Framework adapter interfaces for canonical API route extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ResolvedRoute:
    method: str
    full_path: str
    normalized_key: str
    operation_id: str
    auth_type: str
    endpoint_hash: str
    source_file: str
    line_start: int


class ApiSurfaceAdapter(ABC):
    """Framework adapter contract for API route extraction pipeline."""

    name: str = "unknown"

    @abstractmethod
    def extract_candidates(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def resolve_mounts(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def filter_reachable(
        self,
        candidates: list[dict],
        file_paths: list[str],
        read_file: Callable[[str], str | None],
        tech_stack: Optional[dict] = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def build_resolved_routes(self, candidates: list[dict]) -> list[ResolvedRoute]:
        raise NotImplementedError

