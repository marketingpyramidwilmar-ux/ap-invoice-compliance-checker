from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

try:
    from rapidfuzz.fuzz import ratio
except ImportError:  # Developer fallback; production requirements install RapidFuzz.
    from difflib import SequenceMatcher

    def ratio(left: str, right: str) -> float:
        return SequenceMatcher(None, left, right).ratio() * 100

from modules.extraction import invoice_title_present
from modules.utils import clean_text, normalize_key, normalize_name, parse_date, parse_decimal


@dataclass
class CheckResult:
    key: str
    label: str
    category: str
    requirement: str
    status: str
    message: str
    actual: str = ""
    expected: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check(key: str, label: str, category: str, requirement: str, status: str, message: str, actual: Any = "", expected: Any = "") -> CheckResult:
    return CheckResult(key, label, category, requirement, status, message, clean_text(actual), clean_text(expected))


def _presence(fields: dict[str, Any], key: str, label: str, category: str, requirement: str) -> CheckResult:
    value = clean_text(fields.get(key))
    if value:
        return _check(key, label, category, requirement, "PASS", "Field detected.", value)
    status = "FAIL" if requirement == "Mandatory" else "REVIEW" if requirement == "Conditional" else "PASS"
    message = "Required field not detected." if requirement == "Mandatory" else "Field not detected; confirm whether applicable." if requirement == "Conditional" else "Optional field not detected."
    return _check(key, label, category, requirement, status, message)


def applicable_vat_rate(invoice_date: date | None, rates: list[dict[str, Any]]) -> Decimal | None:
    valid: list[tuple[date, Decimal]] = []
    for row in rates:
        effective = parse_date(row.get("effective_from"))
        rate = parse_decimal(row.get("rate"))
        if effective and rate is not None:
            valid.append((effective, rate))
    valid.sort(key=lambda item: item[0])
    selected = None
    for effective, rate in valid:
        if invoice_date and effective <= invoice_date:
            selected = rate
    return selected


