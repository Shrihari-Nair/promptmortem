"""The single-port entry used by Docker: API under /api, the built frontend at /."""
import pytest
from fastapi.testclient import TestClient

from app import web
from app.config import settings


@pytest.fixture
def dist(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Prompt Mortem</title><div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log('hi')")
    monkeypatch.setattr(settings, "static_dir", str(tmp_path))
    return tmp_path


def test_serves_the_frontend_and_the_api_on_one_port(dist):
    with TestClient(web.build()) as c:  # `with` runs the lifespan: the database is created
        assert "Prompt Mortem" in c.get("/").text
        assert c.get("/assets/app.js").text == "console.log('hi')"
        assert "root" in c.get("/some/client/route").text  # SPA fallback
        assert c.get("/assets/missing.js").status_code == 404  # missing assets are real 404s
        h = c.get("/api/health")
        assert h.status_code == 200 and h.json()["ok"] is True
        p = c.post("/api/projects", json={"name": "docker"})
        assert p.status_code == 200 and c.get(f"/api/projects/{p.json()['id']}").json()["name"] == "docker"
        assert c.get("/api/docs").status_code == 200  # the API docs work under the mount


def test_api_token_still_protects_the_api_and_health_stays_open(dist, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "docker-token")
    with TestClient(web.build()) as c:
        assert c.get("/api/health").status_code == 200  # the container health check needs this
        assert c.post("/api/projects", json={"name": "x"}).status_code == 401
        assert c.post("/api/projects", json={"name": "x"}, headers={"Authorization": "Bearer docker-token"}).status_code == 200
        assert "root" in c.get("/").text  # the UI shell itself is public; every API call needs the token


def test_without_a_built_frontend_the_api_still_works_and_root_explains(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "static_dir", str(tmp_path))  # no index.html here
    with TestClient(web.build()) as c:
        r = c.get("/")
        assert r.status_code == 503 and "not built" in r.text
        assert c.get("/api/health").status_code == 200
