from fastapi.testclient import TestClient

from reportback import __version__
from reportback.api.app import app
from reportback.cli import main


def test_healthz() -> None:
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert "Open Government Licence – Toronto" in body["attribution"]


def test_cli_requires_a_command(capsys: object) -> None:
    import pytest

    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