def validate_invoice(fields: dict[str, Any], raw_text: str, config: dict[str, Any]) -> list[CheckResult]:
    rules = config.get("rules", {})
    company = config.get("company", {})
    checks: list[CheckResult] = []
    checks.append(_check("tax_invoice_title", "TAX INVOICE title", "Identity", "Mandatory", "PASS" if invoice_title_present(raw_text) else "FAIL", "Prominent title detected." if invoice_title_present(raw_text) else "TAX INVOICE title not detected."))

    for key, label, category, requirement in [
        ("supplier_tin", "Supplier TIN", "Supplier", "Mandatory"),
        ("supplier_name", "Supplier legal name", "Supplier", "Mandatory"),
        ("supplier_address", "Supplier address", "Supplier", "Mandatory"),
        ("purchaser_tin", "Purchaser TIN", "Purchaser", "Mandatory"),
        ("purchaser_name", "Purchaser name", "Purchaser", "Mandatory"),
        ("purchaser_address", "Purchaser address", "Purchaser", "Mandatory"),
        ("invoice_number", "Invoice serial number", "Invoice format", "Mandatory"),
        ("invoice_date", "Invoice date", "Invoice format", "Mandatory"),
        ("date_of_supply", "Date of supply", "Invoice format", "Mandatory"),
        ("description", "Description of goods/services", "Supply", "Mandatory"),
        ("quantity", "Quantity/volume", "Supply", "Conditional"),
        ("unit_price", "Unit price", "Supply", "Conditional"),
        ("amount_ex_vat", "Amount excluding VAT", "Amounts", "Mandatory"),
        ("total_value_supply", "Total value of supply", "Amounts", "Mandatory"),
        ("vat_amount", "VAT amount", "Amounts", "Mandatory"),
        ("vat_rate", "VAT rate", "Amounts", "Conditional"),
        ("total_including_vat", "Total including VAT", "Amounts", "Mandatory"),
        ("amount_words", "Total amount in words", "Optional", "Optional"),
        ("mode_of_payment", "Mode of payment", "Optional", "Optional"),
        ("place_of_supply", "Place of supply", "Optional", "Optional"),
    ]:
        checks.append(_presence(fields, key, label, category, requirement))

    tin_pattern = rules.get("tin_regex", r"^\d{9}$")
    for key, label in (("supplier_tin", "Supplier TIN format"), ("purchaser_tin", "Purchaser TIN format")):
        value = normalize_key(fields.get(key))
        if value:
            valid = bool(re.fullmatch(tin_pattern, value))
            checks.append(_check(key + "_format", label, "Identity", "Mandatory", "PASS" if valid else "FAIL", "TIN matches configured format." if valid else "TIN does not match configured format.", value, tin_pattern))

    expected_tin = normalize_key(company.get("tin"))
    actual_tin = normalize_key(fields.get("purchaser_tin"))
    if expected_tin:
        status = "PASS" if actual_tin == expected_tin else "FAIL"
        checks.append(_check("purchaser_tin_match", "Purchaser TIN matches master data", "Purchaser", "Mandatory", status, "Exact TIN match." if status == "PASS" else "Material purchaser TIN mismatch.", actual_tin, expected_tin))
    else:
        checks.append(_check("purchaser_tin_match", "Purchaser TIN matches master data", "Purchaser", "Conditional", "REVIEW", "Approved company TIN has not been configured."))

    expected_names = [company.get("legal_name", ""), *company.get("aliases", [])]
    actual_name = normalize_name(fields.get("purchaser_name"))
    name_score = max((ratio(actual_name, normalize_name(name)) for name in expected_names if name), default=0)
    if any(expected_names):
        checks.append(_check("purchaser_name_match", "Purchaser name matches master data", "Purchaser", "Mandatory", "PASS" if name_score >= int(rules.get("name_match_threshold", 88)) else "FAIL", f"Best name similarity: {name_score}%.", fields.get("purchaser_name"), company.get("legal_name")))
    else:
        checks.append(_check("purchaser_name_match", "Purchaser name matches master data", "Purchaser", "Conditional", "REVIEW", "Approved legal name has not been configured."))

    expected_address = normalize_name(company.get("address"))
    actual_address = normalize_name(fields.get("purchaser_address"))
    if expected_address:
        address_score = ratio(actual_address, expected_address) if actual_address else 0
        status = "PASS" if address_score >= int(rules.get("address_match_threshold", 70)) else "REVIEW"
        checks.append(_check("purchaser_address_match", "Purchaser address matches master data", "Purchaser", "Mandatory", status, f"Address similarity: {address_score}%.", fields.get("purchaser_address"), company.get("address")))
    else:
        checks.append(_check("purchaser_address_match", "Purchaser address matches master data", "Purchaser", "Conditional", "REVIEW", "Approved address has not been configured."))

    serial = clean_text(fields.get("invoice_number"))
    serial_pattern = rules.get("invoice_serial_regex", r"^\d{2}[A-Z]{3}_(?=[A-Z0-9]*\d)[A-Z0-9]{1,15}_[0-9]+$")
    gazette_date = parse_date(rules.get("gazette_effective_date"))
    inv_date = parse_date(fields.get("invoice_date"))
    serial_applicable = bool(gazette_date and inv_date and inv_date >= gazette_date)
    if serial:
        serial_valid = bool(re.fullmatch(serial_pattern, serial)) and len(serial) <= 40 and " " not in serial
        status = "PASS" if serial_valid else "FAIL" if serial_applicable else "REVIEW"
        checks.append(_check("invoice_serial_format", "Configured Gazette serial format", "Invoice format", "Mandatory" if serial_applicable else "Conditional", status, "Serial format matches configured rule." if serial_valid else "Serial does not match the configured Gazette pattern.", serial, serial_pattern))

    for key, label in (("invoice_date", "Invoice date format"), ("date_of_supply", "Date of supply format")):
        displayed = clean_text(fields.get(key))
        if displayed:
            mmddyyyy = bool(re.fullmatch(r"(?:0[1-9]|1[0-2])/(?:0[1-9]|[12]\d|3[01])/\d{4}", displayed))
            status = "PASS" if mmddyyyy else "FAIL" if serial_applicable else "REVIEW"
            checks.append(_check(key + "_format", label, "Invoice format", "Mandatory" if serial_applicable else "Conditional", status, "MM/DD/YYYY format detected." if mmddyyyy else "Date is not presented in configured MM/DD/YYYY format.", displayed, "MM/DD/YYYY"))

    currency = clean_text(fields.get("currency")).upper()
    checks.append(_check("currency", "Currency is LKR", "Amounts", "Mandatory", "PASS" if currency in {"LKR", "RS", "RS."} else "FAIL", "Currency is LKR." if currency in {"LKR", "RS", "RS."} else "Configured format requires Sri Lankan Rupees.", currency, "LKR"))
    for key, label in (("amount_ex_vat", "Amount excluding VAT has two decimals"), ("vat_amount", "VAT amount has two decimals"), ("total_including_vat", "Total including VAT has two decimals")):
        value = clean_text(fields.get(key))
        if value:
            checks.append(_check(key + "_decimals", label, "Amounts", "Mandatory", "PASS" if re.search(r"\.\d{2}$", value) else "REVIEW", "Two-decimal format detected." if re.search(r"\.\d{2}$", value) else "Two-decimal presentation was not detected.", value))
    words = normalize_name(fields.get("amount_words"))
    total = parse_decimal(fields.get("total_including_vat"))
    if words and total is not None:
        expected_words = normalize_name(number_to_words(int(total)))
        word_score = ratio(words, expected_words)
        checks.append(_check("amount_words_match", "Amount in words matches total", "Amounts", "Optional", "PASS" if word_score >= 72 else "REVIEW", f"Text-to-total similarity: {word_score:.0f}%.", fields.get("amount_words"), number_to_words(int(total))))
    return checks


