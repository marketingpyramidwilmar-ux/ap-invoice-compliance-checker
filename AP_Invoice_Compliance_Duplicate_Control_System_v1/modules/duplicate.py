from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

try:
    from rapidfuzz.fuzz import ratio
except ImportError:  # Developer fallback; production requirements install RapidFuzz.
    from difflib import SequenceMatcher

    def ratio(left: str, right: str) -> float:
        return SequenceMatcher(None, left, right).ratio() * 100

from modules.excel import SAPDataIndex
from modules.utils import normalize_key, normalize_name, parse_date, parse_decimal


@dataclass
class DuplicateResult:
    score: int = 0
    band: str = "PROBABLY NEW"
    reasons: list[str] = field(default_factory=list)
    matched_row: dict[str, Any] | None = None


def _amount_similarity(left: Decimal | None, right: Decimal | None) -> float:
    if left is None or right is None:
        return 0.0
    difference = abs(left - right)
    if difference <= Decimal("0.01"):
        return 100.0
    base = max(abs(left), abs(right), Decimal("1"))
    return max(0.0, 100.0 - float(difference / base * 100))


def detect_duplicate(fields: dict[str, Any], sap: SAPDataIndex | None) -> DuplicateResult:
    if sap is None or sap.frame.empty:
        return DuplicateResult(reasons=["No mapped SAP export is loaded."])

    supplier = normalize_name(fields.get("supplier_name"))
    invoice_no = normalize_key(fields.get("invoice_number"))
    po_no = normalize_key(fields.get("po_number"))
    amount = parse_decimal(fields.get("total_including_vat"))
    invoice_date = parse_date(fields.get("invoice_date"))
    amount_key = f"{amount:.2f}" if amount is not None else ""

    candidates: set[int] = set()
    if supplier and invoice_no:
        candidates.update(sap.by_supplier_invoice.get((supplier, invoice_no), []))
    if invoice_no:
        candidates.update(sap.by_invoice.get(invoice_no, []))
    if supplier and amount_key:
        candidates.update(sap.by_supplier_amount.get((supplier, amount_key), []))
    if po_no:
        candidates.update(sap.by_po.get(po_no, []))
    if supplier:
        candidates.update(sap.by_supplier.get(supplier, []))

    # Fuzzy fallback stays bounded for normal exports; exact indexes are evaluated first.
    if not candidates:
        candidates.update(range(min(len(sap.frame), 5000)))

    best = DuplicateResult()
    for idx in candidates:
        row = sap.frame.iloc[idx].to_dict()
        row_supplier = normalize_name(row.get("supplier_name"))
        row_invoice = normalize_key(row.get("invoice_number") or row.get("vendor_reference"))
        row_po = normalize_key(row.get("po_number"))
        row_amount = parse_decimal(row.get("total_amount"))
        row_date = parse_date(row.get("invoice_date"))
        supplier_score = ratio(supplier, row_supplier) if supplier and row_supplier else 0
        invoice_score = ratio(invoice_no, row_invoice) if invoice_no and row_invoice else 0
        amount_score = _amount_similarity(amount, row_amount)
        date_match = bool(invoice_date and row_date and invoice_date == row_date)
        po_match = bool(po_no and row_po and po_no == row_po)
        reasons: list[str] = []

        if supplier_score == 100 and invoice_score == 100:
            score = 98 if amount_score >= 99.9 else 94
            reasons.append("Exact supplier and invoice-number match")
            if amount_score >= 99.9:
                reasons.append("Exact amount match")
        elif supplier_score >= 92 and invoice_score >= 88:
            score = round(0.35 * supplier_score + 0.45 * invoice_score + 0.20 * amount_score)
            reasons.append("Supplier and invoice number are highly similar")
        elif supplier_score >= 92 and amount_score >= 99.9 and date_match:
            score = 91
            reasons.append("Same supplier, amount, and invoice date")
        elif supplier_score >= 92 and po_match and amount_score >= 99.9:
            score = 89
            reasons.append("Same supplier, PO number, and amount")
        else:
            score = round(0.35 * supplier_score + 0.35 * invoice_score + 0.20 * amount_score + (10 if po_match else 0))
            if score >= 70:
                reasons.append("Combined fuzzy match requires review")

        if score > best.score:
            band = "HIGH PROBABILITY DUPLICATE" if score >= 90 else "REVIEW" if score >= 70 else "PROBABLY NEW"
            best = DuplicateResult(score=min(score, 100), band=band, reasons=reasons, matched_row=row)
    return best
