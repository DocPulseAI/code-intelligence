import sys
import json
import os
import datetime
from typing import Optional, Union
import re
import subprocess
import shutil
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from src.git_manager import GitManager
from git import InvalidGitRepositoryError
from src.file_filter import FileFilter, ConfigLoader
from src.scorers import SeverityCalculator
from src.parsers.schema_detector import SchemaDetector
from src.parsers.tree_sitter_engine import parse_code, TreeSitterEngine
from src.indexing.scip import extract_scip_symbols
from src.serialization.protobuf_serializer import serialize_ast_record
from src.baseline_store import BaselineStore
from src.breaking_change_engine import compare_reports
from src.risk_scoring import score_report_risk

LOG = logging.getLogger("epic1")
if not LOG.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )


class AnalysisError(Exception):
    def __init__(self, stage: str, details: str, retry_possible: bool):
        super().__init__(details)
        self.stage = stage
        self.details = details
        self.retry_possible = retry_possible


def _redact_token(token: Optional[str]) -> str:
    if not token:
        return ""
    if len(token) <= 6:
        return "***"
    return f"{token[:3]}...{token[-3:]}"


def _detect_language_from_extension(ext: str) -> Optional[str]:
    """Map file extension to language name used in reports."""
    lang = TreeSitterEngine.LANGUAGE_MAP.get(ext)
    if not lang:
        return None
    if lang == "javascript":
        return "javascript"
    if lang in {"typescript", "tsx"}:
        return "typescript"
    return lang


def _normalize_api_endpoints(features: dict) -> list[dict]:
    """
    Normalize endpoint-like entries into a common shape.
    Tree-sitter extractors may emit either api_endpoints or api_routes.
    """
    endpoints: list[dict] = []

    raw_endpoints = features.get("api_endpoints", [])
    if isinstance(raw_endpoints, list):
        for ep in raw_endpoints:
            if isinstance(ep, dict):
                endpoints.append(ep)

    raw_routes = features.get("api_routes", [])
    if isinstance(raw_routes, list):
        for route_item in raw_routes:
            if not isinstance(route_item, dict):
                continue
            endpoints.append({
                "verb": route_item.get("method") or route_item.get("verb") or "GET",
                "route": route_item.get("route") or route_item.get("path") or "",
                "line": route_item.get("line", 0),
                "handler": route_item.get("handler") or ""
            })

    return endpoints


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
        return value if value > 0 else default
    except Exception:
        return default


def _sanitize_api_endpoints(endpoints: list[dict]) -> list[dict]:
    """Keep only deterministically valid endpoint entries."""
    valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
    out: list[dict] = []
    seen = set()
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        method = str(ep.get("verb") or ep.get("method") or "GET").upper().strip()
        route = str(ep.get("route") or ep.get("path") or "").strip()
        line = int(ep.get("line", 0) or 0)
        if method not in valid_methods or not route.startswith("/"):
            continue
        key = (method, route, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"verb": method, "route": route, "line": line, "handler": ep.get("handler") or ""})
    return out


def _parse_and_index_file(file_path: str, ext: str, content: str) -> dict:
    """
    CPU-bound parsing/indexing task suitable for ProcessPool execution.
    Returns parse result + SCIP symbols + protobuf payload size.
    """
    language = _detect_language_from_extension(ext)
    parse_result = parse_code(content, ext)
    syntax_error = bool(parse_result.get("syntax_error"))
    features = parse_result.get("features", {}) or {}
    symbols = extract_scip_symbols(file_path, language, features)
    payload = serialize_ast_record(file_path, language, syntax_error, features, symbols)
    return {
        "file": file_path,
        "language": language,
        "parse_result": parse_result,
        "symbol_count": len(symbols),
        "protobuf_size_bytes": len(payload),
    }


def _build_auth_url(repo_url: str, github_token: Optional[str]) -> str:
    if github_token and repo_url.startswith("https://github.com/"):
        return repo_url.replace("https://github.com/", f"https://{github_token}@github.com/")
    return repo_url


def _run_git_ls_remote(repo_url: str, github_token: Optional[str], branch: Optional[str]) -> subprocess.CompletedProcess:
    auth_url = _build_auth_url(repo_url, github_token)
    args = ["git", "ls-remote"]
    if branch:
        args.extend(["--heads", auth_url, branch])
    else:
        args.append(auth_url)
    timeout_s = int(os.environ.get("CODE_DETECT_LS_REMOTE_TIMEOUT_SEC", "45"))
    return subprocess.run(args, capture_output=True, text=True, timeout=max(10, timeout_s))


def _validate_dependencies() -> None:
    if not shutil.which("git"):
        raise AnalysisError("analysis", "git is not installed or not on PATH", False)
    try:
        import yaml  # noqa: F401
    except Exception:
        raise AnalysisError("analysis", "python dependency PyYAML is missing", False)


def _validate_remote_repo(repo_url: str, github_token: Optional[str], branch: str) -> None:
    retries = int(os.environ.get("CODE_DETECT_LS_REMOTE_RETRIES", "2"))
    proc = None
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            proc = _run_git_ls_remote(repo_url, github_token, branch)
            last_error = None
            break
        except subprocess.TimeoutExpired as e:
            last_error = e
            if attempt < retries:
                continue
        except Exception as e:
            last_error = e
            break
    if proc is None:
        raise AnalysisError(
            "clone",
            f"Failed to reach remote repository after {retries + 1} attempt(s): {last_error}",
            True
        )

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if github_token:
            stderr = stderr.replace(github_token, "***")
        redacted = _redact_token(github_token)
        if "Authentication failed" in stderr or "fatal: could not read" in stderr or "403" in stderr:
            raise AnalysisError(
                "clone",
                f"Git authentication failed for repo_url with token {redacted}",
                False
            )
        raise AnalysisError("clone", f"Unable to access repository: {stderr or 'unknown error'}", True)

    if not (proc.stdout or "").strip():
        raise AnalysisError("clone", f"Branch '{branch}' does not exist on remote", False)


def _validate_local_repo(repo_path: str) -> None:
    if not os.path.exists(repo_path):
        raise AnalysisError("clone", f"Repository path does not exist: {repo_path}", False)
    if not os.path.isdir(repo_path):
        raise AnalysisError("clone", f"Repository path is not a directory: {repo_path}", False)


def _preflight_validate(repo_path_or_url: str, github_token: Optional[str], branch: str, skip_remote_preflight: bool = False) -> None:
    _validate_dependencies()
    if repo_path_or_url.startswith(("http://", "https://", "git@")):
        if not skip_remote_preflight:
            _validate_remote_repo(repo_path_or_url, github_token, branch)
    else:
        _validate_local_repo(repo_path_or_url)


def _write_report(report: dict) -> None:
    output_path = os.path.join(os.path.dirname(__file__), "impact_report.json")
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)


def _build_fallback_report(repo_path_or_url: str,
                           branch: str,
                           failure_reason: str,
                           context: Optional[dict] = None) -> dict:
    working = {
        "repo": {
            "path_or_url": repo_path_or_url,
            "branch": branch
        },
        "context": context or {},
        "analysis_summary": {
            "total_files": 0,
            "highest_severity": "UNKNOWN",
            "breaking_changes_detected": False
        },
        "changes": [],
        "api_contract": {
            "endpoints": []
        },
        "indexing": {
            "format": "SCIP",
            "symbol_count": 0
        },
        "serialization": {
            "format": "protobuf",
            "total_payload_bytes": 0
        },
        "extraction_quality": {
            "api_endpoint_count": 0,
            "invalid_endpoint_count": 0,
            "normalized_endpoint_count": 0,
            "warnings": []
        },
        "analysis_status": "PARTIAL",
        "failure_reason": failure_reason
    }
    return _to_canonical_v3({"meta": {"generated_at": datetime.datetime.utcnow().isoformat() + "Z", "tool_version": "1.0.0"}, **working}, status="partial")

def _dedupe_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _project_id_from_context(context: dict) -> str:
    repository = str(context.get("repository", "")).strip() or "unknown-repo"
    author = str(context.get("author", "")).strip()
    return f"{author}/{repository}" if author else repository


def _classify_component(file_path: str, content: Optional[str]) -> str:
    path = file_path.lower()
    tokens = [t for t in path.replace("\\", "/").split("/") if t]

    def has_any(keys: list[str]) -> bool:
        return any(k in path for k in keys)

    if has_any(["/auth", "authentication", "login", "signup", "oauth", "jwt"]):
        return "authentication"
    if has_any(["/security", "crypto", "encryption", "tls", "ssl", "cipher"]):
        return "security"
    if has_any(["/db", "database", "schema", "migration", "migrations", "/model", "/models"]):
        return "database"
    if has_any(["/frontend", "/ui", "/client", "/web", "/pages", "/components", "/views"]):
        return "frontend"
    if "quiz" in tokens or "quiz" in path:
        return "quiz"
    return "other"


def _detect_security_features(content: str) -> list[str]:
    text = content.lower()
    detected = []
    patterns = [
        (["mfa", "multi-factor", "multi factor", "two-factor", "2fa"], "MFA"),
        (["jwt", "json web token"], "JWT Authentication"),
        (["oauth", "openid"], "OAuth"),
        (["aes-256", "aes256"], "AES-256 Encryption"),
        (["encrypt", "encryption", "decrypt", "decryption"], "Encryption"),
        (["bcrypt", "scrypt", "argon2", "pbkdf2"], "Password Hashing"),
        (["tls", "ssl"], "TLS/SSL"),
        (["xss", "csrf", "sqli", "sql injection"], "Web Vulnerability Mitigation"),
    ]
    for keys, label in patterns:
        if any(k in text for k in keys):
            detected.append(label)
    return _dedupe_order(detected)


