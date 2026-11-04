"""Editable language packs and optional per-plugin dictionaries.

Only validated string dictionaries are public; no plugin files are served directly.
"""
import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException

log = logging.getLogger(__name__)
LANGUAGE_ID = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}\Z")
PLACEHOLDER = re.compile(r"\{\d+\}")


def read_json(path: Path, root: Path) -> dict:
    if (path.is_symlink() or path.parent.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or path.stat().st_size > 524288):
        raise ValueError("Invalid language file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def read_messages(path: Path, root: Path) -> dict[str, str]:
    value = read_json(path, root)
    messages = value.get("messages")
    if (value.get("api_version") != 1 or not isinstance(messages, dict)
            or len(messages) > 5000
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or len(k) > 8192 or len(v) > 8192
                   or set(PLACEHOLDER.findall(k)) != set(PLACEHOLDER.findall(v))
                   for k, v in messages.items())):
        raise ValueError("Invalid translation dictionary")
    return messages


def discover_languages(root: Path) -> tuple[list[dict], list[dict]]:
    languages, errors = [], []
    if not root.is_dir():
        return languages, errors
    for directory in sorted(root.iterdir()):
        if not LANGUAGE_ID.fullmatch(directory.name):
            continue
        try:
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError("Invalid language directory")
            manifest = read_json(directory / "manifest.json", root)
            if (manifest.get("id") != directory.name or manifest.get("api_version") != 1
                    or not isinstance(manifest.get("name"), str)
                    or not 1 <= len(manifest["name"]) <= 80
                    or not isinstance(manifest.get("locale"), str)
                    or not LANGUAGE_ID.fullmatch(manifest["locale"])
                    or manifest.get("direction", "ltr") not in {"ltr", "rtl"}):
                raise ValueError("Invalid language manifest")
            read_messages(directory / "core.json", root)
            languages.append({k: manifest.get(k, "ltr")
                              for k in ("id", "name", "locale", "direction")})
        except (OSError, ValueError, TypeError):
            errors.append({"id": directory.name, "message": "Invalid language pack"})
            log.warning("Skipping invalid language pack: %s", directory.name)
    return languages, errors


def register(app: FastAPI, frontend: Path) -> None:
    root = frontend / "locales"

    @app.get("/api/languages", tags=["languages"])
    async def list_languages():
        languages, errors = discover_languages(root)
        return {"languages": languages, "errors": errors, "fallback": "en"}

    @app.get("/locales/{language}/core.json", include_in_schema=False)
    async def core_dictionary(language: str):
        languages, _ = discover_languages(root)
        if language not in {item["id"] for item in languages}:
            raise HTTPException(404, "Language not found")
        try:
            messages = read_messages(root / language / "core.json", root)
        except (OSError, ValueError, TypeError):
            raise HTTPException(404, "Language not available") from None
        from fastapi.responses import JSONResponse
        return JSONResponse({"api_version": 1, "messages": messages},
                            headers={"Cache-Control": "no-cache"})

    @app.get("/locales/{language}/plugins.json", include_in_schema=False)
    async def plugin_dictionaries(language: str):
        if not LANGUAGE_ID.fullmatch(language):
            raise HTTPException(404, "Language not found")
        dictionaries = {}
        plugins = getattr(app.state, "plugins", None)
        for plugin_id, entry in (plugins.entries.items() if plugins else []):
            if entry.get("error") or not entry.get("descriptor"):
                continue
            directory = entry["directory"]
            path = directory / "locales" / f"{language}.json"
            if not path.exists():
                continue
            try:
                dictionaries[plugin_id] = read_messages(path, directory)
            except (OSError, ValueError, TypeError):
                log.warning("Skipping invalid plugin translations: %s / %s", plugin_id, language)
        from fastapi.responses import JSONResponse
        return JSONResponse({"api_version": 1, "plugins": dictionaries},
                            headers={"Cache-Control": "no-cache"})
