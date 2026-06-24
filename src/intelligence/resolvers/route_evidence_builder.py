import os
import re
from typing import Any, Dict, List, Optional
from src.file_filter import FileFilter
from src.intelligence.evidence.context import AnalysisContext

def _basename(path: str) -> str:
    return os.path.basename(path)

def build_routers(context: AnalysisContext) -> List[Dict[str, Any]]:
    routers: List[Dict[str, Any]] = []
    seen: set[str] = set()

    EXPRESS_ROUTER_RX = re.compile(
        r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:express\.)?Router\s*\(", re.MULTILINE
    )
    FLASK_BLUEPRINT_RX = re.compile(
        r"\b(\w+)\s*=\s*Blueprint\s*\(", re.MULTILINE
    )

    for path in sorted(context.file_paths):
        if FileFilter.should_exclude_from_analysis(path):
            continue
        lower = path.lower().replace("\\", "/")
        feats = context.features_map.get(path, {})
        annotations = feats.get("annotations", [])
        endpoints = feats.get("api_endpoints", []) or feats.get("api_routes", []) or []

        routes = []
        for ep in endpoints:
            if isinstance(ep, dict):
                method = str(ep.get("verb") or ep.get("method") or "GET").upper()
                route = str(ep.get("route") or ep.get("path") or "")
                routes.append(f"{method} {route}")

        if lower.endswith((".js", ".ts", ".mjs", ".cjs")):
            content = context.read_file(path) or ""
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

        if lower.endswith(".py"):
            content = context.read_file(path) or ""
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


def build_frontend(
    context: AnalysisContext,
    tech_stack: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    tech_stack = tech_stack or {}
    frontend_data = {
        "frontend_routes": [],
        "api_calls": [],
        "components": []
    }
    
    seen_routes: set[str] = set()
    seen_api_calls: set[tuple[str, str, int]] = set()
    seen_components: set[str] = set()

    for path in sorted(context.file_paths):
        lower = path.lower().replace("\\", "/")
        feats = context.features_map.get(path, {})
        
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
                
        # 3. Routes (React Router)
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
        if react_components or feats.get("exported_functions") or feats.get("exported_classes"):
            route_prefix = ""
            framework = ""
            is_nextjs_context = "next" in str(tech_stack.get("frontend_framework", "")).lower()
            is_nextjs_pages_path = (
                ("/pages/" in lower or lower.startswith("pages/"))
                and (
                    is_nextjs_context
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


def build_mounts(context: AnalysisContext) -> List[Dict[str, Any]]:
    def _resolve_router_file(mounted_router: str, source_file: str) -> str:
        m = re.search(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", mounted_router)
        if m:
            import_path = m.group(1)
            base_dir = os.path.dirname(source_file)
            joined = os.path.normpath(os.path.join(base_dir, import_path)).replace("\\", "/")
            candidates = [joined, joined + ".js", joined + ".ts", joined + "/index.js", joined + "/index.ts"]
            for candidate in candidates:
                if candidate in context.all_files:
                    return candidate
        return ""

    mounts: List[Dict[str, Any]] = []
    seen: set[tuple] = set()

    try:
        from src.intelligence.route_resolution_engine import _build_graph

        edges, _, _, _ = _build_graph(context.file_paths, context.read_file)
        for edge in edges:
            key = (edge.mount_path, edge.child.file_path, edge.child.router_symbol, edge.parent.file_path, edge.parent.router_symbol)
            if key in seen:
                continue
            seen.add(key)
            mounts.append(
                {
                    "mount_path": edge.mount_path,
                    "mounted_router": edge.child.router_symbol,
                    "parent": edge.parent.router_symbol,
                    "router": edge.child.router_symbol,
                    "router_file": edge.child.file_path,
                    "path": edge.mount_path,
                    "source_file": edge.parent.file_path,
                    "line": 0,
                }
            )
    except Exception:
        pass

    for path in sorted(context.file_paths):
        if FileFilter.should_exclude_from_analysis(path):
            continue
        feats = context.features_map.get(path, {})
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
    return sorted(mounts, key=lambda m: (m.get("mount_path", ""), m.get("source_file", ""), m.get("router_file", "")))
