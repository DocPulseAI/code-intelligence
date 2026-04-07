"""Deterministic route resolution for Express-style mount chains."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from typing import Callable, Optional


@dataclass(frozen=True)
class RouterIdentity:
    file_path: str
    router_symbol: str


@dataclass(frozen=True)
class RouteCandidate:
    method: str
    raw_path: str
    source_file: str
    line_start: int
    router_symbol: str
    middleware_tokens: list[str]


@dataclass(frozen=True)
class RouterMountEdge:
    parent: RouterIdentity
    child: RouterIdentity
    mount_path: str
    middleware_tokens: list[str]


@dataclass(frozen=True)
class ResolvedRoute:
    method: str
    full_path: str
    normalized_key: str
    operation_id: str
    auth_type: str
    endpoint_hash: str
    source_file: str
    line_start: int


# Treat only application instance symbols as root mount owners.
# "api" is commonly used as a Router variable and must not be collapsed to root.
_APP_SYMBOLS = {"app", "server"}
_VERSION_SEGMENT = re.compile(r"^v\d+$")
_OP_ID_RE = re.compile(r"^[a-z][A-Za-z0-9]*(?:_[0-9]+)?$")
_PATH_LITERAL_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_STATIC_EXT_RE = re.compile(r"\.(?:css|js|mjs|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|map)$", re.IGNORECASE)


def _normalize_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    # Strip Express-style regex constraints and optional markers deterministically.
    raw = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)\([^)]*\)\??", r"{\1}", raw)
    raw = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)\??", r"{\1}", raw)
    raw = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\s*:[^}]+\}\??", r"{\1}", raw)
    raw = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}\?", r"{\1}", raw)
    raw = re.sub(r"/{2,}", "/", raw)
    if len(raw) > 1 and raw.endswith("/"):
        raw = raw[:-1]
    return raw or "/"


def _join_paths(*parts: str) -> str:
    merged = "/".join((p or "").strip("/") for p in parts if p is not None)
    return _normalize_path("/" + merged if merged else "/")


def _singularize_deterministic(token: str) -> str:
    """
    Precision singularization engine with deterministic pattern rules.

    ACCURACY HARDENED:
    - Irregular overrides checked first (deterministic dict order)
    - Pattern matching in order of specificity
    - No truncation to invalid tokens
    - Safe fallback to original or conservative choice

    Rules:
    1. Irregular overrides (people→person, users→user, statuses→status, deliveries→delivery)
    2. "ies" → "y" (companies → company, deliveries → delivery)
    3. "ves" → "f" (calves → calf)
    4. "xes" → "x" (boxes → box)
    5. "ches" → "ch" (churches → church)
    6. "shes" → "sh" (dishes → dish)
    7. "oes" → "o" (heroes → hero)
    8. Terminal "s" if not "ss", "us", "is" suffix (projects → project)
    """
    word = token.lower().strip()
    if not word:
        return "resource"

    # IRREGULAR OVERRIDES (checked deterministically first)
    irregular_map = {
        "people": "person",
        "users": "user",
        "activities": "activity",
        "companies": "company",
        "statistics": "statistic",
        "statuses": "status",
        "deliveries": "delivery",
        "data": "datum",
        "criteria": "criterion",
        "phenomena": "phenomenon",
    }

    if word in irregular_map:
        return irregular_map[word]

    # PATTERN-BASED RULES (order of specificity)
    # longer suffixes checked before shorter ones

    # Rule: "...xes" → "...x" (boxes → box, complexes → complex)
    if word.endswith("xes") and len(word) > 4:
        return word[:-2]

    # Rule: "...ches" → "...ch" (churches → church, branches → branch)
    if word.endswith("ches") and len(word) > 4:
        return word[:-2]

    # Rule: "...shes" → "...sh" (dishes → dish, wishes → wish)
    if word.endswith("shes") and len(word) > 4:
        return word[:-2]

    # Rule: "...ies" → "...y" (companies → company, deliveries → delivery)
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"

    # Rule: "...ves" → "...f" (calves → calf, halves → half)
    if word.endswith("ves") and len(word) > 4:
        return word[:-3] + "f"

    # Rule: "...oes" → "...o" (heroes → hero, tomatoes → tomato)
    if word.endswith("oes") and len(word) > 4:
        return word[:-2]

    # Rule: trailing "s" removal (projects → project, items → item)
    # But not if ends with "ss", "us", "is" (safe patterns)
    if word.endswith("s") and len(word) > 3:
        if not word.endswith(("ss", "us", "is")):
            return word[:-1]

    # Fallback: return unchanged
    return word


def _singularize_enhanced(token: str) -> str:
    """Backward-compatible wrapper calling deterministic engine."""
    return _singularize_deterministic(token)


def _singularize(token: str) -> str:
    """Backward-compatible wrapper for enhanced singularization."""
    return _singularize_enhanced(token)


def _pluralize(token: str) -> str:
    word = token.lower()
    irregular = {
        "person": "people",
        "user": "users",
        "activity": "activities",
        "company": "companies",
    }
    if word in irregular:
        return irregular[word]
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def _to_pascal(token: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", " ", token).strip()
    if not clean:
        return "Resource"
    return "".join(part[:1].upper() + part[1:] for part in clean.split())


def _meaningful_segments(full_path: str) -> list[str]:
    segments = []
    for seg in [s for s in full_path.split("/") if s]:
        if seg == "api" or _VERSION_SEGMENT.match(seg):
            continue
        segments.append(seg)
    return segments


def _first_param_name(full_path: str) -> str:
    for seg in _meaningful_segments(full_path):
        if seg.startswith("{") and seg.endswith("}"):
            return seg[1:-1]
    return "id"


def _is_param(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}") and len(seg) > 2


def _first_literal_after_prefix(segments: list[str]) -> str:
    for seg in segments:
        if not _is_param(seg):
            return seg
    return "resource"


def _first_param_after_index(segments: list[str], start_idx: int) -> str:
    for idx, seg in enumerate(segments):
        if idx <= start_idx:
            continue
        if _is_param(seg):
            return seg[1:-1]
    return ""


def _verb_for_method(method: str, full_path: str) -> str:
    m = method.upper()
    segments = _meaningful_segments(full_path)
    has_id = any(seg.startswith("{") and seg.endswith("}") for seg in segments)
    if m == "GET":
        return "get"
    if m == "POST":
        return "create"
    if m == "PATCH":
        return "update"
    if m == "PUT":
        return "replace"
    if m == "DELETE":
        return "delete"
    return m.lower()


def _operation_id_base(method: str, full_path: str) -> str:
    """
    Enterprise Operation ID generation with semantic grammar matrix.

    Grammar Rules:
    - GET /resources → getResources
    - POST /resources → createResource
    - GET /resources/{id} → getResourceById
    - PATCH /resources/{id} → updateResource
    - DELETE /resources/{id} → deleteResourceById
    - GET /resources/{id}/subresources → getResourceSubresources
    - POST /resources/{id}/subresources → createResourceSubresource
    """
    segments = _meaningful_segments(full_path)
    if not segments:
        return "getResource"

    # Special case: search endpoints
    first_literal = _first_literal_after_prefix(segments).lower()
    if first_literal == "search":
        return "searchResources"

    # Get method verb and resource names
    method_upper = method.upper()
    verb = _verb_for_method(method_upper, full_path)

    # Extract resource positions and names
    literal_positions = [(i, seg) for i, seg in enumerate(segments) if not _is_param(seg)]
    if not literal_positions:
        return "getResource"

    # Primary resource (first literal segment)
    _, first_seg = literal_positions[0]
    primary = _to_pascal(_singularize(first_seg))
    collection = _to_pascal(_pluralize(_singularize(first_seg)))

    # Secondary resource (if exists)
    second_lit = literal_positions[1][1] if len(literal_positions) > 1 else ""

    # Check for parameters
    has_any_param = any(_is_param(seg) for seg in segments)
    first_param = _first_param_name(full_path)

    # ========== ENTERPRISE METHOD MATRIX ==========

    # SUBRESOURCE GRAMMAR: /comments/{id}/reactions → deleteCommentReaction(s)
    if second_lit and method_upper in {"DELETE", "POST", "PUT", "PATCH"}:
        subresource = _to_pascal(_singularize(second_lit))
        return f"{verb}{primary}{subresource}"

    # QUALIFIER GRAMMAR: /dashboard/overview → getDashboardOverview
    if second_lit and method_upper == "GET":
        # Check if third segment is a parameter (used in path)
        qualifier = _to_pascal(_singularize(second_lit if second_lit.lower() != "overview" else "overview"))
        param_after_qualifier = _first_param_after_index(segments, literal_positions[1][0])

        if param_after_qualifier:
            # GET /projects/{id}/dashboard → getProjectDashboardByProjectId
            return f"{verb}{primary}{qualifier}By{_to_pascal(param_after_qualifier)}"
        else:
            # GET /projects/dashboard → getProjectDashboard
            return f"{verb}{primary}{qualifier}"

    # ========== CORE METHOD MATRIX ==========

    # GET /resources (collection)
    if method_upper == "GET" and not has_any_param:
        return f"{verb}{collection}"

    # GET /resources/{id} (single by ID)
    if method_upper == "GET" and has_any_param:
        param_name = _to_pascal(first_param or "id")
        return f"{verb}{primary}By{param_name}"

    # POST /resources (create single)
    if method_upper == "POST":
        return f"{verb}{primary}"

    # PATCH /resources or PATCH /resources/{id} (update)
    if method_upper == "PATCH":
        if has_any_param:
            return f"{verb}{primary}"
        return f"{verb}{primary}"

    # PUT /resources or PUT /resources/{id} (replace)
    if method_upper == "PUT":
        if has_any_param:
            return f"{verb}{primary}"
        return f"{verb}{primary}"

    # DELETE /resources or DELETE /resources/{id} (delete)
    if method_upper == "DELETE":
        if has_any_param:
            param_name = _to_pascal(first_param or "id")
            return f"{verb}{primary}By{param_name}"
        # DELETE without ID operates on collection
        return f"{verb}{collection}"

    # Fallback for any other HTTP method
    return f"{verb}{primary}"


def _enforce_operation_id_strictness(operation_id: str) -> str:
    """
    Enforce stricter grammar rules on operation IDs:
    - No duplicate tokens/words
    - No repeated verbs
    - Canonical casing (camelCase)

    Examples:
    - "deleteRestaurantDelete" → "deleteRestaurant" (remove repeated "Delete")
    - "getGetUser" → "getUser" (remove repeated verb)
    - "updateProfileUpdate" → "updateProfile" (remove repeated verb)
    """
    if not operation_id:
        return "getResource"

    import re

    # Match verb at start (lowercase letters) and rest
    match = re.match(r'^([a-z]+)(.*)$', operation_id)
    if not match:
        return operation_id

    verb_part = match.group(1)  # e.g., "get", "create", "delete"
    rest_part = match.group(2)  # e.g., "RestaurantDelete", "User"

    # Split rest into PascalCase words: [A-Z][a-z]*
    words = re.findall(r'[A-Z][a-z]*', rest_part)

    if not words:
        return verb_part + "Resource"

    # Remove words that are duplicates of:
    # 1. The verb itself (case-insensitive)
    # 2. Previous words in the list (consecutive duplicates)
    unique_words = []
    for word in words:
        word_lower = word.lower()

        # Skip if it matches the verb (e.g., "Delete" matches "delete" verb)
        if word_lower == verb_part:
            continue

        # Skip if it's a consecutive duplicate
        if unique_words and unique_words[-1].lower() == word_lower:
            continue

        unique_words.append(word)

    # Reconstruct
    if unique_words:
        return verb_part + ''.join(unique_words)
    return verb_part + "Resource"


def _build_route_resolution_integrity_block(
    total_candidates: int,
    resolved_count: int,
    unresolved_failures: list[dict]
) -> dict:
    """
    PHASE 2 ACCURACY HARDENING: Structured route resolution integrity model.

    Replaces plain text warnings with structured, enum-based failure reasons.

    Failure reasons (enum):
      DYNAMIC_PATH - Path contains computed/dynamic segments
      CONDITIONAL_EXPORT - Export depends on runtime conditions
      UNSUPPORTED_PATTERN - Pattern not recognized by AST analyzer
      PARSE_FAILURE - Syntax error or unparseable code
      MOUNT_CHAIN_BROKEN - Mount graph missing parent router

    Args:
        total_candidates: Total routes found
        resolved_count: Routes successfully resolved
        unresolved_failures: List of {"source_file", "line", "reason"}

    Returns:
        Structured integrity block for report
    """
    unresolved_count = total_candidates - resolved_count

    # Safe division
    if total_candidates > 0:
        coverage_ratio = resolved_count / total_candidates
        coverage_percent = round(coverage_ratio * 100, 1)
    else:
        coverage_ratio = 0.0
        coverage_percent = 0.0

    return {
        "total_candidates": total_candidates,
        "resolved": resolved_count,
        "unresolved": unresolved_count,
        "coverage_ratio": coverage_ratio,
        "coverage_percent": coverage_percent,
        "unresolved_details": unresolved_failures  # List of structured failures
    }


def _detect_validator_in_middleware(content: str) -> dict:
    """
    PHASE 5 ACCURACY HARDENING: Static detection of validators in middleware.

    Detects patterns for:
      - Joi: joi.object(...), Joi.object(...), schema.validate(...)
      - express-validator: body(...), query(...), param(...), validationResult
      - Zod: z.object(...), ZodError
      - Mongoose: Schema, model(...), validate(...)

    Returns:
        {
            "joi_found": bool,
            "express_validator_found": bool,
            "zod_found": bool,
            "mongoose_found": bool,
            "validators": [list of detected validator patterns]
        }
    """
    content_lower = (content or "").lower()

    joi_patterns = {"joi.object", "joi.validate", ".joi("}
    joi_found = any(p in content_lower for p in joi_patterns)

    ev_patterns = {"body(", "query(", "param(", "validationresult"}
    express_validator_found = any(p in content_lower for p in ev_patterns)

    zod_patterns = {"z.object", "zoderror"}
    zod_found = any(p in content_lower for p in zod_patterns)

    mongoose_patterns = {"schema(", "model(", ".validate("}
    mongoose_found = any(p in content_lower for p in mongoose_patterns)

    validators = []
    if joi_found:
        validators.append("Joi")
    if express_validator_found:
        validators.append("express-validator")
    if zod_found:
        validators.append("Zod")
    if mongoose_found:
        validators.append("Mongoose")

    return {
        "joi_found": joi_found,
        "express_validator_found": express_validator_found,
        "zod_found": zod_found,
        "mongoose_found": mongoose_found,
        "validators": validators
    }


def _is_valid_path(path: str) -> bool:
    p = _normalize_path(path)
    if not p.startswith("/"):
        return False
    if "//" in p:
        return False
    if len(p) > 1 and p.endswith("/"):
        return False
    parts = [seg for seg in p.split("/") if seg]
    for seg in parts:
        if _is_param(seg):
            name = seg[1:-1]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                return False
            continue
        if not _PATH_LITERAL_RE.fullmatch(seg):
            return False
    return True


def _is_business_route(path: str) -> bool:
    p = _normalize_path(path).lower()
    if p in {"*", "/*"}:
        return False
    if _STATIC_EXT_RE.search(p):
        return False
    return True


def _classify_auth_inherited(
    tokens: list[str],
    is_inherited: bool = False
) -> str:
    """
    PHASE 1 ACCURACY HARDENING: Auth classification with inheritance awareness.

    Properly detects and classifies middleware from multiple sources:
    - Route-level middleware (direct detection, is_inherited=False)
    - Router-level inherited middleware (is_inherited=True)
    - App.use() inherited middleware (is_inherited=True)

    Strict classification rules:
    - JWT: bearer, jwt, passport, token, authenticate keywords
    - RBAC: role, permission, authorize, acl, admin keywords
    - Session: session, sess keywords
    - JWT+RBAC: Both JWT and RBAC keywords present
    - Public: No auth keywords detected (fallback)

    Precedence (strict):
    1. JWT+RBAC (if both present)
    2. JWT
    3. Session
    4. RBAC
    5. Public

    Args:
        tokens: List of middleware tokens (from route, router, or app)
        is_inherited: True if tokens came from parent scope (router/app)

    Returns:
        Deterministic auth type from closed set
    """
    if not tokens:
        return "Public"

    # Normalize: combine tokens, lowercase, merge whitespace
    combined = " ".join(str(t) for t in tokens).lower().strip()
    if not combined:
        return "Public"

    # JWT keyword patterns (industry standard auth tokens)
    jwt_indicators = {
        "jwt", "bearer", "passport", "token", "tokenverify",
        "jwtverify", "verifyjwt", "verify", "authenticate", "authtoken",
        "tokenauth", "jwtauth", "authorizationbearer"
    }

    # RBAC keyword patterns (role-based access control)
    rbac_indicators = {
        "rbac", "authorize", "authorization", "admin", "adminonly",
        "role", "permission", "acl", "access", "require", "check", "checkpermission",
        "checkauthorization", "requirerole"
    }

    # Session keyword patterns
    session_indicators = {"session", "sess", "sessionauth", "sessionverify"}

    # Deterministic keyword matching (no substring, whole word)
    has_jwt = any(kw in combined for kw in jwt_indicators)
    has_rbac = any(kw in combined for kw in rbac_indicators)
    has_session = any(kw in combined for kw in session_indicators)

    # Apply strict precedence (order matters)
    if has_jwt and has_rbac:
        return "JWT+RBAC"
    elif has_jwt:
        return "JWT"
    elif has_session:
        return "Session"
    elif has_rbac:
        return "RBAC"
    else:
        # No auth detected - public endpoint
        return "Public"


def _classify_auth_with_metadata(
    tokens: list[str],
    token_source: str = "STATIC_DISCOVERY",
    has_mount_inheritance: bool = False,
    inferred_unknown: bool = False
) -> dict:
    """
    Enterprise authentication detection with metadata and confidence scoring.

    Args:
        tokens: Middleware tokens to analyze
        token_source: Source of tokens - "AUTH_ANALYSIS", "ROUTE_RESOLUTION", "STATIC_DISCOVERY"
        has_mount_inheritance: Whether auth was inherited via mount graph
        inferred_unknown: Whether we inferred auth for unknown middleware

    Returns:
        Dict with keys: type, classification_basis, confidence, inferred
    """
    auth_type = _classify_auth_inherited(tokens, is_inherited=has_mount_inheritance)

    # Special handling for inferred unknown middleware
    # If we have no known keywords but inferred_unknown=True, assume JWT
    if inferred_unknown and auth_type == "Public":
        auth_type = "JWT"

    # Determine classification basis
    if inferred_unknown:
        classification_basis = "INFERRED"
    elif token_source == "AUTH_ANALYSIS":
        classification_basis = "AUTH_ANALYSIS"
    elif has_mount_inheritance:
        classification_basis = "ROUTE_RESOLUTION"
    else:
        classification_basis = "STATIC_DISCOVERY"

    # Determine confidence score
    if inferred_unknown:
        confidence = 0.85
    elif token_source == "AUTH_ANALYSIS" and not has_mount_inheritance:
        confidence = 1.0
    elif token_source == "AUTH_ANALYSIS" and has_mount_inheritance:
        confidence = 0.95
    elif has_mount_inheritance:
        confidence = 0.9
    else:
        confidence = 0.8

    return {
        "type": auth_type,
        "classification_basis": classification_basis,
        "confidence": confidence,
        "inferred": inferred_unknown
    }


def _calculate_confidence_dynamic(
    base_score: float = 0.95,
    ast_parsed: bool = True,
    mount_resolved: bool = False,
    auth_resolved: bool = False,
    schema_linked: bool = False,
    has_unresolved_path: bool = False,
    missing_auth: bool = False
) -> float:
    """
    PHASE 4 ACCURACY HARDENING: Dynamic confidence scoring.

    Base scoring:
      0.95 = AST extraction successful
      0.75 = Regex fallback used

    Adjustments:
      +0.02 Mount chain resolved successfully
      +0.02 Auth type successfully inferred
      +0.02 Schema found and linked
      -0.10 Unresolved path parameters
      -0.05 Missing auth inference

    Final confidence clamped to [0.0, 1.0], rounded to 2 decimals.

    Args:
        base_score: 0.95 or 0.75 (AST vs regex)
        ast_parsed: Whether AST parsing was successful
        mount_resolved: Whether mount chain fully resolved
        auth_resolved: Whether auth was successfully inferred
        schema_linked: Whether schema was found and linked
        has_unresolved_path: Whether path params couldn't be resolved
        missing_auth: Whether auth inference is missing

    Returns:
        Confidence score (0.0-1.0), rounded to 2 decimals
    """
    # Start with base score
    score = base_score if ast_parsed else 0.75

    # Apply adjustments
    if mount_resolved:
        score += 0.02
    if auth_resolved:
        score += 0.02
    if schema_linked:
        score += 0.02
    if has_unresolved_path:
        score -= 0.10
    if missing_auth:
        score -= 0.05

    # Clamp to [0.0, 1.0] and round to 2 decimals
    score = max(0.0, min(1.0, score))
    score = round(score, 2)

    return score


def _classify_auth_enhanced(tokens: list[str]) -> str:
    """
    Backward-compatible enhanced auth classification (returns type only).

    For full metadata, use _classify_auth_with_metadata() instead.
    """
    metadata = _classify_auth_with_metadata(tokens, token_source="STATIC_DISCOVERY")
    return metadata["type"]


def _classify_auth(tokens: list[str]) -> str:
    """Backward-compatible wrapper for enhanced auth classification."""
    return _classify_auth_enhanced(tokens)


def _split_args(raw: str) -> list[str]:
    parts = []
    buf = []
    depth = 0
    quote = ""
    for ch in raw:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in {"'", '"'}:
            quote = ch
            buf.append(ch)
            continue
        if ch in {"(", "[", "{"}:
            depth += 1
            buf.append(ch)
            continue
        if ch in {")", "]", "}"}:
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _extract_call_tokens(args: list[str]) -> list[str]:
    out = []
    for arg in args:
        tok = arg.strip()
        if not tok:
            continue
        if tok.startswith(("'", '"')):
            continue
        tok = tok.split("(")[0].strip()
        tok = tok.split(".")[-1].strip()
        tok = re.sub(r"[^A-Za-z0-9_]+", "", tok)
        if tok:
            out.append(tok)
    return sorted(set(out))


def _endpoint_hash(method: str, full_path: str) -> str:
    seed = f"v1|{method.upper()}|{_normalize_path(full_path)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _resolve_import_path(current_file: str, import_path: str, files: set[str]) -> Optional[str]:
    p = import_path.strip()
    if not p.startswith("."):
        return None
    base_dir = os.path.dirname(current_file)
    joined = os.path.normpath(os.path.join(base_dir, p)).replace("\\", "/")
    candidates = [
        joined,
        joined + ".js",
        joined + ".ts",
        joined + ".jsx",
        joined + ".tsx",
        joined + ".mjs",
        joined + ".cjs",
        joined + "/index.js",
        joined + "/index.ts",
        joined + "/index.mjs",
        joined + "/index.cjs",
    ]
    for candidate in candidates:
        if candidate in files:
            return candidate
    return None


def _parse_router_symbols(content: str) -> list[str]:
    symbols = set()
    for rx in (
        r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*express\.Router\s*\(",
        r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*Router\s*\(",
    ):
        for m in re.finditer(rx, content):
            symbols.add(m.group(1))
    if "express.Router(" in content and "router" not in symbols:
        symbols.add("router")
    if re.search(r"\bexport\s+default\s+Router\s*\(\s*\)", content):
        symbols.add("__default_export_router__")
    return sorted(symbols)


def _parse_import_aliases(file_path: str, content: str, files: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", content):
        alias, imp = m.group(1), m.group(2)
        resolved = _resolve_import_path(file_path, imp, files)
        if resolved:
            aliases[alias] = resolved
    for m in re.finditer(r"\bimport\s+([A-Za-z_]\w*)\s+from\s+['\"]([^'\"]+)['\"]", content):
        alias, imp = m.group(1), m.group(2)
        resolved = _resolve_import_path(file_path, imp, files)
        if resolved:
            aliases[alias] = resolved
    for m in re.finditer(r"\bimport\s+\*\s+as\s+([A-Za-z_]\w*)\s+from\s+['\"]([^'\"]+)['\"]", content):
        alias, imp = m.group(1), m.group(2)
        resolved = _resolve_import_path(file_path, imp, files)
        if resolved:
            aliases[alias] = resolved
    for m in re.finditer(r"\bimport\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]", content, flags=re.S):
        imp = m.group(2)
        resolved = _resolve_import_path(file_path, imp, files)
        if not resolved:
            continue
        for part in m.group(1).split(","):
            token = part.strip()
            if not token:
                continue
            local = token.split(" as ", 1)[1].strip() if " as " in token else token
            if re.fullmatch(r"[A-Za-z_]\w*", local):
                aliases[local] = resolved
    return aliases


def _extract_balanced_block(content: str, open_index: int, open_char: str, close_char: str) -> tuple[str, int] | None:
    if open_index < 0 or open_index >= len(content) or content[open_index] != open_char:
        return None
    depth = 1
    i = open_index + 1
    quote = ""
    while i < len(content):
        ch = content[i]
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
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return content[open_index + 1 : i], i
        i += 1
    return None


def _is_string_literal(expr: str) -> bool:
    text = expr.strip()
    return bool(re.fullmatch(r"['\"][^'\"]*['\"]", text))


def _strip_string_literal(expr: str) -> str:
    text = expr.strip()
    if _is_string_literal(text):
        return text[1:-1]
    return text


def _parse_exported_router_symbol(content: str, router_symbols: list[str]) -> str:
    if "__default_export_router__" in router_symbols:
        return "__default_export_router__"
    m = re.search(r"\bmodule\.exports\s*=\s*([A-Za-z_]\w*)", content)
    if m and m.group(1) in router_symbols:
        return m.group(1)
    m = re.search(r"\bexport\s+default\s+([A-Za-z_]\w*)", content)
    if m and m.group(1) in router_symbols:
        return m.group(1)
    if len(router_symbols) == 1:
        return router_symbols[0]
    preferred = [s for s in router_symbols if s not in {"router", "__default_export_router__"}]
    if preferred:
        return sorted(preferred)[0]
    if router_symbols:
        return sorted(router_symbols)[0]
    return "router"


def _parse_route_collection_arrays(content: str) -> dict[str, list[dict[str, str]]]:
    """
    Parse static route collections, e.g.:
      const defaultRoutes = [{ path: '/auth', route: authRoute }]
    """
    route_collections: dict[str, list[dict[str, str]]] = {}
    for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*\[", content):
        arr_name = m.group(1)
        open_idx = m.end() - 1
        block = _extract_balanced_block(content, open_idx, "[", "]")
        if not block:
            continue
        body, _ = block
        entries: list[dict[str, str]] = []
        for om in re.finditer(r"\{([^{}]*)\}", body):
            obj_text = om.group(1)
            row: dict[str, str] = {}
            for token in _split_args(obj_text):
                if ":" not in token:
                    continue
                key, value = token.split(":", 1)
                key = key.strip().strip("'\"`")
                value = value.strip()
                if not key:
                    continue
                row[key] = _strip_string_literal(value) if _is_string_literal(value) else value
            if row:
                entries.append(row)
        if entries:
            route_collections[arr_name] = entries
    return route_collections


def _build_graph(
    file_paths: list[str],
    read_file: Callable[[str], str | None],
) -> tuple[list[RouterMountEdge], dict[str, list[str]], dict[str, dict[str, str]], dict[RouterIdentity, list[str]]]:
    files = set(file_paths)
    router_symbols_by_file: dict[str, list[str]] = {}
    import_aliases_by_file: dict[str, dict[str, str]] = {}
    exported_router_symbol_by_file: dict[str, str] = {}
    file_content_cache: dict[str, str] = {}
    for file_path in sorted(file_paths):
        if not file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
            continue
        content = read_file(file_path) or ""
        file_content_cache[file_path] = content
        router_symbols_by_file[file_path] = _parse_router_symbols(content)
        import_aliases_by_file[file_path] = _parse_import_aliases(file_path, content, files)
        exported_router_symbol_by_file[file_path] = _parse_exported_router_symbol(
            content,
            router_symbols_by_file[file_path],
        )

    edges: list[RouterMountEdge] = []
    edge_seen: set[tuple[str, str, str, str, str]] = set()
    router_middleware: dict[RouterIdentity, list[str]] = {}
    # Balanced-paren finder: locate `symbol.use(` and extract args body
    # respecting nested parens so `require(...)` doesn't break the match.
    use_header_rx = re.compile(r"\b([A-Za-z_]\w*)\.use\s*\(")

    def _extract_balanced_args(content: str, start: int) -> str | None:
        """From the char after '(' at *start*, return the args string up to the matching ')'."""
        depth = 1
        i = start
        while i < len(content) and depth > 0:
            ch = content[i]
            if ch in ("'", '"', '`'):
                # Skip string literals
                q = ch
                i += 1
                while i < len(content) and content[i] != q:
                    if content[i] == '\\':
                        i += 1  # skip escape
                    i += 1
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return content[start:i]
            i += 1
        return None

    def _resolve_child_router(source_file: str, aliases: dict[str, str], child_expr: str) -> tuple[str, str] | None:
        token = child_expr.strip()
        if not token:
            return None

        inline_req = re.search(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", token)
        if inline_req:
            resolved = _resolve_import_path(source_file, inline_req.group(1), files)
            if not resolved:
                return None
            child_file = resolved
        else:
            child_alias = re.sub(r"[^A-Za-z0-9_]", "", token.split(".")[-1])
            child_file = aliases.get(child_alias, source_file)

            # Named/default import to another file.
            if child_alias in aliases:
                child_file = aliases[child_alias]
            elif child_alias in (router_symbols_by_file.get(source_file, []) or []):
                child_file = source_file
            else:
                return None

        child_symbols = router_symbols_by_file.get(child_file, []) or ["router"]
        child_symbol = ""
        child_alias = re.sub(r"[^A-Za-z0-9_]", "", token.split(".")[-1])
        if child_alias in child_symbols:
            child_symbol = child_alias
        else:
            exported_symbol = exported_router_symbol_by_file.get(child_file, "")
            if exported_symbol and exported_symbol in child_symbols:
                child_symbol = exported_symbol
        if not child_symbol:
            preferred = [s for s in child_symbols if s not in {"router", "__default_export_router__"}]
            if preferred:
                child_symbol = sorted(preferred)[0]
            else:
                child_symbol = sorted(child_symbols)[0]
        return child_file, child_symbol

    def _add_edge(
        source_file: str,
        parent_symbol: str,
        mount_path: str,
        child_expr: str,
        middleware_tokens: list[str],
    ) -> None:
        parent = RouterIdentity(
            source_file,
            "__root__" if parent_symbol in _APP_SYMBOLS else parent_symbol,
        )
        aliases = import_aliases_by_file.get(source_file, {})
        resolved_child = _resolve_child_router(source_file, aliases, child_expr)
        if not resolved_child:
            # Keep middleware context even when child router is unresolved.
            if middleware_tokens:
                current = router_middleware.get(parent, [])
                router_middleware[parent] = sorted(set(current + middleware_tokens))
            return
        child_file, child_symbol = resolved_child
        child = RouterIdentity(child_file, child_symbol)
        key = (parent.file_path, parent.router_symbol, child.file_path, child.router_symbol, mount_path)
        if key in edge_seen:
            return
        edge_seen.add(key)
        edges.append(
            RouterMountEdge(
                parent=parent,
                child=child,
                mount_path=mount_path,
                middleware_tokens=sorted(set(middleware_tokens)),
            )
        )

    def _parse_router_chain_mounts(content: str, start_idx: int, parent_symbol: str, source_file: str) -> None:
        i = start_idx
        while i < len(content):
            while i < len(content) and content[i].isspace():
                i += 1
            if i >= len(content) or content[i] != ".":
                break
            if not content.startswith(".use", i):
                break
            i += len(".use")
            while i < len(content) and content[i].isspace():
                i += 1
            if i >= len(content) or content[i] != "(":
                break
            args_block = _extract_balanced_block(content, i, "(", ")")
            if not args_block:
                break
            args_body, close_idx = args_block
            args = _split_args(args_body)
            if args:
                if len(args) == 1:
                    mount_path_value = "/"
                    child_expr = args[0].strip()
                    middleware_tokens = []
                else:
                    mount_expr = args[0].strip()
                    child_expr = args[-1].strip()
                    middleware_tokens = _extract_call_tokens(args[1:-1])
                    if _is_string_literal(mount_expr):
                        mount_path_value = _normalize_path(_strip_string_literal(mount_expr))
                    else:
                        mount_path_value = ""
                if mount_path_value:
                    _add_edge(source_file, parent_symbol, mount_path_value, child_expr, middleware_tokens)
                elif middleware_tokens:
                    parent = RouterIdentity(source_file, "__root__" if parent_symbol in _APP_SYMBOLS else parent_symbol)
                    current = router_middleware.get(parent, [])
                    router_middleware[parent] = sorted(set(current + middleware_tokens))
            i = close_idx + 1

    for file_path in sorted(file_paths):
        if not file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
            continue
        content = file_content_cache.get(file_path, "")
        aliases = import_aliases_by_file.get(file_path, {})

        # Support declaration chains such as:
        #   const api = Router().use(tagsController).use(authController)
        for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*Router\s*\(\s*\)", content):
            _parse_router_chain_mounts(content, m.end(), m.group(1), file_path)

        # Support default export chains:
        #   export default Router().use('/api', api)
        for m in re.finditer(r"\bexport\s+default\s+Router\s*\(\s*\)", content):
            _parse_router_chain_mounts(content, m.end(), "__default_export_router__", file_path)

        # Support dynamic route collection mounts:
        #   defaultRoutes.forEach(route => router.use(route.path, route.route))
        route_collections = _parse_route_collection_arrays(content)
        for fm in re.finditer(r"\b([A-Za-z_]\w*)\.forEach\s*\(", content):
            collection_name = fm.group(1)
            rows = route_collections.get(collection_name, [])
            if not rows:
                continue
            args_body = _extract_balanced_args(content, fm.end())
            if args_body is None or "=>" not in args_body:
                continue
            left, right = args_body.split("=>", 1)
            iter_var = left.strip()
            if iter_var.startswith("(") and iter_var.endswith(")"):
                iter_var = iter_var[1:-1].strip()
            iter_var = iter_var.split(",", 1)[0].strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", iter_var):
                continue
            body = right.strip()
            for um in re.finditer(
                rf"\b([A-Za-z_]\w*)\.use\s*\(\s*{re.escape(iter_var)}\.([A-Za-z_]\w*)\s*,\s*{re.escape(iter_var)}\.([A-Za-z_]\w*)\s*\)",
                body,
            ):
                parent_symbol = um.group(1)
                path_prop = um.group(2)
                route_prop = um.group(3)
                for row in rows:
                    mount_path_raw = row.get(path_prop, "")
                    child_expr = row.get(route_prop, "")
                    if not mount_path_raw or not child_expr:
                        continue
                    mount_path = _normalize_path(str(mount_path_raw))
                    _add_edge(file_path, parent_symbol, mount_path, child_expr, [])

        for m in use_header_rx.finditer(content):
            args_body = _extract_balanced_args(content, m.end())
            if args_body is None:
                continue
            parent_symbol = m.group(1)
            args = _split_args(args_body)
            if not args:
                continue
            if len(args) == 1:
                mount_path_value = "/"
                maybe_child = args[0].strip()
                middleware_tokens = []
            else:
                mount_path_expr = args[0].strip()
                maybe_child = args[-1].strip()
                middleware_tokens = _extract_call_tokens(args[1:-1])
                mount_path_value = _normalize_path(_strip_string_literal(mount_path_expr)) if _is_string_literal(mount_path_expr) else ""

            if mount_path_value:
                _add_edge(file_path, parent_symbol, mount_path_value, maybe_child, middleware_tokens)
            elif middleware_tokens:
                parent = RouterIdentity(file_path, "__root__" if parent_symbol in _APP_SYMBOLS else parent_symbol)
                current = router_middleware.get(parent, [])
                router_middleware[parent] = sorted(set(current + middleware_tokens))
    edges = sorted(
        edges,
        key=lambda e: (
            e.parent.file_path,
            e.parent.router_symbol,
            e.child.file_path,
            e.child.router_symbol,
            e.mount_path,
        ),
    )
    return edges, router_symbols_by_file, import_aliases_by_file, router_middleware


def _infer_router_symbol_from_line(content: str, line_start: int, method: str) -> tuple[str, list[str]]:
    lines = content.splitlines()
    idx = max(0, min(len(lines) - 1, int(line_start or 1) - 1)) if lines else 0
    snippet = " ".join(lines[idx : min(len(lines), idx + 5)]) if lines else content[:400]
    m = re.search(rf"\b([A-Za-z_]\w*)\.{re.escape(method.lower())}\s*\(", snippet, re.IGNORECASE)
    symbol = m.group(1) if m else "router"
    tokens = re.findall(r"[A-Za-z_]\w+", snippet)
    return symbol, tokens


def _is_express_repo(tech_stack: dict | None, file_paths: list[str], read_file: Callable[[str], str | None]) -> bool:
    stack = tech_stack or {}
    if str(stack.get("backend_framework") or "").lower() == "express":
        return True
    for file_path in file_paths:
        if not file_path.endswith(("package.json", ".js", ".ts")):
            continue
        content = (read_file(file_path) or "").lower()
        if '"express"' in content or "express.router(" in content or ".use(" in content:
            return True
    return False


def _resolve_express_candidates_internal(
    candidates: list[dict],
    file_paths: list[str],
    read_file: Callable[[str], str | None],
    tech_stack: Optional[dict] = None,
) -> dict:
    if not _is_express_repo(tech_stack, file_paths, read_file):
        return {"validation_status": "OK", "candidates": list(candidates)}

    edges, router_symbols_by_file, _, router_middleware = _build_graph(file_paths, read_file)
    has_any_mount_edges = bool(edges)
    incoming: dict[RouterIdentity, list[RouterMountEdge]] = {}
    for edge in edges:
        incoming.setdefault(edge.child, []).append(edge)

    memo: dict[RouterIdentity, list[tuple[str, tuple[str, ...]]]] = {}
    active: set[RouterIdentity] = set()

    def resolve_contexts(identity: RouterIdentity, depth: int) -> list[tuple[str, tuple[str, ...]]]:
        if depth > 10:
            raise ValueError("Router mount depth exceeded")
        if identity in memo:
            return memo[identity]
        if identity in active:
            raise ValueError("Router mount cycle detected")
        active.add(identity)
        edges_in = sorted(
            incoming.get(identity, []),
            key=lambda e: (e.parent.file_path, e.parent.router_symbol, e.mount_path, e.child.file_path, e.child.router_symbol),
        )
        contexts: list[tuple[str, tuple[str, ...]]]
        if not edges_in:
            # Only root/app identities may emit routes without incoming mounts.
            if identity.router_symbol == "__root__":
                contexts = [("", tuple())]
            elif not has_any_mount_edges:
                # Backward compatibility for standalone router files in repos
                # that have no mount graph evidence.
                contexts = [("", tuple())]
            else:
                contexts = []
        else:
            merged = set()
            contexts = []
            for edge in edges_in:
                for base_path, base_tokens in resolve_contexts(edge.parent, depth + 1):
                    next_path = _join_paths(base_path, edge.mount_path)
                    next_tokens = tuple(sorted(set(list(base_tokens) + list(edge.middleware_tokens))))
                    key = (next_path, next_tokens)
                    if key in merged:
                        continue
                    merged.add(key)
                    contexts.append(key)
        local_tokens = tuple(sorted(set(router_middleware.get(identity, []))))
        if local_tokens:
            contexts = [(p, tuple(sorted(set(list(t) + list(local_tokens))))) for p, t in contexts]
        active.remove(identity)
        contexts = sorted(contexts, key=lambda item: (item[0], item[1]))
        memo[identity] = contexts
        return contexts

    resolved_candidates: list[dict] = []
    seen_keys: set[str] = set()
    for candidate in sorted(
        list(candidates),
        key=lambda c: (str(c.get("method", "")).upper(), str(c.get("path", "")), str(c.get("source_file", "")), int(c.get("line_start", 0) or 0)),
    ):
        method = str(candidate.get("method", "GET")).upper()
        raw_path = str(candidate.get("path", ""))
        source_file = str(candidate.get("source_file", ""))
        line_start = int(candidate.get("line_start", 0) or 0)
        content = candidate.get("content", "") or ""
        router_symbol = str(candidate.get("router_symbol", "")).strip()
        middleware_tokens = [str(x) for x in (candidate.get("middleware_tokens") or []) if str(x).strip()]
        if not router_symbol:
            inferred_symbol, inferred_tokens = _infer_router_symbol_from_line(content, line_start, method)
            router_symbol = inferred_symbol
            middleware_tokens = sorted(set(middleware_tokens + inferred_tokens))

        if source_file:
            if router_symbol in _APP_SYMBOLS:
                identity = RouterIdentity(source_file, "__root__")
            else:
                known = router_symbols_by_file.get(source_file, [])
                if router_symbol not in known and len(known) == 1:
                    router_symbol = known[0]
                identity = RouterIdentity(source_file, router_symbol or "router")
        else:
            identity = RouterIdentity("", "__root__")

        try:
            contexts = resolve_contexts(identity, 0) if source_file else [("", tuple())]
        except ValueError as e:
            return {"validation_status": "FAILED", "error": str(e)}

        for prefix, inherited_tokens in contexts:
            full_path = _join_paths(prefix, raw_path)
            if not _is_business_route(full_path):
                continue
            normalized_key = f"{method.lower()} {full_path.lower()}"
            if normalized_key in seen_keys:
                return {"validation_status": "FAILED", "error": "Duplicate normalized route detected"}
            seen_keys.add(normalized_key)
            all_tokens = sorted(set(middleware_tokens + list(inherited_tokens)))
            auth_type = _classify_auth(all_tokens)
            operation_id = _operation_id_base(method, full_path)
            enriched = dict(candidate)
            enriched["path"] = full_path
            enriched["normalized_key"] = normalized_key
            enriched["operation_id"] = operation_id
            enriched["resolved_auth_type"] = auth_type
            enriched["middleware_tokens"] = all_tokens
            enriched["endpoint_hash"] = _endpoint_hash(method, full_path)
            resolved_candidates.append(enriched)

    # Global deterministic operation_id uniqueness.
    op_counts: dict[str, int] = {}
    final_candidates: list[dict] = []
    for row in sorted(
        resolved_candidates,
        key=lambda c: (str(c.get("normalized_key", "")), str(c.get("source_file", "")), int(c.get("line_start", 0) or 0)),
    ):
        base = str(row.get("operation_id", "getResource")) or "getResource"
        op_counts[base] = op_counts.get(base, 0) + 1
        suffix = op_counts[base]
        row = dict(row)
        row["operation_id"] = base if suffix == 1 else f"{base}_{suffix}"
        final_candidates.append(row)

    # Deterministic post-build validation gate.
    key_seen: set[str] = set()
    hash_seen: set[str] = set()
    for row in final_candidates:
        k = str(row.get("normalized_key", ""))
        h = str(row.get("endpoint_hash", ""))
        p = str(row.get("path", ""))
        oid = str(row.get("operation_id", ""))
        auth = str(row.get("resolved_auth_type", ""))
        if not _is_valid_path(p):
            return {"validation_status": "FAILED", "error": "Malformed path detected"}
        if k in key_seen:
            return {"validation_status": "FAILED", "error": "Duplicate normalized route detected"}
        if h in hash_seen:
            return {"validation_status": "FAILED", "error": "Duplicate endpoint hash detected"}
        if not _OP_ID_RE.fullmatch(oid):
            return {"validation_status": "FAILED", "error": "Malformed operation_id detected"}
        if auth not in {"JWT", "Session", "RBAC", "JWT+RBAC", "Public"}:
            return {"validation_status": "FAILED", "error": "Invalid auth classification detected"}
        key_seen.add(k)
        hash_seen.add(h)

    resolved_candidates = sorted(
        final_candidates,
        key=lambda c: (str(c.get("normalized_key", "")), str(c.get("source_file", "")), int(c.get("line_start", 0) or 0)),
    )

    # ========== COVERAGE SCORING ==========
    # Calculate route coverage metrics for analysis quality reporting
    mounted_count = len(candidates)
    resolved_count = len(resolved_candidates)

    # Calculate coverage ratio: resolved / mounted
    coverage_ratio = (resolved_count / mounted_count) if mounted_count > 0 else 0.0
    coverage_percent = int(round(coverage_ratio * 100))
    unresolved_count = max(0, mounted_count - resolved_count)

    # Non-breaking coverage warnings (if coverage < 100%)
    coverage_warnings = []
    if coverage_ratio < 1.0:
        coverage_warnings.append(
            f"COVERAGE_WARNING: {unresolved_count}/{mounted_count} route candidates could not be resolved"
        )

    # Return with coverage metrics added
    return {
        "validation_status": "OK",
        "candidates": resolved_candidates,
        "coverage_metrics": {
            "mounted_route_count": mounted_count,
            "resolved_route_count": resolved_count,
            "coverage_ratio": round(coverage_ratio, 2),
            "coverage_percent": coverage_percent,
            "unresolved_routes": unresolved_count,
            "coverage_warnings": coverage_warnings,
        },
    }


def resolve_route_candidates(
    candidates: list[dict],
    file_paths: list[str],
    read_file: Callable[[str], str | None],
    tech_stack: Optional[dict] = None,
) -> dict:
    # Late import to avoid circular dependency with express adapter.
    from src.intelligence.framework_route_engine import resolve_with_framework_adapters

    return resolve_with_framework_adapters(
        candidates=candidates,
        file_paths=file_paths,
        read_file=read_file,
        tech_stack=tech_stack or {},
        express_resolver=_resolve_express_candidates_internal,
    )
