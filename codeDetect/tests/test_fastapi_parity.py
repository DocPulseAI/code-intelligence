"""
Tests for FastAPI parity engine.
Validates FastAPI route extraction, Pydantic model metadata, and deterministic behavior.
"""

import pytest
from src.intelligence.fastapi_engine import (
    extract_fastapi_metadata,
    extract_fastapi_schema_diffs,
    extract_fastapi_route_diffs,
)


FASTAPI_APP_SAMPLE = """
from fastapi import FastAPI, APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI(title="User API", version="1.0.0")

# Request/Response models
class UserBase(BaseModel):
    email: str
    name: str
    age: Optional[int] = None

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: int

class UserListResponse(BaseModel):
    items: List[UserResponse]
    total: int

# Router with prefix
users_router = APIRouter(prefix="/users", tags=["users"])

@users_router.get("/", response_model=UserListResponse)
async def list_users(skip: int = 0, limit: int = 10):
    \"\"\"List all users.\"\"\"
    return {"items": [], "total": 0}

@users_router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int):
    \"\"\"Get a specific user.\"\"\"
    raise HTTPException(status_code=404, detail="User not found")

@users_router.post("/", response_model=UserResponse)
async def create_user(user: UserCreate):
    \"\"\"Create a new user.\"\"\"
    return {"id": 1, **user.dict()}

@users_router.put("/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, user: UserBase):
    \"\"\"Update a user.\"\"\"
    return {"id": user_id, **user.dict()}

@users_router.delete("/{user_id}")
async def delete_user(user_id: int):
    \"\"\"Delete a user.\"\"\"
    return {"deleted": True}

# Include router with additional prefix
app.include_router(users_router, prefix="/api/v1", tags=["api"])

# Direct route on app
@app.get("/health")
async def health():
    \"\"\"Health check endpoint.\"\"\"
    return {"status": "ok"}
"""

FASTAPI_WITH_AUTH = """
from fastapi import FastAPI, Depends
from fastapi.security import OAuth2PasswordBearer, HTTPBearer
from pydantic import BaseModel

app = FastAPI()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
http_bearer = HTTPBearer()

class Token(BaseModel):
    access_token: str
    token_type: str

class UserProfile(BaseModel):
    id: int
    name: str

async def get_current_user(token: str = Depends(oauth2_scheme)):
    return {"sub": token}

async def get_admin_user(user = Depends(get_current_user)):
    # Check admin role
    return user

@app.get("/api/profile", response_model=UserProfile)
async def get_profile(current_user = Depends(get_current_user)):
    \"\"\"Get current user profile.\"\"\"
    return {"id": 1, "name": "Admin"}

@app.post("/api/admin/users", response_model=UserProfile)
async def create_admin_user(user: UserProfile, admin = Depends(get_admin_user)):
    \"\"\"Create user (admin only).\"\"\"
    return user
"""


