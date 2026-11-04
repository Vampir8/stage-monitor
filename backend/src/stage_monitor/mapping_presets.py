"""Reusable plugin mapping configurations, excluding endpoints and credentials."""
import json
import sqlite3

from fastapi import Depends, HTTPException, Query
from jsonschema import Draft202012Validator, ValidationError
from pydantic import Field

from .contracts import JsonValue, Model, new_id, now


class MappingPresetInput(Model):
    schema_version: int = Field(default=1, ge=1, le=1)
    plugin_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    mapping: dict[str, JsonValue]


def validate_mapping(plugins, preset):
    if not preset.name.strip() or len(preset.model_dump_json().encode()) > 256*1024:
        raise ValueError('Invalid mapping preset size/name')
    schema = plugins.get(preset.plugin_id)['descriptor'].configuration_schema
    fields = schema.get('x-mapping-fields', [])
    if not fields or set(preset.mapping) != set(fields):
        raise ValueError('Unsupported mapping fields')
    Draft202012Validator(schema).validate(preset.mapping)


def register(app, admin):
    @app.get('/api/mapping-presets')
    async def list_presets(plugin_id: str = Query(min_length=1, max_length=120), user=Depends(admin)):
        return [json.loads(row['data']) for row in await app.state.storage.rows(
            'SELECT data FROM mapping_presets WHERE plugin_id=? ORDER BY name', (plugin_id,))]

    @app.post('/api/mapping-presets', status_code=201)
    async def save_preset(data: MappingPresetInput, user=Depends(admin)):
        try:
            validate_mapping(app.state.plugins, data)
        except (ValueError, ValidationError):
            raise HTTPException(422, 'Invalid mapping preset') from None
        storage = app.state.storage
        result = {**data.model_dump(), 'id': new_id(), 'name': data.name.strip(), 'created_at': now().isoformat()}
        async with storage.lock:
            count = (await (await storage.db.execute('SELECT COUNT(*) FROM mapping_presets')).fetchone())[0]
            if count >= 200:
                raise HTTPException(409, 'Mapping preset limit reached')
            try:
                await storage.db.execute('INSERT INTO mapping_presets VALUES (?,?,?,?)',
                    (result['id'], result['plugin_id'], result['name'], json.dumps(result, allow_nan=False)))
                await storage._audit(user['id'], 'mapping_preset.create', result['id'], {'plugin_id': data.plugin_id, 'name': result['name']})
                await storage.db.commit()
            except sqlite3.IntegrityError:
                await storage.db.rollback()
                raise HTTPException(409, 'Mapping preset name already exists') from None
            except BaseException:
                await storage.db.rollback()
                raise
        return result

    @app.delete('/api/mapping-presets/{preset_id}', status_code=204)
    async def delete_preset(preset_id: str, user=Depends(admin)):
        storage = app.state.storage
        async with storage.lock:
            cursor = await storage.db.execute('DELETE FROM mapping_presets WHERE id=?', (preset_id,))
            if not cursor.rowcount:
                await storage.db.rollback()
                raise HTTPException(404, 'Mapping preset not found')
            try:
                await storage._audit(user['id'], 'mapping_preset.delete', preset_id, {})
                await storage.db.commit()
            except BaseException:
                await storage.db.rollback()
                raise
