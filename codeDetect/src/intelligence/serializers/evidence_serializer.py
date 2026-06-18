import os
from typing import Any, Dict, List, Optional
from src.intelligence.contracts.evidence_models import (
    RepositoryEvidence,
    ModuleEvidence,
    ApiEvidence,
    SymbolEvidence,
    DependencyEvidence,
    MountEvidence,
    RelationshipEvidence,
    FrontendRouteEvidence,
    RouteEvidence,
)

def serialize_evidence(
    components: List[Dict[str, Any]],
    apis: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    services: List[Dict[str, Any]],
    repositories: List[Dict[str, Any]],
    mounts: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
    frontend_routes: List[Dict[str, Any]],
    routers: List[Dict[str, Any]],
    tech_stack: Dict[str, Any],
    features_map: Dict[str, Dict[str, Any]],
    quality_warnings: List[str],
    include_extended: bool = False
) -> Dict[str, Any]:
    # Format tech_stack
    formatted_tech_stack = {
        "backend": [],
        "frontend": [],
        "database": [],
        "infrastructure": []
    }
    
    if tech_stack.get("backend_framework"):
        formatted_tech_stack["backend"].append(tech_stack.get("backend_framework"))
    if tech_stack.get("frontend_framework"):
        formatted_tech_stack["frontend"].append(tech_stack.get("frontend_framework"))
    if tech_stack.get("database"):
        formatted_tech_stack["database"].append(tech_stack.get("database"))
    if tech_stack.get("orm"):
        formatted_tech_stack["database"].append(tech_stack.get("orm"))
    if tech_stack.get("infra"):
        formatted_tech_stack["infrastructure"].extend(tech_stack.get("infra", []))

    # Map to Pydantic objects for schema validation
    modules_validated = [ModuleEvidence(**comp) for comp in components]
    apis_validated = [ApiEvidence(**api) for api in apis]
    entities_validated = [SymbolEvidence(**ent) for ent in entities]
    services_validated = [DependencyEvidence(**svc) for svc in services]
    repositories_validated = [DependencyEvidence(**repo) for repo in repositories]
    
    # MountEvidence mapping - make sure optional fields have default values
    mounts_validated = []
    for m in mounts:
        mounts_validated.append(MountEvidence(
            mount_path=m.get("mount_path", ""),
            mounted_router=m.get("mounted_router", ""),
            parent=m.get("parent", ""),
            router=m.get("router", ""),
            router_file=m.get("router_file", ""),
            path=m.get("path", ""),
            source_file=m.get("source_file", ""),
            line=m.get("line", 0),
        ))

    relationships_validated = []
    for rel in relationships:
        relationships_validated.append(RelationshipEvidence(
            type=rel.get("type", ""),
            from_=rel.get("from", ""),
            to=rel.get("to", ""),
            relation=rel.get("relation"),
            field=rel.get("field"),
        ))

    frontend_routes_validated = [FrontendRouteEvidence(**fr) for fr in frontend_routes]
    routers_validated = [RouteEvidence(**r) for r in routers]

    # Validate using core RepositoryEvidence model
    evidence_model = RepositoryEvidence(
        tech_stack=formatted_tech_stack,
        modules=modules_validated,
        apis=apis_validated,
        entities=entities_validated,
        services=services_validated,
        repositories=repositories_validated,
        mounts=mounts_validated,
        relationships=relationships_validated,
        frontend_routes=frontend_routes_validated,
        components=modules_validated,
        routers=routers_validated,
    )

    # Dump to nested dictionary structure with aliases (from -> from_)
    result = evidence_model.model_dump(by_alias=True)

    # Ensure components and modules refer to the exact same list object
    result["components"] = result["modules"]

    # Prune null/default attributes to match original output structure precisely
    for api in result.get("apis", []):
        if api.get("auth_required") is not True:
            api.pop("auth_required", None)

    for ent in result.get("entities", []):
        if not ent.get("fields"):
            ent.pop("fields", None)

    for rel in result.get("relationships", []):
        if rel.get("relation") is None:
            rel.pop("relation", None)
        if rel.get("field") is None:
            rel.pop("field", None)

    # Extended details
    if include_extended:
        # Extract Router Mount Graph
        api_mounts = []
        for m in mounts:
            router_val = m.get("router_file", "")
            if router_val:
                router_val = os.path.basename(router_val)
            else:
                router_val = m.get("router", "")
            
            api_mounts.append({
                "base_path": m.get("mount_path", ""),
                "router": router_val
            })
        api_mounts = [dict(t) for t in {tuple(d.items()) for d in api_mounts}]
        api_mounts = sorted(api_mounts, key=lambda m: (m.get("base_path", ""), m.get("router", "")))

        result["api_mounts"] = api_mounts
        result["file_evidence"] = features_map
        result["quality_warnings"] = quality_warnings

    return result
