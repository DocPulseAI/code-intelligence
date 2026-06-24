import os
import re
from typing import Any, Dict, List
from src.intelligence.evidence.context import AnalysisContext, _split_by_comma_nested, _deduplicate_preserve_order

def _basename(path: str) -> str:
    return os.path.basename(path)

def build_relationships(
    context: AnalysisContext,
    components: List[Dict[str, Any]],
    services: List[Dict[str, Any]],
    repositories: List[Dict[str, Any]],
    routers: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    schema_edges: List[Dict[str, Any]],
    apis: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    relationships: List[Dict[str, Any]] = []

    # Explicit entity schema relations
    relationships.extend(schema_edges)

    file_to_component: Dict[str, str] = {}
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

    # EXPOSES_API: component exposing resolved api endpoint
    for api in apis:
        comp_name = api.get("module") or api.get("component")
        if comp_name:
            endpoint = f'{api["method"]} {api["path"]}'
            relationships.append({
                "type": "EXPOSES_API",
                "from": comp_name,
                "to": endpoint,
            })

    entity_names = {e["name"] for e in entities}
    service_names = {s["name"] for s in services}
    repository_names = {r["name"] for r in repositories}
    
    # service -> repository relationships
    for svc in services:
        content = context.read_file(svc["file"]) or ""
        for repo_name in [str(r.get("name", "")) for r in repositories if r.get("name")]:
            if repo_name in content:
                relationships.append({
                    "type": "uses",
                    "from": svc["name"],
                    "to": repo_name,
                })

    # repository -> entity relationships
    for repo in repositories:
        if repo.get("entity"):
            relationships.append({
                "type": "persists",
                "from": repo["name"],
                "to": repo["entity"],
            })

    # Spring DI: @Autowired field injection
    autowired_rx = re.compile(
        r"@Autowired[\s\r\n]+(?:private|protected|public)?\s*([A-Za-z_][A-Za-z0-9_]*)\s+[A-Za-z_][A-Za-z0-9_]*\s*;",
        re.MULTILINE,
    )

    bean_classes_by_file: Dict[str, List[str]] = {}
    for comp in components:
        for file_path in comp.get("files", []):
            feats = context.features_map.get(file_path, {})
            annotations = feats.get("annotations", [])
            if not any(
                ann for ann in annotations
                if any(tok in ann for tok in ("@Service", "@Repository", "@Component", "@Controller", "@RestController"))
            ):
                continue
            classes = feats.get("classes", []) or []
            if classes:
                bean_classes_by_file.setdefault(file_path, []).extend(str(c) for c in classes)

    for file_path, bean_classes in bean_classes_by_file.items():
        content = context.read_file(file_path) or ""
        if not content:
            continue
        for m in autowired_rx.finditer(content):
            injected_type = m.group(1)
            if not injected_type:
                continue
            for bean in bean_classes:
                relationships.append({
                    "type": "CALLS_SERVICE",
                    "from": bean,
                    "to": injected_type,
                })

    # controller -> service calls
    controllers = set()
    for api in apis:
        if api.get("controller"):
            ctrl = str(api["controller"])
            if "." in ctrl:
                ctrl = ctrl.split(".", 1)[0]
            controllers.add(ctrl)

    for comp in components:
        comp_files = comp.get("files", [])
        for file_path in comp_files:
            content = context.read_file(file_path) or ""
            has_controller = any(c in content for c in controllers)
            if has_controller:
                for svc in service_names:
                    svc_str = str(svc) if svc else ""
                    if svc_str and svc_str in content:
                        for c in controllers:
                            if c in content:
                                relationships.append({
                                    "type": "calls",
                                    "from": c,
                                    "to": svc_str,
                                })

    # Generic heuristics fallback
    for comp in components:
        comp_name = comp["name"]
        comp_files = comp.get("files", [])
        
        for file_path in comp_files:
            # IMPORTS_MODULE
            imports = context.features_map.get(file_path, {}).get("imports", [])
            for imp in imports:
                if isinstance(imp, dict):
                    imp_source = imp.get("source", "")
                else:
                    imp_source = imp
                if not imp_source:
                    continue
                imp_name = os.path.splitext(os.path.basename(imp_source))[0]
                if imp_name:
                    relationships.append({
                        "type": "IMPORTS_MODULE",
                        "from": comp_name,
                        "to": imp_name,
                    })

            # USES_ENTITY / CALLS_SERVICE
            content = context.read_file(file_path) or ""
            if content:
                for ent in entity_names:
                    if not ent:
                        continue
                    defined_here = any(e["name"] == ent and e.get("source_file") == file_path for e in entities)
                    if not defined_here and str(ent) in content:
                        relationships.append({
                            "type": "USES_ENTITY",
                            "from": comp_name,
                            "to": ent,
                        })
                
                for svc in service_names:
                    if not svc:
                        continue
                    defined_here = any(s["name"] == svc and s.get("file") == file_path for s in services)
                    if not defined_here and str(svc) in content:
                        relationships.append({
                            "type": "CALLS_SERVICE",
                            "from": comp_name,
                            "to": svc,
                        })

    # Legacy router/service associations mapping
    for router in routers:
        comp = file_to_component.get(router["source_file"])
        if comp:
            relationships.append({
                "type": "router_serves_component",
                "from": router["name"],
                "to": comp,
            })

    for svc in services:
        comp = file_to_component.get(svc["file"])
        if comp:
            relationships.append({
                "type": "service_used_by_component",
                "from": svc["name"],
                "to": comp,
            })

    for entity in entities:
        comp = file_to_component.get(entity["source_file"])
        if comp:
            relationships.append({
                "type": "entity_used_by_component",
                "from": entity["name"],
                "to": comp,
            })
            
    # Deduplicate relationships
    unique_rels = []
    seen_rels = set()
    for rel in relationships:
        key = (rel.get("type"), rel.get("from"), rel.get("to"), rel.get("relation"))
        if key not in seen_rels:
            seen_rels.add(key)
            unique_rels.append(rel)

    return sorted(unique_rels, key=lambda r: (r.get("from", ""), r.get("to", "")))


def build_quality_warnings(apis: List[Dict[str, Any]], mounts: List[Dict[str, Any]], entities: List[Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []

    if not apis:
        warnings.append("QUALITY_WARNING: endpoint_count=0 (no API endpoints detected)")

    mount_prefixes = sorted(
        {
            str(m.get("mount_path", "")).strip()
            for m in mounts
            if str(m.get("mount_path", "")).strip() and str(m.get("mount_path", "")).strip() != "/"
        }
    )
    mounted_router_files = {
        str(m.get("router_file", "")).strip()
        for m in mounts
        if str(m.get("router_file", "")).strip()
    }
    if mount_prefixes and mounted_router_files:
        unresolved_paths: List[str] = []
        for api in apis:
            src = str(api.get("source_file", "")).strip()
            path = str(api.get("path", "")).strip()
            if src not in mounted_router_files:
                continue
            if not any(path == pfx or path.startswith(pfx.rstrip("/") + "/") for pfx in mount_prefixes):
                unresolved_paths.append(f"{api.get('method', 'GET')} {path}")
        if unresolved_paths:
            sample = ", ".join(sorted(unresolved_paths)[:5])
            warnings.append(
                "QUALITY_WARNING: Some mounted-router endpoints appear unresolved (missing mount prefix): "
                + sample
            )

    schema_entities = [e for e in entities if str(e.get("type", "")).lower() in {"mongoose_model", "prisma_model"}]
    missing_schema_fields = [e.get("name", "") for e in schema_entities if not isinstance(e.get("fields"), dict) or not e.get("fields")]
    if missing_schema_fields:
        sample = ", ".join(sorted(str(x) for x in missing_schema_fields if x)[:5])
        warnings.append(
            "QUALITY_WARNING: schema entities missing field definitions: " + sample
        )

    return warnings
