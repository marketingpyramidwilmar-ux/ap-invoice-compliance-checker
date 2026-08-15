from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def ensure_directories() -> None:
    for name in ("config", "data", "database", "sample_data"):
        (PROJECT_ROOT / name).mkdir(parents=True, exist_ok=True)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", clean_text(value).upper())


def normalize_name(value: Any) -> str:
    text = clean_text(value).upper()
    substitutions = {
        "PRIVATE LIMITED": "PVT LTD",
        "(PRIVATE) LIMITED": "PVT LTD",
        "LIMITED": "LTD",
        "COMPANY": "CO",
    }
    for old, new in substitutions.items():
        text = text.replace(old, new)
    return re.sub(r"[^A-Z0-9]", "", text)


def parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    try:
        return Decimal(cleaned) if cleaned not in {"", ".", "-"} else None
    except InvalidOperation:
        return None


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, default=str)