def number_to_words(number: int) -> str:
    if number == 0:
        return "zero"
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    def below_thousand(value: int) -> str:
        parts: list[str] = []
        if value >= 100:
            parts.extend([ones[value // 100], "hundred"]); value %= 100
        if value >= 20:
            parts.append(tens[value // 10]); value %= 10
        if value:
            parts.append(ones[value])
        return " ".join(parts)
    groups = ((1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand"), (1, ""))
    parts: list[str] = []
    remaining = abs(number)
    for divisor, label in groups:
        value, remaining = divmod(remaining, divisor)
        if value:
            parts.append(below_thousand(value))
            if label:
                parts.append(label)
    return ("minus " if number < 0 else "") + " ".join(parts)


def validate_supplier_master(fields: dict[str, Any], sap: Any) -> list[CheckResult]:
    if sap is None or sap.frame.empty:
        return [_check("supplier_master", "Supplier master comparison", "Supplier", "Conditional", "REVIEW", "No mapped SAP supplier data is loaded.")]
    actual_name = normalize_name(fields.get("supplier_name"))
    actual_tin = normalize_key(fields.get("supplier_tin"))
    best_row = None
    best_score = -1.0
    candidate_indices: set[int] = set()
    if actual_name:
        candidate_indices.update(sap.by_supplier.get(actual_name, []))
    if actual_tin:
        candidate_indices.update(sap.by_supplier_tin.get(actual_tin, []))
    if not candidate_indices:
        candidate_indices.update(range(min(len(sap.frame), 5000)))
    for idx in candidate_indices:
        row = sap.frame.iloc[idx]
        row_tin = normalize_key(row.get("supplier_tin"))
        name_score = ratio(actual_name, normalize_name(row.get("supplier_name"))) if actual_name else 0
        score = 110 if actual_tin and row_tin and actual_tin == row_tin else name_score
        if score > best_score:
            best_score, best_row = score, row
    if best_row is None or (best_score < 70 and best_score != 110):
        return [_check("supplier_master", "Supplier found in SAP data", "Supplier", "Conditional", "REVIEW", "No sufficiently similar supplier was found in the mapped SAP export.", fields.get("supplier_name"))]
    checks = [_check("supplier_name_master", "Supplier name matches SAP data", "Supplier", "Conditional", "PASS" if ratio(actual_name, normalize_name(best_row.get("supplier_name"))) >= 85 else "REVIEW", f"Name similarity: {ratio(actual_name, normalize_name(best_row.get('supplier_name'))):.0f}%.", fields.get("supplier_name"), best_row.get("supplier_name"))]
    for key, label, material in (("supplier_tin", "Supplier TIN matches SAP data", True), ("supplier_vat", "Supplier VAT number matches SAP data", True), ("supplier_address", "Supplier address matches SAP data", False)):
        actual = normalize_key(fields.get(key)) if key != "supplier_address" else normalize_name(fields.get(key))
        expected = normalize_key(best_row.get(key)) if key != "supplier_address" else normalize_name(best_row.get(key))
        if not expected:
            checks.append(_check(key + "_master", label, "Supplier", "Conditional", "REVIEW", f"{label} cannot be checked because that SAP column is empty or unmapped."))
        elif not actual:
            checks.append(_check(key + "_master", label, "Supplier", "Conditional", "REVIEW", "Invoice field was not detected."))
        else:
            similarity = ratio(actual, expected)
            match = actual == expected if material else similarity >= 70
            checks.append(_check(key + "_master", label, "Supplier", "Conditional", "PASS" if match else "FAIL" if material else "REVIEW", "Exact match." if match and material else f"Similarity: {similarity:.0f}%.", fields.get(key), best_row.get(key)))
    return checks


def validate_arithmetic(fields: dict[str, Any], config: dict[str, Any]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    tolerance = parse_decimal(config.get("rules", {}).get("rounding_tolerance")) or Decimal("0.05")
    invoice_date = parse_date(fields.get("invoice_date"))
    rate = applicable_vat_rate(invoice_date, config.get("vat_rates", []))
    base = parse_decimal(fields.get("total_value_supply") or fields.get("amount_ex_vat"))
    actual_vat = parse_decimal(fields.get("vat_amount"))
    actual_total = parse_decimal(fields.get("total_including_vat"))
    if rate is None:
        return [_check("vat_rate", "Applicable VAT rate", "VAT", "Conditional", "REVIEW", "No effective VAT rate is configured for the invoice date.")]
    printed_rate = parse_decimal(fields.get("vat_rate"))
    if printed_rate is None:
        checks.append(_check("vat_rate", "Applicable VAT rate", "VAT", "Conditional", "REVIEW", "Invoice VAT rate was not detected; configured rate selected by invoice date.", "FIELD NOT DETECTED", f"{rate}%"))
    else:
        checks.append(_check("vat_rate", "Applicable VAT rate", "VAT", "Mandatory", "PASS" if printed_rate == rate else "FAIL", "Printed rate matches the effective-date table." if printed_rate == rate else "Printed VAT rate differs from the configured effective rate.", f"{printed_rate}%", f"{rate}%"))
    if base is not None and actual_vat is not None:
        expected_vat = (base * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        difference = abs(expected_vat - actual_vat)
        checks.append(_check("vat_calculation", "VAT calculation", "VAT", "Mandatory", "PASS" if difference <= tolerance else "FAIL", f"Difference: LKR {difference:.2f}; tolerance: LKR {tolerance:.2f}.", f"{actual_vat:.2f}", f"{expected_vat:.2f}"))
    else:
        checks.append(_check("vat_calculation", "VAT calculation", "VAT", "Mandatory", "REVIEW", "Base value or VAT amount was not detected."))
    if base is not None and actual_vat is not None and actual_total is not None:
        expected_total = (base + actual_vat).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        difference = abs(expected_total - actual_total)
        checks.append(_check("total_calculation", "Total including VAT", "VAT", "Mandatory", "PASS" if difference <= tolerance else "FAIL", f"Difference: LKR {difference:.2f}; tolerance: LKR {tolerance:.2f}.", f"{actual_total:.2f}", f"{expected_total:.2f}"))
    else:
        checks.append(_check("total_calculation", "Total including VAT", "VAT", "Mandatory", "REVIEW", "Required numeric fields were not detected."))
    quantity = parse_decimal(fields.get("quantity"))
    unit_price = parse_decimal(fields.get("unit_price"))
    amount_ex = parse_decimal(fields.get("amount_ex_vat"))
    if quantity is not None and unit_price is not None and amount_ex is not None:
        expected_line = (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        difference = abs(expected_line - amount_ex)
        checks.append(_check("line_arithmetic", "Quantity × unit price", "VAT", "Conditional", "PASS" if difference <= tolerance else "FAIL", f"Difference: LKR {difference:.2f}.", f"{amount_ex:.2f}", f"{expected_line:.2f}"))
    else:
        checks.append(_check("line_arithmetic", "Quantity × unit price", "VAT", "Conditional", "REVIEW", "Line quantity, unit price, or line amount was not detected."))
    return checks


def summarise_checks(checks: list[CheckResult]) -> tuple[str, dict[str, int]]:
    counts = {"PASS": 0, "REVIEW": 0, "FAIL": 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    overall = "FAIL" if counts["FAIL"] else "REVIEW" if counts["REVIEW"] else "PASS"
    return overall, counts