class TestFastAPIMetadataExtraction:
    """Test FastAPI metadata extraction."""

    def test_extract_app_routes(self):
        """Test extraction of FastAPI app routes."""
        metadata = extract_fastapi_metadata(FASTAPI_APP_SAMPLE, "main.py")
        routes = metadata.get("routes", [])

        # Should have routes from both direct and router-included endpoints
        assert len(routes) >= 5, f"Should extract at least 5 routes, got {len(routes)}"

        # Verify /health route
        health = [r for r in routes if "/health" in r.get("path", "")]
        assert len(health) > 0, "Should have /health route"

        # Verify included routes have prefixes applied
        v1_routes = [r for r in routes if "/api/v1" in r.get("path", "")]
        assert len(v1_routes) > 0, "Should have /api/v1 prefixed routes"

    def test_router_prefix_resolution(self):
        """Test resolution of router prefix chains."""
        metadata = extract_fastapi_metadata(FASTAPI_APP_SAMPLE, "main.py")
        resolved_prefixes = metadata.get("resolved_prefixes", {})

        # Check that users_router has correct prefix
        users_router_prefixes = resolved_prefixes.get("users_router", [])
        assert len(users_router_prefixes) > 0, "Should resolve users_router prefixes"

    def test_pydantic_model_extraction(self):
        """Test extraction of Pydantic models."""
        metadata = extract_fastapi_metadata(FASTAPI_APP_SAMPLE, "main.py")
        models = metadata.get("models", [])

        assert len(models) >= 3, f"Should extract at least 3 models, got {len(models)}"

        # Check UserResponse model
        user_response = [m for m in models if m["name"] == "UserResponse"]
        assert len(user_response) > 0, "Should extract UserResponse model"

        # Check fields
        if user_response:
            fields = user_response[0].get("fields", [])
            field_names = [f["name"] for f in fields]
            assert "id" in field_names, "UserResponse should have id field"
            assert "email" in field_names, "UserResponse should have email field"

    def test_auth_type_extraction(self):
        """Test extraction of authentication types from Depends."""
        metadata = extract_fastapi_metadata(FASTAPI_WITH_AUTH, "main.py")
        routes = metadata.get("routes", [])

        # Should detect OAuth2 auth from Depends(oauth2_scheme)
        oauth_routes = [r for r in routes if r.get("auth_type") == "JWT"]
        assert len(oauth_routes) > 0, "Should detect OAuth2/JWT routes"

    def test_deterministic_extraction(self):
        """Test that extraction is deterministic (3 consecutive runs)."""
        results = []
        for _ in range(3):
            metadata = extract_fastapi_metadata(FASTAPI_APP_SAMPLE, "main.py")
            key = str(sorted([f"{r['method']} {r['path']}" for r in metadata.get("routes", [])]))
            results.append(key)

        assert results[0] == results[1] == results[2], "Three runs should produce identical extraction"

    def test_empty_input(self):
        """Test handling of empty input."""
        metadata = extract_fastapi_metadata("", "empty.py")
        assert metadata.get("routes", []) == []
        assert metadata.get("models", []) == []

    def test_nested_router_prefixes(self):
        """Test resolution of nested router prefixes."""
        nested_app = """
from fastapi import FastAPI, APIRouter

app = FastAPI()

v1_router = APIRouter(prefix="/v1")
users_router = APIRouter(prefix="/users")

@users_router.get("/{id}")
async def get_user(id: int):
    return {"id": id}

v1_router.include_router(users_router)
app.include_router(v1_router, prefix="/api")

@app.get("/health")
async def health():
    return {"ok": True}
        """

        metadata = extract_fastapi_metadata(nested_app, "main.py")
        resolved_prefixes = metadata.get("resolved_prefixes", {})

        # Should have resolved prefixes for nested routers
        assert "users_router" in resolved_prefixes, "Should resolve nested router prefixes"


