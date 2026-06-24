import os
import re
from typing import Any, Dict, List
from src.file_filter import FileFilter
from src.intelligence.evidence.context import AnalysisContext

def _basename(path: str) -> str:
    return os.path.basename(path)

def build_services(
    context: AnalysisContext,
    components: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    services: List[Dict[str, Any]] = []
    seen: set[str] = set()

    _JS_METHOD_RX = re.compile(r"^\s{2,}(?:async\s+)?([a-z_]\w*)\s*\(", re.MULTILINE)
    _JS_SKIP_NAMES = {"if", "for", "while", "switch", "catch", "return", "throw",
                      "function", "constructor", "super", "new", "await", "else"}

    file_to_component: Dict[str, str] = {}
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

    for path in sorted(context.file_paths):
        if FileFilter.should_exclude_from_analysis(path):
            continue
        lower = path.lower().replace("\\", "/")
        feats = context.features_map.get(path, {})
        classes = feats.get("classes", [])
        methods = feats.get("methods", []) or feats.get("functions", []) or []
        annotations = feats.get("annotations", [])
        exported_classes = feats.get("exported_classes", [])

        in_services_dir = (
            "/services/" in lower or lower.startswith("services/") or
            "/repositories/" in lower or lower.startswith("repositories/")
        )

        orig_basename = os.path.basename(path)
        is_service_file = re.match(r'^(.+\.service\.(js|ts|jsx|tsx)|.+Service\.(js|ts|jsx|tsx))$', orig_basename) is not None
        has_service_ann = any(
            "@Service" in ann or "@Injectable" in ann
            for ann in annotations
        )

        if in_services_dir or has_service_ann or is_service_file:
            candidates = (
                exported_classes if (is_service_file and exported_classes) else classes
            )
            svc_type = "service_module"

            resolved_methods: List[str] = sorted(set(str(m) for m in methods if str(m) not in _JS_SKIP_NAMES))
            if not resolved_methods and lower.endswith((".js", ".ts", ".jsx", ".tsx")):
                content = context.read_file(path) or ""
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

            if not candidates:
                name = os.path.splitext(_basename(path))[0]
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


def build_repositories(
    context: AnalysisContext,
    components: List[Dict[str, Any]],
    entities: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    repositories: List[Dict[str, Any]] = []
    seen: set[str] = set()

    file_to_component: Dict[str, str] = {}
    for comp in components:
        for f in comp.get("files", []):
            file_to_component[f] = comp["name"]

    entity_names = {e["name"] for e in entities}

    for path in sorted(context.file_paths):
        if FileFilter.should_exclude_from_analysis(path):
            continue
        lower = path.lower().replace("\\", "/")
        feats = context.features_map.get(path, {})
        classes = feats.get("classes", [])
        annotations = feats.get("annotations", [])
        exported_classes = feats.get("exported_classes", [])

        in_repos_dir = "/repositories/" in lower or lower.startswith("repositories/")
        orig_basename = os.path.basename(path)
        is_repo_file = re.match(r'^(.+\.repository\.(js|ts|jsx|tsx)|.+Repository\.(js|ts|jsx|tsx))$', orig_basename) is not None
        has_repo_ann = any("@Repository" in ann for ann in annotations)

        if in_repos_dir or has_repo_ann or is_repo_file:
            candidates = exported_classes if (is_repo_file and exported_classes) else classes
            content = context.read_file(path) or ""
            
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
