"""Deterministic repository evidence graph builder.

Constructs a structural graph of the repository from AST-extracted features
and file-structure heuristics.  Every list is sorted for byte-reproducibility.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _basename(path: str) -> str:
    return os.path.basename(path)


def _module_key(path: str) -> str:
    """Derive a module grouping key from a file path strictly based on Phase 4 rules.
    Only create modules from routes/, controllers/, models/, services/, features/, pages/
    Ignore root-level config scripts natively via _NOISE_COMPONENT_NAMES.
    """
    normalized_path = path.replace("\\", "/")
    
    # Priority 1: features/ directories (Phase 4 spec)
    m_feat = re.search(r'/features/([a-zA-Z0-9_-]+)/', normalized_path, re.IGNORECASE)
    if m_feat:
        return m_feat.group(1).lower()

    # Priority 2: backend/src/modules/{module}/*
    m_mod = re.search(r'/modules/([a-zA-Z0-9_-]+)/', normalized_path, re.IGNORECASE)
    if m_mod:
        return m_mod.group(1).lower()

    basename = _basename(path)
    # Priority 3: Component suffix patterns
    m_suf = re.match(r'^([a-zA-Z0-9_]+)(Routes?|Controller|Service|Repository)\.jsx?$', basename, re.IGNORECASE)
    if m_suf:
        return m_suf.group(1).lower()

    # Priority 4: Strict allowed parent directory logic
    ALLOWED_MODULE_DIRS = {"routes", "controllers", "models", "services", "features", "pages"}
    parts = [p for p in normalized_path.split("/") if p]
    
    # If the file's direct parent is one of the allowed dirs, the module is the prefix of the file
    if len(parts) >= 2 and parts[-2].lower() in ALLOWED_MODULE_DIRS:
        name_without_ext = os.path.splitext(basename)[0]
        # Strip common suffixes conceptually if they slipped via casing
        for suffix in ["route", "routes", "controller", "service", "model", "page", "repository"]:
            if name_without_ext.lower().endswith(suffix):
                return name_without_ext[:-len(suffix)].rstrip('.-_').lower()
        return name_without_ext.rstrip('.-_').lower()

    # Priority 5: Fallback to the top-level conceptual folder if deeply nested inside allowed dirs
    for idx, part in enumerate(parts):
        if part.lower() in ALLOWED_MODULE_DIRS and idx + 1 < len(parts):
             # Just use the name of the directory or file directly under it
             name_without_ext = os.path.splitext(parts[idx+1])[0]
             return name_without_ext.lower()

    # Fallback default
    return parts[0].lower() if parts else "root"


def _classify_component_type(file_paths: list[str]) -> str:
    """Classify a set of files as a component type.

    Fix 4: Frontend directories take priority. If files live under
    frontend/client/ui/web or src/features/src/pages/src/components,
    always classify as frontend_module.
    """
    lowered = [p.lower() for p in file_paths]
    # Strong frontend signals — these override any backend tokens inside them
    _FRONTEND_DIRS = ["/frontend/", "/client/", "/ui/", "/web/"]
    _FRONTEND_SRC_DIRS = ["/src/features/", "/src/pages/", "/src/components/"]
    has_frontend = any(
        any(tok in p for tok in _FRONTEND_DIRS + _FRONTEND_SRC_DIRS)
        or p.endswith((".tsx", ".jsx"))
        for p in lowered
    )
    # Only count backend if NOT inside a frontend directory
    has_backend = any(
        any(tok in p for tok in ["/backend/", "/server/", "/api/",
                                  "/routes/", "/controllers/", "/services/"])
        and not any(ftok in p for ftok in _FRONTEND_DIRS + _FRONTEND_SRC_DIRS)
        for p in lowered
    )
    has_infra = any(
        "dockerfile" in p or "docker-compose" in p
        or p.endswith(".tf") or "terraform" in p
        or ".github/workflows/" in p
        for p in lowered
    )
    if has_frontend:
        return "frontend_module"
    if has_infra and not has_backend:
        return "infra_module"
    return "backend_module"


def _detect_framework(file_paths: list[str], features_map: dict[str, dict],
                      tech_stack: dict) -> str | None:
    """Detect the framework for a set of files using parsed features + tech stack."""
    for path in file_paths:
        feats = features_map.get(path, {})
        annotations = feats.get("annotations", [])
        decorators = feats.get("decorators", [])

        # Java Spring
        for ann in annotations:
            if any(s in ann for s in ["@RestController", "@Controller", "@Service"]):
                return "spring"
        # Python Flask/FastAPI
        for dec in decorators:
            if "app.route" in dec or "blueprint" in dec.lower():
                return "flask"
            if "router." in dec:
                return "fastapi"

    # Fall back to tech_stack
    # Fix 4: If files are under frontend directories, use frontend framework
    backend = tech_stack.get("backend_framework")
    frontend = tech_stack.get("frontend_framework")
    lowered = [p.lower() for p in file_paths]
    _FRONTEND_DIRS = ["/frontend/", "/client/", "/ui/", "/web/", "/src/features/", "/src/pages/", "/src/components/"]
    is_frontend_context = any(
        any(tok in p for tok in _FRONTEND_DIRS) or p.endswith((".tsx", ".jsx"))
        for p in lowered
    )
    if is_frontend_context and frontend:
        return frontend
    if backend and not is_frontend_context:
        return backend
    if frontend:
        return frontend
    return backend or None


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

# Noise file names that should not become standalone component entries.
# These are root-level config/meta files with no meaningful module grouping.
_NOISE_COMPONENT_NAMES: frozenset[str] = frozenset({
    ".gitignore", ".eslintrc", ".eslintrc.js", ".eslintrc.json",
    ".env", ".env.example", ".envexample", ".babelrc",
    "readme.md", "readme", "license", "license.md", "changelog.md",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "tsconfig.json", "jsconfig.json", "vite.config.js", "vite.config.ts",
    "webpack.config.js", ".prettierrc",
    "dockerfile", "docker-compose.yml",
})


def _build_components(file_paths: list[str], features_map: dict[str, dict],
                      tech_stack: dict) -> list[dict]:
    """Group files into logical components/modules."""
    modules: dict[str, list[str]] = {}
    for path in sorted(file_paths):
        # Skip files that only resolve to a noise/config filename as their module key
        basename = os.path.basename(path).lower()
        if basename in _NOISE_COMPONENT_NAMES:
            # Only skip if this is the only file that would make up the module
            # (i.e. skip single-file noise modules, not multi-file modules that happen to contain one)
            key = _module_key(path)
            if key == basename or key == os.path.splitext(basename)[0]:
                continue
        key = _module_key(path)
        modules.setdefault(key, []).append(path)

    components: list[dict] = []
    for name, files in sorted(modules.items()):
        # Also skip purely noise-named single-file groups that slipped through
        if len(files) == 1 and os.path.basename(files[0]).lower() in _NOISE_COMPONENT_NAMES:
            continue
        comp_type = _classify_component_type(files)
        framework = _detect_framework(files, features_map, tech_stack)
        components.append({
            "name": name,
            "path": os.path.dirname(files[0]) if files else "",
            "type": comp_type,
            "framework": framework,
            "files": sorted(files),
        })
    components = sorted(components, key=lambda c: c.get("name", ""))
    return components


def _build_apis(file_paths: list[str], features_map: dict[str, dict],
                read_file: Callable[[str], str | None],
                components: list[dict]) -> list[dict]:
    """Collect all API endpoints from parsed features.

    For Express repos, resolves mount prefixes via the existing
    route_resolution_engine so that ``app.use("/api/auth", router)`` +
    ``router.post("/login")`` yields ``POST /api/auth/login``.
    """
    # Build file→component index for component mapping.
    file_to_component: dict[str, str] = {}
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

    # ---- Express mount resolution ----
    # Detect whether the repo has Express patterns.
    express_files: list[str] = [
        p for p in file_paths
        if p.lower().endswith((".js", ".ts", ".mjs", ".cjs"))
    ]
    mount_prefixes: dict[str, str] = {}  # file_path → resolved prefix
    if express_files:
        try:
            from src.intelligence.route_resolution_engine import (
                _build_graph, _join_paths, _APP_SYMBOLS, RouterIdentity,
            )
            edges, router_symbols_by_file, import_aliases_by_file, _ = _build_graph(file_paths, read_file)

            # Build a map: child file → mount_path from the mount edges.
            # If a file is mounted multiple times, concatenate the full chain.
            # We do a simple BFS from root nodes.
            incoming: dict[str, list] = {}
            for edge in edges:
                incoming.setdefault(edge.child.file_path, []).append(edge)

            def _resolve_prefix(file_path: str, visited: set[str] | None = None) -> str:
                if visited is None:
                    visited = set()
                if file_path in visited:
                    return ""
                visited.add(file_path)
                edges_in = incoming.get(file_path, [])
                if not edges_in:
                    return ""
                # Take the first mount chain (deterministic: edges are sorted).
                edge = edges_in[0]
                parent_prefix = _resolve_prefix(edge.parent.file_path, visited)
                return _join_paths(parent_prefix, edge.mount_path)

            for fp in express_files:
                prefix = _resolve_prefix(fp)
                if prefix:
                    mount_prefixes[fp] = prefix
        except (ImportError, Exception):
            # If route_resolution_engine is unavailable, fall through.
            pass

    # ---- Python router prefix resolution ----
    # Python files may have internal router_prefixes (e.g. APIRouter(prefix="/api"))
    # or AST "USE" entries for app.include_router(..., prefix=...)
    for path in sorted(file_paths):
        if not path.lower().endswith(".py"):
            continue
        feats = features_map.get(path, {})
        # 1. Local prefixes from router instantiation
        local_prefixes = feats.get("router_prefixes", {})
        
        # 2. Extract USE verbs representing mounts (app.include_router, app.register_blueprint)
        # Note: We only map intra-file or simple string mappings here without a full graph.
        endpoints = feats.get("api_endpoints", []) or feats.get("api_routes", []) or []
        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            if str(ep.get("verb")).upper() == "USE":
                mount_route = str(ep.get("route", ""))
                handler_symbol = str(ep.get("handler", ""))
                if mount_route and handler_symbol:
                    # Map the handler symbol to its prefix
                    local_prefixes[handler_symbol] = mount_route
                    
        # Apply the merged prefix back to features map for endpoint resolution below
        feats["router_prefixes_resolved"] = local_prefixes

    # ---- Collect endpoints ----
    apis: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    # ---- Regex fallback: chained .route('/path').get().post() patterns ----
    # The AST parser misses chained routes like router.route('/').get(protect, getGoals).post(protect, setGoal)
    _CHAIN_ROUTE_RX = re.compile(
        r"""(?:router|app)\s*\.\s*route\s*\(\s*['"]([^'"]+)['"]\s*\)"""
        r"""((?:\s*\.\s*(?:get|post|put|delete|patch)\s*\([^)]*\))+)""",
        re.IGNORECASE
    )
    _CHAIN_METHOD_RX = re.compile(
        r"""\.\s*(get|post|put|delete|patch)\s*\(([^)]*)\)""",
        re.IGNORECASE
    )
    _AUTH_KEYWORDS_SET = {"protect", "auth", "authenticate", "verifytoken", "jwt"}

    for path in sorted(file_paths):
        if not path.lower().endswith((".js", ".ts", ".mjs", ".cjs")):
            continue
        content = read_file(path)
        if not content:
            continue
        prefix = mount_prefixes.get(path, "")
        for rm in _CHAIN_ROUTE_RX.finditer(content):
            route_path = rm.group(1)
            chain_body = rm.group(2)
            line_num = content[:rm.start()].count("\n") + 1
            for cm in _CHAIN_METHOD_RX.finditer(chain_body):
                method = cm.group(1).upper()
                args_str = cm.group(2)
                arg_tokens = [t.strip() for t in args_str.split(",") if t.strip()]
                handler = arg_tokens[-1] if arg_tokens else ""
                middleware_tokens = arg_tokens[:-1] if len(arg_tokens) > 1 else []
                auth_required = any(
                    any(kw in mw.lower() for kw in _AUTH_KEYWORDS_SET)
                    for mw in middleware_tokens
                )

                # Qualify handler with controller file from import
                if handler and "." not in handler and not handler.startswith("("):
                    m_imp = re.search(r"(?:const|let|var)\s+[^;'\"=]*?\b" + re.escape(handler) + r"\b[^;'\"=]*?=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", content)
                    if not m_imp:
                        m_imp = re.search(r"import\s+[^;'\"=]*?\b" + re.escape(handler) + r"\b[^;'\"=]*?from\s+['\"]([^'\"]+)['\"]", content)
                    if m_imp:
                        ctrl_file = _basename(m_imp.group(1))
                        ctrl_file = re.sub(r"\.(jsx?|tsx?)$", "", ctrl_file, flags=re.IGNORECASE)
                        handler = f"{ctrl_file}.{handler}"

            # Resolve full path with mount prefix
                if prefix and route_path:
                    if route_path == "/":
                        full_path = prefix
                    else:
                        full_path = prefix.rstrip("/") + "/" + route_path.lstrip("/")
                elif prefix:
                    full_path = prefix
                else:
                    full_path = route_path
                if full_path and not full_path.startswith("/"):
                    full_path = "/" + full_path
                full_path = full_path.rstrip("/") or "/"

                # Fix 3: SPA Fallback Filtering
                if full_path in ["", "/", "/*"]:
                    continue
                if "sendFile" in content or "index.html" in content:
                    continue

                key = (method, full_path, path)
                if key in seen:
                    continue
                seen.add(key)

                component = file_to_component.get(path)
                if not component or component == "root":
                    comp_key = _module_key(path)
                    if not comp_key or comp_key == "root":
                        # Component inference from routes/ path
                        parts = path.replace("\\", "/").split("/")
                        if "routes" in parts:
                            r_idx = parts.index("routes")
                            if r_idx + 1 < len(parts):
                                comp_key = parts[r_idx + 1]
                                comp_key = re.sub(r'\.\w+$', '', comp_key)
                                # strip common suffixes
                                for sfx in ["route", "routes", "controller", "service"]:
                                    if comp_key.lower().endswith(sfx):
                                        comp_key = comp_key[:-len(sfx)].rstrip(".-_")
                    component = comp_key or "root"

                entry: dict[str, Any] = {
                    "method": method,
                    "path": full_path,
                    "controller": handler,
                    "module": component,
                    "router_file": path,
                    "source_file": path,
                    "line": line_num,
                }
                if auth_required:
                    entry["auth_required"] = True
                apis.append(entry)

    for path in sorted(file_paths):
        feats = features_map.get(path, {})
        endpoints = feats.get("api_endpoints", []) or feats.get("api_routes", []) or []
        prefix = mount_prefixes.get(path, "")

        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            method = str(ep.get("verb") or ep.get("method") or "GET").upper()
            if method == "USE":
                continue # Skip mount points internally
                
            raw_route = str(ep.get("route") or ep.get("path") or "")
            line = int(ep.get("line", 0))
            handler = str(ep.get("handler") or ep.get("controller") or "")
            router_symbol = str(ep.get("router_symbol", ""))

            # Snap-dish constraint: fully qualify controller methods if raw name
            if handler and "." not in handler and not handler.startswith("("):
                content = read_file(path) or ""
                # Try ES6 import: import { deleteAddress } from "../controllers/addressController.js"
                m = re.search(r"import\s+[^;'\"=]*?\b" + re.escape(handler) + r"\b[^;'\"=]*?from\s+['\"]([^'\"]+)['\"]", content)
                # Try CommonJS: const { deleteAddress } = require("../controllers/addressController")
                if not m:
                    m = re.search(r"(?:const|let|var)\s+[^;'\"=]*?\b" + re.escape(handler) + r"\b[^;'\"=]*?=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", content)
                if m:
                    imported_path = m.group(1)
                    controller_file = _basename(imported_path)
                    # Strip extension
                    controller_file = re.sub(r"\.(jsx?|tsx?)$", "", controller_file, flags=re.IGNORECASE)
                    handler = f"{controller_file}.{handler}"
            
            # Sanitize bare lambdas for documentation stability
            if not handler or re.match(r'^(async\s+)?(function\b|\()', handler.strip()):
                b_name = os.path.splitext(_basename(path))[0]
                b_name = re.sub(r"[-_\.]", "", b_name)
                handler = f"{b_name}.rootHandler" if raw_route in ["", "/"] else f"{b_name}.inlineHandler"

            # Resolve full path with mount prefix.
            # 1. Express cross-file prefix
            # 2. Python local router/blueprint prefix
            resolved_prefix = prefix
            if path.lower().endswith(".py"):
                local_prefs = feats.get("router_prefixes_resolved", {})
                if router_symbol in local_prefs:
                    resolved_prefix = local_prefs[router_symbol]

            if resolved_prefix and raw_route:
                if raw_route == "/":
                    full_path = resolved_prefix
                else:
                    full_path = resolved_prefix.rstrip("/") + "/" + raw_route.lstrip("/")
            elif resolved_prefix:
                full_path = resolved_prefix
            else:
                full_path = raw_route

            # Normalize: ensure leading slash, no trailing slash.
            if full_path and not full_path.startswith("/"):
                full_path = "/" + full_path
            full_path = full_path.rstrip("/") or "/"

            # Fix 3: SPA Fallback Filtering
            if full_path in ["", "/", "/*"]:
                continue
            content = read_file(path) or ""
            if "sendFile" in content or "index.html" in content:
                continue

            key = (method, full_path, path)
            if key in seen:
                continue
            seen.add(key)

            # Fix 2: Detect authentication middleware
            _AUTH_KEYWORDS = {"protect", "auth", "authenticate", "verifytoken", "jwt"}
            middleware = ep.get("middleware", []) or []
            auth_required = any(
                any(kw in mw.lower() for kw in _AUTH_KEYWORDS)
                for mw in middleware
            )

            component = file_to_component.get(path)
            if not component or component == "root":
                comp_key = _module_key(path)
                if not comp_key or comp_key == "root":
                    parts = path.replace("\\", "/").split("/")
                    if "routes" in parts:
                        r_idx = parts.index("routes")
                        if r_idx + 1 < len(parts):
                            comp_key = parts[r_idx + 1]
                            comp_key = re.sub(r'\.\w+$', '', comp_key)
                            for sfx in ["route", "routes", "controller", "service"]:
                                if comp_key.lower().endswith(sfx):
                                    comp_key = comp_key[:-len(sfx)].rstrip(".-_")
                component = comp_key or "root"

            entry: dict[str, Any] = {
                "method": method,
                "path": full_path,
                "controller": handler,
                "module": component,
                "router_file": path,
                "source_file": path,
                "line": line,
            }
            if auth_required:
                entry["auth_required"] = True
            apis.append(entry)

    # Determine strict deterministic sorting
    return sorted(apis, key=lambda a: (a["method"], a["path"]))


def _build_entities(file_paths: list[str], features_map: dict[str, dict],
                    schema_tags_map: dict[str, list[str]],
                    tech_stack: dict,
                    read_file: Callable[[str], str | None]) -> tuple[list[dict], list[dict]]:
    """Detect data model entities from AST features and schema tags, plus any distinct schema edges."""
    entities: list[dict] = []
    schema_edges: list[dict] = []
    seen: set[str] = set()

    MONGOOSE_MODEL_RX = re.compile(r"mongoose\.model\s*\(\s*['\"](\w+)['\"]")
    PRISMA_MODEL_RX = re.compile(r"^\s*model\s+(\w+)\s*\{", re.MULTILINE)
    JPA_CLASS_RX = re.compile(r"class\s+(\w+)")
    DJANGO_CLASS_RX = re.compile(r"class\s+(\w+)\s*\(\s*(?:models\.)?Model\s*\)")

    for path in sorted(file_paths):
        feats = features_map.get(path, {})
        tags = schema_tags_map.get(path, [])
        lower = path.lower()

        # JPA entities (from schema_annotations or tags)
        if "JPA_ENTITY" in tags or feats.get("schema_annotations"):
            content = read_file(path) or ""
            m = JPA_CLASS_RX.search(content)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                entities.append({
                    "name": m.group(1),
                    "type": "jpa_entity",
                    "database": "sql",
                    "orm": "jpa",
                    "source_file": path,
                })

        # Mongoose models
        mongoose_names = set()
        for tag in tags:
            if tag.startswith("MONGOOSE_MODEL:"):
                model_name = tag.split(":", 1)[1]
                mongoose_names.add(model_name)

        if any(t == "MONGOOSE_SCHEMA" for t in tags) and not mongoose_names:
            content = read_file(path) or ""
            for match in MONGOOSE_MODEL_RX.finditer(content):
                mongoose_names.add(match.group(1))
                
            # Snap-dish constraint: const menuItemSchema = new mongoose.Schema({...})
            SNAP_SCHEMA_RX = re.compile(r"(?:const|let|var)\s+(\w+?)(?:Schema)?\s*=\s*new\s+(?:mongoose\.)?Schema\b", re.MULTILINE)
            for match in SNAP_SCHEMA_RX.finditer(content):
                name = match.group(1)
                # Normalize menuItem to MenuItem
                if name:
                    name = name[0].upper() + name[1:]
                mongoose_names.add(name)
                
        # Register Mongoose Models
        for model_name in mongoose_names:
            if model_name:
                model_name = model_name[0].upper() + model_name[1:]
            
            if model_name not in seen:
                seen.add(model_name)
                entities.append({
                    "name": model_name,
                    "type": "mongoose_model",
                    "database": "mongodb",
                    "orm": "mongoose",
                    "source_file": path,
                })
                
        # Mongoose relationships (from AST extraction)
        mongoose_schemas = feats.get("mongoose_schemas", [])
        for schema_fields in mongoose_schemas:
            # We assume the schema in the file belongs to the mongoose model in the file
            # If multiple models, map them broadly. Usually 1 model per file.
            if len(mongoose_names) > 0:
                parent_model = list(mongoose_names)[0]
                for field_name, ref_model in schema_fields.items():
                    schema_edges.append({
                        "type": "entity_relation",
                        "from": parent_model,
                        "to": ref_model,
                        "relation": "references",
                    })

        # Django models
        if "DJANGO_MODEL" in tags:
            content = read_file(path) or ""
            for match in DJANGO_CLASS_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    entities.append({
                        "name": name,
                        "type": "django_model",
                        "database": "sql",
                        "orm": "django",
                        "source_file": path,
                    })

        # Prisma models
        if lower.endswith("schema.prisma"):
            content = read_file(path) or ""
            PRISMA_MODEL_FULL_RX = re.compile(r"^\s*model\s+(\w+)\s*\{([^}]+)\}", re.MULTILINE)
            prisma_models_data = {}

            # First pass: collect all models and their raw fields
            for match in PRISMA_MODEL_FULL_RX.finditer(content):
                name = match.group(1)
                block = match.group(2)
                if name not in seen:
                    seen.add(name)
                
                fields = []
                field_types = {}
                for line in block.strip().split("\n"):
                    line = line.strip()
                    if not line or line.startswith("//") or line.startswith("@@"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        fname = parts[0]
                        ftype = parts[1]
                        fields.append(fname)
                        field_types[fname] = ftype
                        
                # Ensure sorted fields without duplicates
                fields = sorted(list(set(fields)))
                prisma_models_data[name] = {"fields": fields, "types": field_types}

                entities.append({
                    "name": name,
                    "type": "prisma_model",
                    "database": "sql",
                    "orm": "prisma",
                    "fields": fields,
                    "source_file": path,
                })
                
            # Second pass: infer relationships structurally
            # Build a list of edges, then we'll deduplicate them.
            for model_name, data in prisma_models_data.items():
                for fname, ftype in data["types"].items():
                    is_array = ftype.endswith("[]")
                    base_type = ftype.replace("[]", "").replace("?", "")
                    
                    if base_type in prisma_models_data: # It's a relation to another model
                        # Determine relationship type
                        other_data = prisma_models_data[base_type]
                        # Does the other model point back to us?
                        back_refs = [t for n, t in other_data["types"].items() if t.replace("[]", "").replace("?", "") == model_name]
                        
                        is_other_array = any(t.endswith("[]") for t in back_refs)
                        
                        if is_array and is_other_array:
                            rel_type = "many-to-many"
                        elif is_array and not is_other_array:
                            rel_type = "one-to-many"
                        elif not is_array and is_other_array:
                            rel_type = "many-to-one" # We'll normalize this later
                        else:
                            rel_type = "one-to-one"
                            
                        # Normalize direction to match user expectations (e.g. one-to-many goes from One -> Many)
                        # So if we are "many-to-one" to base_type, it means base_type is "one-to-many" to us.
                        # We only emit the forward direction to avoid contradicts.
                        if rel_type == "many-to-one":
                            from_m = base_type
                            to_m = model_name
                            emit_type = "one-to-many"
                        elif rel_type == "many-to-many":
                            # Sort lexicographically so A -> B many-to-many matches B -> A
                            from_m, to_m = sorted([model_name, base_type])
                            emit_type = "many-to-many"
                        elif rel_type == "one-to-one":
                            from_m, to_m = sorted([model_name, base_type])
                            emit_type = "one-to-one"
                        else:
                            # one-to-many
                            from_m = base_type
                            to_m = model_name
                            emit_type = "one-to-many"
                            
                        schema_edges.append({
                            "type": emit_type,
                            "from": from_m,
                            "to": to_m,
                        })
        # Sequelize models
        SEQUELIZE_RX = re.compile(r"sequelize\.define\s*\(\s*['\"](\w+)['\"]", re.IGNORECASE)
        content = read_file(path) or ""
        for match in SEQUELIZE_RX.finditer(content):
            name = match.group(1)
            # Capitalize to normalize
            if name:
                name = name[0].upper() + name[1:]
            if name not in seen:
                seen.add(name)
                entities.append({
                    "name": name,
                    "type": "sequelize_model",
                    "database": "sql",
                    "orm": "sequelize",
                    "source_file": path,
                })

        # TypeORM models
        TYPEORM_RX = re.compile(r"@Entity\b[\s\S]*?(?:export\s+)?class\s+(\w+)\b")
        if any("Entity" in str(ann) for ann in feats.get("annotations", [])) or "@Entity" in content:
            for match in TYPEORM_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    entities.append({
                        "name": name,
                        "type": "typeorm_entity",
                        "database": "sql",
                        "orm": "typeorm",
                        "source_file": path,
                    })
            for match in PRISMA_MODEL_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    entities.append({
                        "name": name,
                        "type": "prisma_model",
                        "database": "sql",
                        "orm": "prisma",
                        "source_file": path,
                    })

        # SQL table definitions (from schema tags)
        for tag in tags:
            if tag.startswith("SQL_CREATE_TABLE:"):
                table_name = tag.split(":", 1)[1]
                if table_name not in seen:
                    seen.add(table_name)
                    entities.append({
                        "name": table_name,
                        "type": "sql_table",
                        "database": "postgres" if "POSTGRES_SCHEMA_CHANGE" in tags else "sql",
                        "orm": "none",
                        "source_file": path,
                    })
            elif tag.startswith("SQL_FOREIGN_KEY:"):
                parts = tag.split(":")
                if len(parts) >= 3:
                    src_table = parts[1]
                    target_table = parts[2]
                    schema_edges.append({
                        "type": "entity_relation",
                        "from": src_table,
                        "to": target_table,
                        "relation": "foreign_key",
                    })

    # Sort output structures
    entities = sorted(entities, key=lambda e: e.get("name", ""))
    schema_edges = sorted(
        # Dedupe strictly identical schema edges
        [dict(s) for s in set(frozenset(d.items()) for d in schema_edges)],
        key=lambda e: (e["from"], e["to"])
    )
    return entities, schema_edges


def _build_services(file_paths: list[str],
                    features_map: dict[str, dict],
                    components: list[dict],
                    read_file: Callable[[str], str | None] | None = None) -> list[dict]:
    """Detect service classes/modules from parsed features and path heuristics."""
    services: list[dict] = []
    seen: set[str] = set()

    # Method extraction regex for JS/TS class bodies:
    #   async methodName(  OR   methodName(   at indented class method position
    _JS_METHOD_RX = re.compile(r"^\s{2,}(?:async\s+)?([a-z_]\w*)\s*\(", re.MULTILINE)
    # Control-flow keywords that look like methods but aren't
    _JS_SKIP_NAMES = {"if", "for", "while", "switch", "catch", "return", "throw",
                      "function", "constructor", "super", "new", "await", "else"}

    file_to_component: dict[str, str] = {}
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

    for path in sorted(file_paths):
        lower = path.lower().replace("\\", "/")
        feats = features_map.get(path, {})
        classes = feats.get("classes", [])
        methods = feats.get("methods", []) or feats.get("functions", []) or []
        annotations = feats.get("annotations", [])
        exported_classes = feats.get("exported_classes", [])

        in_services_dir = (
            "/services/" in lower or lower.startswith("services/") or
            "/repositories/" in lower or lower.startswith("repositories/")
        )

        # Detect *.service.(js|ts|jsx|tsx) explicitly or *Service.js (PascalCase)
        basename = _basename(lower)
        # We check original basename not lower because for *Service.js it's case sensitive
        orig_basename = os.path.basename(path)
        is_service_file = re.match(r'^(.+\.service\.(js|ts|jsx|tsx)|.+Service\.(js|ts|jsx|tsx))$', orig_basename) is not None

        # Java @Service / @Injectable
        has_service_ann = any(
            "@Service" in ann or "@Injectable" in ann
            for ann in annotations
        )

        if in_services_dir or has_service_ann or is_service_file:
            # Prefer exported class names for JS/TS service files
            candidates = (
                exported_classes if (is_service_file and exported_classes) else classes
            )
            svc_type = "service_module"

            # --- Method extraction with JS/TS fallback ---
            # tree-sitter may not emit 'methods' for ES6 class bodies; extract from raw source
            resolved_methods: list[str] = sorted(set(str(m) for m in methods if str(m) not in _JS_SKIP_NAMES))
            if not resolved_methods and read_file and lower.endswith((".js", ".ts", ".jsx", ".tsx")):
                content = read_file(path) or ""
                if content:
                    raw = [
                        m.group(1) for m in _JS_METHOD_RX.finditer(content)
                        if m.group(1) not in _JS_SKIP_NAMES
                    ]
                    resolved_methods = sorted(set(raw))

            for cls in candidates:
                cls_name = str(cls)
                if cls_name not in seen:
                    seen.add(cls_name)
                    services.append({
                        "name": cls_name,
                        "module": file_to_component.get(path, ""),
                        "file": path,
                        "type": svc_type,
                        "functions": resolved_methods,
                    })
            # If no classes found but file is in services dir, use filename
            if not candidates:
                name = os.path.splitext(_basename(path))[0]
                # Strip .service suffix: auth.service -> auth
                name = re.sub(r'\.service$', '', name)
                if name not in seen:
                    seen.add(name)
                    services.append({
                        "name": name,
                        "module": file_to_component.get(path, ""),
                        "file": path,
                        "type": "service_module",
                        "functions": resolved_methods,
                    })

    return sorted(services, key=lambda s: s["name"])


def _build_repositories(file_paths: list[str],
                        features_map: dict[str, dict],
                        components: list[dict],
                        entities: list[dict],
                        read_file: Callable[[str], str | None] | None = None) -> list[dict]:
    """Detect repository layer classes/modules from parsed features and heuristics."""
    repositories: list[dict] = []
    seen: set[str] = set()

    file_to_component: dict[str, str] = {}
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

    entity_names = {e["name"] for e in entities}

    for path in sorted(file_paths):
        lower = path.lower().replace("\\", "/")
        feats = features_map.get(path, {})
        classes = feats.get("classes", [])
        annotations = feats.get("annotations", [])
        exported_classes = feats.get("exported_classes", [])

        in_repos_dir = "/repositories/" in lower or lower.startswith("repositories/")
        orig_basename = os.path.basename(path)
        is_repo_file = re.match(r'^(.+\.repository\.(js|ts|jsx|tsx)|.+Repository\.(js|ts|jsx|tsx))$', orig_basename) is not None

        has_repo_ann = any("@Repository" in ann for ann in annotations)

        if in_repos_dir or has_repo_ann or is_repo_file:
            candidates = exported_classes if (is_repo_file and exported_classes) else classes
            content = read_file(path) if read_file else ""
            
            # Find closest entity mapping
            matched_entity = ""
            if content:
                for ent in entity_names:
                    if str(ent) in str(content):
                        matched_entity = str(ent)
                        break

            for cls in candidates:
                cls_name = str(cls)
                if cls_name not in seen:
                    seen.add(cls_name)
                    if not matched_entity:
                        for ent in entity_names:
                            if ent.lower() in cls_name.lower() or cls_name.lower().startswith(ent.lower()):
                                matched_entity = ent
                                break

                    repositories.append({
                        "name": cls_name,
                        "entity": matched_entity,
                        "module": file_to_component.get(path, ""),
                        "file": path,
                    })
                    
            if not candidates:
                name = os.path.splitext(orig_basename)[0]
                name = re.sub(r'\.repository$', '', name, flags=re.IGNORECASE)
                if name not in seen:
                    seen.add(name)
                    if not matched_entity:
                        for ent in entity_names:
                            if ent.lower() in name.lower() or name.lower().startswith(ent.lower()):
                                matched_entity = ent
                                break

                    repositories.append({
                        "name": name,
                        "entity": matched_entity,
                        "module": file_to_component.get(path, ""),
                        "file": path,
                    })

    return sorted(repositories, key=lambda r: r["name"])



def _build_routers(file_paths: list[str],
                   features_map: dict[str, dict],
                   read_file: Callable[[str], str | None]) -> list[dict]:
    """Detect router definitions from parsed features."""
    routers: list[dict] = []
    seen: set[str] = set()

    EXPRESS_ROUTER_RX = re.compile(
        r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:express\.)?Router\s*\(", re.MULTILINE
    )
    FLASK_BLUEPRINT_RX = re.compile(
        r"\b(\w+)\s*=\s*Blueprint\s*\(", re.MULTILINE
    )

    for path in sorted(file_paths):
        lower = path.lower().replace("\\", "/")
        feats = features_map.get(path, {})
        annotations = feats.get("annotations", [])
        endpoints = feats.get("api_endpoints", []) or feats.get("api_routes", []) or []

        routes = []
        for ep in endpoints:
            if isinstance(ep, dict):
                method = str(ep.get("verb") or ep.get("method") or "GET").upper()
                route = str(ep.get("route") or ep.get("path") or "")
                routes.append(f"{method} {route}")

        # Express Router instances
        if lower.endswith((".js", ".ts", ".mjs", ".cjs")):
            content = read_file(path) or ""
            for match in EXPRESS_ROUTER_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    routers.append({
                        "name": name,
                        "type": "express_router",
                        "source_file": path,
                        "routes": sorted(set(routes)),
                    })

        # Flask Blueprints
        if lower.endswith(".py"):
            content = read_file(path) or ""
            for match in FLASK_BLUEPRINT_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    routers.append({
                        "name": name,
                        "type": "flask_blueprint",
                        "source_file": path,
                        "routes": sorted(set(routes)),
                    })

        # Java @Controller / @RestController
        has_controller_ann = any(
            "@Controller" in ann or "@RestController" in ann
            for ann in annotations
        )
        if has_controller_ann:
            classes = feats.get("classes", [])
            for cls in classes:
                cls_name = str(cls)
                if cls_name not in seen:
                    seen.add(cls_name)
                    routers.append({
                        "name": cls_name,
                        "type": "spring_controller",
                        "source_file": path,
                        "routes": sorted(set(routes)),
                    })

        # Files in /routes/ directory with endpoints but no explicit router detected
        in_routes_dir = "/routes/" in lower or lower.startswith("routes/")
        if in_routes_dir and routes and path not in {r["source_file"] for r in routers}:
            name = os.path.splitext(_basename(path))[0]
            if name not in seen:
                seen.add(name)
                ext = os.path.splitext(path)[1].lower()
                rtype = "express_router" if ext in (".js", ".ts", ".mjs", ".cjs") else "route_module"
                routers.append({
                    "name": name,
                    "type": rtype,
                    "source_file": path,
                    "routes": sorted(set(routes)),
                })

    return sorted(routers, key=lambda r: r["name"])


def _build_frontend(file_paths: list[str],
                    features_map: dict[str, dict],
                    read_file: Callable[[str], str | None],
                    tech_stack: dict | None = None) -> dict[str, Any]:
    """Detect frontend features including React routes, Next.js routes, components, and API calls."""
    tech_stack = tech_stack or {}
    frontend_data = {
        "frontend_routes": [],
        "api_calls": [],
        "components": []
    }
    
    seen_routes: set[str] = set()
    seen_api_calls: set[tuple[str, str, int]] = set()
    seen_components: set[str] = set()

    for path in sorted(file_paths):
        lower = path.lower().replace("\\", "/")
        feats = features_map.get(path, {})
        
        # 1. API Calls
        api_calls = feats.get("api_calls", [])
        for call in api_calls:
            client = call.get("client")
            method = call.get("method", "UNKNOWN")
            line = call.get("line", 0)
            key = (path, client, line)
            if key not in seen_api_calls:
                seen_api_calls.add(key)
                frontend_data["api_calls"].append({
                    "client": client,
                    "method": method,
                    "source_file": path,
                    "line": line
                })
        
        # 2. Components
        react_components = feats.get("react_components", [])
        if react_components:
            comp_name = os.path.splitext(_basename(path))[0]
            if comp_name not in seen_components:
                seen_components.add(comp_name)
                frontend_data["components"].append({
                    "name": comp_name,
                    "type": "react_component",
                    "source_file": path
                })
                
        # 3. Routes (React Router from AST)
        jsx_routes = feats.get("jsx_routes", [])
        for route in jsx_routes:
            route_path = route.get("path")
            comp = route.get("component")
            route_key = f"{route_path}::{comp}"
            if route_path and route_key not in seen_routes:
                seen_routes.add(route_key)
                frontend_data["frontend_routes"].append({
                    "path": route_path,
                    "component": comp,
                    "source_file": path,
                    "framework": "react_router"
                })
                
        # 4. Next.js App / Pages Router Heuristics
        # Pages Router: pages/about.tsx -> /about
        # App Router: app/about/page.tsx -> /about
        if react_components or feats.get("exported_functions") or feats.get("exported_classes"):
            route_prefix = ""
            framework = ""
            # Fix 3: Only classify as Next.js pages if the path is a true
            # Next.js pages dir (top-level pages/) OR the tech_stack says nextjs.
            is_nextjs_context = "next" in str(tech_stack.get("frontend_framework", "")).lower()
            is_nextjs_pages_path = (
                ("/pages/" in lower or lower.startswith("pages/"))
                and (
                    is_nextjs_context  # tech_stack says it's Next.js
                    or ("/src/pages/" not in lower and "/frontend/" not in lower and "/client/" not in lower)
                )
            )
            if is_nextjs_pages_path:
                parts = lower.split("/pages/")
                route_prefix = parts[-1] if len(parts) > 1 else parts[0]
                route_prefix = re.sub(r'\.tsx?|\.jsx?$', '', route_prefix)
                if route_prefix == "index":
                    route_prefix = "/"
                elif route_prefix.endswith("/index"):
                    route_prefix = "/" + route_prefix[:-6]
                else:
                    route_prefix = "/" + route_prefix
                framework = "nextjs_pages"
                
            elif "/app/" in lower or lower.startswith("app/"):
                if lower.endswith("/page.tsx") or lower.endswith("/page.jsx") or lower.endswith("/page.ts") or lower.endswith("/page.js"):
                    parts = lower.split("/app/")
                    route_prefix = parts[-1] if len(parts) > 1 else parts[0]
                    # path is director name essentially
                    route_prefix = os.path.dirname(route_prefix)
                    route_prefix = "/" + route_prefix if route_prefix else "/"
                    framework = "nextjs_app"
            
            route_key = f"{route_prefix}::{os.path.splitext(_basename(path))[0]}"
            if framework and route_key not in seen_routes:
                seen_routes.add(route_key)
                frontend_data["frontend_routes"].append({
                    "path": route_prefix,
                    "component": os.path.splitext(_basename(path))[0],
                    "source_file": path,
                    "framework": framework
                })

    unique_routes_map = {}
    for r in frontend_data["frontend_routes"]:
        key = (r["path"], r["component"])
        if key not in unique_routes_map:
            unique_routes_map[key] = r

    frontend_data["frontend_routes"] = sorted(
        unique_routes_map.values(),
        key=lambda x: (x["path"], x["component"])
    )
    frontend_data["components"] = sorted(frontend_data["components"], key=lambda x: x["name"])
    frontend_data["api_calls"] = sorted(frontend_data["api_calls"], key=lambda x: (x["source_file"], x["line"]))
    
    return frontend_data


def _build_relationships(components: list[dict],
                         services: list[dict],
                         repositories: list[dict],
                         routers: list[dict],
                         entities: list[dict],
                         schema_edges: list[dict],
                         apis: list[dict],
                         features_map: dict[str, dict],
                         read_file: Callable[[str], str | None]) -> list[dict]:
    """Infer relationships between evidence items via co-location and explicit schema references."""
    relationships: list[dict] = []

    # Explicit entity schema relations discovered in DB parsing
    relationships.extend(schema_edges)

    # Index: file -> component name
    file_to_component: dict[str, str] = {}
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

    # EXPOSES_API: component exposing resolved api endpoint
    for api in apis:
        comp_name = api.get("module") or api.get("component")
        if comp_name:
            # Map the verb + path combined
            endpoint = f'{api["method"]} {api["path"]}'
            relationships.append({
                "type": "EXPOSES_API",
                "from": comp_name,
                "to": endpoint,
            })

    # For fast heuristics
    entity_names = {e["name"] for e in entities}
    service_names = {s["name"] for s in services}
    
    # service -> repository relationships
    for svc in services:
        content = read_file(svc["file"]) or ""
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

    # controller -> service calls (using apis for controllers)
    controllers = set()
    for api in apis:
        if api.get("controller"):
            ctrl = str(api["controller"])
            # snap-dish requirement: from orderController, not orderController.placeOrder
            if "." in ctrl:
                ctrl = ctrl.split(".", 1)[0]
            controllers.add(ctrl)
    for comp in components:
        comp_files = comp.get("files", [])
        for file_path in comp_files:
            content = read_file(file_path) or ""
            # Map router/controller actions to services
            has_controller = any(c in content for c in controllers)
            if has_controller:
                for svc in service_names:
                    svc_str = str(svc) if svc else ""
                    if svc_str and svc_str in content:
                        # Find which controller specifically:
                        for c in controllers:
                            if c in content:
                                relationships.append({
                                    "type": "calls",
                                    "from": c,
                                    "to": svc_str,
                                })

    # Generic heuristics fallback if specific components missing
    for comp in components:
        comp_name = comp["name"]
        comp_files = comp.get("files", [])
        
        for file_path in comp_files:
            # IMPORTS_MODULE: Cross reference based on AST imports mapping or string file path presence
            imports = features_map.get(file_path, {}).get("imports", [])
            for imp in imports:
                imp_name = os.path.splitext(os.path.basename(imp))[0]
                if imp_name:
                    relationships.append({
                        "type": "IMPORTS_MODULE",
                        "from": comp_name,
                        "to": imp_name,
                    })

            # USES_ENTITY / CALLS_SERVICE: File textual matching
            content = read_file(file_path) or ""
            if content:
                for ent in entity_names:
                    if not ent: continue
                    # Don't tag entities defined inside this very file
                    defined_here = any(e["name"] == ent and e.get("source_file") == file_path for e in entities)
                    if not defined_here and str(ent) in content:
                        relationships.append({
                            "type": "USES_ENTITY",
                            "from": comp_name,
                            "to": ent,
                        })
                
                for svc in service_names:
                    if not svc: continue
                    defined_here = any(s["name"] == svc and s.get("file") == file_path for s in services)
                    if not defined_here and str(svc) in content:
                        relationships.append({
                            "type": "CALLS_SERVICE",
                            "from": comp_name,
                            "to": svc,
                        })

    # Legacy router/service associations mapping (keep for backwards compat/testing if needed)
    # router_serves_component
    for router in routers:
        comp = file_to_component.get(router["source_file"])
        if comp:
            relationships.append({
                "type": "router_serves_component",
                "from": router["name"],
                "to": comp,
            })

    # service_used_by_component
    for svc in services:
        comp = file_to_component.get(svc["file"])
        if comp:
            relationships.append({
                "type": "service_used_by_component",
                "from": svc["name"],
                "to": comp,
            })

    # entity_used_by_component
    for entity in entities:
        comp = file_to_component.get(entity["source_file"])
        if comp:
            relationships.append({
                "type": "entity_used_by_component",
                "from": entity["name"],
                "to": comp,
            })
            
    # Deduplicate relationships exactly
    unique_rels = []
    seen_rels = set()
    for rel in relationships:
        key = (rel.get("type"), rel.get("from"), rel.get("to"), rel.get("relation"))
        if key not in seen_rels:
            seen_rels.add(key)
            unique_rels.append(rel)

    return sorted(unique_rels, key=lambda r: (r.get("from", ""), r.get("to", "")))


def _build_mounts(file_paths: list[str], features_map: dict[str, dict]) -> list[dict]:
    """Collect all explicit router mounts (app.use('/prefix', routerVar)) from AST features."""
    files_set = set(file_paths)

    def _resolve_router_file(mounted_router: str, source_file: str) -> str:
        """Fix 5: Resolve inline require('...') to actual file path."""
        m = re.search(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", mounted_router)
        if m:
            import_path = m.group(1)
            base_dir = os.path.dirname(source_file)
            joined = os.path.normpath(os.path.join(base_dir, import_path)).replace("\\", "/")
            candidates = [joined, joined + ".js", joined + ".ts", joined + "/index.js", joined + "/index.ts"]
            for candidate in candidates:
                if candidate in files_set:
                    return candidate
        return ""

    mounts: list[dict] = []
    seen: set[tuple] = set()
    for path in sorted(file_paths):
        feats = features_map.get(path, {})
        # From api_mounts (explicit field)
        for m in feats.get("api_mounts", []):
            key = (m.get("mount_path", ""), m.get("mounted_router", ""), path)
            if key not in seen:
                seen.add(key)
                router_file = _resolve_router_file(m.get("mounted_router", ""), path)
                mounts.append({
                    "mount_path": m.get("mount_path", ""),
                    "mounted_router": m.get("mounted_router", ""),
                    "parent": "app" if m.get("router_symbol", "") in ("app", "server", "express") else m.get("router_symbol", "app"),
                    "router": m.get("mounted_router", ""),
                    "router_file": router_file,
                    "path": m.get("mount_path", ""),
                    "source_file": path,
                    "line": m.get("line", 0),
                })
        # From USE-verb endpoints (backwards-compatible)
        for ep in feats.get("api_endpoints", []):
            if not isinstance(ep, dict):
                continue
            if str(ep.get("verb", "")).upper() == "USE" and ep.get("mount_path"):
                key = (ep.get("mount_path", ""), ep.get("mounted_router", ""), path)
                if key not in seen:
                    seen.add(key)
                    mounted_str = ep.get("mounted_router", ep.get("handler", ""))
                    router_file = _resolve_router_file(mounted_str, path)
                    mounts.append({
                        "mount_path": ep.get("mount_path", ""),
                        "mounted_router": mounted_str,
                        "parent": "app" if ep.get("router_symbol", "") in ("app", "server", "express") else ep.get("router_symbol", "app"),
                        "router": mounted_str,
                        "router_file": router_file,
                        "path": ep.get("mount_path", ""),
                        "source_file": path,
                        "line": ep.get("line", 0),
                    })
    return sorted(mounts, key=lambda m: m.get("mount_path", ""))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_repository_evidence(
    file_paths: list[str],
    read_file: Callable[[str], str | None],
    features_map: dict[str, dict],
    schema_tags_map: dict[str, list[str]],
    tech_stack: dict,
) -> dict[str, Any]:
    """Build the deterministic repository evidence graph."""
    paths = sorted(str(p) for p in file_paths if str(p).strip())

    components = _build_components(paths, features_map, tech_stack)
    apis = _build_apis(paths, features_map, read_file, components)
    entities, schema_edges = _build_entities(paths, features_map, schema_tags_map, tech_stack, read_file)
    services = _build_services(paths, features_map, components, read_file)
    repositories = _build_repositories(paths, features_map, components, entities, read_file)
    routers = _build_routers(paths, features_map, read_file)
    mounts = _build_mounts(paths, features_map)
    frontend = _build_frontend(paths, features_map, read_file, tech_stack)
    relationships = _build_relationships(components, services, repositories, routers, entities, schema_edges, apis, features_map, read_file)

    # Restructure tech_stack for Phase 3 strict compliance
    formatted_tech_stack = {
        "backend": [],
        "frontend": [],
        "database": [],
        "infrastructure": []
    }
    
    if tech_stack.get("backend_framework"):
        formatted_tech_stack["backend"].append(tech_stack.get("backend_framework"))
    if tech_stack.get("frontend_framework"):
        formatted_tech_stack["frontend"].append(tech_stack.get("frontend_framework"))
    if tech_stack.get("database"):
        formatted_tech_stack["database"].append(tech_stack.get("database"))
    if tech_stack.get("orm"):
        formatted_tech_stack["database"].append(tech_stack.get("orm"))
    if tech_stack.get("infra"):
        formatted_tech_stack["infrastructure"].extend(tech_stack.get("infra", []))

    # FIX 4: Extract Router Mount Graph
    api_mounts = []
    for m in mounts:
        router_val = m.get("router_file", "")
        if router_val:
            router_val = os.path.basename(router_val)
        else:
            router_val = m.get("router", "")
        
        api_mounts.append({
            "base_path": m.get("mount_path", ""),
            "router": router_val
        })
    # Deduplicate and sort
    api_mounts = [dict(t) for t in {tuple(d.items()) for d in api_mounts}]
    api_mounts = sorted(api_mounts, key=lambda m: (m.get("base_path", ""), m.get("router", "")))

    return {
        # Primary evidence keys
        "tech_stack": formatted_tech_stack,
        "modules": components,          # User-facing alias: same as components
        "apis": apis,
        "entities": entities,
        "services": services,
        "repositories": repositories,
        "api_mounts": api_mounts,       # Added for EPIC-1 Stabilization
        "mounts": mounts,
        "relationships": relationships,
        "frontend_routes": frontend.get("frontend_routes", []),
        # Additional detail
        "components": components,       # Kept for backwards compatibility
        "routers": routers,
        "file_evidence": features_map,
    }
