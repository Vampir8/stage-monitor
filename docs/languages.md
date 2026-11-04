# Languages and plugin translations

English is the source language and the default for a browser without a saved preference.
Polish is supplied as a translation. New code, plugin labels and default messages must be written in English.

Choose **Language** on the sign-in screen or in **View → Language**. Changing it reloads the page and remembers the choice in this browser. Save any open form first. Existing sessions remain signed in. The choice applies across themes and does not change device configuration or other operators' browsers.

## Files

```text
locales/
  en/
    manifest.json
    core.json
  pl/
    manifest.json
    core.json
backend/src/stage_monitor/plugins/
  generic_osc/
    manifest.yaml              # English display labels; stable protocol IDs
    locales/
      en.json
      pl.json
  obsbot_tail_air/
    locales/
      en.json
      pl.json
```

The core dictionary covers common panels, controls, validation and UI messages.
Every plugin has its own dictionaries, distributed with its plugin directory. There is no need to edit a central plugin dictionary or change the plugin API version. Existing plugins without dictionaries still work and show their English source labels.

## Add a language

1. Copy `locales/en` to a new folder, for example `locales/de`.
2. Edit `manifest.json`:

   ```json
   {
     "id": "de",
     "name": "Deutsch",
     "locale": "de-DE",
     "direction": "ltr",
     "api_version": 1
   }
   ```

3. Edit **values only** in `core.json`, leaving English message keys unchanged:

   ```json
   {
     "api_version": 1,
     "messages": {
       "Save": "Speichern",
       "Delete device {0}": "Gerät {0} löschen"
     }
   }
   ```

4. For each plugin, copy its `locales/en.json` to `locales/de.json` and translate values.
5. Refresh the page. The backend discovers valid language packs and plugin dictionaries without a restart.

Files must be UTF-8 JSON. IDs can use language and region codes, for example `de`, `pt-BR` or `zh-Hant`. The language name is its native name. Use a locale supported by the browser's `Intl` implementation. Date and number formatting use that locale; event time zones remain unchanged.

Missing translations fall back to the plugin's English dictionary, then common English messages or the English source text. Invalid files are skipped independently. A dictionary is limited to 512 KiB and 5,000 messages. Keep all numbered placeholders, such as `{0}` and `{1}`, in translated values; their order may change. Values are rendered as text, never HTML.

## Add or edit application text

Use an English source message:

```javascript
button.textContent = stageText("Save");
title.textContent = stageText("Delete device {0}", [device.name]);
```

Each UI file uses the shared `StageI18n.t` function through `stageText`; the standalone fallback also shows English. Modules initialize on `stage:ready`, after dictionaries are available. Add new UI scripts to `bootstrap.js` and the backend asset allowlist.

Plugin manifests retain English `title`, `description`, `label` and `x-credential-label` fields. The frontend localizes a copy of their display metadata. Command names, capability IDs, metric keys, enum values, defaults, OSC addresses, arguments and saved configurations are never translated.

From the project root, synchronize new source messages while preserving existing translations:

```powershell
.\backend\.venv\Scripts\python.exe tools\sync_locales.py
.\backend\.venv\Scripts\python.exe tools\sync_locales.py --check
```

New entries initially contain English text; edit their translated values afterwards. The tool scans built-in plugin manifests; external plugin authors can maintain the same JSON format in their own plugin packages. Do not rename English message keys in one dictionary alone: they identify source messages.

## Content and API boundaries

Device names, notes, tags, group names, show names, preset names, chat messages and custom OSC control labels are operator content and keep their original spelling. Existing saved default names and historical messages are not rewritten when a language changes. Browser status codes and common protocol abbreviations such as ONLINE, RF, AF, NDI and RTSP retain their technical meaning.

REST/WebSocket identifiers and backend logs remain language independent. New backend messages are English. The frontend translates known presentation messages, including video errors; raw vendor diagnostics remain as reported by the device. A language pack never changes authorization, command execution or network traffic.
