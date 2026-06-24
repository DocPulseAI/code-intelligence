"""Deterministic repository evidence graph builder.

Constructs a structural graph of the repository from AST-extracted features
and file-structure heuristics. Re-routed to modular analyzers and builders.
"""

from __future__ import annotations
from typing import Any, Callable

from src.intelligence.evidence.context import AnalysisContext
from src.intelligence.collectors.repository_collector import build_components
from src.intelligence.resolvers.api_evidence_builder import build_apis
from src.intelligence.resolvers.route_evidence_builder import build_routers, build_frontend, build_mounts
from src.intelligence.analyzers.symbol_evidence_builder import build_entities
from src.intelligence.analyzers.dependency_evidence_builder import build_services, build_repositories
from src.intelligence.enrichers.evidence_enricher import build_relationships, build_quality_warnings
from src.intelligence.serializers.evidence_serializer import serialize_evidence

def _resolve_import_path(current_file_path: str, import_path: str, all_files: set[str]) -> str | None:
    """Helper wrapper for call_graph_engine compatibility."""
    ctx = AnalysisContext(list(all_files), lambda p: None, {})
    return ctx.resolve_import_path(current_file_path, import_path)

def _resolve_symbol_source(
    symbol_name: str,
    file_path: str,
    all_files: set[str],
    features_map: dict,
    depth: int = 0
) -> tuple[str, str] | None:
    """Helper wrapper for call_graph_engine compatibility."""
    ctx = AnalysisContext(list(all_files), lambda p: None, features_map)
    return ctx.resolve_symbol_source(symbol_name, file_path)

def build_repository_evidence(
    file_paths: list[str],
    read_file: Callable[[str], str | None],
    features_map: dict[str, dict],
    schema_tags_map: dict[str, list[str]],
    tech_stack: dict,
    include_extended: bool = False,
) -> dict[str, Any]:
    """Orchestrates modular builders using an isolated analysis session context."""
    context = AnalysisContext(file_paths, read_file, features_map)
    
    components = build_components(context, tech_stack)
    apis = build_apis(context, components)
    entities, schema_edges = build_entities(context, schema_tags_map, tech_stack)
    services = build_services(context, components)
    repositories = build_repositories(context, components, entities)
    routers = build_routers(context)
    mounts = build_mounts(context)
    frontend = build_frontend(context, tech_stack)
    
    relationships = build_relationships(
        context, components, services, repositories, routers, entities, schema_edges, apis
    )
    quality_warnings = build_quality_warnings(apis, mounts, entities)
    
    return serialize_evidence(
        components=components,
        apis=apis,
        entities=entities,
        services=services,
        repositories=repositories,
        mounts=mounts,
        relationships=relationships,
        frontend_routes=frontend.get("frontend_routes", []),
        routers=routers,
        tech_stack=tech_stack,
        features_map=features_map,
        quality_warnings=quality_warnings,
        include_extended=include_extended
    )
