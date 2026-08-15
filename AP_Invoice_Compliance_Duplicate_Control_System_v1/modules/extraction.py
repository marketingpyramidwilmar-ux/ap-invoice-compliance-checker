from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from modules.utils import clean_text, parse_decimal


FIELD_LABELS = {
    "supplier_name": "Supplier name",
    "supplier_address": "Supplier address",
    "supplier_tin": "Supplier TIN",
    "supplier_vat": "Supplier VAT registration number",
    "purchaser_name": "Purchaser name",
    "purchaser_address": "Purchaser address",
    "purchaser_tin": "Purchaser TIN",
    "invoice_number": "Invoice number",
    "invoice_date": "Invoice date",
    "date_of_supply": "Date of supply",
    "po_number": "PO number",
    "description": "Description",
    "quantity": "Quantity",
    "unit": "Unit",
    "unit_price": "Unit price",
    "amount_ex_vat": "Amount excluding VAT",
    "total_value_supply": "Total value of supply",
    "vat_rate": "VAT rate",
    "vat_amount": "VAT amount",
    "total_including_vat": "Total including VAT",
    "amount_words": "Total amount in words",
    "mode_of_payment": "Mode of payment",
    "place_of_supply": "Place of supply",
    "currency": "Currency",
}


@dataclass
class ExtractedInvoice:
    fields: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    line_items: list[dict[str, Any]] = field(default_factory=list)


def _capture(text: str, labels: list[str], value_pattern: str = r"[^\n]+") -> str:
    joined = "|".join(labels)
    match = re.search(rf"^[ \t|]*(?:{joined})\s*[:#\-]?\s*({value_pattern})", text, re.I | re.M)
    return clean_text(match.group(1)) if match else ""


def _money_capture(text: str, labels: list[str]) -> str:
    value = _capture(text, labels, r"(?:LKR|Rs\.?|රු\.?|USD)?\s*[\d,]+(?:\.\d{1,2})?")
    return str(parse_decimal(value)) if parse_decimal(value) is not None else ""


def extract_fields(text: str) -> ExtractedInvoice:
    normalized = text.replace("\r", "")
    fields: dict[str, Any] = {key: "" for key in FIELD_LABELS}
    fields.update(
        supplier_name=_capture(normalized, [r"Supplier(?:'s)?\s*Name", r"Vendor\s*Name"]),
        supplier_address=_capture(normalized, [r"Supplier(?:'s)?\s*Address", r"Vendor\s*Address"]),
        supplier_tin=_capture(normalized, [r"Supplier(?:'s)?\s*TIN", r"Vendor\s*TIN", r"TIN"], r"[A-Z0-9\- ]{6,20}"),
        supplier_vat=_capture(normalized, [r"Supplier(?:'s)?\s*VAT(?:\s*(?:No|Number|Registration))?", r"VAT\s*Reg(?:istration)?\s*(?:No)?"], r"[A-Z0-9\- ]{6,25}"),
        purchaser_name=_capture(normalized, [r"Purchaser(?:'s)?\s*Name", r"Bill\s*To", r"Customer\s*Name"]),
        purchaser_address=_capture(normalized, [r"Purchaser(?:'s)?\s*Address", r"Bill\s*To\s*Address", r"Customer\s*Address"]),
        purchaser_tin=_capture(normalized, [r"Purchaser(?:'s)?\s*TIN", r"Customer\s*TIN", r"Buyer\s*TIN"], r"[A-Z0-9\- ]{6,20}"),
        invoice_number=_capture(normalized, [r"Tax\s*Invoice\s*(?:No|Number)", r"Invoice\s*(?:No|Number|#)", r"Inv\s*No"], r"[A-Z0-9_\-/]{1,40}"),
        invoice_date=_capture(normalized, [r"Date\s*of\s*Invoice", r"Invoice\s*Date"], r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}"),
        date_of_supply=_capture(normalized, [r"Date\s*of\s*Supply", r"Supply\s*Date", r"Date\s*of\s*Delivery"], r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}"),
        po_number=_capture(normalized, [r"PO\s*(?:No|Number|#)", r"Purchase\s*Order\s*(?:No|Number)"], r"[A-Z0-9_\-/]+"),
        description=_capture(normalized, [r"Description(?:\s*of\s*(?:Goods|Services|Supply))?"]),
        quantity=_capture(normalized, [r"Quantity", r"Qty"], r"[\d,.]+"),
        unit=_capture(normalized, [r"Unit(?!\s*Price)"], r"[A-Za-z]+"),
        unit_price=_money_capture(normalized, [r"Unit\s*Price"]),
        amount_ex_vat=_money_capture(normalized, [r"Amount\s*Excluding\s*VAT", r"Net\s*Amount", r"Subtotal"]),
        total_value_supply=_money_capture(normalized, [r"Total\s*Value\s*of\s*Supply"]),
        vat_rate=_capture(normalized, [r"VAT\s*Rate", r"VAT\s*@"], r"\d+(?:\.\d+)?\s*%?"),
        vat_amount=_money_capture(normalized, [r"VAT\s*Amount", r"VAT(?!\s*(?:Reg|Registration|Rate|No))"]),
        total_including_vat=_money_capture(normalized, [r"Total\s*(?:Amount|Consideration)?\s*(?:Including|Inclusive\s*of)\s*VAT", r"Grand\s*Total"]),
        amount_words=_capture(normalized, [r"Total\s*Amount\s*in\s*Words", r"Amount\s*in\s*Words"]),
        mode_of_payment=_capture(normalized, [r"Mode\s*of\s*Payment", r"Payment\s*Mode"]),
        place_of_supply=_capture(normalized, [r"Place\s*of\s*Supply"]),
    )
    fields["currency"] = "LKR" if re.search(r"\b(?:LKR|Rs\.?)\b", normalized, re.I) else _capture(normalized, [r"Currency"], r"[A-Z]{3}")
    if not fields["total_value_supply"]:
        fields["total_value_supply"] = fields["amount_ex_vat"]
    return ExtractedInvoice(fields=fields, raw_text=normalized)


def invoice_title_present(text: str) -> bool:
    return bool(re.search(r"\bTAX\s+INVOICE\b", text, re.I))
