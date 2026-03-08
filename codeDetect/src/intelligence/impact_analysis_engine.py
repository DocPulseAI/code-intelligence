"""Change Impact Analysis Engine.

Calculates the blast radius and transitive architectural impact of changes
using dependency and call graphs.
"""

from typing import Any, Dict, List, Set


def build_impact_analysis(
    repository_evidence: Dict[str, Any],
    dependency_graph: Dict[str, Any],
    call_graph: List[Dict[str, Any]],
    changes: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Performs transitive impact analysis across components and functions.
    """
    # 1. Identify initial changed components
    changed_components = set()
    for change in changes:
        file_path = change.get("file")
        if not file_path:
            continue
        
        # Map file to component
        for module in repository_evidence.get("modules", []):
            if file_path in module.get("files", []):
                changed_components.add(module["name"])
                break

    # 2. Transitive Impact via Dependency Graph
    affected_components = set()
    dep_nodes = dependency_graph.get("nodes", [])
    dep_edges = dependency_graph.get("edges", [])
    
    # Adjacency list for reverse traversal (who depends on me)
    # edge: { "from": "A", "to": "B" } means A depends on B.
    # We want to find A if B changed.
    dependents_map = {}
    for edge in dep_edges:
        u, v = edge.get("from"), edge.get("to")
        if u and v:
            dependents_map.setdefault(v, set()).add(u)

    queue = list(changed_components)
    visited = set(changed_components)
    
    while queue:
        current = queue.pop(0)
        for dep in dependents_map.get(current, []):
            if dep not in visited:
                visited.add(dep)
                affected_components.add(dep)
                queue.append(dep)

    # 3. Transitive Impact via Call Graph
    # call_graph: [ { "caller": "funcA", "calls": ["funcB"] } ]
    # If funcB changed, funcA is affected.
    callers_map = {}
    for entry in call_graph:
        caller = entry.get("caller")
        for callee in entry.get("calls", []):
            callers_map.setdefault(callee, set()).add(caller)

    # Identify changed functions
    changed_functions = set()
    for change in changes:
        features = change.get("features", {})
        for func in features.get("functions", []):
            # Simplistic: use function name. Improved version would use qualified name.
            changed_functions.add(func)

    # BFS for affected functions
    visited_funcs = set(changed_functions)
    queue_funcs = list(changed_functions)
    affected_funcs = set()

    while queue_funcs:
        curr_func = queue_funcs.pop(0)
        for caller in callers_map.get(curr_func, []):
            if caller not in visited_funcs:
                visited_funcs.add(caller)
                affected_funcs.add(caller)
                queue_funcs.append(caller)
                
                # Map affected function back to component
                # Search in file_evidence
                file_evidence = repository_evidence.get("file_evidence", {})
                for path, feats in file_evidence.items():
                    if caller in feats.get("functions", []):
                        # Find module for this path
                        for module in repository_evidence.get("modules", []):
                            if path in module.get("files", []):
                                if module["name"] not in changed_components:
                                    affected_components.add(module["name"])

    # 4. Calculate Domain-Specific Changes
    # API Changes (does a changed file back an API endpoint?)
    api_endpoints = repository_evidence.get("api_contract", {}).get("endpoints", [])
    api_source_files = {ep.get("source_file") for ep in api_endpoints if ep.get("source_file")}
    changed_files = {c.get("file") for c in changes if c.get("file")}
    api_changes_count = len(api_source_files.intersection(changed_files))

    # DB Changes (does a changed file contain a DB model?)
    db_models = repository_evidence.get("models", [])
    db_source_files = {m.get("file") for m in db_models if m.get("file")}
    db_changes_count = len(db_source_files.intersection(changed_files))

    # Module Changes
    module_changes_count = len(changed_components)

    # Call Chain Depth (max depth of BFS from changed functions)
    # We can use the max level reached in the BFS queue
    # Let's re-run BFS to calculate max depth
    visited_depths = {f: 0 for f in changed_functions}
    queue_funcs_depth = [(f, 0) for f in changed_functions]
    max_call_chain_depth = 0

    while queue_funcs_depth:
        curr_func, depth = queue_funcs_depth.pop(0)
        max_call_chain_depth = max(max_call_chain_depth, depth)
        for caller in callers_map.get(curr_func, []):
            if caller not in visited_depths:
                visited_depths[caller] = depth + 1
                queue_funcs_depth.append((caller, depth + 1))

    # 5. Calculate Impact Score
    impact_score = (
        (api_changes_count * 3) +
        (db_changes_count * 5) +
        (module_changes_count * 2) +
        max_call_chain_depth
    )

    # 6. Determine Severity
    has_api_changes = api_changes_count > 0
    has_db_changes = db_changes_count > 0
    
    # Are there method signature changes? 
    # Determine by looking at the change objects (which might have signature change flags)
    # If our AST diff doesn't explicitly flag signature changes, we can heuristically assume
    # any change to a function's code is at least MINOR if it's exported, otherwise PATCH.
    # For now, we will look for 'METHOD_SIGNATURE_CHANGED' in schema_tags or similar, 
    # or just assume MINOR if functions changed.
    has_signature_changes = False
    for change in changes:
        if "SIGNATURE" in str(change.get("schema_tags", [])).upper():
            has_signature_changes = True
            break
        # Heuristic: if a function changed, and it wasn't just a PATCH severity
        if change.get("severity") in ("MAJOR", "MINOR"):
            has_signature_changes = True

    if has_api_changes or has_db_changes:
        severity = "MAJOR"
    elif has_signature_changes or len(changed_functions) > 0:
        severity = "MINOR"
    else:
        severity = "PATCH"

    blast_radius = len(changed_components.union(affected_components))

    return {
        "impact_analysis": {
            "changed_components": sorted(list(changed_components)),
            "affected_components": sorted(list(affected_components)),
            "blast_radius": blast_radius,
            "risk_score": float(impact_score),
            "severity": severity,
            "metrics": {
                "api_changes": api_changes_count,
                "db_changes": db_changes_count,
                "module_changes": module_changes_count,
                "call_chain_depth": max_call_chain_depth
            }
        }
    }
