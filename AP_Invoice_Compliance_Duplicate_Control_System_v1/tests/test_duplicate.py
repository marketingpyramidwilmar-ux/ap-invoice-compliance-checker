import pandas as pd

from modules.duplicate import detect_duplicate
from modules.excel import SAPDataIndex


def sap_index():
    return SAPDataIndex(pd.DataFrame([{
        "supplier_code": "V001", "supplier_name": "ABC Oils (Pvt) Ltd", "supplier_tin": "123456789",
        "invoice_number": "26OCT_BR03_1", "invoice_date": "10/15/2026", "posting_date": "2026-10-16",
        "document_number": "190034", "po_number": "PO1001", "total_amount": 1180.00,
        "vat_amount": 180.00, "currency": "LKR", "vendor_reference": "26OCT_BR03_1",
    }]))


def fields(**changes):
    base = {"supplier_name": "ABC Oils (Pvt) Ltd", "invoice_number": "26OCT_BR03_1", "invoice_date": "10/15/2026", "po_number": "PO1001", "total_including_vat": "1180.00"}
    base.update(changes)
    return base


def test_exact_duplicate_is_high():
    result = detect_duplicate(fields(), sap_index())
    assert result.score >= 90
    assert result.band == "HIGH PROBABILITY DUPLICATE"
    assert result.matched_row["document_number"] == "190034"


def test_similar_invoice_is_review_or_high():
    result = detect_duplicate(fields(invoice_number="26OCT_BR03_I"), sap_index())
    assert result.score >= 70


def test_same_amount_different_supplier_is_not_duplicate():
    result = detect_duplicate(fields(supplier_name="Unrelated Supplier", invoice_number="NEW999", po_number="PO999"), sap_index())
    assert result.score < 70

