import os
from typing import List, Dict, Any, Set

def build_call_graph(repository_evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Constructs a call graph from the extracted repository evidence.
    Resolves caller and callee to fully qualified names (module.function).
    """
    call_graph = []
    
    # Map from filename to module key
    file_to_mod = {}
    for module in repository_evidence.get("modules", []):
        for file_path in module.get("files", []):
            file_to_mod[file_path] = module["name"]

    # Collect all calls across all files
    all_calls_raw = []
    for file_path, evidence in repository_evidence.get("file_evidence", {}).items():
        module_id = file_to_mod.get(file_path, "unknown")
        file_calls = evidence.get("calls", [])
        
        # Local symbols (functions defined in this file)
        local_functions = set(evidence.get("functions", []))
        local_classes = set(evidence.get("classes", []))
        
        # Imports in this file: map from imported name to source module/file
        # This is a bit tricky; we'll use a heuristic for now.
        imports = evidence.get("imports", [])
        
        for call in file_calls:
            caller_name = f"{module_id}.{call['caller']}"
            callee_raw = call['callee']
            
            # Resolution Heuristic:
            # 1. Is it a local function? -> module.function
            # 2. Is it a property of a local class? -> module.Class.method
            # 3. Is it an imported symbol? -> resolve to source module
            # 4. Handle DB/ORM: prisma.x.y -> prisma.x.y
            
            resolved_callee = _resolve_callee(callee_raw, local_functions, local_classes, imports, module_id)
            
            call_graph.append({
                "caller": caller_name,
                "calls": [resolved_callee],
                "line": call.get("line")
            })

    # Deduplicate and aggregate
    aggregated_graph = {}
    for call_item in call_graph:
        caller = call_item["caller"]
        for callee in call_item["calls"]:
            if caller not in aggregated_graph:
                aggregated_graph[caller] = set()
            aggregated_graph[caller].add(callee)

    result = []
    for caller, callees in sorted(aggregated_graph.items()):
        result.append({
            "caller": caller,
            "calls": sorted(list(callees))
        })

    return result

def _resolve_callee(callee_raw: str, local_functions: Set[str], local_classes: Set[str], imports: List[str], current_module: str) -> str:
    """Helper to resolve callee to a qualified name."""
    
    # 1. Local function check
    if callee_raw in local_functions:
        return f"{current_module}.{callee_raw}"
    
    # 2. Heuristic for common patterns
    if "." in callee_raw:
        parts = callee_raw.split('.')
        base = parts[0]
        
        # Is it a call on a local class?
        if base in local_classes:
            return f"{current_module}.{callee_raw}"
            
        # Is it a common infrastructure call? (prisma, db, etc)
        if base.lower() in ('prisma', 'db', 'entitymanager', 'querybuilder'):
            return callee_raw # Keep as infrastructure call
            
    # 3. Import resolution (simplified)
    # Search for base in imports
    base = callee_raw.split('.')[0] if '.' in callee_raw else callee_raw
    for imp in imports:
        # imp looks like 'import { x } from "..."' or 'import x from "..."'
        if base in imp:
            # Try to guess the module from import string
            # This is very rough; a real resolver would use the dependency graph
            source = _guess_module_from_import(imp)
            if source:
                return f"{source}.{callee_raw.split('.')[-1]}"
    
    return callee_raw # Fallback to raw if unresolvable

def _guess_module_from_import(import_str: str) -> str:
    """Roughly guess module name from import string."""
    import re
    # matches: from "module" or require("module")
    match = re.search(r'from\s+["\']([^"\']+)["\']', import_str)
    if not match:
        match = re.search(r'require\s*\(\s*["\']([^"\']+)["\']\s*\)', import_str)
    
    if match:
        path = match.group(1)
        # Normalize relative paths to a module-like name or keep as is
        if path.startswith('.'):
            return path.split('/')[-1] # Heuristic: use filename
        return path
    return ""
