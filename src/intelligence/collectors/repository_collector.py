import os
import re
from typing import Dict, List, Any
from src.file_filter import FileFilter
from src.intelligence.evidence.context import AnalysisContext

# Noise file names that should not become standalone component entries.
_NOISE_COMPONENT_NAMES: frozenset[str] = frozenset({
    ".gitignore", ".eslintrc", ".eslintrc.js", ".eslintrc.json",
    ".env", ".env.example", ".envexample", ".babelrc",
    "readme.md", "readme", "license", "license.md", "changelog.md",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "tsconfig.json", "jsconfig.json", "vite.config.js", "vite.config.ts",
    "webpack.config.js", ".prettierrc",
    "dockerfile", "docker-compose.yml",
})

def _module_key(path: str) -> str:
    """Derive domain module names from folder structure.

    Example:
      routes/auth, controllers/auth, services/auth -> auth
    """
    normalized_path = path.replace("\\", "/").strip("/")
    parts = [p for p in normalized_path.split("/") if p]
    if not parts:
        return "root"

    # Strong module-domain hint.
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
        # routes/auth/index.js -> auth
        if idx + 2 < len(parts):
            after = parts[idx + 2]
            if "." in after:
                return next_part.lower()
        # routes/auth.routes.js -> auth
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

def _classify_component_type(file_paths: List[str]) -> str:
    """Classify a set of files as a component type.

    Frontend directories take priority. If files live under
    frontend/client/ui/web or src/features/src/pages/src/components,
    always classify as frontend_module.
    """
    lowered = [p.lower() for p in file_paths]
    _FRONTEND_DIRS = ["/frontend/", "/client/", "/ui/", "/web/"]
    _FRONTEND_SRC_DIRS = ["/src/features/", "/src/pages/", "/src/components/"]
    has_frontend = any(
        any(tok in p for tok in _FRONTEND_DIRS + _FRONTEND_SRC_DIRS)
        or p.endswith((".tsx", ".jsx"))
        for p in lowered
    )
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

def _detect_framework(file_paths: List[str], features_map: Dict[str, Dict],
                      tech_stack: Dict) -> str | None:
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

def build_components(context: AnalysisContext, tech_stack: Dict) -> List[Dict[str, Any]]:
    """Group files into logical components/modules."""
    modules: Dict[str, List[str]] = {}
    for path in context.file_paths:
        if FileFilter.should_exclude_from_analysis(path):
            continue
        basename = os.path.basename(path).lower()
        if basename in _NOISE_COMPONENT_NAMES:
            key = _module_key(path)
            if key == basename or key == os.path.splitext(basename)[0]:
                continue
        key = _module_key(path)
        modules.setdefault(key, []).append(path)

    components: List[Dict[str, Any]] = []
    for name, files in sorted(modules.items()):
        if len(files) == 1 and os.path.basename(files[0]).lower() in _NOISE_COMPONENT_NAMES:
            continue
        comp_type = _classify_component_type(files)
        framework = _detect_framework(files, context.features_map, tech_stack)
        components.append({
            "name": name,
            "path": os.path.dirname(files[0]) if files else "",
            "type": comp_type,
            "framework": framework,
            "files": sorted(files),
        })
    return sorted(components, key=lambda c: c.get("name", ""))
