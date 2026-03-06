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
    """Derive a module grouping key from a file path.

    Strategy: use the *deepest meaningful directory* as the module name.
    For paths like ``backend/src/modules/auth/routes/auth.routes.js`` the
    module key is ``auth``.  Falls back to the first directory component.
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    # Strip known leaf directories that are "layers", not modules.
    LAYER_DIRS = {"routes", "controllers", "services", "models", "middleware",
                  "utils", "helpers", "lib", "config", "entities",
                  "repositories", "schemas", "dtos", "views", "templates",
                  "components", "pages", "hooks", "store", "styles"}
    # Walk from the filename backwards, skipping layer dirs.
    for idx in range(len(parts) - 2, -1, -1):
        if parts[idx].lower() not in LAYER_DIRS:
            return parts[idx].lower()
    return parts[0].lower() if parts else "root"


def _classify_component_type(file_paths: list[str]) -> str:
    """Classify a set of files as a component type."""
    lowered = [p.lower() for p in file_paths]
    has_frontend = any(
        any(tok in p for tok in ["/frontend/", "/client/", "/ui/", "/web/"])
        or p.endswith((".tsx", ".jsx"))
        for p in lowered
    )
    has_infra = any(
        "dockerfile" in p or "docker-compose" in p
        or p.endswith(".tf") or "terraform" in p
        or ".github/workflows/" in p
        for p in lowered
    )
    has_backend = any(
        any(tok in p for tok in ["/backend/", "/server/", "/api/",
                                  "/routes/", "/controllers/", "/services/"])
        for p in lowered
    )
    if has_infra and not has_backend and not has_frontend:
        return "infra_module"
    if has_frontend and not has_backend:
        return "frontend_module"
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
    backend = tech_stack.get("backend_framework")
    frontend = tech_stack.get("frontend_framework")
    lowered = [p.lower() for p in file_paths]
    if any(p.endswith((".tsx", ".jsx")) for p in lowered) and frontend:
        return frontend
    if backend:
        return backend
    return None


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_components(file_paths: list[str], features_map: dict[str, dict],
                      tech_stack: dict) -> list[dict]:
    """Group files into logical components/modules."""
    modules: dict[str, list[str]] = {}
    for path in sorted(file_paths):
        key = _module_key(path)
        modules.setdefault(key, []).append(path)

    components: list[dict] = []
    for name, files in sorted(modules.items()):
        comp_type = _classify_component_type(files)
        framework = _detect_framework(files, features_map, tech_stack)
        components.append({
            "name": name,
            "type": comp_type,
            "framework": framework,
            "files": sorted(files),
        })
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

    # ---- Collect endpoints ----
    apis: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for path in sorted(file_paths):
        feats = features_map.get(path, {})
        endpoints = feats.get("api_endpoints", []) or feats.get("api_routes", []) or []
        prefix = mount_prefixes.get(path, "")

        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            method = str(ep.get("verb") or ep.get("method") or "GET").upper()
            raw_route = str(ep.get("route") or ep.get("path") or "")
            line = int(ep.get("line", 0))
            handler = str(ep.get("handler") or ep.get("controller") or "")

            # Resolve full path with mount prefix.
            if prefix and raw_route:
                full_path = prefix.rstrip("/") + "/" + raw_route.lstrip("/")
            elif prefix:
                full_path = prefix
            else:
                full_path = raw_route

            # Normalize: ensure leading slash, no trailing slash.
            if full_path and not full_path.startswith("/"):
                full_path = "/" + full_path
            full_path = full_path.rstrip("/") or "/"

            key = (method, full_path, path)
            if key in seen:
                continue
            seen.add(key)

            component = file_to_component.get(path, "")
            apis.append({
                "method": method,
                "path": full_path,
                "component": component,
                "router_file": path,
                "controller": handler,
                "source_file": path,
                "line": line,
            })

    return sorted(apis, key=lambda a: (a["method"], a["path"], a["source_file"]))


def _build_entities(file_paths: list[str], features_map: dict[str, dict],
                    schema_tags_map: dict[str, list[str]],
                    read_file: Callable[[str], str | None]) -> list[dict]:
    """Detect data model entities from AST features and schema tags."""
    entities: list[dict] = []
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
                    "source_file": path,
                })

        # Mongoose models
        for tag in tags:
            if tag.startswith("MONGOOSE_MODEL:"):
                model_name = tag.split(":", 1)[1]
                if model_name not in seen:
                    seen.add(model_name)
                    entities.append({
                        "name": model_name,
                        "type": "mongoose_model",
                        "source_file": path,
                    })

        if any(t == "MONGOOSE_SCHEMA" for t in tags) and not any(t.startswith("MONGOOSE_MODEL:") for t in tags):
            content = read_file(path) or ""
            for match in MONGOOSE_MODEL_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    entities.append({
                        "name": name,
                        "type": "mongoose_model",
                        "source_file": path,
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
                        "source_file": path,
                    })

        # Prisma models
        if lower.endswith("schema.prisma"):
            content = read_file(path) or ""
            for match in PRISMA_MODEL_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    entities.append({
                        "name": name,
                        "type": "prisma_model",
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
                        "source_file": path,
                    })

    return sorted(entities, key=lambda e: (e["type"], e["name"]))


def _build_services(file_paths: list[str],
                    features_map: dict[str, dict]) -> list[dict]:
    """Detect service classes/modules from parsed features and path heuristics."""
    services: list[dict] = []
    seen: set[str] = set()

    for path in sorted(file_paths):
        lower = path.lower().replace("\\", "/")
        feats = features_map.get(path, {})
        classes = feats.get("classes", [])
        methods = feats.get("methods", []) or feats.get("functions", []) or []
        annotations = feats.get("annotations", [])

        in_services_dir = "/services/" in lower or lower.startswith("services/")

        # Java @Service / @Injectable
        has_service_ann = any(
            "@Service" in ann or "@Injectable" in ann
            for ann in annotations
        )

        if in_services_dir or has_service_ann:
            for cls in classes:
                cls_name = str(cls)
                if cls_name not in seen:
                    seen.add(cls_name)
                    services.append({
                        "name": cls_name,
                        "source_file": path,
                        "methods": sorted(set(str(m) for m in methods)),
                    })
            # If no classes found but file is in services dir, use filename
            if not classes:
                name = os.path.splitext(_basename(path))[0]
                if name not in seen:
                    seen.add(name)
                    services.append({
                        "name": name,
                        "source_file": path,
                        "methods": sorted(set(str(m) for m in methods)),
                    })

    return sorted(services, key=lambda s: s["name"])


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


def _build_relationships(components: list[dict],
                         services: list[dict],
                         routers: list[dict],
                         entities: list[dict]) -> list[dict]:
    """Infer relationships between evidence items via co-location."""
    relationships: list[dict] = []

    # Index: file -> component name
    file_to_component: dict[str, str] = {}
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

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
        comp = file_to_component.get(svc["source_file"])
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

    return sorted(relationships, key=lambda r: (r["type"], r["from"], r["to"]))


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
    """Build the deterministic repository evidence graph.

    Args:
        file_paths: Full file inventory (sorted).
        read_file: Callback to read file content by path.
        features_map: ``{path: features_dict}`` from per-file AST parsing.
        schema_tags_map: ``{path: [schema_tags]}`` from SchemaDetector.
        tech_stack: Already-computed tech stack dict.

    Returns:
        A dict with keys: components, apis, entities, services, routers,
        relationships.  All lists are sorted for deterministic output.
    """
    paths = sorted(str(p) for p in file_paths if str(p).strip())

    components = _build_components(paths, features_map, tech_stack)
    apis = _build_apis(paths, features_map, read_file, components)
    entities = _build_entities(paths, features_map, schema_tags_map, read_file)
    services = _build_services(paths, features_map)
    routers = _build_routers(paths, features_map, read_file)
    relationships = _build_relationships(components, services, routers, entities)

    return {
        "components": components,
        "apis": apis,
        "entities": entities,
        "services": services,
        "routers": routers,
        "relationships": relationships,
    }
