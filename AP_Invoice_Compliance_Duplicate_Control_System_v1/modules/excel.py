from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from rapidfuzz.fuzz import ratio
except ImportError:
    from difflib import SequenceMatcher

    def ratio(left: str, right: str) -> float:
        return SequenceMatcher(None, left, right).ratio() * 100

from modules.utils import normalize_key, normalize_name, parse_decimal, save_json, load_json


CANONICAL_COLUMNS = [
    "supplier_code", "supplier_name", "supplier_tin", "supplier_address", "supplier_vat",
    "invoice_number", "invoice_date", "posting_date", "document_number", "po_number",
    "total_amount", "vat_amount", "currency", "vendor_reference", "description",
]

DISPLAY_NAMES = {name: name.replace("_", " ").title() for name in CANONICAL_COLUMNS}


def read_excel_bytes(data: bytes, sheet_name: str | int = 0) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(data), sheet_name=sheet_name, engine="openpyxl")


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    synonyms = {
        "supplier_code": ["vendor code", "cardcode", "supplier code"],
        "supplier_name": ["vendor name", "cardname", "supplier name"],
        "supplier_tin": ["supplier tin", "vendor tin", "tin"],
        "supplier_address": ["supplier address", "vendor address", "address"],
        "supplier_vat": ["vat registration", "vat no", "supplier vat"],
        "invoice_number": ["invoice no", "invoice number", "numatcard", "vendor ref"],
        "invoice_date": ["invoice date", "docdate"],
        "posting_date": ["posting date", "taxdate"],
        "document_number": ["document number", "docnum", "sap document"],
        "po_number": ["po number", "purchase order", "po no"],
        "total_amount": ["total amount", "doctotal", "gross total"],
        "vat_amount": ["vat amount", "vatsum", "tax amount"],
        "currency": ["currency", "doccur"],
        "vendor_reference": ["vendor reference", "reference"],
        "description": ["description", "remarks", "comments"],
    }
    normalized = {str(c).strip().lower(): str(c) for c in columns}
    mapping: dict[str, str] = {}
    for canonical, options in synonyms.items():
        for option in options:
            if option in normalized:
                mapping[canonical] = normalized[option]
                break
    return mapping


def apply_mapping(frame: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    result = pd.DataFrame(index=frame.index)
    for canonical in CANONICAL_COLUMNS:
        source = mapping.get(canonical)
        result[canonical] = frame[source] if source and source in frame.columns else ""
    return result.fillna("")


class SAPDataIndex:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame.copy().reset_index(drop=True)
        self.by_supplier_invoice: dict[tuple[str, str], list[int]] = {}
        self.by_invoice: dict[str, list[int]] = {}
        self.by_supplier_amount: dict[tuple[str, str], list[int]] = {}
        self.by_po: dict[str, list[int]] = {}
        self.by_supplier: dict[str, list[int]] = {}
        self.by_supplier_tin: dict[str, list[int]] = {}
        for idx, row in self.frame.iterrows():
            supplier = normalize_name(row.get("supplier_name"))
            invoice = normalize_key(row.get("invoice_number") or row.get("vendor_reference"))
            po = normalize_key(row.get("po_number"))
            amount = parse_decimal(row.get("total_amount"))
            amount_key = f"{amount:.2f}" if amount is not None else ""
            self._add(self.by_supplier_invoice, (supplier, invoice), idx, supplier and invoice)
            self._add(self.by_invoice, invoice, idx, invoice)
            self._add(self.by_supplier_amount, (supplier, amount_key), idx, supplier and amount_key)
            self._add(self.by_po, po, idx, po)
            self._add(self.by_supplier, supplier, idx, supplier)
            supplier_tin = normalize_key(row.get("supplier_tin"))
            self._add(self.by_supplier_tin, supplier_tin, idx, supplier_tin)

    @staticmethod
    def _add(index: dict, key: Any, row: int, condition: Any) -> None:
        if condition:
            index.setdefault(key, []).append(row)

    def rows(self, indices: set[int] | list[int]) -> pd.DataFrame:
        return self.frame.iloc[sorted(indices)] if indices else self.frame.iloc[0:0]


def resolve_supplier_identity(fields: dict[str, Any], sap: SAPDataIndex | None) -> str:
    """Resolve a stable learning scope without overriding invoice validation values."""
    invoice_name = str(fields.get("supplier_name") or "")
    if sap is None or sap.frame.empty:
        return invoice_name
    name_key = normalize_name(invoice_name)
    tin_key = normalize_key(fields.get("supplier_tin"))
    if tin_key and tin_key in sap.by_supplier_tin:
        return str(sap.frame.iloc[sap.by_supplier_tin[tin_key][0]].get("supplier_name") or invoice_name)
    if name_key and name_key in sap.by_supplier:
        return str(sap.frame.iloc[sap.by_supplier[name_key][0]].get("supplier_name") or invoice_name)
    best_name, best_score = invoice_name, 0.0
    for candidate in sap.frame.get("supplier_name", pd.Series(dtype=str)).dropna().astype(str).unique()[:5000]:
        score = ratio(name_key, normalize_name(candidate)) if name_key else 0
        if score > best_score:
            best_name, best_score = candidate, score
    # Never use fuzzy name resolution when the invoice TIN materially conflicts.
    if best_score >= 92:
        matching_rows = sap.frame[sap.frame["supplier_name"].astype(str) == str(best_name)]
        master_tins = {normalize_key(value) for value in matching_rows.get("supplier_tin", pd.Series(dtype=str)) if normalize_key(value)}
        if not tin_key or not master_tins or tin_key in master_tins:
            return str(best_name)
    return invoice_name


def save_mapping(path: Path, mapping: dict[str, str]) -> None:
    save_json(path, mapping)


def load_mapping(path: Path) -> dict[str, str]:
    return load_json(path, {})
