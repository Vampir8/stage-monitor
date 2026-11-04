import sqlite3

import pytest

from stage_monitor import cli
from stage_monitor.settings import Settings


def launcher_settings(tmp_path, monkeypatch, password=None):
    settings = Settings(data_dir=tmp_path, bootstrap_password=password, mock_mode=False)
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings)
    return settings


def test_bootstrap_probe_does_not_create_database(tmp_path):
    settings = Settings(data_dir=tmp_path, bootstrap_password=None)
    assert cli.needs_bootstrap(settings)
    assert not (tmp_path / "stage_monitor.sqlite3").exists()
    with sqlite3.connect(tmp_path / "stage_monitor.sqlite3") as db:
        db.execute("CREATE TABLE users (id TEXT)")
    assert cli.needs_bootstrap(settings)
    with sqlite3.connect(tmp_path / "stage_monitor.sqlite3") as db:
        db.execute("INSERT INTO users VALUES ('existing')")
    assert not cli.needs_bootstrap(settings)


def test_launcher_prompts_before_start_and_validates_password(tmp_path, monkeypatch, capsys):
    settings = launcher_settings(tmp_path, monkeypatch)
    entries = iter(["short", "valid-test-password", "different-password",
                    "valid-test-password", "valid-test-password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(entries))
    import stage_monitor.main
    import uvicorn
    calls = []
    monkeypatch.setattr(stage_monitor.main, "create_app", lambda config: config)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    assert cli.main(["--port", "8123"]) == 0
    assert settings.bootstrap_password == "valid-test-password"
    assert len(calls) == 1
    assert calls[0][1]["port"] == 8123
    assert calls[0][1]["workers"] == 1
    assert "valid-test-password" not in capsys.readouterr().out


def test_existing_user_skips_password_prompt(tmp_path, monkeypatch):
    launcher_settings(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "stage_monitor.sqlite3") as db:
        db.execute("CREATE TABLE users (id TEXT)")
        db.execute("INSERT INTO users VALUES ('existing')")
    def unexpected_prompt(prompt):
        pytest.fail("Existing database must not request a bootstrap password")
    monkeypatch.setattr(cli.getpass, "getpass", unexpected_prompt)
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(app))
    assert cli.main([]) == 0
    assert len(calls) == 1


def test_short_environment_password_never_starts_server(tmp_path, monkeypatch, capsys):
    launcher_settings(tmp_path, monkeypatch, password="short")
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("Must not start"))
    assert cli.main([]) == 1
    assert "STAGE_ADMIN_PASSWORD" in capsys.readouterr().err
