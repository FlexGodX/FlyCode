"""Desktop and classic presentations share the application, not their styles."""

from fastapi.testclient import TestClient

from flycode.web.app import create_app


def test_desktop_is_an_isolated_presentation_of_the_existing_app(tmp_path):
    with TestClient(create_app(tmp_path, tmp_path / "history"), base_url="http://localhost") as client:
        classic = client.get("/")
        desktop = client.get("/desktop")
        assert desktop.status_code == 200
        assert 'class="desktop-ui"' in desktop.text
        assert '/static/desktop.css' in desktop.text
        assert '/static/desktop.js' in desktop.text
        assert '/static/app.js' in desktop.text
        assert classic.status_code == 200
        assert '/static/desktop.css' not in classic.text
        assert '/static/desktop.js' not in classic.text
        assert 'class="desktop-ui"' not in classic.text
        assert 'href="/desktop"' in classic.text
        assert 'id="taskForm"' in classic.text and 'id="taskForm"' in desktop.text
        assert client.get('/static/desktop.css').status_code == 200
        assert client.get('/static/desktop.js').status_code == 200