def _extract_packages(file_path: str, content: str, language: Optional[str]) -> list[str]:
    packages: list[str] = []
    if language == "java":
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("package "):
                pkg = line[len("package "):].rstrip(";").strip()
                if pkg:
                    packages.append(pkg)
                break
    elif language == "python":
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("import "):
                pkg = line[len("import "):].split(" as ")[0].split(",")[0].strip()
                if pkg:
                    packages.append(pkg)
            elif line.startswith("from "):
                pkg = line[len("from "):].split(" import ")[0].strip()
                if pkg:
                    packages.append(pkg)
    elif language in {"javascript", "typescript"}:
        for line in content.splitlines():
            line = line.strip()
            if " from " in line and ("import " in line or "export " in line):
                parts = line.split(" from ")
                if len(parts) > 1:
                    mod = parts[1].strip().strip(";").strip("'\"")
                    if mod:
                        packages.append(mod)
            if "require(" in line:
                start = line.find("require(")
                if start != -1:
                    frag = line[start + len("require("):].strip()
                    if frag.startswith(("'", '"')):
                        mod = frag.split(frag[0], 2)[1]
                        if mod:
                            packages.append(mod)
    return _dedupe_order(packages)


def _is_valid_package_entry(pkg: str) -> bool:
    if not isinstance(pkg, str):
        return False
    value = pkg.strip()
    if not value:
        return False
    if any(ch in value for ch in ['"', "'"]):
        return False
    if "#" in value:
        return False
    if "//" in value:
        return False
    if re.search(r"\s", value):
        return False
    return True


def _normalize_method(method: Optional[str]) -> tuple[str, list[str], bool]:
    valid = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
    warnings: list[str] = []
    raw = (method or "GET").upper().strip()
    if raw in valid:
        return raw, warnings, False
    warnings.append(f"Unsupported method '{method}', defaulted to GET")
    return "GET", warnings, True


def _split_concat_route_token(token: str) -> str:
    if "/" in token or not token:
        return token
    prefixes = [
        "question", "questions", "quiz", "quizzes", "user", "users", "auth", "api",
        "course", "courses", "lesson", "lessons", "admin", "profile", "payment", "order"
    ]
    for p in sorted(prefixes, key=len, reverse=True):
        if token.startswith(p) and len(token) > len(p):
            tail = token[len(p):]
            if tail.startswith("-"):
                tail = tail[1:]
            if tail:
                return f"{p}/{tail}"
    return token


def _normalize_path(path: Optional[str]) -> tuple[str, list[str], bool]:
    warnings: list[str] = []
    invalid = False
    raw = (path or "").strip()
    if not raw:
        warnings.append("Missing route path, defaulted to '/'")
        return "/", warnings, True

    normalized = raw
    normalized = re.sub(r'["\']', "", normalized).strip()
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"/{2,}", "/", normalized)
    normalized = normalized.replace(":id", "{id}")
    normalized = re.sub(r":([A-Za-z_]\w*)", r"{\1}", normalized)

    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
        warnings.append("Route prefixed with '/'")

    parts = [p for p in normalized.split("/") if p]
    fixed_parts: list[str] = []
    for part in parts:
        fixed = _split_concat_route_token(part)
        if fixed != part:
            warnings.append(f"Normalized concatenated route segment '{part}' -> '{fixed}'")
        fixed_parts.extend([x for x in fixed.split("/") if x])
    normalized = "/" + "/".join(fixed_parts) if fixed_parts else "/"
    normalized = re.sub(r"/{2,}", "/", normalized)

    if not normalized.startswith("/"):
        invalid = True
    return normalized, warnings, invalid


def _sanitize_operation_id(method: str, path: str) -> str:
    parts = [method.lower()]
    for seg in path.strip("/").split("/"):
        if not seg:
            continue
        clean = seg.replace("{", "").replace("}", "")
        clean = re.sub(r"[^a-zA-Z0-9_]", "_", clean)
        parts.append(clean.lower())
    return "_".join(parts) or f"{method.lower()}_root"


def _titleize(value: str) -> str:
    token = value.replace("-", " ").replace("_", " ").strip()
    return token.title() if token else "Resource"


