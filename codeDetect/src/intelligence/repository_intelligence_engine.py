"""Repository Intelligence Reasoning Engine.

Performs deep architectural analysis, precise call graph correction, dead function detection,
and explicit change impact propagation using only repository intelligence.
"""

from typing import Any, Dict, List, Set


def analyze_repository_intelligence(
    api_contract: Dict[str, Any],
    dependency_graph: Dict[str, Any],
    call_graph: Dict[str, Any],
    architecture_reconstruction: Dict[str, Any],
    changes: List[str],
    database_models: List[Dict[str, Any]],
    dependency_analysis: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Produces deeper architectural insights across four tasks.
    """
    
    # Task 1: Architecture Analysis
    layer_violations = []
    
    # Map components to their layers
    component_layers = {}
    for comp in architecture_reconstruction.get("components", []):
        component_layers[comp.get("id", "Unknown")] = comp.get("layer", "Unknown")
        
    layer_hierarchy = {
        "Presentation": 1,
        "Application": 2,
        "Data": 3,
        "Infrastructure": 4,
        "Unknown": 5
    }
    
    for edge in architecture_reconstruction.get("edges", []):
        source = edge.get("from")
        target = edge.get("to")
        s_layer = component_layers.get(source)
        t_layer = component_layers.get(target)
        
        if s_layer and t_layer and s_layer != "Unknown" and t_layer != "Unknown":
            if layer_hierarchy.get(s_layer, 5) > layer_hierarchy.get(t_layer, 5):
                layer_violations.append({
                    "violation_type": "Upward Dependency",
                    "description": f"{s_layer} component '{source}' unlawfully depends on {t_layer} component '{target}'"
                })

    architecture_insights = {
        "layer_violations": layer_violations,
        "tightly_coupled_modules": dependency_analysis.get("high_coupling_modules", []),
        "module_centrality": dependency_analysis.get("core_modules", [])
    }
    
    # Additional Dependency Insights combined with architecture
    cycles = dependency_analysis.get("cycles", [])
    high_fan_in = []
    unstable_modules = []
    node_metrics = dependency_analysis.get("graph_metrics", {}).get("node_metrics", {})
    for mod, metrics in node_metrics.items():
        if metrics.get("fan_in", 0) >= 3:
            high_fan_in.append(mod)
        if metrics.get("instability", 0.0) >= 0.8:
            unstable_modules.append(mod)

    architecture_insights["dependency_insights"] = {
        "circular_dependencies": cycles,
        "high_fan_in_modules": sorted(high_fan_in),
        "unstable_modules": sorted(unstable_modules)
    }

    # Task 2 & 3: Call Graph Correction and Dead Function Detection
    # 1. Identify true entry points from api_contract
    api_matched = set()
    endpoints = api_contract.get("endpoints", [])
    
    # Build the call graph adjacency
    if isinstance(call_graph, list):
        functions = call_graph
    else:
        functions = call_graph.get("functions", [])
        
    func_names = set()
    for f in functions:
        if f.get("caller"):
            func_names.add(f.get("caller"))
        for call in f.get("calls", []):
            func_names.add(call)
            
    func_names.discard("")
    
    adj_out = {f: [] for f in func_names}
    adj_in = {f: [] for f in func_names}
    
    for f in functions:
        fname = f.get("caller")
        if not fname: continue
        for call in f.get("calls", []):
            if call in func_names:
                adj_out[fname].append(call)
                adj_in[call].append(fname)
                
    # Match functions by API path components - Strategy C
    entry_points = [
        node for node in func_names
        if node.startswith("API:")
    ]
    
    # Fallback to zero-in-degree nodes if no API nodes exist in the graph
    if not entry_points:
        entry_points = [
            node for node in func_names
            if not adj_in[node]
        ]
        
    matched_entry_points = entry_points

    # Trace execution paths
    reachable_functions = set()
    def bfs_traverse(starts):
        queue = list(starts)
        visited = set()
        paths = []
        while queue:
            curr = queue.pop(0)
            if curr not in visited:
                visited.add(curr)
                # Keep exploring children
                children = adj_out.get(curr, [])
                queue.extend(children)
                # A path is just one level jump for this simple representation
                for c in children:
                    paths.append(f"{curr} -> {c}")
        return visited, paths

    reachable_functions, execution_paths = bfs_traverse(matched_entry_points)
    
    # 3. Dead Function Detection
    # A function is dead if it is not called by anyone (in == 0) AND is NOT reachable from an API endpoint
    # OR if it's completely isolated from the main spanning tree.
    # The prompt explicitly says: it is dead ONLY if = not called by another function AND not reachable from API endpoint.
    true_dead_functions = []
    for f in func_names:
        if len(adj_in.get(f, [])) == 0 and f not in reachable_functions:
            true_dead_functions.append(f)

    corrected_call_graph = {
        "true_entry_points": sorted(matched_entry_points),
        "execution_paths": sorted(list(set(execution_paths)))[:100] # Cap to prevent massive JSON string array deterministically
    }
    
    dead_function_analysis = {
        "true_dead_functions": sorted(true_dead_functions),
        "count": len(true_dead_functions)
    }

    # Task 4: Change Impact Propagation
    # For every changed file -> traverse dependency graph -> traverse call graph
    # We need the dependency graph edges
    dep_nodes = dependency_graph.get("nodes", [])
    dep_edges = dependency_graph.get("edges", [])
    
    # Build Dep Adjacency (Forward - who depends ON me)
    dep_adj_out = {n["id"]: [] for n in dep_nodes}
    for e in dep_edges:
        dep_adj_out[e["from"]].append(e["to"])
        
    # Map changed files to modules (heuristics: file path basename without extension)
    changed_modules = []
    import os
    for c in changes:
        c_file = c.get("file", str(c)) if isinstance(c, dict) else str(c)
        # Simplistic mapping: extract folder name or base name
        base = os.path.basename(c_file).split('.')[0]
        if base != "index" and base != "":
            changed_modules.append(base)
            
    # Propagate through deps
    affected_modules = set()
    queue = list(changed_modules)
    visited_deps = set()
    while queue:
        curr = queue.pop(0)
        # We try to match curr to a known module ID
        matched_mods = [n["id"] for n in dep_nodes if curr in n["id"].lower()]
        for mm in matched_mods:
            if mm not in visited_deps:
                visited_deps.add(mm)
                affected_modules.add(mm)
                queue.extend(dep_adj_out.get(mm, []))

    # Construct affected functions
    # A file changes, so any function IN that file changes.
    # We find what callers use those changed functions.
    changed_functions = []
    
    # functions may not have file metadata directly on the node in this call graph schema,
    # or the changed files logic from the prompt implies simply matching module names.
    for f in func_names:
        # Simplistic heuristic: if caller string contains the basename of a changed module
        f_lower = f.lower()
        for base in changed_modules:
            if base in f_lower:
                changed_functions.append(f)
                break
                
    # Traverse call graph upwards (who calls me)
    affected_callers = set()
    q_calls = list(changed_functions)
    v_calls = set()
    while q_calls:
        curr = q_calls.pop(0)
        if curr not in v_calls:
            v_calls.add(curr)
            affected_callers.add(curr)
            q_calls.extend(adj_in.get(curr, []))

    blast_radius = len(affected_modules) + len(affected_callers)

    impact_propagation = {
        "changed_files": changes,
        "affected_modules": sorted(list(affected_modules)),
        "affected_callers": sorted(list(affected_callers)),
        "blast_radius": blast_radius
    }

    return {
        "corrected_call_graph": corrected_call_graph,
        "dead_function_analysis": dead_function_analysis,
        "impact_propagation": impact_propagation,
        "architecture_insights": architecture_insights
    }
