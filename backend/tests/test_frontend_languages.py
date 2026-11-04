import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stage_monitor.frontend_languages import discover_languages, register


def add_pack(frontend, code="en", name="English", messages=None):
    directory = frontend / "locales" / code
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({
        "id": code, "name": name, "locale": code, "api_version": 1, "direction": "ltr",
    }), encoding="utf-8")
    (directory / "core.json").write_text(json.dumps({
        "api_version": 1, "messages": messages or {"Save": "Save"},
    }), encoding="utf-8")
    return directory


def test_language_install_edit_remove_without_restart(tmp_path):
    add_pack(tmp_path)
    app = FastAPI()
    register(app, tmp_path)
    client = TestClient(app)
    assert [p["id"] for p in client.get("/api/languages").json()["languages"]] == ["en"]
    directory = add_pack(tmp_path, "de", "Deutsch", {"Save": "Speichern"})
    assert len(client.get("/api/languages").json()["languages"]) == 2
    response = client.get("/locales/de/core.json")
    assert response.headers["cache-control"] == "no-cache"
    assert response.json()["messages"]["Save"] == "Speichern"
    (directory / "core.json").write_text('{"api_version":1,"messages":{"Save":"Sichern"}}')
    assert client.get("/locales/de/core.json").json()["messages"]["Save"] == "Sichern"
    (directory / "manifest.json").unlink()
    assert client.get("/locales/de/core.json").status_code == 404
    for path in ["/locales/en/manifest.json", "/locales/en/../../backend/data", "/locales/no/core.json"]:
        assert client.get(path).status_code == 404


def test_bad_pack_isolated_and_placeholders_validated(tmp_path):
    add_pack(tmp_path)
    add_pack(tmp_path, "pl", "Polski", {"Device {0}": "Urządzenie"})
    languages, errors = discover_languages(tmp_path / "locales")
    assert [item["id"] for item in languages] == ["en"]
    assert errors[0]["id"] == "pl"
    (tmp_path / "locales/pl/core.json").write_text("{bad json")
    assert len(discover_languages(tmp_path / "locales")[0]) == 1


def test_plugin_dictionaries_are_separate_and_cannot_expose_files(tmp_path):
    add_pack(tmp_path)
    plugin = tmp_path / "device_plugin"
    (plugin / "locales").mkdir(parents=True)
    (plugin / "locales/en.json").write_text('{"api_version":1,"messages":{"Level":"Level"}}')
    (plugin / "locales/pl.json").write_text('{broken')
    (plugin / "private.json").write_text('{"password":"never public"}')
    app = FastAPI()
    app.state.plugins = SimpleNamespace(entries={"test.plugin": {
        "directory": plugin, "descriptor": object(), "error": None,
    }})
    register(app, tmp_path)
    client = TestClient(app)
    assert client.get("/locales/en/plugins.json").json()["plugins"] == {"test.plugin": {"Level": "Level"}}
    assert client.get("/locales/pl/plugins.json").json()["plugins"] == {}
    assert client.get("/locales/..%5cprivate/plugins.json").status_code == 404


def test_shipped_language_packs_and_all_plugin_dictionaries():
    frontend = Path(__file__).resolve().parents[2]
    languages, errors = discover_languages(frontend / "locales")
    assert errors == []
    assert {item["id"] for item in languages} == {"en", "pl"}
    from stage_monitor.frontend_languages import read_messages
    en = read_messages(frontend / "locales/en/core.json", frontend)
    pl = read_messages(frontend / "locales/pl/core.json", frontend)
    assert en.keys() == pl.keys()
    assert all(key == value for key, value in en.items())
    assert pl["Save"] == "Zapisz"
    import re
    # English source calls must be covered, including future UI additions.
    for script in frontend.glob("*.js"):
        for message in re.findall(r'stageText\(("(?:[^"\\]|\\.)*")', script.read_text(encoding="utf-8")):
            assert json.loads(message) in en, (script.name, message)
    for directory in (frontend / "backend/src/stage_monitor/plugins").iterdir():
        if not (directory / "manifest.yaml").exists():
            continue
        base = read_messages(directory / "locales/en.json", directory)
        translated = read_messages(directory / "locales/pl.json", directory)
        assert base.keys() == translated.keys()
        assert all(key == value for key, value in base.items())