def _is_balanced_angles(value: str) -> bool:
    depth = 0
    for ch in value:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _sanitize_schema_ref(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if candidate.startswith("#/components/schemas/"):
        name = candidate.split("#/components/schemas/", 1)[1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            return candidate
        return None
    candidate = re.sub(r"\s+", "", candidate)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_<>,.]*", candidate):
        return None
    if "<" in candidate and not _is_balanced_angles(candidate):
        return None
    return candidate


def _build_summary(method: str, path: str) -> str:
    segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    resource = segments[0] if segments else "resource"
    tail = segments[-1] if segments else resource
    has_id = any(s.startswith("{") and s.endswith("}") for s in path.strip("/").split("/"))
    action_map = {
        "GET": "List" if not has_id else "Get",
        "POST": "Create",
        "PUT": "Replace",
        "PATCH": "Update",
        "DELETE": "Delete"
    }
    action = action_map.get(method, method.title())
    special_tail_actions = {
        "create": "Create",
        "update": "Update",
        "delete": "Delete",
        "search": "Search",
        "login": "Authenticate",
        "logout": "Logout",
        "register": "Register",
        "health": "Check",
        "status": "Get"
    }
    if tail in special_tail_actions:
        return f"{special_tail_actions[tail]} {_titleize(resource)}".strip()[:80]
    tail_hint = ""
    if tail and tail not in {resource, "create"} and not has_id:
        tail_hint = f" ({tail.replace('-', ' ')})"
    summary = f"{action} {_titleize(resource)}{tail_hint}".strip()
    if summary.lower() in {"list hello", "get hello", "create hello"}:
        return f"{action} {_titleize(tail if tail != resource else 'resource')}"[:80]
    if summary.lower().endswith(" resource"):
        summary = f"{action} {_titleize(resource)} endpoint"
    return summary[:80]


def _build_description(method: str, path: str, summary: str, auth_required: Optional[bool]) -> str:
    auth_clause = "requires authentication" if auth_required is True else "is publicly accessible" if auth_required is False else "has unknown authentication requirements"
    return f"{summary}. Handles {method} requests for `{path}` and {auth_clause}."


def _infer_handler_name(candidate: dict, method: str, path: str) -> Optional[str]:
    handler = str(candidate.get("handler") or "").strip()
    if handler and handler != "unknown_handler":
        return handler
    content = candidate.get("content") or ""
    line_start = int(candidate.get("line_start", 0) or 0)
    lines = content.splitlines()
    if content and line_start > 0 and line_start <= len(lines):
        window = "\n".join(lines[line_start - 1: min(len(lines), line_start + 8)])
        route_rx = re.search(
            r'\b(?:app|router|api|server)\.(?:get|post|put|patch|delete)\s*\([^)]*[\'"][^\'"]+[\'"]\s*,\s*(?:async\s+)?([A-Za-z_]\w*)',
            window
        )
        if route_rx:
            return route_rx.group(1)
        js_inline = re.search(
            r'\b(?:app|router|api|server)\.(?:get|post|put|patch|delete)\s*\([^)]*,\s*(?:async\s+)?(?:function\s+)?([A-Za-z_]\w*)\s*\(',
            window
        )
        if js_inline:
            return js_inline.group(1)
        java_method = re.search(
            r'\b(?:public|private|protected)\s+[\w<>\[\],\s]+\s+([A-Za-z_]\w*)\s*\(',
            window
        )
        if java_method:
            return java_method.group(1)
        py_def = re.search(r'^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(', window, re.MULTILINE)
        if py_def:
            return py_def.group(1)
    return None


def _infer_query_params(path: str, content: str, method: str) -> list[dict]:
    params: list[dict] = []
    parsed = re.findall(r'[?&]([A-Za-z_]\w*)=', path)
    for p in _dedupe_order(parsed):
        params.append({"name": p, "type": "string", "required": False, "description": f"{p} query parameter"})

    if method == "GET":
        common = ["page", "limit", "offset", "sort", "order", "search", "q", "filter"]
        text = (content or "").lower()
        for p in common:
            if re.search(rf"\b{re.escape(p)}\b", text):
                if not any(item["name"] == p for item in params):
                    params.append({"name": p, "type": "string", "required": False, "description": f"{p} query parameter"})
    return params


def _infer_body_schema(candidate: dict, method: str) -> tuple[Optional[dict], bool]:
    empty = {"type": "object", "properties": {}, "required": []}
    if method in {"GET", "DELETE", "HEAD", "OPTIONS"}:
        return None, True

    content = candidate.get("content") or ""
    line_start = int(candidate.get("line_start", 0) or 0)
    lines = content.splitlines()
    snippet = content
    if line_start > 0 and line_start <= len(lines):
        snippet = "\n".join(lines[max(0, line_start - 1): min(len(lines), line_start + 40)])

    has_body_indicator = bool(re.search(r"(req\.body|request\.body|@RequestBody|\bbody\s*:|Body\(|payload)", snippet))
    props: list[str] = []
    props.extend(re.findall(r'req\.body\.([A-Za-z_]\w*)', snippet))
    props.extend(re.findall(r'request\.body\.([A-Za-z_]\w*)', snippet))
    props.extend(re.findall(r'body\.get\(\s*[\'"]([A-Za-z_]\w*)[\'"]', snippet))
    props.extend(re.findall(r'[\'"]([A-Za-z_]\w*)[\'"]\s*:\s*(?:req\.body|request\.body)', snippet))
    props = _dedupe_order(props)
    java_body_type = re.search(r'@RequestBody\s+([A-Z][A-Za-z0-9_<>,.]*)', snippet)
    if java_body_type:
        body_type = _sanitize_schema_ref(java_body_type.group(1))
        if not body_type:
            body_type = None
        return {
            "type": "object",
            **({"schema_ref": body_type} if body_type else {}),
            "properties": {},
            "required": []
        }, True
    java_param_type = re.search(
        r'\(\s*(?:@Valid\s+)?@RequestBody\s+([A-Z][A-Za-z0-9_<>,.]*)\s+[A-Za-z_]\w*',
        snippet
    )
    if java_param_type:
        body_type = _sanitize_schema_ref(java_param_type.group(1))
        if not body_type:
            body_type = None
        return {
            "type": "object",
            **({"schema_ref": body_type} if body_type else {}),
            "properties": {},
            "required": []
        }, True
    if not has_body_indicator and not props:
        return empty, False

    properties = {name: {"type": "string"} for name in props[:20]}
    required = []
    for name in props[:20]:
        if re.search(rf"\b(required|notnull|not_blank)\b.*\b{name}\b", snippet, re.IGNORECASE):
            required.append(name)
    return {"type": "object", "properties": properties, "required": _dedupe_order(required)}, True


def _infer_responses(candidate: dict, method: str, auth_required: bool) -> tuple[list[dict], bool]:
    content = candidate.get("content") or ""
    line_start = int(candidate.get("line_start", 0) or 0)
    lines = content.splitlines()
    snippet = content
    if line_start > 0 and line_start <= len(lines):
        snippet = "\n".join(lines[max(0, line_start - 1): min(len(lines), line_start + 80)])

    status_desc = {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        422: "Validation Error",
        500: "Internal Server Error"
    }
    default_by_method = {"GET": [200], "POST": [200], "PUT": [200], "PATCH": [200], "DELETE": [204]}
    codes = set(default_by_method.get(method, [200]))
    for m in re.findall(r'\b(?:status|sendStatus|code)\s*\(?\s*(\d{3})\b', snippet):
        codes.add(int(m))
    for m in re.findall(r'\b(2\d\d|4\d\d|5\d\d)\b', snippet):
        val = int(m)
        if val in status_desc:
            codes.add(val)
    if auth_required:
        codes.update({401, 403})
    if re.search(r'(invalid|validation|bad request|exception)', snippet, re.IGNORECASE):
        codes.add(400)
    if re.search(r'(not\s+found|throw\s+new\s+\w*NotFound|404)', snippet, re.IGNORECASE):
        codes.add(404)
    if method in {"GET", "PUT", "PATCH", "DELETE"} and re.search(r'\{[A-Za-z_]\w*\}', candidate.get("path", "")):
        codes.add(404)
    if method in {"POST", "PUT", "PATCH"}:
        codes.add(400)
    codes.add(500)

    schema_ref = None
    schema_match = re.search(r'\b([A-Z][A-Za-z0-9_<>,.]*(?:Response|Dto|DTO|Result))\b', snippet)
    if schema_match:
        schema_ref = _sanitize_schema_ref(schema_match.group(1))

    responses = []
    for code in sorted(codes):
        entry = {"status": code, "description": status_desc.get(code, f"HTTP {code}")}
        if schema_ref and 200 <= code < 300:
            entry["schema_ref"] = schema_ref
        responses.append(entry)
    resolved = len(responses) > 0 and any(r["status"] in {200, 201, 204} for r in responses)
    return responses, resolved


def _score_endpoint(handler_resolved: bool, auth_resolved: bool, request_resolved: bool, responses_resolved: bool) -> float:
    score = 0.0
    if handler_resolved:
        score += 0.25
    if auth_resolved:
        score += 0.25
    if request_resolved:
        score += 0.25
    if responses_resolved:
        score += 0.25
    return round(max(0.0, min(1.0, score)), 2)


def _extract_path_params(path: str) -> list[dict]:
    params = re.findall(r"\{([A-Za-z_]\w*)\}", path)
    result = []
    for name in _dedupe_order(params):
        result.append({
            "name": name,
            "type": "string",
            "required": True,
            "description": f"{name} path parameter"
        })
    return result


def _detect_auth_and_middleware(content: str) -> tuple[Optional[bool], str, list[str]]:
    text = (content or "").lower()
    middleware: list[str] = []
    for token in ["auth", "authenticate", "jwt", "passport", "session", "apikey", "api_key", "bearer", "oauth", "oauth2"]:
        if re.search(rf"\b{re.escape(token)}\b", text):
            middleware.append(token)
    middleware = _dedupe_order(middleware)
    if not middleware:
        return None, "Unknown", []
    if any(t in middleware for t in ["jwt", "bearer", "passport"]):
        return True, "JWT", middleware
    if any(t in middleware for t in ["session"]):
        return True, "Session", middleware
    if any(t in middleware for t in ["apikey", "api_key"]):
        return True, "APIKey", middleware
    if any(t in middleware for t in ["oauth", "oauth2"]):
        return True, "OAuth2", middleware
    return True, "Unknown", middleware


def _build_api_contract_endpoints(candidates: list[dict]) -> tuple[list[dict], dict]:
    endpoints: list[dict] = []
    invalid_endpoint_count = 0
    normalized_endpoint_count = 0
    unresolved_auth_count = 0
    unresolved_request_count = 0
    unresolved_response_count = 0

    dedupe_seen = set()
    for c in candidates:
        method, method_warnings, method_invalid = _normalize_method(c.get("method"))
        path, path_warnings, path_invalid = _normalize_path(c.get("path"))
        warnings = method_warnings + path_warnings + list(c.get("warnings", []))
        invalid = method_invalid or path_invalid
        if invalid:
            invalid_endpoint_count += 1
        if warnings:
            normalized_endpoint_count += 1

        normalized_key = f"{method} {path}"
        dedupe_key = (normalized_key, c.get("source_file", ""), c.get("line_start", 0))
        if dedupe_key in dedupe_seen:
            continue
        dedupe_seen.add(dedupe_key)

        auth_required, auth_type, middleware = _detect_auth_and_middleware(c.get("content", ""))
        path_lower = path.lower()
        if auth_required is None and any(p in path_lower for p in ["/login", "/register", "/health", "/public", "/status"]):
            auth_required = False
            if auth_type == "Unknown":
                auth_type = "Unknown"
        path_params = _extract_path_params(path)
        query_params = _infer_query_params(path, c.get("content", ""), method)
        body_schema, body_resolved = _infer_body_schema(c, method)
        responses, responses_resolved = _infer_responses(c, method, bool(auth_required))
        tag = path.strip("/").split("/")[0] if path.strip("/") else "general"
        summary = c.get("summary") if c.get("summary") and not re.match(rf"^{method}\s+{re.escape(path)}$", str(c.get('summary')).strip(), re.IGNORECASE) else _build_summary(method, path)
        handler = _infer_handler_name(c, method, path)
        handler_resolved = bool(handler)
        auth_resolved = auth_type != "Unknown" or auth_required is not None
        request_resolved = bool(path_params or query_params or body_resolved)
        endpoint_warnings = list(warnings)
        if not auth_resolved:
            endpoint_warnings.append("Authentication could not be resolved from source")
            unresolved_auth_count += 1
        if not request_resolved:
            endpoint_warnings.append("Request schema/params could not be confidently inferred")
            unresolved_request_count += 1
        if not responses_resolved:
            endpoint_warnings.append("Response contract inferred from defaults only")
            unresolved_response_count += 1
        confidence = _score_endpoint(handler_resolved, auth_resolved, request_resolved, responses_resolved)

        endpoint = {
            "operation_id": _sanitize_operation_id(method, path),
            "method": method,
            "path": path,
            "normalized_key": normalized_key,
            "summary": summary,
            "description": _build_description(method, path, summary, auth_required),
            "tags": [tag],
            "auth": {
                "required": auth_required,
                "type": auth_type,
                "middleware": middleware
            },
            "request": {
                "path_params": path_params,
                "query_params": query_params,
                "body_schema": body_schema
            },
            "responses": responses,
            "example": {
                "curl": f"curl -X {method} '<base-url>{path}' ..."
            },
            "source": {
                "file": c.get("source_file", ""),
                "line_start": int(c.get("line_start", 0) or 0),
                "line_end": max(int(c.get("line_end", 0) or 0), int(c.get("line_start", 0) or 0)),
                "handler": handler
            },
            "confidence": confidence,
            "warnings": _dedupe_order(endpoint_warnings)
        }
        endpoints.append(endpoint)

    quality_warnings: list[str] = []
    if invalid_endpoint_count:
        quality_warnings.append(f"{invalid_endpoint_count} endpoint(s) had invalid method/path and were normalized")
    if normalized_endpoint_count:
        quality_warnings.append(f"{normalized_endpoint_count} endpoint(s) required normalization adjustments")
    if unresolved_auth_count:
        quality_warnings.append(f"{unresolved_auth_count} endpoint(s) have unresolved authentication type")
    if unresolved_request_count:
        quality_warnings.append(f"{unresolved_request_count} endpoint(s) have incomplete request metadata")
    if unresolved_response_count:
        quality_warnings.append(f"{unresolved_response_count} endpoint(s) rely on inferred/default response metadata")
    extraction_quality = {
        "api_endpoint_count": len(endpoints),
        "invalid_endpoint_count": invalid_endpoint_count,
        "normalized_endpoint_count": normalized_endpoint_count,
        "warnings": quality_warnings
    }
    return endpoints, extraction_quality


def _build_doc_contract(report: dict) -> dict:
    endpoints = report.get("api_contract", {}).get("endpoints", [])
    components = report.get("component_distribution", {})
    security_features = report.get("security_features_detected", [])
    db_impact = report.get("database_impact", {})
    config = report.get("configuration_changes", {})
    extraction_quality = report.get("extraction_quality", {})
    change_summary = report.get("change_summary", "")
    impact_scope = report.get("impact_scope", "INTERNAL_ONLY")

    changed_components = [name for name, count in components.items() if count > 0]
    key_features = _dedupe_order(list(report.get("feature_tags", [])))
    risk_highlights = []
    if report.get("analysis_summary", {}).get("breaking_changes_detected"):
        risk_highlights.append("Breaking behavior changes detected")
    if db_impact.get("schema_changed"):
        risk_highlights.append("Database schema changes require migration validation")
    if security_features:
        risk_highlights.append("Security-sensitive changes require focused review")
    if extraction_quality.get("invalid_endpoint_count", 0):
        risk_highlights.append("Some endpoints required normalization from raw route extraction")

    deployment_notes = []
    if config.get("docker"):
        deployment_notes.append("Container/deployment definitions were modified")
    if config.get("build_files"):
        deployment_notes.append("Build pipeline or package manifests changed")
    if config.get("environment_variables"):
        deployment_notes.append("Environment variable contract updated")

    architecture_components = []
    for name in changed_components:
        ctype = "service"
        if name == "database":
            ctype = "data"
        elif name in {"frontend"}:
            ctype = "client"
        elif name in {"security", "authentication"}:
            ctype = "security"
        architecture_components.append({
            "name": name,
            "type": ctype,
            "responsibility": f"Owns {name} related behavior in this change set"
        })

    data_stores = []
    if db_impact.get("schema_changed") or db_impact.get("tables_affected"):
        data_stores.append({
            "name": "primary-database",
            "type": "relational",
            "entities": db_impact.get("tables_affected", [])
        })

    external_integrations = []
    if any(ep.get("auth", {}).get("type") == "JWT" for ep in endpoints):
        external_integrations.append({
            "name": "auth-provider",
            "auth": "JWT",
            "purpose": "Token verification for protected endpoints"
        })

    interactions = []
    for ep in endpoints[:50]:
        interactions.append({
            "from": "client",
            "to": ep.get("source", {}).get("handler", "service"),
            "protocol": "HTTP",
            "purpose": ep.get("summary", "Serve API request")
        })

    sequence_steps = [
        {"actor": "client", "action": "Send API request", "target": "http-endpoint"},
        {"actor": "backend", "action": "Validate/authenticate request", "target": "middleware"},
        {"actor": "backend", "action": "Execute handler logic", "target": "service/data-store"},
        {"actor": "backend", "action": "Return HTTP response", "target": "client"}
    ]

    entities = []
    for store in data_stores:
        for e in store.get("entities", []):
            entities.append({"name": e, "fields": []})

    test_impact = report.get("test_impact", {})
    impacted_areas = _dedupe_order(
        changed_components +
        (["api"] if endpoints else []) +
        (["database"] if db_impact.get("schema_changed") else []) +
        (["security"] if security_features else [])
    )

    return {
        "readme_contract": {
            "project_summary": change_summary or "Repository changes analyzed for impact and documentation updates.",
            "key_features": key_features,
            "changed_components": changed_components,
            "risk_highlights": _dedupe_order(risk_highlights),
            "deployment_notes": _dedupe_order(deployment_notes)
        },
        "api_contract": {
            "endpoints": endpoints
        },
        "adr_contract": {
            "decision_title": f"{impact_scope.replace('_', ' ').title()} Documentation Update",
            "status": "proposed",
            "context": change_summary or "Changes impact architecture and delivery documentation.",
            "options_considered": [
                "Document only changed files",
                "Publish full EPIC-2 contract set"
            ],
            "selected_option": "Publish full EPIC-2 contract set",
            "consequences_positive": [
                "Downstream documentation generation is deterministic",
                "Reduces guesswork in API and architecture docs"
            ],
            "consequences_negative": [
                "Requires maintaining richer extraction logic"
            ],
            "followups": _dedupe_order(extraction_quality.get("warnings", []))
        },
        "architecture_contract": {
            "components": architecture_components,
            "interactions": interactions,
            "data_stores": data_stores,
            "external_integrations": external_integrations,
            "sequence_steps": sequence_steps,
            "entities": entities
        },
        "quality_contract": {
            "risk_register": [
                {"risk": r, "severity": "MEDIUM", "mitigation": "Review and validate before release"}
                for r in _dedupe_order(risk_highlights)
            ],
            "test_impact": {
                "requires_new_tests": bool(test_impact.get("requires_new_tests", False)),
                "impacted_areas": impacted_areas
            },
            "security_impact": {
                "auth_changes": bool(security_features) or any(ep.get("auth", {}).get("required") is True for ep in endpoints),
                "crypto_changes": any("encrypt" in f.lower() or "tls" in f.lower() for f in security_features),
                "sensitive_paths": _dedupe_order([
                    ep.get("path", "")
                    for ep in endpoints
                    if "auth" in ep.get("path", "") or ep.get("auth", {}).get("required") is True
                ])
            },
            "extraction_quality": {
                "api_endpoint_count": int(extraction_quality.get("api_endpoint_count", 0)),
                "invalid_endpoint_count": int(extraction_quality.get("invalid_endpoint_count", 0)),
                "warnings": list(extraction_quality.get("warnings", []))
            }
        }
    }


def _to_canonical_v3(working: dict,
                     status: str = "success",
                     project_id: Optional[str] = None,
                     baseline_commit: Optional[str] = None,
                     breaking_changes: Optional[list[dict]] = None,
                     risk: Optional[dict] = None) -> dict:
    payload = {k: v for k, v in working.items() if k not in {"schema_version", "status", "meta", "report"}}
    payload["doc_contract"] = _build_doc_contract(payload)
    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    resolved_project_id = project_id or _project_id_from_context(context)
    resolved_branch = str(context.get("branch", "unknown"))
    resolved_commit = str(context.get("full_sha") or context.get("commit_sha") or "")
    resolved_breaking = list(breaking_changes or [])
    resolved_risk = risk or {
        "severity": payload.get("analysis_summary", {}).get("highest_severity", "PATCH"),
        "statistics": {"total_changes": 0, "major": 0, "minor": 0, "patch": 0},
        "deterministic": True,
    }
    top = {
        "schema_version": "epic1-impact/v3",
        "version": "2.0",
        "status": status,
        "meta": working.get("meta", {}),
        "project_id": resolved_project_id,
        "branch": resolved_branch,
        "commit_sha": resolved_commit,
        "baseline_commit": baseline_commit,
        "analysis_summary": payload.get("analysis_summary", {}),
        "breaking_changes": resolved_breaking,
        "statistics": resolved_risk.get("statistics", {}),
        "severity": resolved_risk.get("severity", "PATCH"),
        "deterministic": bool(resolved_risk.get("deterministic", True)),
        "report": payload
    }
    return top


def _classify_intent(message: Optional[str], security_features: list[str]) -> str:
    text = (message or "").lower()
    if security_features:
        return "SECURITY"
    if any(k in text for k in ["fix", "bug", "defect", "hotfix", "patch"]):
        return "BUGFIX"
    if any(k in text for k in ["refactor", "cleanup", "restructure", "rename"]):
        return "REFACTOR"
    if any(k in text for k in ["feat", "feature", "add", "introduce", "implement"]):
        return "FEATURE"
    return "FEATURE"


def _generate_change_summary(message: Optional[str],
                             api_summary: dict,
                             security_features: list[str],
                             has_db_change: bool,
                             components: list[str]) -> str:
    base = (message or "").splitlines()[0].strip()
    clauses = []
    if security_features:
        clauses.append("Adds security enhancements")
    if has_db_change:
        clauses.append("includes database schema changes")
    if api_summary.get("added", 0) or api_summary.get("modified", 0) or api_summary.get("removed", 0):
        clauses.append("updates API endpoints")
    if components:
        clauses.append(f"touches {', '.join(components)} components")

    if base and clauses:
        return f"{base}. " + "; ".join(clauses) + "."
    if base:
        return base + "."
    if clauses:
        return "; ".join(clauses).capitalize() + "."
    return "Updates internal code and configuration."


def _detect_doc_impact(file_path: str, api_summary: dict, has_db_change: bool) -> dict:
    path = file_path.lower()
    return {
        "readme": "readme" in path,
        "api_docs": any(api_summary.values()) or "openapi" in path or "swagger" in path,
        "architecture": "architecture" in path or "/docs/arch" in path or "/design" in path,
        "adr_required": has_db_change or any(api_summary.values())
    }


def _extract_env_vars(content: str) -> list[str]:
    vars_found = []
    patterns = [
        r'\bENV\s+([A-Z][A-Z0-9_]{2,})\b',
        r'\bexport\s+([A-Z][A-Z0-9_]{2,})\b',
        r'process\.env\.([A-Z][A-Z0-9_]{2,})',
        r'os\.environ\[\s*[\'"]([A-Z][A-Z0-9_]{2,})[\'"]\s*\]'
    ]
    for rx in patterns:
        vars_found.extend(re.findall(rx, content))
    return _dedupe_order(vars_found)


def _extract_tables_from_sql(content: str) -> list[str]:
    tables = []
    for match in re.finditer(r'\b(?:CREATE|ALTER|DROP)\s+TABLE\s+[`"]?([A-Za-z_][\w]*)', content, re.IGNORECASE):
        tables.append(match.group(1))
    return _dedupe_order(tables)


def _extract_table_annotations(content: str) -> list[str]:
    tables = []
    for match in re.finditer(r'@Table\s*\(\s*name\s*=\s*["\']([A-Za-z_][\w]*)["\']', content):
        tables.append(match.group(1))
    return _dedupe_order(tables)


def _calc_complexity_score(total_files: int,
                           api_total: int,
                           component_count: int,
                           has_db_change: bool,
                           has_security: bool,
                           breaking: bool) -> float:
    score = 0.0
    score += min(0.6, total_files * 0.03)
    score += min(0.3, api_total * 0.02)
    score += min(0.2, component_count * 0.03)
    if has_db_change:
        score += 0.2
    if has_security:
        score += 0.1
    if breaking:
        score += 0.1
    return round(min(1.0, score), 3)

def _extract_markdown_sections(content: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in content.splitlines():
        heading = re.match(r'^\s{0,3}(#{1,6})\s+(.+)$', line)
        if heading:
            title = heading.group(2).strip().lower()
            current = title
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line.rstrip())
    return sections


def _normalize_section_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        bullet = re.match(r'^[-*+]\s+(.*)$', raw)
        ordered = re.match(r'^\d+\.\s+(.*)$', raw)
        if bullet:
            items.append(bullet.group(1).strip())
        elif ordered:
            items.append(ordered.group(1).strip())
        else:
            items.append(raw)
    return _dedupe_order(items)


def _first_paragraph(lines: list[str]) -> Optional[str]:
    buffer: list[str] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            if buffer:
                break
            continue
        buffer.append(raw)
    if buffer:
        return " ".join(buffer).strip()
    return None


def _detect_license_from_text(content: str) -> Optional[str]:
    candidates = [
        "MIT License", "Apache License", "GNU General Public License",
        "Mozilla Public License", "BSD License", "ISC License"
    ]
    for name in candidates:
        if name.lower() in content.lower():
            return name
    return None


def _collect_documentation_context(git_mgr, additional_ignores: list[str], env_vars: list[str]) -> dict:
    context: dict[str, object] = {}
    files = git_mgr.list_all_files()
    doc_files: list[str] = []
    license_files: list[str] = []
    for entry in files:
        file_path = entry["path"] if isinstance(entry, dict) else entry
        lower = file_path.lower()
        if FileFilter.should_exclude_from_analysis(file_path, additional_ignores):
            continue
        if os.path.basename(lower).startswith("readme") and lower.endswith((".md", ".rst", ".txt", ".adoc")):
            doc_files.append(file_path)
        elif "/docs/" in lower and lower.endswith((".md", ".rst", ".txt", ".adoc")):
            doc_files.append(file_path)
        elif os.path.basename(lower) in {"project_status.md", "documentation.md"}:
            doc_files.append(file_path)
        if os.path.basename(lower).startswith("license"):
            license_files.append(file_path)

    sections_map = {
        "project_overview": ["overview", "about", "description"],
        "installation": ["installation", "install", "setup", "getting started"],
        "usage": ["usage", "quickstart", "quick start", "run"],
        "features": ["features"],
        "tech_stack": ["tech stack", "technology", "built with", "stack"],
        "configuration": ["configuration", "config"],
        "troubleshooting": ["troubleshooting", "faq"],
        "contributing": ["contributing", "contribution", "contribute"],
        "license": ["license"]
    }

    aggregated: dict[str, list[str]] = {k: [] for k in sections_map.keys()}
    overview_paragraph: Optional[str] = None
    license_value: Optional[str] = None

    for file_path in doc_files:
        content = git_mgr.get_file_content(file_path)
        if not content:
            continue
        sections = _extract_markdown_sections(content)
        if overview_paragraph is None:
            # Use the first paragraph before any heading as fallback overview.
            pre_heading_lines = []
            for line in content.splitlines():
                if re.match(r'^\s{0,3}#{1,6}\s+', line):
                    break
                pre_heading_lines.append(line)
            overview_paragraph = _first_paragraph(pre_heading_lines)

        for key, aliases in sections_map.items():
            for title, lines in sections.items():
                if any(alias in title for alias in aliases):
                    items = _normalize_section_items(lines)
                    aggregated[key].extend(items)
                    if key == "license":
                        if license_value is None:
                            license_value = _first_paragraph(lines)
                    if key == "project_overview" and overview_paragraph is None:
                        overview_paragraph = _first_paragraph(lines)

    if license_value is None:
        for file_path in license_files:
            content = git_mgr.get_file_content(file_path) or ""
            detected = _detect_license_from_text(content)
            if detected:
                license_value = detected
                break

    if overview_paragraph:
        context["project_overview"] = overview_paragraph
    for key in ["installation", "usage", "features", "tech_stack", "configuration",
                "troubleshooting", "contributing"]:
        values = _dedupe_order([v for v in aggregated.get(key, []) if v])
        if values:
            context[key] = values
    if env_vars:
        context.setdefault("configuration", _dedupe_order(env_vars))
    if license_value:
        context["license"] = license_value
    return context


def _extract_route_params(path: str) -> list[str]:
    params = []
    for token in re.findall(r'<([^>]+)>', path):
        params.append(token.strip())
    for token in re.findall(r'\{([^}]+)\}', path):
        params.append(token.strip())
    for token in re.findall(r':([A-Za-z_][A-Za-z0-9_]*)', path):
        params.append(token.strip())
    return _dedupe_order(params)


def _parse_swagger_docstring(doc: str) -> dict[str, Union[list[str], str]]:
    result: dict[str, list[str] | str] = {}
    lines = doc.splitlines()
    summary = None
    for line in lines:
        raw = line.strip()
        if raw:
            summary = raw
            break
    if summary:
        result["summary"] = summary

    params: list[str] = []
    responses: list[str] = []
    examples: list[str] = []
    in_parameters = False
    in_responses = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("parameters:"):
            in_parameters = True
            in_responses = False
            continue
        if stripped.startswith("responses:"):
            in_responses = True
            in_parameters = False
            continue
        if re.match(r'^\w+:', stripped) and not stripped.startswith(("-", "parameters:", "responses:")):
            in_parameters = False
            in_responses = False
        if in_parameters:
            name_match = re.match(r'^-\s*name:\s*(\w+)', stripped)
            if name_match:
                params.append(name_match.group(1))
        if in_responses:
            code_match = re.match(r'^(\d{3})\s*:', stripped)
            if code_match:
                responses.append(code_match.group(1))
        example_match = re.match(r'^\s*example:\s*(.+)$', stripped)
        if example_match:
            examples.append(example_match.group(1).strip())

    if params:
        result["params"] = _dedupe_order(params)
    if responses:
        result["responses"] = _dedupe_order(responses)
    if examples:
        result["example"] = examples[0]
    if "security:" in doc.lower() or "authorization" in doc.lower():
        result["auth"] = "Authorization"
    return result


def _collect_api_details(git_mgr, additional_ignores: list[str]) -> list[dict]:
    api_details: list[dict] = []
    files = git_mgr.list_all_files()
    for entry in files:
        file_path = entry["path"] if isinstance(entry, dict) else entry
        lower = file_path.lower()
        if FileFilter.should_exclude_from_analysis(file_path, additional_ignores):
            continue
        if not lower.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt")):
            continue
        if not any(token in lower for token in ["/api", "/routes", "/route", "/controller", "/controllers", "/server", "/app"]):
            continue
        content = git_mgr.get_file_content(file_path)
        if not content:
            continue

        # Flask/FastAPI style routes
        for match in re.finditer(r'@(\w+)\.route\(\s*[\'"]([^\'"]+)[\'"]\s*(?:,\s*methods\s*=\s*\[([^\]]+)\])?', content):
            decorator, path, methods_block = match.groups()
            methods = []
            if methods_block:
                methods = re.findall(r'[\'"](\w+)[\'"]', methods_block)
            if not methods:
                methods = ["GET"]
            params = _extract_route_params(path)
            for method in methods:
                api_entry = {"method": method.upper(), "path": path}
                if params:
                    api_entry["params"] = [f"path:{p}" for p in params]
                api_details.append(api_entry)

        for match in re.finditer(r'@(\w+)\.(get|post|put|delete|patch)\(\s*[\'"]([^\'"]+)[\'"]', content):
            decorator, method, path = match.groups()
            params = _extract_route_params(path)
            api_entry = {"method": method.upper(), "path": path}
            if params:
                api_entry["params"] = [f"path:{p}" for p in params]
            api_details.append(api_entry)

        # Express.js style routes
        for match in re.finditer(r'\b(app|router|api|server)\.(get|post|put|delete|patch)\(\s*[\'"]([^\'"]+)[\'"]', content):
            _, method, path = match.groups()
            params = _extract_route_params(path)
            api_entry = {"method": method.upper(), "path": path}
            if params:
                api_entry["params"] = [f"path:{p}" for p in params]
            api_details.append(api_entry)
        for match in re.finditer(r'\.route\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*\.(get|post|put|delete|patch)\b', content):
            path, method = match.groups()
            params = _extract_route_params(path)
            api_entry = {"method": method.upper(), "path": path}
            if params:
                api_entry["params"] = [f"path:{p}" for p in params]
            api_details.append(api_entry)

        # Spring annotations
        for match in re.finditer(r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\(\s*[\'"]([^\'"]+)[\'"]', content):
            mapping, path = match.groups()
            method = mapping.replace("Mapping", "").upper()
            params = _extract_route_params(path)
            api_entry = {"method": method, "path": path}
            if params:
                api_entry["params"] = [f"path:{p}" for p in params]
            api_details.append(api_entry)
        for match in re.finditer(r'@RequestMapping\([^)]*method\s*=\s*RequestMethod\.(GET|POST|PUT|DELETE|PATCH)[^)]*', content):
            method = match.group(1)
            path_match = re.search(r'value\s*=\s*["\']([^"\']+)["\']', match.group(0))
            if path_match:
                path = path_match.group(1)
                params = _extract_route_params(path)
                api_entry = {"method": method, "path": path}
                if params:
                    api_entry["params"] = [f"path:{p}" for p in params]
                api_details.append(api_entry)

        # Swagger-style docstrings for Flask routes
        for route_match in re.finditer(r'@app\.route\(\s*[\'"]([^\'"]+)[\'"][^)]*\)\s*\ndef\s+\w+\([^)]*\):\s*\n\s+("""|\'\'\')([\s\S]*?)\2', content):
            path = route_match.group(1)
            doc = route_match.group(3)
            doc_info = _parse_swagger_docstring(doc)
            methods_match = re.search(r'methods\s*=\s*\[([^\]]+)\]', route_match.group(0))
            methods = re.findall(r'[\'"](\w+)[\'"]', methods_match.group(1)) if methods_match else ["GET"]
            for method in methods:
                api_entry = {"method": method.upper(), "path": path}
                api_entry.update(doc_info)
                api_details.append(api_entry)

    # Deduplicate by method/path/summary
    seen = set()
    deduped: list[dict] = []
    for item in api_details:
        key = (item.get("method"), item.get("path"), item.get("summary"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped

def _ask_new_user_interactive() -> bool:
    """Ask on TTY if the user is new to the tool.

    Returns True when the user confirms they are new. Defaults to False
    when stdin is not a TTY or on empty input.
    """
    try:
        if sys.stdin.isatty():
            answer = input("Are you new to this tool? [y/N]: ").strip().lower()
            return answer in ("y", "yes")
    except Exception:
        pass
    return False


def _is_new_user_flag(argv: list[str]) -> bool:
    """Detect `--new-user` flag in argv or env var CODE_DETECT_NEW_USER.

    Accepts values like: --new-user, --new-user=true, --new-user=yes.
    Env var: CODE_DETECT_NEW_USER=1|true|yes
    """
    truthy = {"1", "true", "yes", "y"}
    # Check CLI
    for a in argv:
        if a == "--new-user":
            return True
        if a.startswith("--new-user="):
            val = a.split("=", 1)[1].strip().lower()
            return val in truthy
    # Check env
    env_val = os.environ.get("CODE_DETECT_NEW_USER", "").strip().lower()
    return env_val in truthy


def _validate_report_schema(report: dict) -> tuple[bool, str]:
    """Validate report structure before writing to disk."""
    if not isinstance(report, dict):
        return False, "Report must be an object"

    required_top = {
        "schema_version",
        "version",
        "status",
        "meta",
        "project_id",
        "branch",
        "commit_sha",
        "baseline_commit",
        "analysis_summary",
        "breaking_changes",
        "statistics",
        "severity",
        "deterministic",
        "report",
    }
    if set(report.keys()) != required_top:
        return False, "Top-level keys mismatch for v2+v3 contract"
    if report.get("schema_version") != "epic1-impact/v3":
        return False, "schema_version must be epic1-impact/v3"
    if report.get("version") != "2.0":
        return False, "version must be 2.0"
    if report.get("status") not in {"success", "partial"}:
        return False, "status must be success|partial"
    if not isinstance(report.get("meta"), dict):
        return False, "meta must be an object"
    if not isinstance(report.get("project_id"), str):
        return False, "project_id must be a string"
    if not isinstance(report.get("branch"), str):
        return False, "branch must be a string"
    if report.get("baseline_commit") is not None and not isinstance(report.get("baseline_commit"), str):
        return False, "baseline_commit must be null|string"
    if not isinstance(report.get("analysis_summary"), dict):
        return False, "top-level analysis_summary must be an object"
    if not isinstance(report.get("breaking_changes"), list):
        return False, "top-level breaking_changes must be an array"
    if not isinstance(report.get("statistics"), dict):
        return False, "top-level statistics must be an object"
    if report.get("severity") not in {"PATCH", "MINOR", "MAJOR"}:
        return False, "top-level severity must be PATCH|MINOR|MAJOR"
    if not isinstance(report.get("deterministic"), bool):
        return False, "top-level deterministic must be boolean"
    report_obj = report.get("report")
    if not isinstance(report_obj, dict):
        return False, "report must be an object"
    if not isinstance(report_obj.get("context"), dict):
        return False, "context must be an object"
    if not isinstance(report_obj.get("analysis_summary"), dict):
        return False, "analysis_summary must be an object"
    if not isinstance(report_obj.get("changes"), list):
        return False, "changes must be an array"
    if not isinstance(report_obj.get("doc_contract"), dict):
        return False, "doc_contract must be an object"

    summary = report_obj["analysis_summary"]
    if report.get("analysis_summary") != summary:
        return False, "Top-level analysis_summary must match report.analysis_summary"
    summary_keys = {"total_files", "highest_severity", "breaking_changes_detected"}
    if not summary_keys.issubset(summary.keys()):
        return False, "analysis_summary missing required keys"

    if not isinstance(summary["total_files"], int):
        return False, "analysis_summary.total_files must be an integer"
    if summary["highest_severity"] not in {"PATCH", "MINOR", "MAJOR"}:
        return False, "analysis_summary.highest_severity must be PATCH|MINOR|MAJOR"
    if not isinstance(summary["breaking_changes_detected"], bool):
        return False, "analysis_summary.breaking_changes_detected must be a boolean"

    required_change_keys = {
        "file", "change_type", "language", "severity", "is_binary", "syntax_error", "features"
    }
    for item in report_obj["changes"]:
        if not isinstance(item, dict):
            return False, "Each changes entry must be an object"
        if not required_change_keys.issubset(item.keys()):
            return False, "A changes entry is missing required keys"
        if item["severity"] not in {"PATCH", "MINOR", "MAJOR"}:
            return False, "Change severity must be PATCH|MINOR|MAJOR"
        if not isinstance(item["is_binary"], bool):
            return False, "Change is_binary must be a boolean"
        if not isinstance(item["syntax_error"], bool):
            return False, "Change syntax_error must be a boolean"
        if not isinstance(item["features"], dict):
            return False, "Change features must be an object"

    stats = report.get("statistics", {})
    stat_keys = {"total_changes", "major", "minor", "patch"}
    if not stat_keys.issubset(stats.keys()):
        return False, "statistics missing required keys"
    for key in stat_keys:
        if not isinstance(stats.get(key), int):
            return False, f"statistics.{key} must be an integer"

    for descriptor in report.get("breaking_changes", []):
        if not isinstance(descriptor, dict):
            return False, "breaking_changes entries must be objects"
        req = {"type", "entity", "file", "severity", "description", "id"}
        if not req.issubset(descriptor.keys()):
            return False, "breaking_changes entry missing required keys"
        if descriptor.get("severity") not in {"PATCH", "MINOR", "MAJOR"}:
            return False, "breaking_changes.severity must be PATCH|MINOR|MAJOR"

    if "documentation_context" in report_obj:
        doc_ctx = report_obj["documentation_context"]
        if not isinstance(doc_ctx, dict):
            return False, "documentation_context must be an object"
        allowed_doc_keys = {
            "project_overview", "installation", "usage", "features", "tech_stack",
            "configuration", "troubleshooting", "contributing", "license"
        }
        for key in doc_ctx.keys():
            if key not in allowed_doc_keys:
                return False, f"documentation_context contains invalid key: {key}"
        string_keys = {"project_overview", "license"}
        list_keys = allowed_doc_keys - string_keys
        for key in string_keys:
            if key in doc_ctx and not isinstance(doc_ctx[key], str):
                return False, f"documentation_context.{key} must be a string"
        for key in list_keys:
            if key in doc_ctx:
                if not isinstance(doc_ctx[key], list) or not all(isinstance(v, str) for v in doc_ctx[key]):
                    return False, f"documentation_context.{key} must be an array of strings"

    if "api_details" in report_obj:
        api_details = report_obj["api_details"]
        if not isinstance(api_details, list):
            return False, "api_details must be an array"
        allowed_api_keys = {
            "method", "path", "summary", "auth", "params", "responses", "example"
        }
        for item in api_details:
            if not isinstance(item, dict):
                return False, "Each api_details entry must be an object"
            for key in item.keys():
                if key not in allowed_api_keys:
                    return False, f"api_details contains invalid key: {key}"
            if "method" not in item or not isinstance(item["method"], str):
                return False, "api_details.method must be a string"
            if "path" not in item or not isinstance(item["path"], str):
                return False, "api_details.path must be a string"
            if "summary" in item and not isinstance(item["summary"], str):
                return False, "api_details.summary must be a string"
            if "auth" in item and not isinstance(item["auth"], str):
                return False, "api_details.auth must be a string"
            if "example" in item and not isinstance(item["example"], str):
                return False, "api_details.example must be a string"
            if "params" in item:
                if not isinstance(item["params"], list) or not all(isinstance(v, str) for v in item["params"]):
                    return False, "api_details.params must be an array of strings"
            if "responses" in item:
                if not isinstance(item["responses"], list) or not all(isinstance(v, str) for v in item["responses"]):
                    return False, "api_details.responses must be an array of strings"

    doc_contract = report_obj.get("doc_contract", {})
    api_contract = doc_contract.get("api_contract", {})
    endpoints = api_contract.get("endpoints", [])
    if not isinstance(endpoints, list):
        return False, "doc_contract.api_contract.endpoints must be an array"
    quality = doc_contract.get("quality_contract", {}).get("extraction_quality", {})
    if int(quality.get("api_endpoint_count", -1)) != len(endpoints):
        return False, "extraction_quality.api_endpoint_count mismatch"
    for ep in endpoints:
        if not isinstance(ep, dict):
            return False, "endpoint must be object"
        method = ep.get("method")
        path = ep.get("path")
        if ep.get("normalized_key") != f"{method} {path}":
            return False, "normalized_key mismatch"
        req = ep.get("request", {})
        if method in {"GET", "DELETE"} and req.get("body_schema", "x") is not None:
            return False, "GET/DELETE request.body_schema must be null"
        src = ep.get("source", {})
        if int(src.get("line_end", 0)) < int(src.get("line_start", 0)):
            return False, "source line_end must be >= line_start"
        for r in ep.get("responses", []):
            if "schema_ref" in r and _sanitize_schema_ref(r.get("schema_ref")) is None:
                return False, "invalid response schema_ref"
        body_schema = req.get("body_schema")
        if isinstance(body_schema, dict) and "schema_ref" in body_schema:
            if _sanitize_schema_ref(body_schema.get("schema_ref")) is None:
                return False, "invalid body schema_ref"
    return True, ""


def main():
    usage_error = {"error": "Usage: python main.py <repo_path_or_url> [github_token] [branch] [--new-user] [--skip-remote-preflight]"}

    if len(sys.argv) < 2:
        print(json.dumps(usage_error))
        sys.exit(1)

    # Extract positional args (ignore known flags we handle separately)
    args = [a for a in sys.argv[1:] if not a.startswith("--new-user") and a != "--skip-remote-preflight" and not a.startswith("--branch=")]
    if not args:
        print(json.dumps(usage_error))
        sys.exit(1)

    repo_path = args[0]
    github_token = None
    branch = "main"

    # Support optional --branch=NAME flag
    branch_flag = next((a for a in sys.argv[1:] if a.startswith("--branch=")), None)
    if branch_flag:
        branch = branch_flag.split("=", 1)[1].strip() or branch

    if len(args) > 1:
        # If only one extra arg and it looks like a branch, treat it as branch
        common_branches = {"main", "master", "dev", "develop", "staging", "prod", "production"}
        if len(args) == 2 and args[1] in common_branches and not os.environ.get('GITHUB_TOKEN'):
            branch = args[1]
            github_token = os.environ.get('GITHUB_TOKEN')
        else:
            github_token = args[1]
            branch = args[2] if len(args) > 2 else branch
    else:
        github_token = os.environ.get('GITHUB_TOKEN')

    new_user = _is_new_user_flag(sys.argv[1:]) or _ask_new_user_interactive()
    skip_remote_preflight = "--skip-remote-preflight" in sys.argv[1:]

    report = {
        "meta": {
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "tool_version": "1.0.0"
        },
        "context": {},
        "analysis_summary": {
            "total_files": 0,
            "highest_severity": "PATCH",
            "breaking_changes_detected": False
        },
        "changes": [],
        "change_summary": "",
        "api_summary": {
            "added": 0,
            "modified": 0,
            "removed": 0
        },
        "impact_scope": "INTERNAL_ONLY",
        "security_features_detected": [],
        "affected_packages": [],
        "intent_classification": "FEATURE",
        "component_distribution": {},
        "documentation_impact": {
            "readme": False,
            "api_docs": False,
            "architecture": False,
            "adr_required": False
        },
        "breaking_change_details": [],
        "configuration_changes": {
            "docker": False,
            "build_files": [],
            "environment_variables": []
        },
        "database_impact": {
            "schema_changed": False,
            "tables_affected": []
        },
        "feature_tags": [],
        "change_complexity_score": 0.0,
        "test_impact": {
            "requires_new_tests": False,
            "test_files_changed": 0
        },
        "api_contract": {
            "endpoints": []
        },
        "guardrails": {
            "applied": [],
            "limits": {}
        },
        "extraction_quality": {
            "api_endpoint_count": 0,
            "invalid_endpoint_count": 0,
            "normalized_endpoint_count": 0,
            "warnings": []
        }
    }

    try:
        _preflight_validate(repo_path, github_token, branch, skip_remote_preflight=skip_remote_preflight)
        baseline_store = BaselineStore()
        baseline_report_payload = None
        baseline_commit = None
        project_id = "unknown-project"
        current_commit_sha = ""
        # Use context manager to ensure cleanup
        try:
            with GitManager(repo_path, github_token, branch) as git_mgr:
                report["context"] = git_mgr.get_metadata()
                project_id = _project_id_from_context(report["context"])
                current_commit_sha = str(
                    report["context"].get("full_sha")
                    or report["context"].get("commit_sha")
                    or ""
                )
                baseline_doc = baseline_store.load_baseline(project_id, branch, current_commit_sha)
                if isinstance(baseline_doc, dict):
                    baseline_report_payload = baseline_doc.get("report", baseline_doc)
                    if isinstance(baseline_report_payload, dict):
                        baseline_context = baseline_report_payload.get("context", {})
                        if isinstance(baseline_context, dict):
                            baseline_commit = str(
                                baseline_context.get("full_sha")
                                or baseline_context.get("commit_sha")
                                or ""
                            ) or None

                config_path = os.path.join(git_mgr.repo_path, "config.yaml")
                config = ConfigLoader.load(config_path)
                additional_ignores = config.get("ignore_patterns", [])

                # New users: analyze the full repository once for baseline.
                changes_list = git_mgr.list_all_files() if new_user else git_mgr.get_changed_files()
                if changes_list and changes_list[0].get("change_type") == "ERROR":
                    raise AnalysisError("diff", changes_list[0].get("error", "Failed to compute diff"), True)
                changes_list = sorted(changes_list, key=lambda c: c.get("path", ""))

                max_files = _env_int("CODE_DETECT_MAX_FILES", 12000)
                max_file_bytes = _env_int("CODE_DETECT_MAX_FILE_BYTES", 1024 * 1024)
                max_total_parse_bytes = _env_int("CODE_DETECT_MAX_TOTAL_PARSE_BYTES", 250 * 1024 * 1024)
                report["guardrails"]["limits"] = {
                    "max_files": max_files,
                    "max_file_bytes": max_file_bytes,
                    "max_total_parse_bytes": max_total_parse_bytes
                }
                if len(changes_list) > max_files:
                    report["guardrails"]["applied"].append(
                        f"Truncated candidate file list from {len(changes_list)} to {max_files}"
                    )
                    changes_list = changes_list[:max_files]

                severity_rank = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}
                max_severity = 0
                api_summary = {"added": 0, "modified": 0, "removed": 0}
                api_contract_candidates: list[dict] = []
                security_features_detected: list[str] = []
                affected_packages: list[str] = []
                component_distribution: dict[str, int] = {}
                has_db_change = False
                tables_affected: list[str] = []
                doc_impact = {
                    "readme": False,
                    "api_docs": False,
                    "architecture": False,
                    "adr_required": False
                }
                config_build_files: list[str] = []
                env_vars: list[str] = []
                docker_changed = False
                test_files_changed = 0
                has_non_test_changes = False
                parse_inputs: list[dict] = []
                symbol_count_total = 0
                protobuf_payload_total = 0
                parse_bytes_total = 0

                for change in changes_list:
                    file_path = change["path"]
                    if FileFilter.should_exclude_from_analysis(file_path, additional_ignores):
                        continue

                    record = {
                        "file": file_path,
                        "change_type": change["change_type"],
                        "language": None,
                        "severity": "PATCH",
                        "is_binary": False,
                        "syntax_error": False,
                        "features": {},
                        "component": _classify_component(file_path, None),
                    }

                    if FileFilter.is_known_binary_extension(file_path):
                        record["is_binary"] = True
                        record["note"] = "Skipped binary file analysis"
                        component_distribution[record["component"]] = component_distribution.get(record["component"], 0) + 1
                        report["changes"].append(record)
                        continue

                    content = git_mgr.get_file_content(file_path)
                    if content is None:
                        record["is_binary"] = True
                        record["note"] = "Skipped binary file analysis"
                        component_distribution[record["component"]] = component_distribution.get(record["component"], 0) + 1
                        report["changes"].append(record)
                        continue
                    content_bytes = len(content.encode("utf-8", errors="ignore"))
                    if content_bytes > max_file_bytes:
                        record["note"] = (
                            f"Skipped oversized file ({content_bytes} bytes > max_file_bytes={max_file_bytes})"
                        )
                        component_distribution[record["component"]] = component_distribution.get(record["component"], 0) + 1
                        report["changes"].append(record)
                        report["guardrails"]["applied"].append(f"Oversized file skipped: {file_path}")
                        continue
                    if parse_bytes_total + content_bytes > max_total_parse_bytes:
                        record["note"] = (
                            "Skipped due to total parse byte budget exhaustion "
                            f"({max_total_parse_bytes} bytes)"
                        )
                        component_distribution[record["component"]] = component_distribution.get(record["component"], 0) + 1
                        report["changes"].append(record)
                        continue
                    parse_bytes_total += content_bytes

                    ext = os.path.splitext(file_path)[1].lower()
                    parse_inputs.append({
                        "change": change,
                        "file_path": file_path,
                        "ext": ext,
                        "content": content,
                        "record": record,
                    })

                parse_outputs: dict[str, dict] = {}
                if parse_inputs:
                    max_workers = max(1, int(os.environ.get("CODE_DETECT_PARSE_WORKERS", str(max(1, (os.cpu_count() or 2) - 1)))))
                    if max_workers > 1 and len(parse_inputs) > 1:
                        try:
                            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                                future_map = {
                                    executor.submit(_parse_and_index_file, item["file_path"], item["ext"], item["content"]): item["file_path"]
                                    for item in parse_inputs
                                }
                                for future in as_completed(future_map):
                                    fpath = future_map[future]
                                    try:
                                        parse_outputs[fpath] = future.result()
                                    except Exception as e:
                                        parse_outputs[fpath] = {"file": fpath, "error": str(e)}
                        except Exception as pool_error:
                            LOG.warning("Process pool unavailable, falling back to sequential parse: %s", pool_error)
                            for item in parse_inputs:
                                fpath = item["file_path"]
                                try:
                                    parse_outputs[fpath] = _parse_and_index_file(fpath, item["ext"], item["content"])
                                except Exception as e:
                                    parse_outputs[fpath] = {"file": fpath, "error": str(e)}
                    else:
                        for item in parse_inputs:
                            fpath = item["file_path"]
                            try:
                                parse_outputs[fpath] = _parse_and_index_file(fpath, item["ext"], item["content"])
                            except Exception as e:
                                parse_outputs[fpath] = {"file": fpath, "error": str(e)}

                for item in parse_inputs:
                    change = item["change"]
                    file_path = item["file_path"]
                    ext = item["ext"]
                    content = item["content"]
                    record = item["record"]
                    try:
                        parsed = parse_outputs.get(file_path, {})
                        if parsed.get("error"):
                            raise ValueError(parsed["error"])

                        record["language"] = parsed.get("language")
                        parse_result = parsed.get("parse_result", {}) or {}
                        record["syntax_error"] = bool(parse_result.get("syntax_error"))
                        features = parse_result.get("features", {}) or {}
                        if record["syntax_error"]:
                            features["note"] = "Partial extraction due to syntax error"
                            if parse_result.get("error_nodes"):
                                record["error_nodes"] = parse_result["error_nodes"]

                        symbol_count_total += int(parsed.get("symbol_count", 0))
                        protobuf_payload_total += int(parsed.get("protobuf_size_bytes", 0))

                        api_endpoints = _sanitize_api_endpoints(_normalize_api_endpoints(features))
                        features["api_endpoints"] = api_endpoints
                        for ep in api_endpoints:
                            route = ep.get("route") or ep.get("path") or ""
                            method = ep.get("verb") or ep.get("method") or "GET"
                            line = ep.get("line", 0)
                            api_contract_candidates.append({
                                "method": method,
                                "path": route,
                                "summary": "",
                                "description": f"Detected endpoint from source analysis in {file_path}",
                                "source_file": file_path,
                                "line_start": line,
                                "line_end": line,
                                "handler": ep.get("handler") or "",
                                "content": content,
                                "features": features,
                                "warnings": [],
                            })

                        record["component"] = _classify_component(file_path, content)
                        component_distribution[record["component"]] = component_distribution.get(record["component"], 0) + 1

                        security_features = _detect_security_features(content)
                        if security_features:
                            security_features_detected.extend(security_features)

                        pkgs = _extract_packages(file_path, content, record["language"])
                        if pkgs:
                            affected_packages.extend(pkgs)

                        record["features"] = features

                        schema_tags = SchemaDetector.analyze(file_path, content)
                        severity = SeverityCalculator.assess(ext, features, schema_tags)
                        record["severity"] = severity
                        if schema_tags:
                            has_db_change = True
                            if ext == ".sql":
                                tables_affected.extend(_extract_tables_from_sql(content))
                            if ext == ".java":
                                tables_affected.extend(_extract_table_annotations(content))
                            for tag in schema_tags:
                                if tag.startswith("MONGOOSE_MODEL:"):
                                    tables_affected.append(tag.split(":", 1)[1])

                        if severity_rank[severity] > max_severity:
                            max_severity = severity_rank[severity]
                            report["analysis_summary"]["highest_severity"] = severity
                        if severity == "MAJOR":
                            report["analysis_summary"]["breaking_changes_detected"] = True

                        if api_endpoints:
                            if change["change_type"] == "ADDED":
                                api_summary["added"] += len(api_endpoints)
                            elif change["change_type"] == "DELETED":
                                api_summary["removed"] += len(api_endpoints)
                            else:
                                api_summary["modified"] += len(api_endpoints)

                        impact = _detect_doc_impact(file_path, api_summary, has_db_change)
                        doc_impact["readme"] = doc_impact["readme"] or impact["readme"]
                        doc_impact["api_docs"] = doc_impact["api_docs"] or impact["api_docs"]
                        doc_impact["architecture"] = doc_impact["architecture"] or impact["architecture"]
                        doc_impact["adr_required"] = doc_impact["adr_required"] or impact["adr_required"]

                        path_lower = file_path.lower()
                        if os.path.basename(path_lower) in {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}:
                            docker_changed = True
                        if os.path.basename(path_lower) in {
                            "pom.xml", "build.gradle", "build.gradle.kts", "package.json",
                            "pyproject.toml", "requirements.txt", "makefile",
                        }:
                            config_build_files.append(os.path.basename(path_lower))
                        if path_lower.endswith((".env", ".yml", ".yaml", ".json", ".toml", ".ini")):
                            env_vars.extend(_extract_env_vars(content))

                        is_test_file = (
                            "/test" in path_lower or "/tests" in path_lower or
                            path_lower.endswith("_test.py") or path_lower.endswith(".spec.ts") or
                            path_lower.endswith(".test.ts") or path_lower.endswith(".spec.js") or
                            path_lower.endswith(".test.js")
                        )
                        if is_test_file:
                            test_files_changed += 1
                        else:
                            has_non_test_changes = True

                        report["changes"].append(record)
                    except Exception as e:
                        LOG.exception("Per-file analysis failed for %s", file_path)
                        record["syntax_error"] = True
                        record["features"] = {"note": f"Partial extraction due to parser error: {e}"}
                        report["changes"].append(record)
                        continue

                report["indexing"] = {
                    "format": "SCIP",
                    "symbol_count": symbol_count_total,
                }
                report["serialization"] = {
                    "format": "protobuf",
                    "total_payload_bytes": protobuf_payload_total,
                }

            report["analysis_summary"]["total_files"] = len(report["changes"])

            # Normalize aggregates
            security_features_detected = _dedupe_order(security_features_detected)
            affected_packages = _dedupe_order(affected_packages)
            affected_packages = [p for p in affected_packages if _is_valid_package_entry(p)]
            tables_affected = _dedupe_order(tables_affected)
            config_build_files = _dedupe_order(config_build_files)
            env_vars = _dedupe_order(env_vars)
            report["security_features_detected"] = security_features_detected
            report["affected_packages"] = affected_packages
            report["api_summary"] = api_summary
            report["component_distribution"] = component_distribution
            report["documentation_impact"] = doc_impact
            report["configuration_changes"] = {
                "docker": docker_changed,
                "build_files": config_build_files,
                "environment_variables": env_vars
            }
            report["database_impact"] = {
                "schema_changed": has_db_change,
                "tables_affected": tables_affected
            }
            report["test_impact"] = {
                "requires_new_tests": False,
                "test_files_changed": test_files_changed
            }

            # Documentation context and API details (only include when evidence exists)
            documentation_context = _collect_documentation_context(git_mgr, additional_ignores, env_vars)
            if documentation_context:
                report["documentation_context"] = documentation_context
            api_details = _collect_api_details(git_mgr, additional_ignores)
            if api_details:
                report["api_details"] = api_details
                for item in api_details:
                    method = item.get("method", "GET")
                    path = item.get("path", "")
                    summary = item.get("summary") or f"{method} {path}"
                    api_contract_candidates.append({
                        "method": method,
                        "path": path,
                        "summary": summary,
                        "description": item.get("summary") or f"Detected endpoint from API details in {path}",
                        "source_file": "",
                        "line_start": 0,
                        "line_end": 0,
                        "handler": "",
                        "content": "",
                        "warnings": []
                    })

            api_contract_candidates = sorted(
                api_contract_candidates,
                key=lambda c: (
                    str(c.get("method", "")),
                    str(c.get("path", "")),
                    str(c.get("source_file", "")),
                    int(c.get("line_start", 0) or 0),
                ),
            )
            endpoints, extraction_quality = _build_api_contract_endpoints(api_contract_candidates)
            report["api_contract"] = {"endpoints": endpoints}
            report["extraction_quality"] = extraction_quality

            # Impact scope classification
            if security_features_detected:
                report["impact_scope"] = "SECURITY_CHANGE"
            elif has_db_change:
                report["impact_scope"] = "DATABASE_CHANGE"
            elif any(api_summary.values()):
                report["impact_scope"] = "API_CHANGE"
            else:
                report["impact_scope"] = "INTERNAL_ONLY"

            # Intent classification
            commit_message = report.get("context", {}).get("intent", {}).get("message")
            report["intent_classification"] = _classify_intent(commit_message, security_features_detected)

            # Change summary narrative
            components_sorted = sorted(
                component_distribution.items(),
                key=lambda item: (-item[1], item[0])
            )
            component_names = [name for name, _ in components_sorted if name != "other"]
            report["change_summary"] = _generate_change_summary(
                commit_message,
                api_summary,
                security_features_detected,
                has_db_change,
                component_names[:3]
            )

            # Breaking change details
            breaking_details = []
            if report["analysis_summary"]["breaking_changes_detected"]:
                if api_summary.get("removed", 0) > 0:
                    breaking_details.append("API endpoints removed or changed")
                if has_db_change:
                    breaking_details.append("Database schema changes detected")
                if report["analysis_summary"]["highest_severity"] == "MAJOR":
                    breaking_details.append("Major severity changes detected")
            if commit_message and "breaking" in commit_message.lower():
                breaking_details.append("Commit message indicates breaking change")
            report["breaking_change_details"] = _dedupe_order(breaking_details)

            # Feature tags
            feature_tags = []
            if security_features_detected:
                feature_tags.append("Security")
            if "authentication" in component_distribution:
                feature_tags.append("Authentication")
            if any(api_summary.values()):
                feature_tags.append("API")
            if has_db_change:
                feature_tags.append("Database")
            if docker_changed or config_build_files:
                feature_tags.append("Deployment")
            feature_tags.extend([name.title() for name in component_distribution.keys() if name not in {"other"}])
            report["feature_tags"] = _dedupe_order(feature_tags)

            # Test impact
            requires_new_tests = (
                test_files_changed == 0 and has_non_test_changes and (
                    any(api_summary.values()) or has_db_change or security_features_detected
                )
            )
            report["test_impact"]["requires_new_tests"] = requires_new_tests

            # Change complexity score
            api_total = api_summary["added"] + api_summary["modified"] + api_summary["removed"]
            report["change_complexity_score"] = _calc_complexity_score(
                report["analysis_summary"]["total_files"],
                api_total,
                len(component_distribution),
                has_db_change,
                bool(security_features_detected),
                report["analysis_summary"]["breaking_changes_detected"]
            )
            breaking_changes = compare_reports(baseline_report_payload, report)
            risk = score_report_risk(report, breaking_changes)
            summary_severity = report.get("analysis_summary", {}).get("highest_severity", "PATCH")
            rank = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}
            if rank.get(summary_severity, 1) > rank.get(risk.get("severity", "PATCH"), 1):
                risk["severity"] = summary_severity
            if breaking_changes:
                report["breaking_change_details"] = _dedupe_order(
                    report.get("breaking_change_details", []) +
                    [item.get("description", "") for item in breaking_changes if item.get("description")]
                )
                report["analysis_summary"]["breaking_changes_detected"] = True
                report["analysis_summary"]["highest_severity"] = risk.get(
                    "severity",
                    report["analysis_summary"]["highest_severity"]
                )

            final_report = _to_canonical_v3(
                report,
                status="success",
                project_id=project_id,
                baseline_commit=baseline_commit,
                breaking_changes=breaking_changes,
                risk=risk,
            )
            valid, validation_error = _validate_report_schema(final_report)
            if not valid:
                raise ValueError(f"Report validation failed: {validation_error}")

            # Save to file
            _write_report(final_report)
            if current_commit_sha:
                baseline_store.save_baseline(project_id, branch, current_commit_sha, final_report)

            # Print to stdout
            print(json.dumps(final_report, indent=2))
        except InvalidGitRepositoryError as e:
            raise AnalysisError("clone", str(e), True)

    except Exception as e:
        if isinstance(e, AnalysisError):
            error_details = e.details
            stage = e.stage
            retry_possible = e.retry_possible
        else:
            error_details = str(e)
            stage = "analysis"
            retry_possible = True

        LOG.exception("Analysis failed at stage=%s", stage)
        fallback = _build_fallback_report(repo_path, branch, error_details, report.get("context"))
        _write_report(fallback)
        error_report = {
            "status": "error",
            "error": "Analysis failed",
            "stage": stage,
            "details": error_details,
            "retry_possible": retry_possible
        }
        print(json.dumps(error_report))
        sys.exit(1)

if __name__ == "__main__":
    main()
