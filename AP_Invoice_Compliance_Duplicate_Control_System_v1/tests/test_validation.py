from copy import deepcopy
from decimal import Decimal

from modules.extraction import extract_fields
from modules.utils import load_json
from modules.validation import applicable_vat_rate, number_to_words, validate_arithmetic, validate_invoice, validate_supplier_master
from modules.utils import PROJECT_ROOT


def config():
    value = load_json(PROJECT_ROOT / "config" / "default_config.json", {})
    value["company"].update({"tin": "987654321", "address": "No 1 Main Road Colombo", "legal_name": "Pyramid Wilmar Oils & Fats (Pvt) Ltd"})
    return value


def good_fields():
    return {
        "supplier_name": "ABC Oils (Pvt) Ltd", "supplier_address": "10 Port Road Colombo",
        "supplier_tin": "123456789", "supplier_vat": "123456789-7000",
        "purchaser_name": "Pyramid Wilmar Oils & Fats (Pvt) Ltd", "purchaser_address": "No 1 Main Road Colombo",
        "purchaser_tin": "987654321", "invoice_number": "26OCT_BR03_1", "invoice_date": "10/15/2026",
        "date_of_supply": "10/15/2026", "po_number": "PO1001", "description": "Refined oil",
        "quantity": "10", "unit": "CTN", "unit_price": "100.00", "amount_ex_vat": "1000.00",
        "total_value_supply": "1000.00", "vat_rate": "18%", "vat_amount": "180.00",
        "total_including_vat": "1180.00", "amount_words": "One thousand one hundred and eighty rupees",
        "mode_of_payment": "Bank Transfer", "place_of_supply": "Colombo", "currency": "LKR",
    }


def test_applicable_rate_by_effective_date():
    from datetime import date
    rates = [{"effective_from": "2024-01-01", "rate": "18"}, {"effective_from": "2027-01-01", "rate": "20"}]
    assert applicable_vat_rate(date(2026, 10, 1), rates) == Decimal("18")


def test_correct_vat_and_total_pass():
    checks = validate_arithmetic(good_fields(), config())
    assert next(c for c in checks if c.key == "vat_calculation").status == "PASS"
    assert next(c for c in checks if c.key == "total_calculation").status == "PASS"
    assert next(c for c in checks if c.key == "line_arithmetic").status == "PASS"


def test_incorrect_vat_fails():
    fields = good_fields(); fields["vat_amount"] = "170.00"
    check = next(c for c in validate_arithmetic(fields, config()) if c.key == "vat_calculation")
    assert check.status == "FAIL"


def test_wrong_printed_vat_rate_fails():
    fields = good_fields(); fields["vat_rate"] = "15%"
    check = next(c for c in validate_arithmetic(fields, config()) if c.key == "vat_rate")
    assert check.status == "FAIL"


def test_incorrect_total_fails():
    fields = good_fields(); fields["total_including_vat"] = "1190.00"
    check = next(c for c in validate_arithmetic(fields, config()) if c.key == "total_calculation")
    assert check.status == "FAIL"


def test_missing_optional_does_not_fail():
    fields = good_fields(); fields["amount_words"] = ""; fields["mode_of_payment"] = ""; fields["place_of_supply"] = ""
    checks = validate_invoice(fields, "TAX INVOICE", config())
    optional = [c for c in checks if c.requirement == "Optional"]
    assert optional and all(c.status != "FAIL" for c in optional)


def test_tin_mismatch_is_material_failure():
    fields = good_fields(); fields["purchaser_tin"] = "111111111"
    check = next(c for c in validate_invoice(fields, "TAX INVOICE", config()) if c.key == "purchaser_tin_match")
    assert check.status == "FAIL"


def test_missing_invoice_number_fails_without_crash():
    fields = good_fields(); fields["invoice_number"] = ""
    checks = validate_invoice(fields, "TAX INVOICE", config())
    assert next(c for c in checks if c.key == "invoice_number").status == "FAIL"


def test_extraction_reads_labelled_text():
    text = """TAX INVOICE
Supplier Name: ABC Oils (Pvt) Ltd
Supplier TIN: 123456789
Purchaser Name: Pyramid Wilmar Oils & Fats (Pvt) Ltd
Purchaser TIN: 987654321
Invoice Number: 26OCT_BR03_1
Invoice Date: 10/15/2026
VAT Amount: LKR 180.00
Total Including VAT: LKR 1,180.00
"""
    result = extract_fields(text)
    assert result.fields["invoice_number"] == "26OCT_BR03_1"
    assert result.fields["vat_amount"] == "180.00"
    assert result.fields["total_including_vat"] == "1180.00"


def test_amount_words_helper():
    assert number_to_words(1180) == "one thousand one hundred eighty"


def test_supplier_tin_mismatch_against_sap_fails():
    import pandas as pd
    from modules.excel import SAPDataIndex
    sap = SAPDataIndex(pd.DataFrame([{"supplier_name": "ABC Oils (Pvt) Ltd", "supplier_tin": "999999999", "supplier_address": "10 Port Road Colombo", "supplier_vat": "123456789-7000"}]))
    checks = validate_supplier_master(good_fields(), sap)
    assert next(c for c in checks if c.key == "supplier_tin_master").status == "FAIL"
