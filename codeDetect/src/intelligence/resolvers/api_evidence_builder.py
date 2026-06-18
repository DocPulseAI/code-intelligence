import os
import re
from typing import Any, Dict, List, Set, Tuple
from src.file_filter import FileFilter
from src.intelligence.evidence.context import AnalysisContext, _split_by_comma_nested, _deduplicate_preserve_order

def _basename(path: str) -> str:
    return os.path.basename(path)

def _module_key(path: str) -> str:
    normalized_path = path.replace("\\", "/").strip("/")
    parts = [p for p in normalized_path.split("/") if p]
    if not parts:
        return "root"

    for idx, part in enumerate(parts):
        if part.lower() == "modules" and idx + 1 < len(parts):
            return parts[idx + 1].lower()

    module_dirs = {"routes", "controllers", "services", "models", "repositories", "features", "pages"}
    for idx, part in enumerate(parts):
        lower = part.lower()
        if lower not in module_dirs:
            continue
        if idx + 1 >= len(parts):
            continue
        next_part = parts[idx + 1]
        if idx + 2 < len(parts):
            after = parts[idx + 2]
            if "." in after:
                return next_part.lower()
        stem = os.path.splitext(next_part)[0]
        stem = re.sub(r"\.(routes?|controller|service|model|repository)$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"(routes?|controller|service|model|repository)$", "", stem, flags=re.IGNORECASE)
        stem = stem.strip("._-").lower()
        if stem:
            return stem

    basename = os.path.splitext(os.path.basename(path))[0]
    basename = re.sub(r"\.(routes?|controller|service|model|repository)$", "", basename, flags=re.IGNORECASE)
    basename = re.sub(r"(routes?|controller|service|model|repository)$", "", basename, flags=re.IGNORECASE)
    basename = basename.strip("._-").lower()
    if basename:
        return basename

    for part in parts:
        if part.lower() in {"src", "server", "backend", "app"}:
            continue
        if part.startswith("."):
            continue
        return part.lower()
    return "root"

def build_apis(
    context: AnalysisContext,
    components: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    file_to_component: Dict[str, str] = {}
    all_files = context.all_files
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

    apis: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()

    # ---- Express mount resolution by router identity ----
    express_files: List[str] = [p for p in context.file_paths if p.lower().endswith((".js", ".ts", ".mjs", ".cjs"))]
    router_symbols_by_file: Dict[str, List[str]] = {}
    mount_contexts: Dict[Tuple[str, str], List[str]] = {}
    has_mount_edges = False
    _app_symbols = {"app", "server", "api"}
    try:
        from src.intelligence.route_resolution_engine import (
            _build_graph,
            _join_paths,
            _APP_SYMBOLS,
            RouterIdentity,
        )
        _app_symbols = set(_APP_SYMBOLS)
        edges, router_symbols_by_file, _, router_middleware = _build_graph(context.file_paths, context.read_file)
        has_mount_edges = bool(edges)
        incoming: Dict[RouterIdentity, List] = {}
        for edge in edges:
            incoming.setdefault(edge.child, []).append(edge)

        dormant_files = set()
        if has_mount_edges:
            for fp, symbols in router_symbols_by_file.items():
                if symbols:
                    if not any(RouterIdentity(fp, s) in incoming for s in symbols):
                        dormant_files.add(fp)

        memo: Dict[RouterIdentity, List[Tuple[str, Tuple[str, ...]]]] = {}
        active: Set[RouterIdentity] = set()

        def resolve_contexts(identity: RouterIdentity, depth: int = 0) -> List[Tuple[str, Tuple[str, ...]]]:
            if depth > 15:
                return []
            if identity in memo:
                return memo[identity]
            if identity in active:
                return []
            active.add(identity)
            edges_in = sorted(
                incoming.get(identity, []),
                key=lambda e: (
                    e.parent.file_path,
                    e.parent.router_symbol,
                    e.mount_path,
                    e.child.file_path,
                    e.child.router_symbol,
                ),
            )
            if not edges_in:
                if identity.file_path in dormant_files:
                    contexts = []
                elif identity.router_symbol == "__root__" or not has_mount_edges:
                    contexts = [("", tuple())]
                else:
                    contexts = []
            else:
                merged = set()
                contexts: List[Tuple[str, Tuple[str, ...]]] = []
                for edge in edges_in:
                    for base_path, base_tokens in resolve_contexts(edge.parent, depth + 1):
                        next_path = _join_paths(base_path, edge.mount_path)
                        next_tokens = tuple(sorted(set(list(base_tokens) + list(edge.middleware_tokens))))
                        key = (next_path, next_tokens)
                        if key not in merged:
                            merged.add(key)
                            contexts.append(key)
            local_tokens = tuple(sorted(set(router_middleware.get(identity, []))))
            if local_tokens:
                contexts = [(p, tuple(sorted(set(list(t) + list(local_tokens))))) for p, t in contexts]
            active.remove(identity)
            contexts = sorted(contexts, key=lambda x: (x[0], x[1]))
            memo[identity] = contexts
            return contexts

        for file_path in express_files:
            symbols = router_symbols_by_file.get(file_path, []) or []
            for symbol in symbols:
                identity = RouterIdentity(file_path, symbol)
                prefixes = sorted({p for p, _ in resolve_contexts(identity, 0)})
                if prefixes:
                    mount_contexts[(file_path, symbol)] = prefixes
            root_identity = RouterIdentity(file_path, "__root__")
            root_prefixes = sorted({p for p, _ in resolve_contexts(root_identity, 0)})
            if root_prefixes:
                mount_contexts[(file_path, "__root__")] = root_prefixes
    except Exception:
        router_symbols_by_file = {}
        mount_contexts = {}
        has_mount_edges = False

    def _prefixes_for(file_path: str, router_symbol: str) -> List[str]:
        symbol = (router_symbol or "").strip()
        if symbol in _app_symbols:
            symbol = "__root__"
        known = router_symbols_by_file.get(file_path, [])
        if not symbol and len(known) == 1:
            symbol = known[0]
        if symbol and symbol not in {"__root__"} and len(known) == 1 and symbol not in known:
            symbol = known[0]
        if symbol:
            prefixes = mount_contexts.get((file_path, symbol), [])
            if prefixes:
                return prefixes
        if mount_contexts.get((file_path, "__root__")):
            return mount_contexts[(file_path, "__root__")]
        if not has_mount_edges:
            return [""]
        return []

    # ---- Python router prefix resolution ----
    for path in sorted(context.file_paths):
        if not path.lower().endswith(".py"):
            continue
        feats = context.features_map.get(path, {})
        local_prefixes = feats.get("router_prefixes", {})
        
        endpoints = feats.get("api_endpoints", []) or feats.get("api_routes", []) or []
        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            if str(ep.get("verb")).upper() == "USE":
                mount_route = str(ep.get("route", ""))
                handler_symbol = str(ep.get("handler", ""))
                if mount_route and handler_symbol:
                    local_prefixes[handler_symbol] = mount_route
                    
        feats["router_prefixes_resolved"] = local_prefixes

    _CHAIN_ROUTE_HEAD_RX = re.compile(
        r"\b([A-Za-z_]\w*)\s*\.\s*route\s*\(",
        re.IGNORECASE,
    )
    _CHAIN_METHOD_HEAD_RX = re.compile(
        r"\.\s*(get|post|put|delete|patch|options|head)\s*\(",
        re.IGNORECASE,
    )
    _AUTH_KEYWORDS_SET = {"protect", "auth", "authenticate", "verifytoken", "jwt"}
    FASTIFY_ROUTE_RX = re.compile(
        r"\bfastify\.(get|post|put|delete|patch|options|head)\s*\(\s*['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    KOA_ROUTER_ROUTE_RX = re.compile(
        r"\b([A-Za-z_]\w*)\.(get|post|put|delete|patch|options|head)\s*\(\s*['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )

    def _find_matching_paren(text: str, open_idx: int) -> int:
        if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "(":
            return -1
        depth = 1
        i = open_idx + 1
        quote = ""
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
                i += 1
                continue
            if ch in {"'", '"', "`"}:
                quote = ch
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return -1

    def _split_top_level_args(args_text: str) -> List[str]:
        tokens: List[str] = []
        buf: List[str] = []
        par = arr = obj = 0
        quote = ""
        i = 0
        while i < len(args_text):
            ch = args_text[i]
            if quote:
                buf.append(ch)
                if ch == "\\" and i + 1 < len(args_text):
                    buf.append(args_text[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
                i += 1
                continue
            if ch in {"'", '"', "`"}:
                quote = ch
                buf.append(ch)
                i += 1
                continue
            if ch == "(":
                par += 1
            elif ch == ")":
                par = max(0, par - 1)
            elif ch == "[":
                arr += 1
            elif ch == "]":
                arr = max(0, arr - 1)
            elif ch == "{":
                obj += 1
            elif ch == "}":
                obj = max(0, obj - 1)
            if ch == "," and par == arr == obj == 0:
                tok = "".join(buf).strip()
                if tok:
                    tokens.append(tok)
                buf = []
            else:
                buf.append(ch)
            i += 1
        tok = "".join(buf).strip()
        if tok:
            tokens.append(tok)
        return tokens

    def _parse_chain_methods_from(content_text: str, start_idx: int) -> Tuple[List[Tuple[str, str]], int]:
        methods: List[Tuple[str, str]] = []
        idx = start_idx
        while idx < len(content_text):
            while idx < len(content_text) and content_text[idx].isspace():
                idx += 1
            mm = _CHAIN_METHOD_HEAD_RX.match(content_text, idx)
            if not mm:
                break
            method = mm.group(1).upper()
            open_idx = mm.end() - 1
            close_idx = _find_matching_paren(content_text, open_idx)
            if close_idx < 0:
                break
            args_str = content_text[open_idx + 1 : close_idx]
            methods.append((method, args_str))
            idx = close_idx + 1
        return methods, idx

    for path in sorted(context.file_paths):
        if FileFilter.should_exclude_from_analysis(path):
            continue
        if not path.lower().endswith((".js", ".ts", ".mjs", ".cjs")):
            continue
        content = context.read_file(path)
        if not content:
            continue
        for rm in _CHAIN_ROUTE_HEAD_RX.finditer(content):
            router_symbol = rm.group(1)
            route_open = rm.end() - 1
            route_close = _find_matching_paren(content, route_open)
            if route_close < 0:
                continue
            route_args = _split_top_level_args(content[route_open + 1 : route_close])
            if not route_args:
                continue
            route_path_token = route_args[0].strip()
            if len(route_path_token) < 2 or route_path_token[0] != route_path_token[-1] or route_path_token[0] not in {"'", '"', "`"}:
                continue
            route_path = route_path_token[1:-1]
            chain_methods, _ = _parse_chain_methods_from(content, route_close + 1)
            if not chain_methods:
                continue
            line_num = content[:rm.start()].count("\n") + 1
            prefixes = _prefixes_for(path, router_symbol)
            if not prefixes:
                continue
            for method, args_str in chain_methods:
                arg_tokens = _split_top_level_args(args_str)
                handler = arg_tokens[-1] if arg_tokens else ""
                middleware_tokens = arg_tokens[:-1] if len(arg_tokens) > 1 else []
                auth_required = any(
                    any(kw in mw.lower() for kw in _AUTH_KEYWORDS_SET)
                    for mw in middleware_tokens
                )

                if handler and "." not in handler and not handler.startswith("("):
                    handler = context.qualify_handler_with_imports(handler, path)

                full_paths = []
                for prefix in prefixes:
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
                    full_paths.append(full_path.rstrip("/") or "/")

                if "sendFile" in content or "index.html" in content:
                    continue

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

                for full_path in full_paths:
                    if full_path in ["", "/*"]:
                        continue
                    key = (method, full_path, path)
                    if key in seen:
                        continue
                    seen.add(key)
                    entry: Dict[str, Any] = {
                        "method": method,
                        "path": full_path,
                        "controller": handler,
                        "module": component,
                        "router_file": path,
                        "source_file": path,
                        "line": line_num,
                        "middleware": [],
                    }
                    if auth_required:
                        entry["auth_required"] = True
                    apis.append(entry)

        # ---- Fastify direct routes ----
        if "fastify" in content:
            for fm in FASTIFY_ROUTE_RX.finditer(content):
                method = fm.group(1).upper()
                route_path = fm.group(2)
                line_num = content[: fm.start()].count("\n") + 1

                full_path = route_path or "/"
                if full_path and not full_path.startswith("/"):
                    full_path = "/" + full_path
                full_path = full_path.rstrip("/") or "/"

                key = (method, full_path, path)
                if key in seen:
                    continue
                seen.add(key)

                component = file_to_component.get(path) or _module_key(path) or "root"
                apis.append(
                    {
                        "method": method,
                        "path": full_path,
                        "controller": "",
                        "module": component,
                        "router_file": path,
                        "source_file": path,
                        "line": line_num,
                        "middleware": [],
                    }
                )

        # ---- Koa router routes ----
        if "koa-router" in content or "from 'koa-router'" in content or 'from "koa-router"' in content:
            for km in KOA_ROUTER_ROUTE_RX.finditer(content):
                router_sym = km.group(1)
                method = km.group(2).upper()
                route_path = km.group(3)
                line_num = content[: km.start()].count("\n") + 1

                full_path = route_path or "/"
                if full_path and not full_path.startswith("/"):
                    full_path = "/" + full_path
                full_path = full_path.rstrip("/") or "/"

                key = (method, full_path, path)
                if key in seen:
                    continue
                seen.add(key)

                component = file_to_component.get(path) or _module_key(path) or "root"
                apis.append(
                    {
                        "method": method,
                        "path": full_path,
                        "controller": router_sym,
                        "module": component,
                        "router_file": path,
                        "source_file": path,
                        "line": line_num,
                        "middleware": [],
                    }
                )

    # ---- Django REST Framework router.register(...) fallback ----
    DRF_ROUTER_DEF_RX = re.compile(
        r"\b([A-Za-z_]\w*)\s*=\s*(?:DefaultRouter|SimpleRouter)\s*\(",
        re.MULTILINE,
    )
    DRF_REGISTER_RX = re.compile(
        r"\b([A-Za-z_]\w*)\s*\.register\s*\(\s*[ru]?[\"']([^\"']+)[\"']\s*,\s*([A-Za-z_]\w*)",
        re.MULTILINE,
    )

    for path in sorted(context.file_paths):
        if FileFilter.should_exclude_from_analysis(path):
            continue
        if not path.lower().endswith(".py"):
            continue
        content = context.read_file(path) or ""
        if not content:
            continue

        router_symbols = {m.group(1) for m in DRF_ROUTER_DEF_RX.finditer(content)}
        if not router_symbols and "rest_framework" not in content:
            continue

        for m in DRF_REGISTER_RX.finditer(content):
            router_sym, raw_prefix, viewset_name = m.group(1), m.group(2), m.group(3)
            if router_symbols and router_sym not in router_symbols:
                continue

            base_path = raw_prefix.strip().strip("/")
            if not base_path:
                base_path = ""
            list_path = "/" + base_path if base_path else "/"
            detail_path = list_path.rstrip("/") + "/{id}"

            drf_methods = [
                ("GET", list_path),
                ("POST", list_path),
                ("GET", detail_path),
                ("PUT", detail_path),
                ("PATCH", detail_path),
                ("DELETE", detail_path),
            ]

            component = file_to_component.get(path)
            if not component or component == "root":
                comp_key = _module_key(path)
                component = comp_key or "root"

            line_num = content[: m.start()].count("\n") + 1
            controller = f"{viewset_name}.viewset"

            for method, full_path in drf_methods:
                key = (method, full_path, path)
                if key in seen:
                    continue
                seen.add(key)

                apis.append(
                    {
                        "method": method,
                        "path": full_path,
                        "controller": controller,
                        "module": component,
                        "router_file": path,
                        "source_file": path,
                        "line": line_num,
                        "auth_required": False,
                        "middleware": [],
                    }
                )

    for path in sorted(context.file_paths):
        if FileFilter.should_exclude_from_analysis(path):
            continue
        feats = context.features_map.get(path, {})
        endpoints = feats.get("api_endpoints", []) or feats.get("api_routes", []) or []

        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            method = str(ep.get("verb") or ep.get("method") or "GET").upper()
            if method == "USE":
                continue
                
            raw_route = str(ep.get("route") or ep.get("path") or "")
            line = int(ep.get("line", 0))
            handler = str(ep.get("handler") or ep.get("controller") or "")
            router_symbol = str(ep.get("router_symbol", ""))

            if handler and "." not in handler and not handler.startswith("("):
                handler = context.qualify_handler_with_imports(handler, path)
            
            if not handler or re.match(r'^(async\s+)?(function\b|\()', handler.strip()):
                b_name = os.path.splitext(_basename(path))[0]
                b_name = re.sub(r"[-_\.]", "", b_name)
                handler = f"{b_name}.rootHandler" if raw_route in ["", "/"] else f"{b_name}.inlineHandler"

            content = context.read_file(path) or ""
            if "sendFile" in content or "index.html" in content:
                continue

            if path.lower().endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")):
                resolved_prefixes = _prefixes_for(path, router_symbol)
                if not resolved_prefixes:
                    continue
            elif path.lower().endswith(".py"):
                local_prefs = feats.get("router_prefixes_resolved", {})
                if router_symbol in local_prefs:
                    resolved_prefixes = [str(local_prefs[router_symbol])]
                else:
                    resolved_prefixes = [""]
            else:
                resolved_prefixes = [""]

            _AUTH_KEYWORDS = {"protect", "auth", "authenticate", "verifytoken", "jwt"}
            middleware = ep.get("middleware", []) or []
            qualified_mws = []
            for mw in middleware:
                sub_tokens = _split_by_comma_nested(mw)
                for sub_tok in sub_tokens:
                    sub_tok_clean = sub_tok.split("(")[0].strip() if "(" in sub_tok else sub_tok
                    if not sub_tok_clean or sub_tok_clean.startswith("("):
                        continue
                        
                    if "." in sub_tok_clean:
                        base = sub_tok_clean.split(".")[0].strip()
                    else:
                        base = sub_tok_clean
                        
                    resolved_list = context.resolve_symbol_source_multi(base, path)
                    if resolved_list:
                        for resolved_file, orig_name in resolved_list:
                            b_name = os.path.basename(resolved_file)
                            b_name = re.sub(r"\.(jsx?|tsx?|py)$", "", b_name, flags=re.IGNORECASE)
                            if orig_name in ("default", "*"):
                                if "." in sub_tok_clean:
                                    prop = sub_tok_clean.split(".", 1)[1]
                                    qualified_mws.append(f"{b_name}.{prop}")
                                else:
                                    qualified_mws.append(b_name)
                            else:
                                qualified_mws.append(f"{b_name}.{orig_name}")
                    else:
                        qualified_mws.append(sub_tok_clean)

            qualified_mws = _deduplicate_preserve_order(qualified_mws)

            auth_required = any(
                any(kw in mw.lower() for kw in _AUTH_KEYWORDS)
                for mw in qualified_mws
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

            for resolved_prefix in resolved_prefixes:
                if resolved_prefix and raw_route:
                    if raw_route == "/":
                        full_path = resolved_prefix
                    else:
                        full_path = resolved_prefix.rstrip("/") + "/" + raw_route.lstrip("/")
                elif resolved_prefix:
                    full_path = resolved_prefix
                else:
                    full_path = raw_route

                if full_path and not full_path.startswith("/"):
                    full_path = "/" + full_path
                full_path = full_path.rstrip("/") or "/"
                if full_path in ["", "/*"]:
                    continue

                key = (method, full_path, path)
                if key in seen:
                    continue
                seen.add(key)

                entry: Dict[str, Any] = {
                    "method": method,
                    "path": full_path,
                    "controller": handler,
                    "module": component,
                    "router_file": path,
                    "source_file": path,
                    "line": line,
                    "middleware": qualified_mws,
                }
                if auth_required:
                    entry["auth_required"] = True
                apis.append(entry)

    return sorted(apis, key=lambda a: (a["method"], a["path"]))
