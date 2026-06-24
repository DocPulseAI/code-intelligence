"""
Tests for Spring Boot parity engine.
Validates Spring annotation extraction, DTO metadata, and deterministic behavior.
"""

import pytest
from src.intelligence.java_spring_engine import (
    extract_spring_metadata,
    extract_spring_schema_diffs,
    extract_spring_route_diffs,
)


SPRING_CONTROLLER_SAMPLE = """
@RestController
@RequestMapping("/api/v1/users")
public class UserController {

    @GetMapping
    public ResponseEntity<List<UserDTO>> listUsers() {
        return ResponseEntity.ok(new ArrayList<>());
    }

    @GetMapping("/{id}")
    public ResponseEntity<UserDTO> getUserById(@PathVariable Long id) {
        return ResponseEntity.ok(new UserDTO());
    }

    @PostMapping
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<UserDTO> createUser(@RequestBody CreateUserRequest request) {
        return ResponseEntity.status(201).body(new UserDTO());
    }

    @PutMapping("/{id}")
    @PreAuthorize("hasRole('ADMIN')")
    public ResponseEntity<UserDTO> updateUser(
        @PathVariable Long id,
        @RequestBody UpdateUserRequest request
    ) {
        return ResponseEntity.ok(new UserDTO());
    }

    @DeleteMapping("/{id}")
    @Secured("ROLE_ADMIN")
    public ResponseEntity<Void> deleteUser(@PathVariable Long id) {
        return ResponseEntity.noContent().build();
    }
}

@Entity
@Table(name = "users")
public class User {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String email;

    private String name;

    @Column(nullable = false)
    private LocalDateTime createdAt;
}

public class UserDTO {
    private Long id;
    private String email;
    private String name;

    // getters, setters
}
"""

APPLICATION_PROPERTIES = """
server.servlet.context-path=/myapp
spring.application.name=user-service
"""


class TestSpringMetadataExtraction:
    """Test Spring metadata extraction."""

    def test_extract_controller_routes(self):
        """Test extraction of Spring controller routes."""
        metadata = extract_spring_metadata(SPRING_CONTROLLER_SAMPLE, "UserController.java")
        routes = metadata.get("routes", [])

        assert len(routes) == 5, "Should extract 5 routes"

        # Verify GET /api/v1/users
        get_all = [r for r in routes if r["method"] == "GET" and r["path"] == "/myapp/api/v1/users" or "/api/v1/users" in r["path"]]
        assert len(get_all) > 0, "Should have GET all users route"

        # Verify GET /api/v1/users/{id}
        get_one = [r for r in routes if r["method"] == "GET" and "{id}" in r["path"]]
        assert len(get_one) > 0, "Should have GET by ID route"

        # Verify POST /api/v1/users
        post = [r for r in routes if r["method"] == "POST"]
        assert len(post) > 0, "Should have POST route"

        # Verify DELETE /api/v1/users/{id}
        delete = [r for r in routes if r["method"] == "DELETE"]
        assert len(delete) > 0, "Should have DELETE route"

    def test_auth_type_extraction(self):
        """Test extraction of authentication types."""
        metadata = extract_spring_metadata(SPRING_CONTROLLER_SAMPLE, "UserController.java")
        routes = metadata.get("routes", [])

        # Find POST route
        post_routes = [r for r in routes if r["method"] == "POST"]
        assert len(post_routes) > 0
        assert post_routes[0]["auth_type"] == "JWT", "POST should have JWT auth"

        # Find DELETE route (with @Secured)
        delete_routes = [r for r in routes if r["method"] == "DELETE"]
        assert len(delete_routes) > 0
        assert delete_routes[0]["auth_type"] == "RBAC", "DELETE should have RBAC auth"

    def test_dto_extraction(self):
        """Test extraction of DTO classes."""
        metadata = extract_spring_metadata(SPRING_CONTROLLER_SAMPLE, "UserController.java")
        dtos = metadata.get("dtos", [])

        assert len(dtos) >= 1, "Should extract at least 1 DTO"

        # Check User entity
        user_entity = [d for d in dtos if d["name"] == "User"]
        assert len(user_entity) > 0, "Should extract User entity"
        assert user_entity[0].get("is_entity"), "User should be marked as entity"

    def test_deterministic_extraction(self):
        """Test that extraction is deterministic (3 consecutive runs produce same result)."""
        results = []
        for _ in range(3):
            metadata = extract_spring_metadata(SPRING_CONTROLLER_SAMPLE, "UserController.java")
            # Create a deterministic key
            key = str(sorted([f"{r['method']} {r['path']}" for r in metadata.get("routes", [])]))
            results.append(key)

        assert results[0] == results[1] == results[2], "Three runs should produce identical extraction"

    def test_empty_input(self):
        """Test handling of empty input."""
        metadata = extract_spring_metadata("", "Empty.java")
        assert metadata.get("routes", []) == []
        assert metadata.get("dtos", []) == []

    def test_parameter_extraction(self):
        """Test extraction of path and query parameters."""
        metadata = extract_spring_metadata(SPRING_CONTROLLER_SAMPLE, "UserController.java")
        routes = metadata.get("routes", [])

        # Find routes with parameters
        param_routes = [r for r in routes if r.get("path_params") or r.get("query_params")]
        assert len(param_routes) > 0, "Should extract parameter information"


