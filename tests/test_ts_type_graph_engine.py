"""
Test suite for TypeScript Type Graph Engine (Phase 1).
Validates deterministic parsing, structural hashing, and change detection.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.intelligence.ts_type_graph_engine import TypeGraphEngine


def test_interface_parsing():
    """Test parsing of TypeScript interfaces."""
    engine = TypeGraphEngine()

    content = """
export interface User {
  id: string;
  email: string;
  name?: string;
}

interface Config {
  port: number;
  host: string;
}
"""

    nodes = engine.parse_file("models/user.ts", content)

    assert len(nodes) >= 2
    user_node = [n for n in nodes if n.name == "User"][0]
    assert user_node.kind == "interface"
    assert user_node.exported
    assert "id" in user_node.fields
    assert user_node.fields["id"]["type"] == "string"
    assert not user_node.fields["name"]["required"]


def test_type_alias_parsing():
    """Test parsing of TypeScript type aliases."""
    engine = TypeGraphEngine()

    content = """
export type Status = "active" | "inactive" | "pending";
type Handler<T> = (data: T) => void;
"""

    nodes = engine.parse_file("types.ts", content)

    status_node = [n for n in nodes if n.name == "Status"][0]
    assert status_node.kind == "type"
    assert status_node.exported
    assert len(status_node.union_types) > 0

    handler_node = [n for n in nodes if n.name == "Handler"][0]
    assert "T" in handler_node.generic_params


def test_enum_parsing():
    """Test parsing of TypeScript enums."""
    engine = TypeGraphEngine()

    content = """
export enum Role {
  ADMIN = "admin",
  USER = "user",
  GUEST = "guest",
}
"""

    nodes = engine.parse_file("enums.ts", content)

    role_node = [n for n in nodes if n.name == "Role"][0]
    assert role_node.kind == "enum"
    assert role_node.exported


def test_class_parsing():
    """Test parsing of TypeScript classes with extends/implements."""
    engine = TypeGraphEngine()

    content = """
export class UserController implements IController {
  private repo: Repository;

  constructor(repo: Repository) {
    this.repo = repo;
  }
}

class BaseService<T> extends Service {
  data?: T;
}
"""

    nodes = engine.parse_file("controllers.ts", content)

    user_ctrl = [n for n in nodes if n.name == "UserController"][0]
    assert user_ctrl.kind == "class"
    assert "IController" in user_ctrl.implements

    base_svc = [n for n in nodes if n.name == "BaseService"][0]
    assert "Service" in base_svc.extends
    assert "T" in base_svc.generic_params


def test_import_extraction():
    """Test extraction of import statements."""
    engine = TypeGraphEngine()

    content = """
import { User } from "./models";
import type { Config } from "./config";
import * as utils from "./utils";
import express from "express";
"""

    engine.parse_file("app.ts", content)

    imports = engine.imports.get("app.ts", [])
    import_symbols = [sym for sym, _ in imports]

    assert "User" in import_symbols
    assert "Config" in import_symbols
    assert "utils" in import_symbols
    assert "express" in import_symbols


def test_structural_hash_determinism():
    """Test that structural hashing is deterministic."""
    engine = TypeGraphEngine()

    content = """
export interface Product {
  id: string;
  name: string;
  price: number;
  inStock?: boolean;
}
"""

    # Parse twice
    nodes1 = engine.parse_file("models.ts", content)
    product1 = [n for n in nodes1 if n.name == "Product"][0]
    hash1 = product1.compute_structural_hash()

    # Reset and parse again
    engine = TypeGraphEngine()
    nodes2 = engine.parse_file("models.ts", content)
    product2 = [n for n in nodes2 if n.name == "Product"][0]
    hash2 = product2.compute_structural_hash()

    assert hash1 == hash2, "Structural hash must be deterministic"


def test_three_run_deterministic_parsing():
    """Three-run determinism test for complete parsing pipeline."""
    content = """
export interface CreateUserRequest<T = string> {
  username: string;
  email: string;
  roles: Role[];
  metadata?: T;
}

export enum Role {
  ADMIN,
  USER,
  GUEST,
}

export type APIResponse<T> = {
  data: T;
  status: "success" | "error";
  timestamp: number;
};

export class UserService {
  private repo: any;

  async getUser(id: string): Promise<void> {}
}

import { Database } from "./db";
import type { Config } from "./config";
"""

    snapshots = []
    for run in range(3):
        engine = TypeGraphEngine()
        nodes = engine.parse_file("users.ts", content)
        graph = engine.build_symbol_graph()

        # Create snapshot with deterministic order
        snapshot = {
            "nodes_count": len(graph["nodes"]),
            "node_hashes": sorted([
                (nid, node.get("structural_hash", ""))
                for nid, node in graph["nodes"].items()
            ]),
            "edges": graph["edges"],
        }
        snapshots.append(snapshot)

    # All three snapshots must be identical
    assert snapshots[0] == snapshots[1] == snapshots[2], f"Snapshot mismatch: {snapshots}"


def test_change_detection_removed_field():
    """Test detection of removed fields in interfaces."""
    engine = TypeGraphEngine()

    baseline_content = """
export interface User {
  id: string;
  email: string;
  name: string;
  phone: string;
}
"""

    current_content = """
export interface User {
  id: string;
  email: string;
  name: string;
}
"""

    nodes1 = engine.parse_file("user.ts", baseline_content)
    baseline_graph = engine.build_symbol_graph()

    engine = TypeGraphEngine()
    nodes2 = engine.parse_file("user.ts", current_content)
    current_graph = engine.build_symbol_graph()

    changes = engine.detect_type_changes(baseline_graph, current_graph)

    # Should detect that User interface changed
    type_changes = [c for c in changes if c["type"] == "TYPE_STRUCTURE_CHANGE"]
    assert len(type_changes) > 0, "Should detect removed field in interface"


def test_change_detection_added_required_field():
    """Test detection of added required fields."""
    engine = TypeGraphEngine()

    baseline_content = """
