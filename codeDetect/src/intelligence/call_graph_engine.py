import os
from typing import List, Dict, Any, Set
from src.intelligence.repository_evidence import _resolve_import_path, _resolve_symbol_source

def get_file_layer(file_path: str) -> str:
    """Classifies a file path into its layer name."""
    lowered = file_path.lower().replace("\\", "/")
    if "frontend_backup" in lowered or "backup" in lowered:
        return "backup"
    if "/frontend/" in lowered or "/client/" in lowered or "/ui/" in lowered or "/web/" in lowered:
        return "frontend"
    if "/routes/" in lowered or ".routes." in lowered or lowered.endswith("/routes"):
        return "route"
    if "/controllers/" in lowered or ".controller." in lowered or lowered.endswith("/controllers"):
        return "controller"
    if "/services/" in lowered or ".service." in lowered or lowered.endswith("/services"):
        return "service"
    if "/repositories/" in lowered or ".repository." in lowered or lowered.endswith("/repositories"):
        return "repository"
    if "/models/" in lowered or ".model." in lowered or lowered.endswith("/models"):
        return "model"
    if "/middleware/" in lowered or ".middleware." in lowered:
        return "middleware"
    if "/validators/" in lowered or ".validator." in lowered:
        return "validator"
    if "/utils/" in lowered or ".utils." in lowered or "/util/" in lowered or ".util." in lowered:
        return "utility"
    if "/config/" in lowered or ".config." in lowered:
        return "config"
    return "other"

