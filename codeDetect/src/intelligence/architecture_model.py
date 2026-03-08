"""Deterministic architecture metadata extraction."""

from __future__ import annotations

from typing import Callable


def build_architecture_model(file_paths: list[str], read_file: Callable[[str], str | None]) -> dict:
    paths = sorted(str(p) for p in file_paths if str(p).strip())
    lowered = [p.lower().replace("\\", "/") for p in paths]

    has_routes = any("/routes/" in p or p.endswith("/routes") for p in lowered)
    has_controllers = any("/controllers/" in p or p.endswith("/controllers") for p in lowered)
    has_services = any("/services/" in p or p.endswith("/services") for p in lowered)

    top_dirs = set()
    for p in lowered:
        parts = [x for x in p.split("/") if x]
        if parts:
            top_dirs.add(parts[0])

    has_independent_services = sum(1 for d in top_dirs if d.endswith("-service") or d == "services") >= 2
    multi_app_dirs = {"back", "admin", "food", "deliveryagent"}
    has_multi_apps = len([d for d in top_dirs if d in multi_app_dirs]) >= 2

    service_call_markers = ["http://", "https://", "axios.", "fetch(", "requests.", "resttemplate", "webclient"]
    has_service_to_service_calls = False
    for p in paths:
        if not p.lower().endswith((".js", ".ts", ".jsx", ".tsx", ".py", ".java", ".kt")):
            continue
        text = (read_file(p) or "").lower()
        if any(marker in text for marker in service_call_markers):
            has_service_to_service_calls = True
            break

    if has_service_to_service_calls and has_independent_services:
        pattern = "distributed services"
    elif has_multi_apps:
        pattern = "multi-application modular monolith"
    elif has_routes and has_controllers and has_services:
        pattern = "layered"
    elif has_independent_services:
        pattern = "microservice"
    else:
        pattern = "modular monolith"

    layers = []
    if has_routes:
        layers.append("routes")
    if has_controllers:
        layers.append("controllers")
    if has_services:
        layers.append("services")
    if any("/models/" in p for p in lowered):
        layers.append("models")
    if any("/repositories/" in p for p in lowered):
        layers.append("repositories")
    if any("/prisma/" in p or p.endswith("/prisma") for p in lowered):
        layers.append("prisma")
    if any("/utils/" in p or p.endswith("/utils") for p in lowered):
        layers.append("utils")
    if any("/mappers/" in p or p.endswith("/mappers") for p in lowered):
        layers.append("mappers")

    deps = set()
    for p in paths:
        name = p.lower()
        if name.endswith("package.json"):
            text = (read_file(p) or "").lower()
            for marker, label in [
                ("aws-sdk", "aws"),
                ("@aws-sdk", "aws"),
                ("stripe", "stripe"),
                ("twilio", "twilio"),
                ("sendgrid", "sendgrid"),
            ]:
                if marker in text:
                    deps.add(label)

    return {
        "pattern": pattern,
        "layers": sorted(set(layers)),
        "external_dependencies": sorted(deps),
    }
