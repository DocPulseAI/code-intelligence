import os
import re
from typing import Callable, Dict, Any, Set, Tuple, List, Optional

def _split_by_comma_nested(text: str) -> List[str]:
    text = text.strip()
    if text.startswith('[') and text.endswith(']'):
        text = text[1:-1].strip()
    
    parts = []
    current = []
    paren_depth = 0
    bracket_depth = 0
    for char in text:
        if char == '(':
            paren_depth += 1
        elif char == ')':
            paren_depth -= 1
        elif char == '[':
            bracket_depth += 1
        elif char == ']':
            bracket_depth -= 1
        
        if char == ',' and paren_depth == 0 and bracket_depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    
    final_parts = []
    for p in parts:
        if p.startswith('[') and p.endswith(']'):
            final_parts.extend(_split_by_comma_nested(p))
        else:
            if p:
                final_parts.append(p)
    return final_parts

def _deduplicate_preserve_order(lst: List[Any]) -> List[Any]:
    seen = set()
    result = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

class AnalysisContext:
    """Isolated execution context for a single run of repository evidence construction.

    Contains session-level caching for:
      1. File content reads (preventing repeated disk I/O)
      2. Symbol source resolutions (preventing repeated deep AST recursive calls)
    """
    def __init__(
        self,
        file_paths: List[str],
        read_file: Callable[[str], str | None],
        features_map: Dict[str, Dict[str, Any]]
    ):
        self.file_paths = sorted(str(p) for p in file_paths if str(p).strip())
        self.all_files = set(self.file_paths)
        self.raw_read_file = read_file
        self.features_map = features_map
        
        # Caches
        self.file_content_cache: Dict[str, str | None] = {}
        self.symbol_resolution_cache: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}

    def read_file(self, path: str) -> str | None:
        """Read file content with session-level caching."""
        if path not in self.file_content_cache:
            self.file_content_cache[path] = self.raw_read_file(path)
        return self.file_content_cache[path]

    def resolve_import_path(self, current_file_path: str, import_path: str) -> Optional[str]:
        if not import_path.startswith('.'):
            if '/' in import_path:
                filename = import_path.split('/')[-1]
                for f in self.all_files:
                    f_base = os.path.splitext(os.path.basename(f))[0]
                    if f_base == filename:
                        return f
            return None

        base_dir = os.path.dirname(current_file_path)
        candidate_raw = os.path.normpath(os.path.join(base_dir, import_path))
        
        for ext in ['', '.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs']:
            cand = candidate_raw + ext
            if cand in self.all_files:
                return cand
                
        for index_name in ['index.js', 'index.ts', 'index.jsx', 'index.tsx']:
            cand = os.path.join(candidate_raw, index_name)
            if cand in self.all_files:
                return cand
                
        return None

    def resolve_symbol_source_multi(
        self,
        symbol_name: str,
        file_path: str,
        visited: Optional[Set[Tuple[str, str]]] = None
    ) -> List[Tuple[str, str]]:
        if visited is None:
            cache_key = (file_path, symbol_name)
            if cache_key in self.symbol_resolution_cache:
                return self.symbol_resolution_cache[cache_key]
            visited = set()
            res = self._resolve_symbol_source_multi_impl(symbol_name, file_path, visited)
            self.symbol_resolution_cache[cache_key] = res
            return res
        return self._resolve_symbol_source_multi_impl(symbol_name, file_path, visited)

    def _resolve_symbol_source_multi_impl(
        self,
        symbol_name: str,
        file_path: str,
        visited: Set[Tuple[str, str]]
    ) -> List[Tuple[str, str]]:
        symbol_clean = symbol_name.split("(")[0].strip() if "(" in symbol_name else symbol_name
        if not symbol_clean or symbol_clean.startswith("("):
            return []
            
        if "." in symbol_clean:
            base_symbol = symbol_clean.split(".")[0].strip()
        else:
            base_symbol = symbol_clean
            
        if (file_path, base_symbol) in visited:
            return []
        visited.add((file_path, base_symbol))
        
        feats = self.features_map.get(file_path, {})
        
        # 1. Check if base_symbol is a local variable
        variables = feats.get("variables", [])
        local_var = None
        for var in variables:
            if isinstance(var, dict) and var.get("name") == base_symbol:
                local_var = var
                break
                
        if local_var:
            kind = local_var.get("kind")
            if kind == "call" and local_var.get("callee") == "require":
                pass
            elif kind == "array":
                results = []
                for elem in local_var.get("elements", []):
                    results.extend(self.resolve_symbol_source_multi(elem, file_path, visited.copy()))
                return results
            elif kind == "call":
                return self.resolve_symbol_source_multi(local_var.get("callee"), file_path, visited.copy())
            elif kind == "identifier":
                return self.resolve_symbol_source_multi(local_var.get("value"), file_path, visited.copy())
            else:
                val = local_var.get("value", "").strip()
                if val and val != base_symbol:
                    return self.resolve_symbol_source_multi(val, file_path, visited.copy())

        # 2. Check if it's imported
        imports = feats.get("imports", [])
        matching_imp = None
        for imp in imports:
            if isinstance(imp, dict) and imp.get("local") == base_symbol:
                matching_imp = imp
                break
                
        if matching_imp:
            source_path_raw = matching_imp.get("source", "")
            imported_name = matching_imp.get("imported", "")
            
            target_file = self.resolve_import_path(file_path, source_path_raw)
            if not target_file:
                filename = source_path_raw.split('/')[-1]
                return [(filename, imported_name)]
                
            if imported_name == "default":
                target_feats = self.features_map.get(target_file, {})
                target_exports = target_feats.get("exports", [])
                local_name = "default"
                for exp in target_exports:
                    if exp.get("exported") == "default":
                        local_name = exp.get("local", "default")
                        break
                res = self.resolve_symbol_source_multi(local_name, target_file, visited.copy())
                if res:
                    return res
                return [(target_file, local_name)]
                
            res = self.resolve_symbol_source_multi(imported_name, target_file, visited.copy())
            if res:
                return res
            return [(target_file, imported_name)]
            
        # 3. Check local functions and classes
        local_funcs = feats.get("functions", [])
        local_classes = feats.get("classes", [])
        if base_symbol in local_funcs or base_symbol in local_classes:
            return [(file_path, base_symbol)]
            
        # 4. Check exports
        exports = feats.get("exports", [])
        for exp in exports:
            if exp.get("exported") == base_symbol:
                if exp.get("source"):
                    target_file = self.resolve_import_path(file_path, exp.get("source"))
                    if target_file:
                        res = self.resolve_symbol_source_multi(exp.get("local"), target_file, visited.copy())
                        if res:
                            return res
                        return [(target_file, exp.get("local"))]
                else:
                    res = self.resolve_symbol_source_multi(exp.get("local"), file_path, visited.copy())
                    if res:
                        return res
                    return [(file_path, exp.get("local"))]
                    
            if exp.get("exported") == "*":
                target_file = self.resolve_import_path(file_path, exp.get("source"))
                if target_file:
                    res = self.resolve_symbol_source_multi(base_symbol, target_file, visited.copy())
                    if res:
                        return res
                        
        return []

    def resolve_symbol_source(
        self,
        symbol_name: str,
        file_path: str
    ) -> Optional[Tuple[str, str]]:
        res = self.resolve_symbol_source_multi(symbol_name, file_path)
        if res:
            return res[0]
        return None

    def qualify_handler_with_imports(
        self,
        handler: str,
        file_path: str
    ) -> str:
        if not handler or handler.startswith("("):
            return handler
            
        if "." in handler:
            parts = handler.split(".", 1)
            base = parts[0]
            prop = parts[1]
            resolved = self.resolve_symbol_source(base, file_path)
            if resolved:
                declaring_file, orig_name = resolved
                target_prop = prop if orig_name in ("default", "*") else orig_name
                ctrl_file = os.path.basename(declaring_file)
                ctrl_file = re.sub(r"\.(jsx?|tsx?|py)$", "", ctrl_file, flags=re.IGNORECASE)
                return f"{ctrl_file}.{target_prop}"
            return handler

        resolved = self.resolve_symbol_source(handler, file_path)
        if resolved:
            declaring_file, orig_name = resolved
            if declaring_file != file_path:
                ctrl_file = os.path.basename(declaring_file)
                ctrl_file = re.sub(r"\.(jsx?|tsx?|py)$", "", ctrl_file, flags=re.IGNORECASE)
                return f"{ctrl_file}.{orig_name}"
            
        return handler
