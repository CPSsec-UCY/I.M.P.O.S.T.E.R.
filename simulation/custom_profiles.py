"""Local persistence for user-created, open training plant specifications."""

from __future__ import annotations

import json
from pathlib import Path


_STORE = Path(__file__).resolve().parent.parent / "data" / "custom_plants.json"


def load_custom_plants():
    try:
        records = json.loads(_STORE.read_text(encoding="utf-8"))
        return records if isinstance(records, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_custom_plant(spec):
    records = load_custom_plants()
    records.append(spec)
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def delete_custom_plant(profile_id):
    records = [record for record in load_custom_plants()
               if record.get("profile_id") != profile_id]
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")