class TestFastAPIDiffDetection:
    """Test FastAPI schema and route diff detection."""

    def test_route_added_detection(self):
        """Test detection of added FastAPI routes."""
        baseline = {
            "routes": [
                {"method": "GET", "path": "/api/users", "router": "app", "line": 10, "auth_type": "Public"}
            ]
        }
        current = {
            "routes": [
                {"method": "GET", "path": "/api/users", "router": "app", "line": 10, "auth_type": "Public"},
                {"method": "POST", "path": "/api/users", "router": "app", "line": 20, "auth_type": "JWT"}
            ]
        }

        diffs = extract_fastapi_route_diffs(baseline, current)

        added = [d for d in diffs if d["change"] == "ROUTE_ADDED"]
        assert len(added) == 1, "Should detect one added route"
        assert "POST" in added[0]["description"]

    def test_route_removed_detection(self):
        """Test detection of removed FastAPI routes."""
        baseline = {
            "routes": [
                {"method": "GET", "path": "/api/users", "router": "app", "line": 10, "auth_type": "Public"},
                {"method": "DELETE", "path": "/api/users/{id}", "router": "app", "line": 30, "auth_type": "JWT"}
            ]
        }
        current = {
            "routes": [
                {"method": "GET", "path": "/api/users", "router": "app", "line": 10, "auth_type": "Public"}
            ]
        }

        diffs = extract_fastapi_route_diffs(baseline, current)

        removed = [d for d in diffs if d["change"] == "ROUTE_REMOVED"]
        assert len(removed) == 1, "Should detect one removed route"
        assert removed[0]["severity"] == "MAJOR"

    def test_auth_change_detection(self):
        """Test detection of auth type changes."""
        baseline = {
            "routes": [
                {"method": "GET", "path": "/api/profile", "router": "app", "line": 10, "auth_type": "Public"}
            ]
        }
        current = {
            "routes": [
                {"method": "GET", "path": "/api/profile", "router": "app", "line": 10, "auth_type": "JWT"}
            ]
        }

        diffs = extract_fastapi_route_diffs(baseline, current)

        auth_changes = [d for d in diffs if d["change"] == "AUTH_TYPE_CHANGED"]
        assert len(auth_changes) == 1, "Should detect one auth change"
        assert auth_changes[0]["severity"] == "MAJOR"

    def test_pydantic_field_removal(self):
        """Test detection of removed Pydantic fields."""
        baseline = {
            "models": [
                {
                    "name": "UserResponse",
                    "fields": [
                        {"name": "id", "type": "int", "required": True},
                        {"name": "email", "type": "str", "required": True},
                        {"name": "phone", "type": "Optional[str]", "required": False}
                    ]
                }
            ]
        }
        current = {
            "models": [
                {
                    "name": "UserResponse",
                    "fields": [
                        {"name": "id", "type": "int", "required": True},
                        {"name": "email", "type": "str", "required": True}
                    ]
                }
            ]
        }

        diffs = extract_fastapi_schema_diffs(baseline, current)

        field_removals = [d for d in diffs if d.get("change") == "FIELD_REMOVED"]
        assert len(field_removals) == 1, "Should detect removed field"
        assert "phone" in field_removals[0].get("description", "")

    def test_required_field_addition(self):
        """Test detection of added required Pydantic fields."""
        baseline = {
            "models": [
                {
                    "name": "UserCreate",
                    "fields": [
                        {"name": "email", "type": "str", "required": True}
                    ]
                }
            ]
        }
        current = {
            "models": [
                {
                    "name": "UserCreate",
                    "fields": [
                        {"name": "email", "type": "str", "required": True},
                        {"name": "password", "type": "str", "required": True}
                    ]
                }
            ]
        }

        diffs = extract_fastapi_schema_diffs(baseline, current)

        field_additions = [d for d in diffs if d.get("change") == "REQUIRED_FIELD_ADDED"]
        assert len(field_additions) == 1, "Should detect added required field"
        assert "password" in field_additions[0].get("description", "")

    def test_field_type_change(self):
        """Test detection of Pydantic field type changes."""
        baseline = {
            "models": [
                {
                    "name": "User",
                    "fields": [
                        {"name": "age", "type": "int", "required": True}
                    ]
                }
            ]
        }
        current = {
            "models": [
                {
                    "name": "User",
                    "fields": [
                        {"name": "age", "type": "str", "required": True}
                    ]
                }
            ]
        }

        diffs = extract_fastapi_schema_diffs(baseline, current)

        type_changes = [d for d in diffs if d.get("change") == "FIELD_TYPE_CHANGED"]
        assert len(type_changes) == 1, "Should detect field type change"
        assert type_changes[0]["severity"] == "MAJOR"

    def test_deterministic_diff_generation(self):
        """Test that diffs are deterministic."""
        baseline = {
            "routes": [
                {"method": "GET", "path": "/api/users", "router": "app", "line": 10, "auth_type": "Public"}
            ]
        }
        current = {
            "routes": [
                {"method": "GET", "path": "/api/users", "router": "app", "line": 10, "auth_type": "JWT"},
                {"method": "POST", "path": "/api/users", "router": "app", "line": 20, "auth_type": "JWT"}
            ]
        }

        results = []
        for _ in range(3):
            diffs = extract_fastapi_route_diffs(baseline, current)
            key = str(sorted([f"{d['change']}:{d['endpoint']}" for d in diffs]))
            results.append(key)

        assert results[0] == results[1] == results[2], "Diffs should be deterministic"
