"""
Phase 4: Hash-Based Breaking Detection Engine

Integrates symbol graph, contract, and middleware changes into unified breaking
change detection with severity ranking and mutation propagation tracking.

Breaking change sources:
1. Symbol mutations (type graph structural hash change)
2. Contract mutations (route parameter/schema changes)
3. Middleware mutations (order, removal, authorization layer changes)
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List, Tuple, Set


class BreakingChangeSeverity(Enum):
    """Severity enum for breaking changes."""
    PATCH = 1
    MINOR = 2
    MAJOR = 3


class ChangeSource(Enum):
    """Source of breaking change."""
    TYPE_MUTATION = "type_mutation"
    CONTRACT_MUTATION = "contract_mutation"
    MIDDLEWARE_MUTATION = "middleware_mutation"


@dataclass
class SymbolMutation:
    """Represents a mutation in a symbol's structure."""
    symbol_name: str
    file_path: str
    baseline_hash: str
    current_hash: str
    baseline_structure: Dict = field(default_factory=dict)
    current_structure: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "symbol_name": self.symbol_name,
            "file_path": self.file_path,
            "baseline_hash": self.baseline_hash,
            "current_hash": self.current_hash,
        }


@dataclass
class ContractBreak:
    """Represents a breaking change in a route contract."""
    endpoint: str  # "GET/api/users"
    change_type: str  # "route_removal", "param_requirement", "field_removal"
    baseline_value: Optional[str] = None
    current_value: Optional[str] = None
    severity: BreakingChangeSeverity = BreakingChangeSeverity.MAJOR

    def to_dict(self) -> Dict:
        return {
            "endpoint": self.endpoint,
            "change_type": self.change_type,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "severity": self.severity.name,
        }


@dataclass
class MiddlewareBreak:
    """Represents a breaking change in middleware configuration."""
    scope: str  # "global" or "router_name"
    change_type: str  # "removal", "order_change", "config_mutation"
    middleware_name: str = ""
    severity: BreakingChangeSeverity = BreakingChangeSeverity.MAJOR
    details: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "scope": self.scope,
            "change_type": self.change_type,
            "middleware_name": self.middleware_name,
            "severity": self.severity.name,
            "details": self.details,
        }


@dataclass
class BreakingChange:
    """Unified breaking change with propagation tracking."""
    id: str
    source: ChangeSource
    severity: BreakingChangeSeverity
    symbol_mutations: List[SymbolMutation] = field(default_factory=list)
    contract_breaks: List[ContractBreak] = field(default_factory=list)
    middleware_breaks: List[MiddlewareBreak] = field(default_factory=list)
    affected_symbols: Set[str] = field(default_factory=set)
    propagation_chain: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "source": self.source.value,
            "severity": self.severity.name,
            "affected_symbols": sorted(self.affected_symbols),
            "propagation_chain": self.propagation_chain,
            "symbol_mutations": [m.to_dict() for m in self.symbol_mutations],
            "contract_breaks": [c.to_dict() for c in self.contract_breaks],
            "middleware_breaks": [m.to_dict() for m in self.middleware_breaks],
        }


