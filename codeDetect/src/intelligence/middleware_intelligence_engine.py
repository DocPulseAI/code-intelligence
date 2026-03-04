"""
Middleware Intelligence Engine — Phase 3.
Detects middleware chains, order mutations, CORS/auth changes with severity mapping.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from enum import Enum


def _canonical(data: Any) -> str:
    """Canonical JSON encoding for deterministic hashing."""
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _stable_hash(payload: dict) -> str:
    """Generate deterministic hash for structure."""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


class MiddlewareType(Enum):
    """Classification of middleware types."""
    CORS = "cors"
    AUTH = "auth"
    RATE_LIMITER = "rate_limiter"
    BODY_PARSER = "body_parser"
    LOGGING = "logging"
    ERROR_HANDLER = "error_handler"
    UNKNOWN = "unknown"


class MiddlewareSeverity(Enum):
    """Breaking change severity for each middleware type."""
    PATCH = 1
    MINOR = 2
    MAJOR = 3


# Severity mapping for middleware changes
MIDDLEWARE_SEVERITY_MAP = {
    MiddlewareType.RATE_LIMITER: MiddlewareSeverity.PATCH,
    MiddlewareType.BODY_PARSER: MiddlewareSeverity.PATCH,
    MiddlewareType.LOGGING: MiddlewareSeverity.PATCH,
    MiddlewareType.CORS: MiddlewareSeverity.MINOR,
    MiddlewareType.ERROR_HANDLER: MiddlewareSeverity.MINOR,
    MiddlewareType.AUTH: MiddlewareSeverity.MAJOR,
}


class MiddlewareDefinition:
    """Represents a middleware definition in the application."""

    def __init__(
        self,
        name: str,
        middleware_type: MiddlewareType,
        file_path: str,
        line: int,
        config: dict[str, Any] | None = None,
    ):
        self.name = name
        self.middleware_type = middleware_type
        self.file_path = file_path
        self.line = line
        self.config = config or {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.middleware_type.value,
            "file_path": self.file_path,
            "line": self.line,
            "config": self.config,
        }

    def compute_config_hash(self) -> str:
        """Compute deterministic hash of middleware configuration."""
        return _stable_hash({"name": self.name, "type": self.middleware_type.value, "config": self.config})


class MiddlewareChain:
    """Represents the middleware chain order in application."""

    def __init__(self, scope: str, file_path: str):
        self.scope = scope  # "global" or "router_name"
        self.file_path = file_path
        self.middlewares: list[MiddlewareDefinition] = []

    def add_middleware(self, middleware: MiddlewareDefinition) -> None:
        """Add middleware to chain, preserving order."""
        self.middlewares.append(middleware)

    def compute_chain_hash(self) -> str:
        """Compute deterministic hash of ordered middleware chain."""
        payload = {
            "scope": self.scope,
            "middlewares": [
                (m.name, m.middleware_type.value)
                for m in self.middlewares
            ],
        }
        return _stable_hash(payload)

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "file_path": self.file_path,
            "middlewares": [m.to_dict() for m in self.middlewares],
            "chain_hash": self.compute_chain_hash(),
        }


class MiddlewareIntelligenceEngine:
    """Detects middleware chains and mutations."""

    # Pattern to match app.use() global middleware
    GLOBAL_MIDDLEWARE_PATTERN = re.compile(
        r"app\.use\s*\(\s*([^,\)]+(?:\([^)]*\))?)",
        re.MULTILINE
    )

    # Pattern to match router.use() middleware
    ROUTER_MIDDLEWARE_PATTERN = re.compile(
        r"router\.use\s*\(\s*([^,\)]+(?:\([^)]*\))?)",
        re.MULTILINE
    )

    # Pattern to match app.use with path
    SCOPED_MIDDLEWARE_PATTERN = re.compile(
        r"app\.use\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^)]+)\)",
        re.MULTILINE
    )

    # Pattern to match CORS middleware
    CORS_PATTERN = re.compile(r"cors\s*\(([^)]*)\)", re.MULTILINE)

    # Pattern to match auth middleware
    AUTH_PATTERN = re.compile(
        r"(?:jwt|passport|auth|authenticate|verifyToken|checkAuth)\s*\(([^)]*)\)",
        re.MULTILINE | re.IGNORECASE
    )

    # Pattern to match rate limiter
    RATE_LIMITER_PATTERN = re.compile(
        r"(?:rateLimit|rateLimiter|limiter)\s*\(([^)]*)\)",
        re.MULTILINE | re.IGNORECASE
    )

    def __init__(self):
        self.middleware_chains: dict[str, MiddlewareChain] = {}
        self.middleware_definitions: dict[str, MiddlewareDefinition] = {}

    def parse_file(self, file_path: str, content: str) -> tuple[list[MiddlewareChain], list[MiddlewareDefinition]]:
        """Parse middleware definitions from file."""
        chains = []
        definitions = []

        # Extract global middleware chain
        global_chain = MiddlewareChain("global", file_path)
        for match in self.GLOBAL_MIDDLEWARE_PATTERN.finditer(content):
            middleware_expr = match.group(1).strip()
            middleware_name = self._extract_middleware_name(middleware_expr)
            mw_type = self._classify_middleware(middleware_expr)
            config = self._extract_middleware_config(content, match.end())

            definition = MiddlewareDefinition(
                name=middleware_name,
                middleware_type=mw_type,
                file_path=file_path,
                line=content[:match.start()].count("\n") + 1,
                config=config,
            )

            global_chain.add_middleware(definition)
            definitions.append(definition)

            # Store definition
            def_key = f"{file_path}#{middleware_name}"
            self.middleware_definitions[def_key] = definition

        if global_chain.middlewares:
            chains.append(global_chain)
            self.middleware_chains["global"] = global_chain

        # Extract router middleware chains
        router_patterns = re.finditer(r"(?:const|let|var)\s+(\w+)\s*=\s*express\.Router\(\)", content)
        for router_match in router_patterns:
            router_name = router_match.group(1)
            router_start = router_match.end()
            router_scope = f"router_{router_name}"

            router_chain = MiddlewareChain(router_scope, file_path)

            # Find router.use() calls
            router_text = content[router_start:router_start + 3000]
            for mw_match in self.ROUTER_MIDDLEWARE_PATTERN.finditer(router_text):
                middleware_expr = mw_match.group(1).strip()
                middleware_name = self._extract_middleware_name(middleware_expr)
                mw_type = self._classify_middleware(middleware_expr)
                config = self._extract_middleware_config(router_text, mw_match.end())

                definition = MiddlewareDefinition(
                    name=middleware_name,
                    middleware_type=mw_type,
                    file_path=file_path,
                    line=content[:router_start + mw_match.start()].count("\n") + 1,
                    config=config,
                )

                router_chain.add_middleware(definition)
                definitions.append(definition)

                def_key = f"{file_path}#{router_scope}#{middleware_name}"
                self.middleware_definitions[def_key] = definition

            if router_chain.middlewares:
                chains.append(router_chain)
                self.middleware_chains[router_scope] = router_chain

        return chains, definitions

    def _extract_middleware_name(self, middleware_expr: str) -> str:
        """Extract middleware name from expression."""
        # Handle function calls: cors(), jwt(), etc.
        match = re.match(r"([A-Za-z_]\w*)\s*\(", middleware_expr)
        if match:
            return match.group(1)

        # Handle variable references
        match = re.match(r"([A-Za-z_]\w*)\s*(?:,|\))", middleware_expr)
        if match:
            return match.group(1)

        # Fallback
        return middleware_expr.split("(")[0].strip()

    def _classify_middleware(self, middleware_expr: str) -> MiddlewareType:
        """Classify middleware type based on name/config."""
        expr_lower = middleware_expr.lower()

        if "cors" in expr_lower:
            return MiddlewareType.CORS
        if any(auth in expr_lower for auth in ["jwt", "passport", "auth", "authenticate", "verifytoken"]):
            return MiddlewareType.AUTH
        if any(rate in expr_lower for rate in ["ratelimit", "limiter", "throttle"]):
            return MiddlewareType.RATE_LIMITER
        if any(body in expr_lower for body in ["json", "urlencoded", "bodyparser"]):
            return MiddlewareType.BODY_PARSER
        if any(log in expr_lower for log in ["log", "morgan", "winston"]):
            return MiddlewareType.LOGGING
        if "error" in expr_lower:
            return MiddlewareType.ERROR_HANDLER

        return MiddlewareType.UNKNOWN

    def _extract_middleware_config(self, content: str, start_pos: int) -> dict[str, Any]:
        """Extract middleware configuration from nearby content."""
        # Look for configuration in next 200 chars
        nearby = content[start_pos:start_pos + 200]

        config: dict[str, Any] = {}

        # Extract CORS config
        cors_config = re.search(r"origin\s*:\s*['\"]?([^'\"}\s,]+)", nearby)
        if cors_config:
            config["cors_origin"] = cors_config.group(1)

        # Extract rate limiter config
        rate_config = re.search(r"windowMs\s*:\s*(\d+)", nearby)
        if rate_config:
            config["window_ms"] = int(rate_config.group(1))

        max_config = re.search(r"max\s*:\s*(\d+)", nearby)
        if max_config:
            config["max_requests"] = int(max_config.group(1))

        return config

    def detect_middleware_changes(
        self,
        baseline_chains: list[dict] | None,
        current_chains: list[dict],
    ) -> list[dict]:
        """Detect breaking changes in middleware configuration."""
        changes = []

        if not baseline_chains:
            return changes

        baseline_by_scope = {c["scope"]: c for c in baseline_chains}
        current_by_scope = {c["scope"]: c for c in current_chains}

        # Check for removed middleware chains
        for scope, baseline in baseline_by_scope.items():
            if scope not in current_by_scope:
                changes.append({
                    "type": "MIDDLEWARE_CHAIN_REMOVED",
                    "scope": scope,
                    "description": f"Middleware chain '{scope}' removed",
                    "severity": "MAJOR",
                    "id": _stable_hash({"scope": scope, "type": "MIDDLEWARE_CHAIN_REMOVED"}),
                })

        # Check for middleware order changes and config mutations
        for scope in baseline_by_scope:
            if scope in current_by_scope:
                baseline_mws = baseline_by_scope[scope].get("middlewares", [])
                current_mws = current_by_scope[scope].get("middlewares", [])

                # Check for removed middlewares
                baseline_names = {m["name"] for m in baseline_mws}
                current_names = {m["name"] for m in current_mws}

                for removed in baseline_names - current_names:
                    # Find type of removed middleware
                    removed_type = next(
                        (m["type"] for m in baseline_mws if m["name"] == removed),
                        MiddlewareType.UNKNOWN.value
                    )
                    severity = MIDDLEWARE_SEVERITY_MAP.get(
                        MiddlewareType(removed_type) if removed_type != "unknown" else MiddlewareType.UNKNOWN,
                        MiddlewareSeverity.MINOR
                    ).name

                    changes.append({
                        "type": "MIDDLEWARE_REMOVED",
                        "scope": scope,
                        "middleware": removed,
                        "middleware_type": removed_type,
                        "description": f"Middleware '{removed}' removed from {scope}",
                        "severity": severity,
                        "id": _stable_hash({"scope": scope, "middleware": removed, "type": "MIDDLEWARE_REMOVED"}),
                    })

                # Check for order changes
                baseline_order = [(m["name"], m["type"]) for m in baseline_mws]
                current_order = [(m["name"], m["type"]) for m in current_mws]

                if baseline_order != current_order:
                    # Compute severity based on middleware types affected
                    affected_types = set()
                    for name, mw_type in baseline_order:
                        if name not in [n for n, _ in current_order]:
                            continue
                        baseline_idx = next(i for i, (n, _) in enumerate(baseline_order) if n == name)
                        current_idx = next((i for i, (n, _) in enumerate(current_order) if n == name), -1)
                        if baseline_idx != current_idx:
                            affected_types.add(MiddlewareType(mw_type) if mw_type != "unknown" else MiddlewareType.UNKNOWN)

                    # Severity is highest of affected types
                    max_severity = max(
                        [MIDDLEWARE_SEVERITY_MAP.get(t, MiddlewareSeverity.MINOR).value for t in affected_types],
                        default=MiddlewareSeverity.MINOR.value
                    )

                    severity_name = {1: "PATCH", 2: "MINOR", 3: "MAJOR"}.get(max_severity, "MINOR")

                    changes.append({
                        "type": "MIDDLEWARE_ORDER_CHANGED",
                        "scope": scope,
                        "description": f"Middleware execution order changed in {scope}",
                        "severity": severity_name,
                        "id": _stable_hash({"scope": scope, "type": "MIDDLEWARE_ORDER_CHANGED"}),
                    })

                # Check for config mutations
                baseline_configs = {m["name"]: m.get("config", {}) for m in baseline_mws}
                current_configs = {m["name"]: m.get("config", {}) for m in current_mws}

                for name in baseline_configs:
                    if name in current_configs:
                        if baseline_configs[name] != current_configs[name]:
                            mw_type = next((m["type"] for m in baseline_mws if m["name"] == name), "unknown")
                            severity = MIDDLEWARE_SEVERITY_MAP.get(
                                MiddlewareType(mw_type) if mw_type != "unknown" else MiddlewareType.UNKNOWN,
                                MiddlewareSeverity.MINOR
                            ).name

                            changes.append({
                                "type": "MIDDLEWARE_CONFIG_CHANGED",
                                "scope": scope,
                                "middleware": name,
                                "middleware_type": mw_type,
                                "description": f"Configuration changed for '{name}' in {scope}",
                                "severity": severity,
                                "id": _stable_hash({"scope": scope, "middleware": name, "type": "CONFIG_CHANGED"}),
                            })

        return sorted(changes, key=lambda c: (c["scope"], c.get("middleware", ""), c["type"]))

    def build_middleware_graph(self, chains: list[MiddlewareChain]) -> dict[str, Any]:
        """Build deterministic middleware dependency graph."""
        graph = {
            "chains": [],
            "middleware_map": {},
        }

        for chain in chains:
            graph["chains"].append(chain.to_dict())

            for middleware in chain.middlewares:
                key = f"{chain.scope}_{middleware.name}"
                graph["middleware_map"][key] = {
                    "name": middleware.name,
                    "type": middleware.middleware_type.value,
                    "config_hash": middleware.compute_config_hash(),
                    "severity": MIDDLEWARE_SEVERITY_MAP.get(middleware.middleware_type, MiddlewareSeverity.MINOR).name,
                }

        # Sort for determinism
        graph["chains"] = sorted(graph["chains"], key=lambda c: c["scope"])
        graph["middleware_map"] = dict(sorted(graph["middleware_map"].items()))

        return graph
