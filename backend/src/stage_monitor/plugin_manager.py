import logging
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from .contracts import Device, PluginDescriptor

log = logging.getLogger(__name__)


class PluginManager:
    def __init__(self, storage, extra_dirs=()):
        self.storage = storage
        self.directories = [Path(__file__).parent / "plugins", *extra_dirs]
        self.entries: dict[str, dict] = {}

    async def discover(self):
        enabled = {r["id"]: bool(r["enabled"]) for r in await self.storage.rows("SELECT * FROM plugin_settings")}
        for directory in self.directories:
            for path in sorted(directory.glob("*/manifest.yaml")):
                key = str(path)
                descriptor = None
                try:
                    descriptor = PluginDescriptor.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
                    key = descriptor.plugin_id
                    if key in self.entries:
                        raise ValueError(f"Duplicate plugin_id: {key}")
                    if descriptor.plugin_api_version.split(".")[0] != "1":
                        raise ValueError("Unsupported plugin API version")
                    if ":" not in descriptor.entrypoint:
                        raise ValueError("Entrypoint must be module:class")
                    Draft202012Validator.check_schema(descriptor.configuration_schema)
                    for command in descriptor.commands:
                        Draft202012Validator.check_schema(command.parameters_schema)
                        if command.snapshot_parameters:
                            if any(key.lower().split(".")[-1] in {"password","secret","token","api_key","private_key","credential_ref"}
                                   for key in command.snapshot_parameters):
                                raise ValueError("Credentials cannot be snapshot parameters")
                            arguments = list(command.snapshot_parameters.values())
                            properties = command.parameters_schema.get("properties", {})
                            if (not command.idempotent or len(arguments) != len(set(arguments))
                                    or not set(arguments) <= set(properties)
                                    or not set(command.parameters_schema.get("required", [])) <= set(arguments)):
                                raise ValueError("Invalid snapshot command binding")
                    names = [c.name for c in descriptor.commands]
                    bindings = [key for c in descriptor.commands for key in c.snapshot_parameters]
                    if len(bindings) != len(set(bindings)):
                        raise ValueError("Ambiguous snapshot parameter binding")
                    if len(names) != len(set(names)):
                        raise ValueError("Duplicate command name")
                    self.entries[key] = {
                        "descriptor": descriptor, "directory": path.parent,
                        "enabled": enabled.get(key, True), "error": None,
                    }
                except Exception as exc:
                    # A bad manifest cannot prevent discovery of the remaining plugins.
                    log.warning("Plugin manifest rejected: %s (%s)", path, exc)
                    if key in self.entries:
                        self.entries[key]["enabled"] = False
                        self.entries[key]["error"] = "Duplicate plugin_id"
                        key = str(path)
                    self.entries[key] = {
                        "descriptor": descriptor, "directory": path.parent,
                        "enabled": False, "error": str(exc),
                    }

    def get(self, plugin_id):
        entry = self.entries.get(plugin_id)
        if not entry or entry["error"]:
            raise ValueError("Plugin is unavailable or incompatible")
        return entry

    def validate_device(self, device: Device):
        entry = self.get(device.plugin_id)
        descriptor = entry["descriptor"]
        def reject_secrets(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.lower() in {"password", "secret", "token", "api_key", "private_key"}:
                        raise ValueError("Secrets must use credential_ref")
                    reject_secrets(child)
            elif isinstance(value, list):
                for child in value:
                    reject_secrets(child)
        reject_secrets(device.custom_config)
        Draft202012Validator(descriptor.configuration_schema).validate(device.custom_config)
        if device.endpoint and device.endpoint.transport not in descriptor.supported_protocols:
            raise ValueError("Unsupported transport")
        return entry

    async def set_enabled(self, plugin_id, enabled):
        entry = self.get(plugin_id)
        await self.storage.write(
            "INSERT INTO plugin_settings VALUES (?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled",
            (plugin_id, int(enabled)),
        )
        entry["enabled"] = enabled

    def list(self):
        return [{
            "plugin_id": key, "enabled": entry["enabled"], "load_error": entry["error"],
            "manifest": entry["descriptor"].model_dump(mode="json") if entry["descriptor"] else None,
        } for key, entry in self.entries.items()]
