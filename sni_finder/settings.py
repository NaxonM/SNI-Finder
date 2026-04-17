from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime

from .shared import SETTINGS_PATH, ScanSettings, ensure_dirs


def _strip_json_comments(content: str) -> str:
    # Support human-readable settings files by removing // and /* */ comments
    # while preserving quoted string content.
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False
    length = len(content)

    while i < length:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < length else ""

        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            i += 2
            while i < length and content[i] not in "\r\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < length and not (content[i] == "*" and content[i + 1] == "/"):
                i += 1
            i += 2
            continue

        out.append(ch)
        i += 1

    # Clean up trailing commas left by manual edits in JSONC-style files.
    return re.sub(r",\s*([}\]])", r"\1", "".join(out))


def save_settings(settings: ScanSettings) -> None:
    SETTINGS_PATH.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def load_settings() -> ScanSettings:
    ensure_dirs()
    if not SETTINGS_PATH.exists():
        settings = ScanSettings()
        save_settings(settings)
        return settings

    content = SETTINGS_PATH.read_text(encoding="utf-8", errors="replace")
    try:
        raw = json.loads(_strip_json_comments(content))
    except json.JSONDecodeError as exc:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bad_path = SETTINGS_PATH.with_name(f"scanner_settings.invalid.{stamp}.json")
        bad_path.write_text(content, encoding="utf-8")
        logging.error(
            "Invalid JSON in %s at line %s col %s: %s. Backed up bad file to %s and using defaults.",
            SETTINGS_PATH,
            exc.lineno,
            exc.colno,
            exc.msg,
            bad_path,
        )
        print(f"Invalid settings JSON detected in {SETTINGS_PATH}.")
        print(f"Backed up bad file to: {bad_path}")
        print("Using default settings for this run.")
        settings = ScanSettings()
        save_settings(settings)
        return settings

    defaults = asdict(ScanSettings())
    allowed = set(defaults.keys())
    filtered = {k: v for k, v in raw.items() if k in allowed}
    defaults.update(filtered)
    return ScanSettings(**defaults)
