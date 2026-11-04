"""Keep editable dictionaries in sync with English UI and plugin source labels.

Run with the backend virtual environment. Existing translations are preserved.
"""
import argparse
import ast
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MESSAGE = re.compile(r'''\bstageText\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')''')
DISPLAY_KEYS = {"title", "description", "label", "x-credential-label"}


def plugin_messages(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in DISPLAY_KEYS and isinstance(child, str):
                yield child
            else:
                yield from plugin_messages(child)
    elif isinstance(value, list):
        for child in value:
            yield from plugin_messages(child)


def sync(path, sources, english, check):
    pack = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"api_version": 1, "messages": {}}
    messages = pack["messages"]
    missing = set(sources) - messages.keys()
    incorrect = {key for key in sources if english and messages.get(key) != key}
    if check:
        if missing or incorrect:
            print(f"OUT OF SYNC: {path.relative_to(ROOT)} ({len(missing)} missing, {len(incorrect)} invalid English sources)")
        return bool(missing or incorrect)
    for key in missing | incorrect:
        messages[key] = key
    if missing or incorrect:
        path.parent.mkdir(parents=True, exist_ok=True)
        pack["messages"] = dict(sorted(messages.items()))
        path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {path.relative_to(ROOT)}; translate new English entries as needed.")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report missing messages without writing files")
    args = parser.parse_args()
    sources = set()
    for script in ROOT.glob("*.js"):
        for match in MESSAGE.finditer(script.read_text(encoding="utf-8")):
            literal = match.group(1)
            sources.add(json.loads(literal) if literal.startswith('"') else ast.literal_eval(literal))
    codes = {path.parent.name for path in (ROOT / "locales").glob("*/manifest.json")}
    failed = False
    for code in sorted(codes):
        failed |= sync(ROOT / "locales" / code / "core.json", sources, code == "en", args.check)
    for manifest in (ROOT / "backend/src/stage_monitor/plugins").glob("*/manifest.yaml"):
        labels = set(plugin_messages(yaml.safe_load(manifest.read_text(encoding="utf-8"))))
        for code in sorted(codes):
            failed |= sync(manifest.parent / "locales" / f"{code}.json", labels, code == "en", args.check)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
