"""
Request/Response Contract Engine — Phase 2.
Extracts Express route contracts with full type intelligence.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional
from enum import Enum


def _canonical(data: Any) -> str:
    """Canonical JSON encoding for deterministic hashing."""
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _stable_hash(payload: dict) -> str:
    """Generate deterministic hash for structure."""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


class SchemaValidator(Enum):
    """Supported schema validators."""
    ZOD = "zod"
    JOI = "joi"
    TYPESCRIPT = "typescript"
    NONE = "none"


class RouteParameter:
    """Represents a route parameter with its type and requirement."""

    def __init__(self, name: str, param_type: str, required: bool = True, description: str = ""):
        self.name = name
        self.param_type = param_type
        self.required = required
        self.description = description

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.param_type,
            "required": self.required,
            "description": self.description,
        }


class BodySchema:
    """Represents request/response body schema."""

    def __init__(
        self,
        validator: SchemaValidator,
        schema_name: str | None = None,
        fields: dict[str, dict] | None = None,
        is_required: bool = True,
    ):
        self.validator = validator
        self.schema_name = schema_name
        self.fields = fields or {}
        self.is_required = is_required

    def to_dict(self) -> dict:
        return {
            "validator": self.validator.value,
            "schema_name": self.schema_name,
            "fields": self.fields,
            "required": self.is_required,
        }


class RouteContract:
    """Represents a complete route contract."""

    def __init__(
        self,
        method: str,
        path: str,
        handler_name: str = "",
        file_path: str = "",
        line: int = 0,
    ):
        self.method = method.lower()
        self.path = path
        self.handler_name = handler_name
        self.file_path = file_path
        self.line = line
        self.path_params: list[RouteParameter] = []
        self.query_params: list[RouteParameter] = []
        self.body_schema: BodySchema | None = None
        self.response_schema: BodySchema | None = None
        self.middleware_chain: list[str] = []
        self.status_codes: set[int] = set()

    def compute_contract_hash(self) -> str:
        """Compute deterministic contract hash."""
        payload = {
            "method": self.method,
            "path": self.path,
            "path_params": sorted([p.to_dict() for p in self.path_params], key=lambda x: x["name"]),
            "query_params": sorted([p.to_dict() for p in self.query_params], key=lambda x: x["name"]),
            "body_schema": self.body_schema.to_dict() if self.body_schema else None,
            "response_schema": self.response_schema.to_dict() if self.response_schema else None,
            "status_codes": sorted(self.status_codes),
        }
        return _stable_hash(payload)

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "path": self.path,
            "handler_name": self.handler_name,
            "file_path": self.file_path,
            "line": self.line,
            "path_params": [p.to_dict() for p in sorted(self.path_params, key=lambda x: x.name)],
            "query_params": [p.to_dict() for p in sorted(self.query_params, key=lambda x: x.name)],
            "body_schema": self.body_schema.to_dict() if self.body_schema else None,
            "response_schema": self.response_schema.to_dict() if self.response_schema else None,
            "middleware_chain": self.middleware_chain,
            "status_codes": sorted(self.status_codes),
            "contract_hash": self.compute_contract_hash(),
        }


class RequestResponseContractEngine:
    """Extracts Request/Response contracts from Express routes."""

    # Pattern to match Express routes
    ROUTE_PATTERN = re.compile(
        r"(?:app|router)\.(get|post|put|patch|delete|all)\s*\(\s*['\"]([^'\"]+)['\"]",
        re.IGNORECASE | re.MULTILINE
    )

    # Pattern to match path parameters
    PARAM_PATTERN = re.compile(r":([A-Za-z_]\w*)")

    # Pattern to match Zod schemas
    ZOD_SCHEMA_PATTERN = re.compile(
        r"(?:const\s+|let\s+|var\s+)?([A-Za-z_]\w*)\s*=\s*z\.(?:object|coerce|string|number|boolean)",
        re.MULTILINE
    )

    # Pattern to match Joi schemas
    JOI_SCHEMA_PATTERN = re.compile(
        r"(?:const\s+|let\s+|var\s+)?([A-Za-z_]\w*)\s*=\s*Joi\.(?:object|string|number|boolean)",
        re.MULTILINE
    )

    # Pattern to match body parsing middleware
    BODY_PARSER_PATTERN = re.compile(
        r"app\.use\s*\(\s*(?:express\.)?json\s*\(\s*(?:\{[^}]*\})?\s*\)"
    )

    # Pattern to match req.body, req.query, req.params
    PARAM_ACCESS_PATTERN = re.compile(
        r"req\.(body|query|params)(?:\.([A-Za-z_]\w*))?",
        re.MULTILINE
    )

    def __init__(self):
        self.contracts: dict[str, list[RouteContract]] = {}
        self.schemas: dict[str, BodySchema] = {}
        self.zod_imports: set[str] = set()
        self.joi_imports: set[str] = set()

    def parse_file(self, file_path: str, content: str) -> list[RouteContract]:
        """Parse Express routes from a file."""
        contracts = []

        # Detect schema validators
        self._detect_imports(content)

        # Find all route definitions
        for match in self.ROUTE_PATTERN.finditer(content):
            method = match.group(1).lower()
            path = match.group(2)
            line = content[:match.start()].count("\n") + 1

            contract = RouteContract(
                method=method,
                path=path,
                file_path=file_path,
                line=line,
            )

            # Extract handler information
            handler_info = self._extract_handler_info(content, match.end())
            if handler_info:
                contract.handler_name = handler_info

            # Extract path parameters
            contract.path_params = self._extract_path_params(path)

            # Extract body and query schema from handler
            self._extract_handler_contracts(content, contract, match)

            contracts.append(contract)
            self.contracts[file_path] = self.contracts.get(file_path, []) + [contract]

        return contracts

    def _detect_imports(self, content: str) -> None:
        """Detect which schema validators are imported."""
        if "from 'zod'" in content or 'from "zod"' in content:
            self.zod_imports.add("z")
        if "from 'joi'" in content or 'from "joi"' in content:
            self.joi_imports.add("Joi")

    def _extract_handler_info(self, content: str, start_pos: int) -> str | None:
        """Extract handler function name."""
        # Look for next token (function name or middleware)
        remaining = content[start_pos:start_pos + 200]

        # Try to find a function name or reference
        match = re.search(r"([A-Za-z_]\w*)\s*(?:\(|,|\))", remaining)
        if match:
            return match.group(1)

        return None

    def _extract_path_params(self, path: str) -> list[RouteParameter]:
        """Extract path parameters from route path."""
        params = []
        for match in self.PARAM_PATTERN.finditer(path):
            param_name = match.group(1)
            params.append(RouteParameter(
                name=param_name,
                param_type="string",
                required=True,
            ))
        return params

    def _extract_handler_contracts(self, content: str, contract: RouteContract, route_match: re.Match) -> None:
        """Extract request/response contracts from handler."""
        # Find the handler function definition
        handler_start = route_match.end()

        # Look for function definition or middleware references
        remaining = content[handler_start:handler_start + 2000]

        # Extract handler scope - find arrow function or regular function body
        arrow_match = re.search(r"=>\s*\{", remaining)
        func_match = re.search(r"function\s*\w*\s*\([^)]*\)\s*\{", remaining)

        handler_text = remaining
        if arrow_match:
            # Arrow function - find matching closing brace
            start_pos = arrow_match.start() + len(arrow_match.group())
            handler_text = self._extract_until_matching_brace(remaining, start_pos - 1)
        elif func_match:
            # Regular function - find matching closing brace
            start_pos = func_match.start() + len(func_match.group())
            handler_text = self._extract_until_matching_brace(remaining, start_pos - 1)

        # Try to extract body schema references
        body_ref_match = re.search(r"req\.body|\.body\(", handler_text)
        if body_ref_match:
            # Look for validation schema references nearby
            near_text = handler_text[:body_ref_match.start() + 500]

            # Check for Zod
            zod_match = re.search(r"([A-Za-z_]\w*)\.parse\(req\.body\)", near_text)
            if zod_match:
                schema_name = zod_match.group(1)
                contract.body_schema = BodySchema(
                    validator=SchemaValidator.ZOD,
                    schema_name=schema_name,
                    is_required=True,
                )

            # Check for Joi
            joi_match = re.search(r"\.validate\(req\.body", near_text) or \
                                 re.search(r"([A-Za-z_]\w*)\.validate\(", near_text)
            if joi_match:
                if joi_match.lastindex and joi_match.group(1):
                    schema_name = joi_match.group(1)
                else:
                    schema_name = None
                contract.body_schema = BodySchema(
                    validator=SchemaValidator.JOI,
                    schema_name=schema_name,
                    is_required=True,
                )

        # Extract query parameters
        query_match = re.search(r"req\.query(?:\.([A-Za-z_]\w*))?", handler_text)
        if query_match:
            if query_match.group(1):
                # Specific query parameter
                contract.query_params.append(RouteParameter(
                    name=query_match.group(1),
                    param_type="string",
                    required=False,
                ))
            else:
                # All query params (from schema if available)
                pass

        # Extract status codes
        status_codes = re.findall(r"\.status\((\d+)\)", handler_text)
        for code in status_codes:
            contract.status_codes.add(int(code))

        # Default status codes
        if not contract.status_codes:
            contract.status_codes.add(200)
            if contract.method == "post":
                contract.status_codes.add(201)

    def _extract_until_matching_brace(self, text: str, start_pos: int) -> str:
        """Extract text until matching closing brace."""
        if start_pos < 0 or start_pos >= len(text) or text[start_pos] != "{":
            return text[:500]

        depth = 1
        pos = start_pos + 1

        while depth > 0 and pos < len(text):
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1

        return text[start_pos:pos]

    def detect_contract_changes(self, baseline_contracts: list[dict] | None, current_contracts: list[dict]) -> list[dict]:
        """Detect contract breaking changes."""
        changes = []

        if not baseline_contracts:
            return changes

        baseline_routes = {(c["method"], c["path"]): c for c in baseline_contracts}
        current_routes = {(c["method"], c["path"]): c for c in current_contracts}

        # Check for removed routes
        for route_key, baseline in baseline_routes.items():
            if route_key not in current_routes:
                changes.append({
                    "type": "ROUTE_REMOVED",
                    "method": route_key[0],
                    "path": route_key[1],
                    "description": f"Route {route_key[0].upper()} {route_key[1]} removed",
                    "severity": "MAJOR",
                    "id": _stable_hash({"method": route_key[0], "path": route_key[1], "type": "ROUTE_REMOVED"}),
                })

        # Check for contract changes
        for route_key in baseline_routes:
            if route_key in current_routes:
                baseline = baseline_routes[route_key]
                current = current_routes[route_key]

                # Check body schema changes
                baseline_body = baseline.get("body_schema")
                current_body = current.get("body_schema")

                if baseline_body and not current_body:
                    changes.append({
                        "type": "BODY_SCHEMA_REMOVED",
                        "method": route_key[0],
                        "path": route_key[1],
                        "description": f"Body schema removed from {route_key[0].upper()} {route_key[1]}",
                        "severity": "MAJOR",
                        "id": _stable_hash({"method": route_key[0], "path": route_key[1], "type": "BODY_SCHEMA_REMOVED"}),
                    })

                # Check required field removal
                if baseline_body and current_body:
                    baseline_fields = set(baseline_body.get("fields", {}).keys())
                    current_fields = set(current_body.get("fields", {}).keys())

                    removed_fields = baseline_fields - current_fields
                    for field in removed_fields:
                        changes.append({
                            "type": "BODY_FIELD_REMOVED",
                            "method": route_key[0],
                            "path": route_key[1],
                            "field": field,
                            "description": f"Body field '{field}' removed",
                            "severity": "MAJOR",
                            "id": _stable_hash({"method": route_key[0], "path": route_key[1], "field": field}),
                        })

                # Check query parameter requirement changes
                baseline_query = {p["name"]: p for p in baseline.get("query_params", [])}
                current_query = {p["name"]: p for p in current.get("query_params", [])}

                for param_name, baseline_param in baseline_query.items():
                    if param_name in current_query:
                        current_param = current_query[param_name]
                        if not baseline_param["required"] and current_param["required"]:
                            changes.append({
                                "type": "QUERY_PARAM_REQUIRED",
                                "method": route_key[0],
                                "path": route_key[1],
                                "parameter": param_name,
                                "description": f"Query parameter '{param_name}' now required",
                                "severity": "MAJOR",
                                "id": _stable_hash({"method": route_key[0], "path": route_key[1], "param": param_name}),
                            })

        return sorted(changes, key=lambda c: (c["method"], c["path"], c["type"]))

    def build_contract_graph(self, contracts: list[RouteContract]) -> dict[str, Any]:
        """Build deterministic contract graph."""
        graph = {
            "contracts": [],
            "endpoints": {},
        }

        for contract in contracts:
            graph["contracts"].append(contract.to_dict())

            # Construct endpoint key: METHOD/path (without leading slash)
            path = contract.path.lstrip("/") if contract.path.startswith("/") else contract.path
            route_key = f"{contract.method.upper()}/{path}"
            graph["endpoints"][route_key] = {
                "handler": contract.handler_name,
                "contract_hash": contract.compute_contract_hash(),
            }

        # Sort for determinism
        graph["contracts"] = sorted(graph["contracts"], key=lambda c: (c["method"], c["path"]))
        graph["endpoints"] = dict(sorted(graph["endpoints"].items()))

        return graph