class TestSpringDiffDetection:
    """Test Spring schema and route diff detection."""

    def test_route_added_detection(self):
        """Test detection of added routes."""
        baseline = {
            "routes": [
                {"method": "GET", "path": "/api/users", "class": "UserController", "line": 10, "auth_type": "Public"}
            ]
        }
        current = {
            "routes": [
                {"method": "GET", "path": "/api/users", "class": "UserController", "line": 10, "auth_type": "Public"},
                {"method": "POST", "path": "/api/users", "class": "UserController", "line": 20, "auth_type": "JWT"}
            ]
        }

        diffs = extract_spring_route_diffs(baseline, current)

        added = [d for d in diffs if d["change"] == "ROUTE_ADDED"]
        assert len(added) == 1, "Should detect one added route"
        assert "POST" in added[0]["description"]

    def test_route_removed_detection(self):
        """Test detection of removed routes."""
        baseline = {
            "routes": [
                {"method": "GET", "path": "/api/users", "class": "UserController", "line": 10, "auth_type": "Public"},
                {"method": "DELETE", "path": "/api/users/{id}", "class": "UserController", "line": 30, "auth_type": "RBAC"}
            ]
        }
        current = {
            "routes": [
                {"method": "GET", "path": "/api/users", "class": "UserController", "line": 10, "auth_type": "Public"}
            ]
        }

        diffs = extract_spring_route_diffs(baseline, current)

        removed = [d for d in diffs if d["change"] == "ROUTE_REMOVED"]
        assert len(removed) == 1, "Should detect one removed route"
        assert removed[0]["severity"] == "MAJOR"

    def test_auth_change_detection(self):
        """Test detection of auth type changes."""
        baseline = {
            "routes": [
                {"method": "GET", "path": "/api/users", "class": "UserController", "line": 10, "auth_type": "Public"}
            ]
        }
        current = {
            "routes": [
                {"method": "GET", "path": "/api/users", "class": "UserController", "line": 10, "auth_type": "JWT"}
            ]
        }

        diffs = extract_spring_route_diffs(baseline, current)

        auth_changes = [d for d in diffs if d["change"] == "AUTH_TYPE_CHANGED"]
        assert len(auth_changes) == 1, "Should detect one auth change"
        assert auth_changes[0]["severity"] == "MAJOR", "Public to JWT should be MAJOR"

    def test_dto_field_removal_detection(self):
        """Test detection of removed DTO fields."""
        baseline = {
            "dtos": [
                {
                    "name": "UserDTO",
                    "fields": [
                        {"name": "id", "type": "Long", "required": True},
                        {"name": "email", "type": "String", "required": True},
                        {"name": "phone", "type": "String", "required": False}
                    ]
                }
            ]
        }
        current = {
            "dtos": [
                {
                    "name": "UserDTO",
                    "fields": [
                        {"name": "id", "type": "Long", "required": True},
                        {"name": "email", "type": "String", "required": True}
                    ]
                }
            ]
        }

        diffs = extract_spring_schema_diffs(baseline, current)

        field_removals = [d for d in diffs if d.get("change") == "FIELD_REMOVED"]
        assert len(field_removals) == 1, "Should detect removed field"
        assert "phone" in field_removals[0].get("description", "")

    def test_required_field_addition_detection(self):
        """Test detection of added required fields."""
        baseline = {
            "dtos": [
                {
                    "name": "UserDTO",
                    "fields": [
                        {"name": "id", "type": "Long", "required": True}
                    ]
                }
            ]
        }
        current = {
            "dtos": [
                {
                    "name": "UserDTO",
                    "fields": [
                        {"name": "id", "type": "Long", "required": True},
                        {"name": "email", "type": "String", "required": True}
                    ]
                }
            ]
        }

        diffs = extract_spring_schema_diffs(baseline, current)

        field_additions = [d for d in diffs if d.get("change") == "REQUIRED_FIELD_ADDED"]
        assert len(field_additions) == 1, "Should detect added required field"
        assert "email" in field_additions[0].get("description", "")

    def test_field_type_change_detection(self):
        """Test detection of DTO field type changes."""
        baseline = {
            "dtos": [
                {
                    "name": "UserDTO",
                    "fields": [
                        {"name": "age", "type": "Integer", "required": True}
                    ]
                }
            ]
        }
        current = {
            "dtos": [
                {
                    "name": "UserDTO",
                    "fields": [
                        {"name": "age", "type": "String", "required": True}
                    ]
                }
            ]
        }

        diffs = extract_spring_schema_diffs(baseline, current)

        type_changes = [d for d in diffs if d.get("change") == "FIELD_TYPE_CHANGED"]
        assert len(type_changes) == 1, "Should detect field type change"
        assert type_changes[0]["severity"] == "MAJOR"

    def test_deterministic_diff_generation(self):
        """Test that diffs are deterministic."""
        baseline = {
            "routes": [
                {"method": "GET", "path": "/api/users", "class": "UserController", "line": 10, "auth_type": "Public"}
            ]
        }
        current = {
            "routes": [
                {"method": "GET", "path": "/api/users", "class": "UserController", "line": 10, "auth_type": "JWT"},
                {"method": "POST", "path": "/api/users", "class": "UserController", "line": 20, "auth_type": "JWT"}
            ]
        }

        results = []
        for _ in range(3):
            diffs = extract_spring_route_diffs(baseline, current)
            # Create deterministic key
            key = str(sorted([f"{d['change']}:{d['endpoint']}" for d in diffs]))
            results.append(key)

        assert results[0] == results[1] == results[2], "Diffs should be deterministic"
