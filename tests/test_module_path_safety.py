import pytest
from fastapi.testclient import TestClient

from qa_agent.web import create_app


@pytest.fixture
def module_client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post(
            "/api/v1/projects",
            json={"project_id": "demo", "name": "Demo", "created_by": "admin"},
        ).status_code == 201
        for module_id in ("login", "payment"):
            assert client.post(
                "/api/v1/projects/demo/modules",
                json={"module_id": module_id, "name": module_id, "created_by": "admin"},
            ).status_code == 201
        yield client
        for module_id in ("login", "payment"):
            assert client.get(
                f"/api/v1/projects/demo/modules/{module_id}"
            ).status_code == 200
        assert client.get("/api/v1/projects/demo").status_code == 200
    assert (tmp_path / "projects" / "demo" / "project.json").is_file()


@pytest.mark.parametrize("module_id", [".", ".."])
def test_api_rejects_special_module_creation(module_client, module_id):
    response = module_client.post(
        "/api/v1/projects/demo/modules",
        json={"module_id": module_id, "name": "Invalid", "created_by": "admin"},
    )
    assert response.status_code == 400
    assert "invalid module_id" in response.json()["message"]


@pytest.mark.parametrize("module_id", ["%2E", "%2E%2E"])
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_api_rejects_special_module_paths(module_client, module_id, method):
    kwargs = {"json": {"name": "Invalid"}} if method == "PUT" else {}
    response = module_client.request(
        method, f"/api/v1/projects/demo/modules/{module_id}", **kwargs
    )
    assert response.status_code == 400
    assert "invalid module_id" in response.json()["message"]


@pytest.mark.parametrize("module_id", ["%2E", "%2E%2E"])
def test_api_rejects_special_workflow_context(module_client, module_id):
    response = module_client.get(
        f"/api/v1/projects/demo/workflow?module_id={module_id}"
    )
    assert response.status_code == 400
    assert "invalid module_id" in response.json()["message"]


def test_api_keeps_normal_dotted_module_names_working(module_client):
    endpoint = "/api/v1/projects/demo/modules/login.v2"
    assert module_client.post(
        "/api/v1/projects/demo/modules",
        json={"module_id": "login.v2", "name": "Login v2", "created_by": "admin"},
    ).status_code == 201
    assert module_client.get(endpoint).status_code == 200
    assert module_client.put(endpoint, json={"name": "Renamed login"}).status_code == 200
    assert module_client.delete(endpoint).status_code == 200
    assert module_client.get(endpoint).status_code == 404
