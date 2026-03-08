"""Structural Dependency Analysis Engine."""

from typing import Any, Dict, List, Set


def analyze_dependencies(dependency_graph: Dict[str, Any]) -> Dict[str, Any]:
    """
    Performs structural analysis on the dependency graph.
    """
    nodes = dependency_graph.get("nodes", [])
    edges = dependency_graph.get("edges", [])
    
    # Initialize metrics
    metrics = {n["id"]: {"fan_in": 0, "fan_out": 0, "instability": 0.0, "centrality": 0.0} for n in nodes}
    
    # 1. Compute Fan-in and Fan-out
    for edge in edges:
        u, v = edge.get("from"), edge.get("to")
        if u in metrics and v in metrics:
            metrics[u]["fan_out"] += 1
            metrics[v]["fan_in"] += 1
            
    # 2. Compute Instability and Centrality
    total_nodes = len(nodes)
    for node_id, m in metrics.items():
        # Instability = out / (in + out)
        denom = m["fan_in"] + m["fan_out"]
        if denom > 0:
            m["instability"] = round(m["fan_out"] / denom, 2)
            
        # Degree Centrality (normalized)
        if total_nodes > 1:
            m["centrality"] = round(denom / (total_nodes - 1), 2)

    # 3. Identify Patterns
    core_modules = []
    high_coupling_modules = []
    
    for node_id, m in metrics.items():
        # Core: High fan-in, low instability
        if m["fan_in"] > 2 and m["instability"] < 0.5:
            core_modules.append(node_id)
        
        # High Coupling: High fan-out
        if m["fan_out"] > 5:
            high_coupling_modules.append(node_id)

    # 4. Dependency Depth (Simple longest path estimate)
    # Since we have cycles, we use a BFS/DFS with visited check to find max depth from each node
    depths = {}
    adj = {n["id"]: [] for n in nodes}
    for e in edges:
        adj[e["from"]].append(e["to"])

    def get_max_depth(u, visited):
        if u in depths: return depths[u]
        if u in visited: return 0
        
        visited.add(u)
        max_d = 0
        for v in adj.get(u, []):
            max_d = max(max_d, 1 + get_max_depth(v, visited))
        visited.remove(u)
        depths[u] = max_d
        return max_d

    max_system_depth = 0
    for n in nodes:
        max_system_depth = max(max_system_depth, get_max_depth(n["id"], set()))

    return {
        "dependency_analysis": {
            "core_modules": sorted(core_modules),
            "high_coupling_modules": sorted(high_coupling_modules),
            "cycles": dependency_graph.get("analysis", {}).get("cycles", []),
            "graph_metrics": {
                "max_dependency_depth": max_system_depth,
                "node_metrics": metrics
            }
        }
    }
