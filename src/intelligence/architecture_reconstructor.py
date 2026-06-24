"""Architecture Reconstruction Engine."""

import os
from typing import Any, Dict, List, Set


def _detect_pattern(nodes: List[Dict[str, Any]], repository_evidence: Dict[str, Any]) -> str:
    """Detects system architecture pattern."""
    layers = {n["layer"] for n in nodes}
    node_types = {n["type"] for n in nodes}
    
    # Heuristics
    if "presentation" in layers and "application" in layers and "data" in layers:
        if len(repository_evidence.get("modules", [])) > 5:
            return "Modular Monolith"
        return "Layered Architecture"
    
    if "controller" in node_types and "model" in node_types:
        return "MVC"
        
    return "Layered Architecture"  # Default


def reconstruct_architecture(repository_evidence: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transforms raw repository evidence into a semantic architecture reconstruction.
    """
    nodes = []
    edges = []
    
    # 1. Identify Nodes and Layers
    entities = repository_evidence.get("entities", [])
    services = repository_evidence.get("services", [])
    routers = repository_evidence.get("routers", [])
    
    entity_names = {e["name"] for e in entities if "name" in e}
    service_names = {s["name"] for s in services if "name" in s}
    router_names = {r["name"] for r in routers if "name" in r}
    
    seen_nodes = set()
    
    # Data Layer
    for ent in entities:
        node_id = ent["name"]
        if node_id not in seen_nodes:
            seen_nodes.add(node_id)
            nodes.append({
                "id": node_id,
                "type": "model",
                "layer": "data"
            })

    # Application Layer
    for svc in services:
        node_id = svc["name"]
        if node_id not in seen_nodes:
            seen_nodes.add(node_id)
            nodes.append({
                "id": node_id,
                "type": "service",
                "layer": "application"
            })

    # Presentation Layer
    for r in routers:
        node_id = r["name"]
        if node_id not in seen_nodes:
            seen_nodes.add(node_id)
            type_val = "controller" if "controller" in node_id.lower() else "router"
            nodes.append({
                "id": node_id,
                "type": type_val,
                "layer": "presentation"
            })

    # Infrastructure Layer
    tech_str = str(repository_evidence.get("tech_stack", {})).lower()
    if any(db in tech_str for db in ("prisma", "mongoose", "mongodb", "postgres")):
        label = "Database"
        if "prisma" in tech_str: label = "Prisma"
        elif "mongoose" in tech_str: label = "Mongoose"
        
        nodes.append({
            "id": f"{label}Client",
            "type": "database",
            "layer": "infrastructure"
        })
        seen_nodes.add(f"{label}Client")

    # 2. Build Edges
    relationships = repository_evidence.get("relationships", [])
    for rel in relationships:
        u, v = rel.get("from"), rel.get("to")
        if u and v and u in seen_nodes and v in seen_nodes:
            edges.append({
                "from": u,
                "to": v,
                "type": "calls" if u in router_names or u in service_names else "uses"
            })

    # Automatic DB edges
    db_node = next((n["id"] for n in nodes if n["type"] == "database"), None)
    if db_node:
        for svc in service_names:
            if svc in seen_nodes:
                edges.append({"from": svc, "to": db_node, "type": "queries"})

    # 3. Finalize
    unique_edges = []
    seen_edges = set()
    for e in edges:
        key = (e["from"], e["to"])
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(e)

    pattern = _detect_pattern(nodes, repository_evidence)
    layers = sorted(list({n["layer"] for n in nodes}))
    
    return {
        "architecture_reconstruction": {
            "pattern": pattern,
            "layers": layers,
            "components": sorted(nodes, key=lambda n: (n["layer"], n["id"])),
            "edges": sorted(unique_edges, key=lambda e: (e["from"], e["to"]))
        }
    }