def build_call_graph(repository_evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Constructs a call graph from the extracted repository evidence.
    Resolves caller and callee to fully qualified names (module.layer.function).
    """
    call_graph = []
    
    # Map from filename to module key and layer
    file_to_mod = {}
    file_to_layer = {}
    for module in repository_evidence.get("modules", []):
        for file_path in module.get("files", []):
            file_to_mod[file_path] = module["name"]
            file_to_layer[file_path] = get_file_layer(file_path)

    features_map = repository_evidence.get("file_evidence", {})
    all_files = set(features_map.keys())

    # Collect all calls across all files
    for file_path, evidence in features_map.items():
        module_id = file_to_mod.get(file_path, "unknown")
        caller_layer = file_to_layer.get(file_path, "other")
        file_calls = evidence.get("calls", [])
        
        # Local symbols (functions defined in this file)
        local_functions = set(evidence.get("functions", []))
        local_classes = set(evidence.get("classes", []))
        
        imports = evidence.get("imports", [])
        
        for call in file_calls:
            caller_name = f"{module_id}.{caller_layer}.{call['caller']}"
            callee_raw = call['callee']
            
            resolved_callee = _resolve_callee(
                callee_raw, local_functions, local_classes, imports,
                file_path, all_files, features_map, file_to_mod, file_to_layer, module_id, caller_layer
            )
            
            call_graph.append({
                "caller": caller_name,
                "calls": [resolved_callee],
                "line": call.get("line")
            })

    # Inject virtual caller edges from API routes to controller and middleware handlers
    file_evidence = repository_evidence.get("file_evidence", {})
    for ep in repository_evidence.get("apis", []):
        method = ep.get("method")
        path = ep.get("path")
        if not method or not path:
            continue

        caller_name = f"API: {method.upper()} {path}"
        
        # 1. Resolve controller
        controller_raw = ep.get("controller")
        if controller_raw:
            resolved_controller = _resolve_api_symbol(controller_raw, file_evidence, file_to_mod, file_to_layer)
            if resolved_controller:
                call_graph.append({
                    "caller": caller_name,
                    "calls": [resolved_controller],
                    "line": ep.get("line")
                })
                
        # 2. Resolve middleware
        for mw in ep.get("middleware", []):
            resolved_mw = _resolve_api_symbol(mw, file_evidence, file_to_mod, file_to_layer)
            if resolved_mw:
                call_graph.append({
                    "caller": caller_name,
                    "calls": [resolved_mw],
                    "line": ep.get("line")
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


def _normalize_controller_name(name: str) -> str:
    """Normalize a controller name or prefix for consistent matching."""
    name_lower = name.lower()
    name_clean = name_lower.replace('.', '').replace('_', '').replace('-', '')
    if name_clean.endswith('controller'):
        name_clean = name_clean[:-10]
    return name_clean


def _resolve_api_symbol(symbol_raw: str, file_evidence: dict, file_to_mod: dict, file_to_layer: dict) -> str | None:
    if not symbol_raw:
        return None

    if "." in symbol_raw:
        prefix, func_name = symbol_raw.rsplit(".", 1)
    else:
        prefix, func_name = None, symbol_raw

    resolved_module = None
    resolved_layer = "other"

    if prefix:
        # 1. Match prefix to a file name in file_evidence using normalized names
        matching_files = []
        normalized_prefix = _normalize_controller_name(prefix)
        for file_path in file_evidence.keys():
            base = os.path.basename(file_path)
            name_without_ext, _ = os.path.splitext(base)
            if _normalize_controller_name(name_without_ext) == normalized_prefix:
                matching_files.append(file_path)

        # 2. Filter match files by function presence
        files_with_func = [
            fp for fp in matching_files
            if func_name in file_evidence[fp].get("functions", [])
        ]

        if len(files_with_func) == 1:
            resolved_module = file_to_mod.get(files_with_func[0])
            resolved_layer = file_to_layer.get(files_with_func[0], "other")
        elif len(matching_files) == 1:
            resolved_module = file_to_mod.get(matching_files[0])
            resolved_layer = file_to_layer.get(matching_files[0], "other")
        elif len(files_with_func) > 1:
            # Disambiguate by matching exact case first
            exact_match = [
                fp for fp in files_with_func
                if _normalize_controller_name(os.path.splitext(os.path.basename(fp))[0]) == normalized_prefix
            ]
            if exact_match:
                resolved_module = file_to_mod.get(exact_match[0])
                resolved_layer = file_to_layer.get(exact_match[0], "other")
            else:
                resolved_module = file_to_mod.get(files_with_func[0])
                resolved_layer = file_to_layer.get(files_with_func[0], "other")

    if not resolved_module:
        # 3. Unambiguous match by function name across all files
        files_defining_func = []
        for file_path, ev in file_evidence.items():
            if func_name in ev.get("functions", []):
                files_defining_func.append(file_path)
        if len(files_defining_func) == 1:
            resolved_module = file_to_mod.get(files_defining_func[0])
            resolved_layer = file_to_layer.get(files_defining_func[0], "other")

    if resolved_module:
        return f"{resolved_module}.{resolved_layer}.{func_name}"
    return None


def _resolve_callee(callee_raw: str, local_functions: Set[str], local_classes: Set[str],
                    imports: List[Any], file_path: str, all_files: Set[str],
                    features_map: Dict[str, Any], file_to_mod: Dict[str, str],
                    file_to_layer: Dict[str, str],
                    current_module: str, current_layer: str) -> str:
    """Helper to resolve callee to a qualified name."""
    
    # 1. Local function check
    if callee_raw in local_functions:
        return f"{current_module}.{current_layer}.{callee_raw}"
    
    # 2. Heuristic for common patterns
    if "." in callee_raw:
        parts = callee_raw.split('.')
        base = parts[0]
        prop = parts[1]
        
        # Check for local instance calls (this.someMethod or self.someMethod)
        if base in ("this", "self"):
            return f"{current_module}.{current_layer}.{prop}"
            
        # Is it a call on a local class?
        if base in local_classes:
            return f"{current_module}.{current_layer}.{callee_raw}"
            
        # Is it a common infrastructure call? (prisma, db, etc)
        if base.lower() in ('prisma', 'db', 'entitymanager', 'querybuilder'):
            return callee_raw
            
        # Check if the base is imported
        resolved = _resolve_symbol_source(base, file_path, all_files, features_map)
        if resolved:
            declaring_file, orig_name = resolved
            target_module = file_to_mod.get(declaring_file)
            target_layer = file_to_layer.get(declaring_file, "other")
            if target_module:
                return f"{target_module}.{target_layer}.{prop}"
            else:
                base_name = os.path.basename(declaring_file).split('.')[0]
                return f"{base_name}.{target_layer}.{prop}"
    else:
        # Check if the unqualified function is imported
        resolved = _resolve_symbol_source(callee_raw, file_path, all_files, features_map)
        if resolved:
            declaring_file, orig_name = resolved
            target_module = file_to_mod.get(declaring_file)
            target_layer = file_to_layer.get(declaring_file, "other")
            if target_module:
                return f"{target_module}.{target_layer}.{orig_name}"
            else:
                base_name = os.path.basename(declaring_file).split('.')[0]
                return f"{base_name}.{target_layer}.{orig_name}"

    # 3. Fallback to legacy import resolution for Python/Java raw import lists
    base = callee_raw.split('.')[0] if '.' in callee_raw else callee_raw
    for imp in imports:
        if isinstance(imp, str) and base in imp:
            source, layer = _guess_module_and_layer_from_import(imp)
            if source:
                return f"{source}.{layer}.{callee_raw.split('.')[-1]}"
    
    return callee_raw # Fallback to raw if unresolvable

def _guess_module_and_layer_from_import(import_str: str) -> tuple[str, str]:
    """Guess module and layer name from import string."""
    import re
    # matches: from "module" or require("module")
    match = re.search(r'from\s+["\']([^"\']+)["\']', import_str)
    if not match:
        match = re.search(r'require\s*\(\s*["\']([^"\']+)["\']\s*\)', import_str)
    
    if match:
        path = match.group(1)
        layer = get_file_layer(path)
        # Normalize relative paths to a module-like name or keep as is
        if path.startswith('.'):
            return path.split('/')[-1].split('.')[0], layer
        return path, layer
    return "", "other"


