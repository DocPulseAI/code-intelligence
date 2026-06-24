"""Polyglot Dependency Graph Engine."""

import os
from typing import Any, Dict, List, Set, Tuple


def build_dependency_graph(repository_evidence: Dict[str, Any], changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Constructs a module-level dependency graph with cycle detection.
    """
    modules = repository_evidence.get("modules", [])
    
    # 1. Map file to module
    file_to_mod = {}
    mod_nodes = set()
    for mod in modules:
        mod_name = mod.get("name")
        if not mod_name:
            continue
        mod_nodes.add(mod_name)
        for f in mod.get("files", []):
            # Normalize file path for lookup
            norm_f = f.replace("\\", "/")
            file_to_mod[norm_f] = mod_name
            
            
    nodes = set()
    edges_set = set() # (from, to, type)
    
    # 2. Extract dependencies from changes (which hold the parsed features)
    for change in changes:
        file_path = change.get("file")
        features = change.get("features", {})
        deps = features.get("dependencies", [])
        
        source_mod = file_to_mod.get(file_path, file_path)
        # If it's a root file, treat it as its own node or under 'root'
        if not source_mod:
            source_mod = "root"
            
        nodes.add(source_mod)
        
        for dep in deps:
            # Check if internal
            target_mod = None
            edge_type = "external"
            
            # Resolve relative paths
            resolved_dep = dep
            if dep.startswith((".", "..")):
                file_dir = os.path.dirname(file_path)
                resolved_dep = os.path.normpath(os.path.join(file_dir, dep)).replace("\\", "/")
            
            # 2.1 Extension-agnostic lookup
            # Try direct match first, then with common suffixes
            found_mod = file_to_mod.get(resolved_dep)
            if not found_mod:
                for suffix in [".ts", ".js", ".tsx", ".jsx", "/index.ts", "/index.js"]:
                    if (resolved_dep + suffix) in file_to_mod:
                        found_mod = file_to_mod[(resolved_dep + suffix)]
                        break

            if found_mod:
                target_mod = found_mod
                edge_type = "internal"
            elif dep in mod_nodes:
                target_mod = dep
                edge_type = "internal"
            else:
                target_mod = dep
                edge_type = "external"
            
            nodes.add(target_mod)
            if source_mod != target_mod:
                edges_set.add((source_mod, target_mod, edge_type))
                
    # 3. Detect Cycles (Tarjan's simplified for finding any circular paths)
    cycles = _find_all_cycles(nodes, edges_set)
    
    # 4. Format Output
    graph_nodes = []
    for n in sorted(list(nodes)):
        n_type = "internal_module" if n in mod_nodes or "/" in n else "external_package"
        graph_nodes.append({"id": n, "type": n_type})
        
    graph_edges = []
    for f, t, ty in sorted(list(edges_set)):
        graph_edges.append({"from": f, "to": t, "type": ty})
    
    return {
        "nodes": graph_nodes,
        "edges": graph_edges,
        "analysis": {
            "cycle_detected": len(cycles) > 0,
            "cycles": cycles
        }
    }


def _find_all_cycles(nodes: Set[str], edges: Set[Tuple[str, str, str]]) -> List[List[str]]:
    """
    Primitive cycle detection to identify strongly connected components.
    """
    adj = {n: [] for n in nodes}
    for f, t, ty in edges:
        if f in adj:
            adj[f].append(t)
            
    visited = set()
    path = []
    cycles = []
    
    def visit(u, current_path):
        if u in current_path:
            # Cycle found
            start_idx = current_path.index(u)
            cycles.append(current_path[start_idx:] + [u])
            return
        
        if u in visited:
            return
            
        visited.add(u)
        for v in adj.get(u, []):
            if v in nodes:
                visit(v, current_path + [u])
                
    for n in sorted(list(nodes)):
        if n not in visited:
            visit(n, [])
            
    return cycles
