import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stage_monitor.frontend_themes import discover_themes, register


def add_theme(root, theme_id="demo", **overrides):
    directory = root / theme_id
    directory.mkdir(parents=True)
    manifest = {"id": theme_id, "name": "Demo", "version": "1.0.0", "api_version": 1}
    manifest.update(overrides)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "theme.css").write_text(
        f'[data-theme="{theme_id}"] body {{ background: #123; }}', encoding="utf-8")
    return directory


def test_install_update_remove_theme_without_restart(tmp_path):
    app = FastAPI()
    register(app, tmp_path)
    client = TestClient(app)
    assert [item["id"] for item in client.get("/api/themes").json()["themes"]] == ["original"]
    directory = add_theme(tmp_path / "themes")
    catalog = client.get("/api/themes").json()
    assert catalog["errors"] == []
    theme = next(item for item in catalog["themes"] if item["id"] == "demo")
    css = client.get(theme["stylesheet"])
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert css.headers["cache-control"] == "no-cache"
    assert "#123" in css.text
    (directory / "theme.css").write_text('[data-theme="demo"] body { color: red; }')
    updated = client.get("/api/themes").json()["themes"][1]
    assert updated["stylesheet"] != theme["stylesheet"]
    (directory / "manifest.json").unlink()
    assert len(client.get("/api/themes").json()["themes"]) == 1
    assert client.get(theme["stylesheet"]).status_code == 404


def test_broken_packages_do_not_hide_valid_theme(tmp_path):
    add_theme(tmp_path, "good")
    add_theme(tmp_path, "future", api_version=2)
    add_theme(tmp_path, "mismatch", id="good")
    broken = add_theme(tmp_path, "broken")
    (broken / "manifest.json").write_text("{invalid json")
    missing = add_theme(tmp_path, "missing")
    (missing / "theme.css").unlink()
    huge = add_theme(tmp_path, "huge")
    (huge / "theme.css").write_bytes(b"x" * 262145)
    themes, errors = discover_themes(tmp_path)
    assert [item["id"] for item in themes] == ["original", "good"]
    assert {error["id"] for error in errors} == {"future", "mismatch", "broken", "missing", "huge"}


def test_only_validated_css_is_public(tmp_path):
    add_theme(tmp_path / "themes")
    app = FastAPI()
    register(app, tmp_path)
    client = TestClient(app)
    for path in ["/themes/demo/manifest.json", "/themes/demo/other.css",
                 "/themes/no-such-theme/theme.css", "/themes/original/theme.css",
                 "/themes/..%5c..%5cbackend/theme.css"]:
        assert client.get(path).status_code == 404
