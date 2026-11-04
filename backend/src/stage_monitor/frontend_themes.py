"""Discover local CSS-only themes without exposing arbitrary workspace files."""
import hashlib
import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)
THEME_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
ORIGINAL = {"id": "original", "name": 'Stage Monitor — original', "version": "1.0.0",
            "api_version": 1, "stylesheet": None}


class ThemeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=32)
    api_version: int


def discover_themes(root: Path) -> tuple[list[dict], list[dict]]:
    themes, errors = [dict(ORIGINAL)], []
    if not root.is_dir():
        return themes, errors
    for directory in sorted(root.iterdir()):
        if not THEME_ID.fullmatch(directory.name) or directory.name == "original":
            continue
        if (not directory.is_dir() or directory.is_symlink()
                or directory.resolve().parent != root.resolve()):
            continue
        try:
            manifest_path, css = directory / "manifest.json", directory / "theme.css"
            if (manifest_path.is_symlink() or css.is_symlink()
                    or manifest_path.resolve().parent != directory.resolve()
                    or css.resolve().parent != directory.resolve()):
                raise ValueError('Theme files must stay inside their package directory')
            if manifest_path.stat().st_size > 8192 or css.stat().st_size > 262144:
                raise ValueError('Theme file size limit exceeded')
            manifest = ThemeManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            if manifest.id != directory.name or manifest.api_version != 1:
                raise ValueError('Invalid theme ID or unsupported theme API version')
            content = css.read_bytes()
            content.decode("utf-8")
            digest = hashlib.sha256(content).hexdigest()[:12]
            themes.append({**manifest.model_dump(),
                           "stylesheet": f"/themes/{manifest.id}/theme.css?v={digest}"})
        except (OSError, ValueError, ValidationError):
            errors.append({"id": directory.name, "message": 'Could not load theme package'})
            log.warning("Skipping invalid frontend theme: %s", directory.name)
    return themes, errors


def register(app: FastAPI, frontend: Path) -> None:
    root = frontend / "themes"

    @app.get("/api/themes", tags=["themes"])
    async def list_themes():
        themes, errors = discover_themes(root)
        return {"themes": themes, "errors": errors}

    @app.get("/themes/{theme_id}/theme.css", include_in_schema=False)
    async def theme_stylesheet(theme_id: str):
        if not THEME_ID.fullmatch(theme_id):
            raise HTTPException(404, "Theme not found")
        themes, _ = discover_themes(root)
        if not any(item["id"] == theme_id and item["stylesheet"] for item in themes):
            raise HTTPException(404, "Theme not found")
        return FileResponse(root / theme_id / "theme.css", media_type="text/css",
                            headers={"Cache-Control": "no-cache"})