class HashBasedBreakingDetector:
    """
    Detects breaking changes by comparing baseline vs current analysis across
    symbol graphs, route contracts, and middleware chains.

    Workflow:
    1. Ingest baseline and current symbol graphs
    2. Detect symbol mutation (structural hash change)
    3. Cross-correlate contract breaks with symbol mutations
    4. Cross-correlate middleware breaks with auth/core middleware mutations
    5. Rank severity and compute propagation chains
    """

    def __init__(self):
        self.breaking_changes: List[BreakingChange] = []
        self.symbol_mutation_map: Dict[str, SymbolMutation] = {}
        self.affected_by_symbol: Dict[str, List[str]] = {}  # symbol → affected endpoints/middleware

    def detect_breaking_changes(
        self,
        baseline_symbol_graph: Dict,
        current_symbol_graph: Dict,
        baseline_contracts: List[Dict],
        current_contracts: List[Dict],
        baseline_middleware: List[Dict],
        current_middleware: List[Dict],
    ) -> List[BreakingChange]:
        """
        Main entry point for breaking change detection.

        Returns list of breaking changes with propagation chains and severity ranking.
        """
        self.breaking_changes = []
        self.symbol_mutation_map = {}
        self.affected_by_symbol = {}

        # Step 1: Detect symbol mutations
        self._detect_symbol_mutations(baseline_symbol_graph, current_symbol_graph)

        # Step 2: Detect contract breaks
        self._detect_contract_breaks(baseline_contracts, current_contracts)

        # Step 3: Detect middleware breaks
        self._detect_middleware_breaks(baseline_middleware, current_middleware)

        # Step 4: Compute propagation chains
        self._compute_propagation_chains(current_symbol_graph)

        # Step 5: Rank by severity
        return sorted(
            self.breaking_changes, key=lambda x: (-x.severity.value, x.id)
        )

    def _detect_symbol_mutations(
        self, baseline_graph: Dict, current_graph: Dict
    ) -> None:
        """Detect symbols with changed structural hashes (type mutations)."""
        baseline_nodes = {n["symbol"]: n for n in baseline_graph.get("nodes", [])}
        current_nodes = {n["symbol"]: n for n in current_graph.get("nodes", [])}

        for symbol_name, current_node in current_nodes.items():
            baseline_node = baseline_nodes.get(symbol_name)

            if not baseline_node:
                # New symbol is not a breaking change
                continue

            baseline_hash = baseline_node.get("structural_hash", "")
            current_hash = current_node.get("structural_hash", "")

            if baseline_hash and current_hash and baseline_hash != current_hash:
                # Structural change detected
                mutation = SymbolMutation(
                    symbol_name=symbol_name,
                    file_path=current_node.get("file_path", ""),
                    baseline_hash=baseline_hash,
                    current_hash=current_hash,
                    baseline_structure=baseline_node.get("structure", {}),
                    current_structure=current_node.get("structure", {}),
                )
                self.symbol_mutation_map[symbol_name] = mutation

                # Create breaking change entry
                change = BreakingChange(
                    id=self._stable_hash(f"sym_mut_{symbol_name}_{baseline_hash}"),
                    source=ChangeSource.TYPE_MUTATION,
                    severity=BreakingChangeSeverity.MAJOR,
                    symbol_mutations=[mutation],
                    affected_symbols={symbol_name},
                )
                self.breaking_changes.append(change)

    def _detect_contract_breaks(
        self,
        baseline_contracts: List[Dict],
        current_contracts: List[Dict],
    ) -> None:
        """Detect route contract changes (breaking contracts)."""
        baseline_map = {c["endpoint"]: c for c in baseline_contracts}
        current_map = {c["endpoint"]: c for c in current_contracts}

        for endpoint, baseline_contract in baseline_map.items():
            if endpoint not in current_map:
                # Route removed
                break_change = ContractBreak(
                    endpoint=endpoint,
                    change_type="route_removal",
                    severity=BreakingChangeSeverity.MAJOR,
                )
                change = BreakingChange(
                    id=self._stable_hash(f"contract_{endpoint}_removed"),
                    source=ChangeSource.CONTRACT_MUTATION,
                    severity=BreakingChangeSeverity.MAJOR,
                    contract_breaks=[break_change],
                    affected_symbols={endpoint},
                )
                self.breaking_changes.append(change)
                continue

            current_contract = current_map[endpoint]

            # Check path parameters
            baseline_params = {
                p["name"]: p
                for p in baseline_contract.get("path_params", [])
            }
            current_params = {
                p["name"]: p for p in current_contract.get("path_params", [])
            }

            for param_name, baseline_param in baseline_params.items():
                if param_name not in current_params:
                    # Parameter removed
                    break_change = ContractBreak(
                        endpoint=endpoint,
                        change_type="param_removal",
                        baseline_value=param_name,
                        severity=BreakingChangeSeverity.MAJOR,
                    )
                    change = BreakingChange(
                        id=self._stable_hash(
                            f"contract_{endpoint}_param_{param_name}_removed"
                        ),
                        source=ChangeSource.CONTRACT_MUTATION,
                        severity=BreakingChangeSeverity.MAJOR,
                        contract_breaks=[break_change],
                        affected_symbols={endpoint},
                    )
                    self.breaking_changes.append(change)
                elif (
                    baseline_param.get("required") != current_params[param_name].get(
                        "required"
                    )
                ):
                    # Parameter requirement changed
                    break_change = ContractBreak(
                        endpoint=endpoint,
                        change_type="param_requirement_change",
                        baseline_value=str(baseline_param.get("required")),
                        current_value=str(current_params[param_name].get("required")),
                        severity=BreakingChangeSeverity.MAJOR,
                    )
                    change = BreakingChange(
                        id=self._stable_hash(
                            f"contract_{endpoint}_param_{param_name}_required"
                        ),
                        source=ChangeSource.CONTRACT_MUTATION,
                        severity=BreakingChangeSeverity.MAJOR,
                        contract_breaks=[break_change],
                        affected_symbols={endpoint},
                    )
                    self.breaking_changes.append(change)

            # Check query parameters
            baseline_query = {
                p["name"]: p
                for p in baseline_contract.get("query_params", [])
            }
            current_query = {
                p["name"]: p for p in current_contract.get("query_params", [])
            }

            for param_name, baseline_param in baseline_query.items():
                if param_name not in current_query:
                    # Query parameter removed
                    break_change = ContractBreak(
                        endpoint=endpoint,
                        change_type="query_param_removal",
                        baseline_value=param_name,
                        severity=BreakingChangeSeverity.MAJOR,
                    )
                    change = BreakingChange(
                        id=self._stable_hash(
                            f"contract_{endpoint}_query_{param_name}_removed"
                        ),
                        source=ChangeSource.CONTRACT_MUTATION,
                        severity=BreakingChangeSeverity.MAJOR,
                        contract_breaks=[break_change],
                        affected_symbols={endpoint},
                    )
                    self.breaking_changes.append(change)
                elif (
                    baseline_param.get("required") != current_query[param_name].get(
                        "required"
                    )
                ):
                    # Query parameter requirement changed
                    break_change = ContractBreak(
                        endpoint=endpoint,
                        change_type="query_param_requirement_change",
                        baseline_value=str(baseline_param.get("required")),
                        current_value=str(current_query[param_name].get("required")),
                        severity=BreakingChangeSeverity.MAJOR,
                    )
                    change = BreakingChange(
                        id=self._stable_hash(
                            f"contract_{endpoint}_query_{param_name}_required"
                        ),
                        source=ChangeSource.CONTRACT_MUTATION,
                        severity=BreakingChangeSeverity.MAJOR,
                        contract_breaks=[break_change],
                        affected_symbols={endpoint},
                    )
                    self.breaking_changes.append(change)

            # Check body schema fields
            baseline_body = baseline_contract.get("body_schema", {})
            current_body = current_contract.get("body_schema", {})

            if baseline_body and current_body:
                baseline_fields = {
                    f["name"]: f for f in baseline_body.get("fields", [])
                }
                current_fields = {
                    f["name"]: f for f in current_body.get("fields", [])
                }

                for field_name, baseline_field in baseline_fields.items():
                    if field_name not in current_fields:
                        # Field removed from schema
                        break_change = ContractBreak(
                            endpoint=endpoint,
                            change_type="body_field_removal",
                            baseline_value=field_name,
                            severity=BreakingChangeSeverity.MAJOR,
                        )
                        change = BreakingChange(
                            id=self._stable_hash(
                                f"contract_{endpoint}_field_{field_name}_removed"
                            ),
                            source=ChangeSource.CONTRACT_MUTATION,
                            severity=BreakingChangeSeverity.MAJOR,
                            contract_breaks=[break_change],
                            affected_symbols={endpoint},
                        )
                        self.breaking_changes.append(change)

    def _detect_middleware_breaks(
        self,
        baseline_middleware: List[Dict],
        current_middleware: List[Dict],
    ) -> None:
        """Detect middleware configuration changes."""
        baseline_map = {m["scope"]: m for m in baseline_middleware}
        current_map = {m["scope"]: m for m in current_middleware}

        for scope, baseline_chain in baseline_map.items():
            if scope not in current_map:
                # Entire middleware scope removed (unlikely but breaking)
                break_change = MiddlewareBreak(
                    scope=scope,
                    change_type="scope_removal",
                    severity=BreakingChangeSeverity.MAJOR,
                )
                change = BreakingChange(
                    id=self._stable_hash(f"middleware_{scope}_removed"),
                    source=ChangeSource.MIDDLEWARE_MUTATION,
                    severity=BreakingChangeSeverity.MAJOR,
                    middleware_breaks=[break_change],
                    affected_symbols={f"middleware_{scope}"},
                )
                self.breaking_changes.append(change)
                continue

            current_chain = current_map[scope]

            baseline_names = [m["name"] for m in baseline_chain.get("middleware", [])]
            current_names = [m["name"] for m in current_chain.get("middleware", [])]

            # Check for removals
            for i, baseline_name in enumerate(baseline_names):
                if baseline_name not in current_names:
                    # Middleware removed
                    severity = self._severity_for_middleware(baseline_name)
                    break_change = MiddlewareBreak(
                        scope=scope,
                        change_type="middleware_removal",
                        middleware_name=baseline_name,
                        severity=severity,
                        details={"position": i},
                    )
                    change = BreakingChange(
                        id=self._stable_hash(
                            f"middleware_{scope}_{baseline_name}_removed"
                        ),
                        source=ChangeSource.MIDDLEWARE_MUTATION,
                        severity=severity,
                        middleware_breaks=[break_change],
                        affected_symbols={f"middleware_{scope}"},
                    )
                    self.breaking_changes.append(change)

            # Check for order changes (only report if same set of middleware)
            if set(baseline_names) == set(current_names) and baseline_names != current_names:
                break_change = MiddlewareBreak(
                    scope=scope,
                    change_type="middleware_order_change",
                    severity=BreakingChangeSeverity.MINOR,
                    details={
                        "baseline_order": baseline_names,
                        "current_order": current_names,
                    },
                )
                change = BreakingChange(
                    id=self._stable_hash(f"middleware_{scope}_order"),
                    source=ChangeSource.MIDDLEWARE_MUTATION,
                    severity=BreakingChangeSeverity.MINOR,
                    middleware_breaks=[break_change],
                    affected_symbols={f"middleware_{scope}"},
                )
                self.breaking_changes.append(change)

            # Check for config changes
            baseline_mw_map = {
                m["name"]: m for m in baseline_chain.get("middleware", [])
            }
            current_mw_map = {
                m["name"]: m for m in current_chain.get("middleware", [])
            }

            for mw_name, baseline_mw in baseline_mw_map.items():
                if mw_name in current_mw_map:
                    current_mw = current_mw_map[mw_name]
                    baseline_config = baseline_mw.get("config", {})
                    current_config = current_mw.get("config", {})

                    if baseline_config != current_config:
                        # Config changed
                        severity = self._severity_for_middleware(mw_name)
                        break_change = MiddlewareBreak(
                            scope=scope,
                            change_type="middleware_config_change",
                            middleware_name=mw_name,
                            severity=severity,
                            details={
                                "baseline_config": baseline_config,
                                "current_config": current_config,
                            },
                        )
                        change = BreakingChange(
                            id=self._stable_hash(
                                f"middleware_{scope}_{mw_name}_config"
                            ),
                            source=ChangeSource.MIDDLEWARE_MUTATION,
                            severity=severity,
                            middleware_breaks=[break_change],
                            affected_symbols={f"middleware_{scope}"},
                        )
                        self.breaking_changes.append(change)

    def _compute_propagation_chains(self, current_graph: Dict) -> None:
        """
        Compute propagation chains showing how symbol mutations affect
        dependent symbols and endpoints.
        """
        # Build dependency map for propagation
        edges = current_graph.get("edges", [])
        depends_on: Dict[str, List[str]] = {}  # symbol -> list of dependents

        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if target not in depends_on:
                depends_on[target] = []
            depends_on[target].append(source)

        # For each symbol mutation, compute propagation
        for symbol_name, mutation in self.symbol_mutation_map.items():
            propagation = self._trace_dependents(symbol_name, depends_on, set())

            # Update breaking changes from this symbol
            for change in self.breaking_changes:
                if symbol_name in change.affected_symbols:
                    change.propagation_chain = propagation
                    change.affected_symbols.update(propagation)

    def _trace_dependents(
        self, symbol: str, depends_on: Dict[str, List[str]], visited: Set[str]
    ) -> List[str]:
        """Recursively trace symbols that depend on the given symbol."""
        if symbol in visited:
            return []

        visited.add(symbol)
        dependents = depends_on.get(symbol, [])
        result = list(dependents)

        for dependent in dependents:
            result.extend(self._trace_dependents(dependent, depends_on, visited))

        return sorted(list(set(result)))

    def _severity_for_middleware(self, middleware_name: str) -> BreakingChangeSeverity:
        """Map middleware name to severity level."""
        name_lower = middleware_name.lower()

        # AUTH is MAJOR severity
        if any(
            x in name_lower
            for x in ["auth", "jwt", "passport", "bearer", "api_key", "oauth"]
        ):
            return BreakingChangeSeverity.MAJOR

        # CORS is MINOR severity
        if "cors" in name_lower:
            return BreakingChangeSeverity.MINOR

        # Rate limiting is PATCH severity
        if any(x in name_lower for x in ["ratelimit", "throttle", "limit"]):
            return BreakingChangeSeverity.PATCH

        # Default to MAJOR for unknown middleware removals
        return BreakingChangeSeverity.MAJOR

    @staticmethod
    def _stable_hash(payload: str) -> str:
        """Generate stable hash for ID generation."""
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