export interface Product {
  id: string;
  name: string;
}
"""

    current_content = """
export interface Product {
  id: string;
  name: string;
  sku: string;
}
"""

    nodes1 = engine.parse_file("product.ts", baseline_content)
    baseline_graph = engine.build_symbol_graph()

    engine = TypeGraphEngine()
    nodes2 = engine.parse_file("product.ts", current_content)
    current_graph = engine.build_symbol_graph()

    changes = engine.detect_type_changes(baseline_graph, current_graph)

    # Should detect structural change
    assert len(changes) > 0, "Should detect added required field"


def test_change_detection_type_removal():
    """Test detection of completely removed types."""
    engine = TypeGraphEngine()

    baseline_content = """
export interface OldAPI {
  deprecated: boolean;
}

export interface NewAPI {
  version: string;
}
"""

    current_content = """
export interface NewAPI {
  version: string;
}
"""

    nodes1 = engine.parse_file("api.ts", baseline_content)
    baseline_graph = engine.build_symbol_graph()

    engine = TypeGraphEngine()
    nodes2 = engine.parse_file("api.ts", current_content)
    current_graph = engine.build_symbol_graph()

    changes = engine.detect_type_changes(baseline_graph, current_graph)
    removed = [c for c in changes if c["type"] == "TYPE_REMOVED"]

    assert len(removed) > 0, "Should detect removed type"
    assert any("OldAPI" in c["symbol_name"] for c in removed)


def test_exported_symbol_tracking():
    """Test tracking of exported symbols."""
    engine = TypeGraphEngine()

    content = """
export interface PublicAPI {}
interface PrivateAPI {}
export enum Status { ACTIVE, INACTIVE }
export type Handler = () => void;
"""

    engine.parse_file("exported.ts", content)

    exports = engine.exports.get("exported.ts", set())
    assert "PublicAPI" in exports
    assert "PrivateAPI" not in exports
    assert "Status" in exports
    assert "Handler" in exports


def test_generics_extraction():
    """Test extraction of generic type parameters."""
    engine = TypeGraphEngine()

    content = """
export interface Repository<Entity, ID = string> {
  findById(id: ID): Entity | null;
}

export type Maybe<T> = T | null | undefined;
"""

    nodes = engine.parse_file("types.ts", content)

    repo = [n for n in nodes if n.name == "Repository"][0]
    assert "Entity" in repo.generic_params
    assert "ID" in repo.generic_params

    maybe = [n for n in nodes if n.name == "Maybe"][0]
    assert "T" in maybe.generic_params


def test_symbol_graph_edges():
    """Test that symbol graph builds dependency edges."""
    engine = TypeGraphEngine()

    content = """
export interface User {
  id: string;
}

export interface UserProfile extends User {
  bio: string;
}
"""

    nodes = engine.parse_file("user.ts", content)

    # Manually add dependency
    user_id = "user.ts#User"
    profile_id = "user.ts#UserProfile"

    if profile_id in engine.nodes:
        engine.nodes[profile_id].dependencies.add(user_id)
        engine.nodes[user_id].imported_by.add(profile_id)

    graph = engine.build_symbol_graph()

    # Check edges exist
    edges = graph["edges"]
    assert len(edges) > 0, "Should have dependency edges"


def test_handles_multiline_interfaces():
    """Test parsing of multiline interface definitions."""
    engine = TypeGraphEngine()

    content = """
export interface ComplexType<T extends Base, U = Default> {
  field1: T;
  field2: U;
  field3?: Array<T>;
  field4: Record<string, U>;
  method(arg: T): Promise<U>;
}
"""

    nodes = engine.parse_file("complex.ts", content)

    complex = [n for n in nodes if n.name == "ComplexType"][0]
    assert complex.kind == "interface"
    assert "T" in complex.generic_params
    assert "U" in complex.generic_params
    assert complex.exported


def test_change_detection_deterministic_ids():
    """Test that change detection produces deterministic change IDs."""
    engine = TypeGraphEngine()

    baseline_content = """
export interface User {
  name: string;
}
"""

    current_content = """
export interface User {
  name: string;
  email: string;
}
"""

    nodes1 = engine.parse_file("user.ts", baseline_content)
    baseline_graph = engine.build_symbol_graph()

    # Run twice to verify deterministic IDs
    changes_set1 = set()
    for run in range(2):
        engine = TypeGraphEngine()
        nodes = engine.parse_file("user.ts", current_content)
        current_graph = engine.build_symbol_graph()
        changes = engine.detect_type_changes(baseline_graph, current_graph)

        change_ids = tuple(sorted([c["id"] for c in changes]))
        changes_set1.add(change_ids)

    assert len(changes_set1) == 1, "Change IDs must be deterministic"


if __name__ == "__main__":
    test_interface_parsing()
    test_type_alias_parsing()
    test_enum_parsing()
    test_class_parsing()
    test_import_extraction()
    test_structural_hash_determinism()
    test_three_run_deterministic_parsing()
    test_change_detection_removed_field()
    test_change_detection_added_required_field()
    test_change_detection_type_removal()
    test_exported_symbol_tracking()
    test_generics_extraction()
    test_symbol_graph_edges()
    test_handles_multiline_interfaces()
    test_change_detection_deterministic_ids()
    print("✅ All TypeGraphEngine tests passed!")
