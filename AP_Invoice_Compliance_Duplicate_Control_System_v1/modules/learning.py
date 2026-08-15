from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Iterable

from modules.utils import clean_text, normalize_name


DEFAULT_LEARNABLE_FIELDS = ("invoice_number", "po_number", "supplier_tin", "supplier_vat", "purchaser_tin")


@dataclass
class LearnedCorrection:
    rule_id: int
    supplier: str
    field_name: str
    original_value: str
    corrected_value: str
    observed_char: str
    corrected_char: str
    position_from_end: int
    occurrences: int
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_confusion_pairs(values: Iterable[str]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for value in values:
        parts = re.split(r"\s*[:↔=><-]+\s*", clean_text(value).upper(), maxsplit=1)
        if len(parts) == 2 and len(parts[0]) == len(parts[1]) == 1:
            pairs.add((parts[0], parts[1]))
            pairs.add((parts[1], parts[0]))
    return pairs


def correction_differences(original: Any, corrected: Any, allowed_pairs: set[tuple[str, str]]) -> list[dict[str, Any]]:
    before = clean_text(original).upper()
    after = clean_text(corrected).upper()
    if not before or len(before) != len(after):
        return []
    differences = []
    for index, (observed, replacement) in enumerate(zip(before, after)):
        if observed != replacement and (observed, replacement) in allowed_pairs:
            differences.append({
                "observed_char": observed,
                "corrected_char": replacement,
                "position_from_end": len(before) - index - 1,
            })
    # Large edits are unlikely to represent a repeatable OCR character confusion.
    return differences if 0 < len(differences) <= 3 else []


def field_profile_valid(field_name: str, value: str) -> bool:
    profiles = {
        "invoice_number": r"^[A-Z0-9_\-/]{1,40}$",
        "po_number": r"^[A-Z0-9_\-/]{1,50}$",
        "supplier_tin": r"^\d{9}$",
        "purchaser_tin": r"^\d{9}$",
        "supplier_vat": r"^[A-Z0-9_\-/]{6,30}$",
    }
    pattern = profiles.get(field_name)
    return bool(pattern and re.fullmatch(pattern, value.upper()))


def apply_rules_to_fields(fields: dict[str, Any], supplier_name: str, rules: list[dict[str, Any]]) -> tuple[dict[str, Any], list[LearnedCorrection]]:
    updated = dict(fields)
    applied: list[LearnedCorrection] = []
    # Highest-evidence rule wins if competing rules exist for the same position.
    ordered = sorted(rules, key=lambda rule: (int(rule["occurrences"]), float(rule["confidence"])), reverse=True)
    used_positions: set[tuple[str, int]] = set()
    for rule in ordered:
        field_name = str(rule["field_name"])
        position = int(rule["position_from_end"])
        position_key = (field_name, position)
        if position_key in used_positions:
            continue
        original = clean_text(updated.get(field_name)).upper()
        index = len(original) - position - 1
        if not original or index < 0 or index >= len(original):
            continue
        observed = str(rule["observed_char"]).upper()
        replacement = str(rule["corrected_char"]).upper()
        if original[index] != observed:
            continue
        candidate = original[:index] + replacement + original[index + 1:]
        if not field_profile_valid(field_name, candidate):
            continue
        updated[field_name] = candidate
        used_positions.add(position_key)
        applied.append(LearnedCorrection(
            rule_id=int(rule["id"]), supplier=supplier_name, field_name=field_name,
            original_value=original, corrected_value=candidate, observed_char=observed,
            corrected_char=replacement, position_from_end=position,
            occurrences=int(rule["occurrences"]), confidence=float(rule["confidence"]),
        ))
    return updated, applied

