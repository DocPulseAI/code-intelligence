from src.intelligence.api_diff_engine import diff_api_surfaces


def _ep(
    method: str,
    path: str,
    auth_type: str = "Public",
    auth_required: bool = False,
    required_fields=None,
    types=None,
    responses=None,
    operation_id: str = "",
):
    required_fields = required_fields or []
    types = types or {}
    responses = responses or [{"status": 200, "description": "OK"}]
    return {
        "method": method,
        "path": path,
        "normalized_key": f"{method.lower()} {path.lower()}",
        "operation_id": operation_id,
        "auth": {"type": auth_type, "required": auth_required, "middleware": []},
        "request": {
            "body_schema": {
                "type": "object",
                "required": list(required_fields),
                "properties": {k: {"type": v} for k, v in types.items()},
            }
        },
        "responses": responses,
        "source": {"file": "routes.js", "handler": "h"},
    }


def test_api_removal_and_auth_tightening():
    baseline = {"api_contract": {"endpoints": [_ep("GET", "/api/users/{id}", "Public", False)]}}
    current = {"api_contract": {"endpoints": [_ep("GET", "/api/users/{id}", "JWT", True)]}}
    findings = diff_api_surfaces(baseline, current)
    types = {f["type"] for f in findings}
    assert "AUTH_TIGHTENING" in types

    removed = diff_api_surfaces(baseline, {"api_contract": {"endpoints": []}})
    assert any(f["type"] == "API_REMOVAL" for f in removed)


def test_required_field_added_is_major_and_optional_field_added_is_minor():
    baseline = {
        "api_contract": {
            "endpoints": [_ep("POST", "/api/users", "JWT", True, required_fields=["email"], types={"email": "string"})]
        }
    }
    current = {
        "api_contract": {
            "endpoints": [
                _ep(
                    "POST",
                    "/api/users",
                    "JWT",
                    True,
                    required_fields=["email", "name"],
                    types={"email": "string", "name": "string", "nickname": "string"},
                )
            ]
        }
    }
    findings = diff_api_surfaces(baseline, current)
    major = [f for f in findings if f["severity"] == "MAJOR"]
    minor = [f for f in findings if f["severity"] == "MINOR"]
    assert any("Required request field added: name" in f["description"] for f in major)
    assert any("Optional request field added: nickname" in f["description"] for f in minor)


def test_method_and_path_change_detected_by_operation_id():
    baseline = {
        "api_contract": {
            "endpoints": [_ep("GET", "/api/users/{id}", operation_id="getUserById")]
        }
    }
    current = {
        "api_contract": {
            "endpoints": [_ep("PATCH", "/api/user/{id}", operation_id="getUserById")]
        }
    }
    findings = diff_api_surfaces(baseline, current)
    descriptions = " | ".join(f["description"] for f in findings)
    assert "HTTP method changed" in descriptions
    assert "Path changed" in descriptions


def test_response_incompatible_change_and_status_removal():
    baseline = {
        "api_contract": {
            "endpoints": [
                _ep(
                    "GET",
                    "/api/users/{id}",
                    responses=[
                        {"status": 200, "description": "OK"},
                        {"status": 404, "description": "Not Found"},
                    ],
                )
            ]
        }
    }
    current = {
        "api_contract": {
            "endpoints": [
                _ep(
                    "GET",
                    "/api/users/{id}",
                    responses=[
                        {"status": 200, "description": "User payload changed"},
                    ],
                )
            ]
        }
    }
    findings = diff_api_surfaces(baseline, current)
    descriptions = " | ".join(f["description"] for f in findings)
    assert "Status code removed: 404" in descriptions
    assert "Response schema incompatible change at status 200" in descriptions
