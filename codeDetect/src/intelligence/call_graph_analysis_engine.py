"""Call Hierarchy Reasoning Engine."""

from typing import Any, Dict, List, Set


def analyze_call_graph(call_graph: List[Dict[str, Any]], repository_evidence: Dict[str, Any]) -> Dict[str, Any]:
    """
    Performs hierarchy reasoning on the call graph.
    """
    # 1. Build adjacency and reverse-adjacency maps
    adj = {}   # caller -> set of callees
    radj = {}  # callee -> set of callers (for in-degree calc)
    for entry in call_graph:
        caller = entry["caller"]
        if caller not in adj:
            adj[caller] = set()
        for callee in entry.get("calls", []):
            adj[caller].add(callee)
            if callee not in radj:
                radj[callee] = set()
            radj[callee].add(caller)

    all_nodes = set(adj.keys())
    for callees in adj.values():
        all_nodes.update(callees)

    # 2. Identify Entry Points
    # Strategy A: Zero-in-degree internal callers (not called by anyone)
    internal_callers = set(adj.keys())  # Only nodes that *call* others
    entry_points_zero_in = {
        n for n in internal_callers
        if n not in radj or len(radj[n]) == 0
    }

    # Strategy B: Match functions by name to API endpoint paths
    api_matched = set()
    api_endpoints = repository_evidence.get("api_contract", {}).get("endpoints", [])
    # Extract unique (component, function_hint) pairs from endpoints
    for ep in api_endpoints:
        comp = ep.get("component", "")
        path = ep.get("path", "")
        path_parts = [p for p in path.split('/') if p and not p.startswith(':')]
        for part in path_parts:
            part_lower = part.lower()
            # Look for module.function where function name contains path part
            for node in internal_callers:
                if node.startswith(f"{comp}.") and part_lower in node.lower():
                    api_matched.add(node)

    entry_points = sorted(list(entry_points_zero_in | api_matched))

    # 3. Trace Execution Paths (Entry Point -> DB/Infra)
    infra_keywords = ("prisma", "mongoose", "db.", "client.", "mongodb", "entitymanager", "repository")
    execution_paths = []

    def find_paths_to_infra(u, current_path, depth):
        if depth > 6 or len(execution_paths) >= 50:
            return

        # Check if current node is infra
        if any(kw in u.lower() for kw in infra_keywords):
            execution_paths.append(current_path + [u])
            return

        for v in adj.get(u, []):
            if v not in current_path:  # Avoid cycles
                find_paths_to_infra(v, current_path + [u], depth + 1)

    for entry in entry_points:
        find_paths_to_infra(entry, [], 0)

    # 4. Dead Functions: defined but never called by any other function
    all_defined = set()
    for file_path, ev in repository_evidence.get("file_evidence", {}).items():
        module = "unknown"
        for mod in repository_evidence.get("modules", []):
            if file_path in mod.get("files", []):
                module = mod["name"]
                break
        for func in ev.get("functions", []):
            all_defined.add(f"{module}.{func}")

    called_functions: Set[str] = set()
    for callees in adj.values():
        called_functions.update(callees)

    dead_functions = sorted(list(all_defined - called_functions))

    # 5. Max Call Depth from any entry point
    memo: Dict[str, int] = {}

    def get_max_depth(u, visited):
        if u in memo:
            return memo[u]
        if u in visited:
            return 0
        visited.add(u)
        max_d = 0
        for v in adj.get(u, []):
            max_d = max(max_d, 1 + get_max_depth(v, visited))
        visited.remove(u)
        memo[u] = max_d
        return max_d

    max_depth = max((get_max_depth(e, set()) for e in entry_points), default=0)

    return {
        "call_graph_analysis": {
            "entry_points": entry_points,
            "execution_paths": execution_paths,
            "dead_functions": dead_functions[:100],
            "max_call_depth": max_depth
        }
    }
