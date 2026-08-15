from __future__ import annotations

from pathlib import Path

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_data"


BASE = {
    "Supplier Name": "ABC Oils (Pvt) Ltd", "Supplier Address": "10 Port Road Colombo",
    "Supplier TIN": "123456789", "Supplier VAT Registration": "123456789-7000",
    "Purchaser Name": "Pyramid Wilmar Oils & Fats (Pvt) Ltd", "Purchaser Address": "No 1 Main Road Colombo",
    "Purchaser TIN": "987654321", "Invoice Number": "26OCT_BR03_1", "Invoice Date": "10/15/2026",
    "Date of Supply": "10/15/2026", "PO Number": "PO1001", "Description": "Refined edible oil",
    "Quantity": "10", "Unit": "CTN", "Unit Price": "LKR 100.00", "Amount Excluding VAT": "LKR 1000.00",
    "Total Value of Supply": "LKR 1000.00", "VAT Rate": "18%", "VAT Amount": "LKR 180.00",
    "Total Including VAT": "LKR 1180.00", "Total Amount in Words": "One thousand one hundred and eighty rupees",
    "Mode of Payment": "Bank Transfer", "Place of Supply": "Colombo", "Currency": "LKR",
}


def make_pdf(path: Path, values: dict[str, str], title: str = "TAX INVOICE") -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    pdf.setFont("Helvetica-Bold", 18); pdf.drawCentredString(width / 2, height - 45, title)
    pdf.setFont("Helvetica", 9)
    y = height - 78
    for key, value in values.items():
        pdf.drawString(48, y, f"{key}: {value}")
        y -= 20
        if y < 50:
            pdf.showPage(); pdf.setFont("Helvetica", 9); y = height - 50
    pdf.save()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = {
        "01_correct_invoice.pdf": {},
        "02_duplicate_invoice.pdf": {},
        "03_wrong_purchaser_tin.pdf": {"Purchaser TIN": "111111111", "Invoice Number": "26OCT_BR03_3"},
        "04_wrong_purchaser_name.pdf": {"Purchaser Name": "Other Company Ltd", "Invoice Number": "26OCT_BR03_4"},
        "05_wrong_address.pdf": {"Purchaser Address": "99 Wrong Road Kandy", "Invoice Number": "26OCT_BR03_5"},
        "06_incorrect_vat.pdf": {"VAT Amount": "LKR 170.00", "Total Including VAT": "LKR 1170.00", "Invoice Number": "26OCT_BR03_6"},
        "07_incorrect_total.pdf": {"Total Including VAT": "LKR 1190.00", "Invoice Number": "26OCT_BR03_7"},
        "08_missing_invoice_number.pdf": {"Invoice Number": ""},
        "09_missing_vat.pdf": {"VAT Amount": "", "Invoice Number": "26OCT_BR03_9"},
        "10_missing_date.pdf": {"Invoice Date": "", "Invoice Number": "26OCT_BR03_10"},
        "11_supplier_mismatch.pdf": {"Supplier Name": "Different Vendor Ltd", "Supplier TIN": "222222222", "Invoice Number": "26OCT_BR03_11"},
        "12_ocr_mistake.pdf": {"Invoice Number": "26OCT_BR03_I", "Purchaser TIN": "98765432I"},
        "13_similar_invoice_number.pdf": {"Invoice Number": "26OCT_BR03_I"},
        "14_same_amount_new_invoice.pdf": {"Supplier Name": "New Supplier Ltd", "Supplier TIN": "333333333", "Invoice Number": "26OCT_NS01_1", "PO Number": "PO9999"},
    }
    for filename, changes in cases.items():
        values = {**BASE, **changes}
        make_pdf(OUT / filename, values)
    batch = OUT / "15_batch_20"
    batch.mkdir(exist_ok=True)
    for number in range(1, 21):
        values = {**BASE, "Invoice Number": f"26OCT_BT01_{number}", "PO Number": f"BATCH{number:03d}"}
        make_pdf(batch / f"batch_invoice_{number:02d}.pdf", values)
    sap = pd.DataFrame([
        {"Vendor Code": "V001", "Vendor Name": "ABC Oils (Pvt) Ltd", "Supplier TIN": "123456789", "Vendor Address": "10 Port Road Colombo", "VAT No": "123456789-7000", "Invoice No": "26OCT_BR03_1", "Invoice Date": "10/15/2026", "Posting Date": "10/16/2026", "Document Number": "190034", "PO Number": "PO1001", "Total Amount": 1180.00, "VAT Amount": 180.00, "Currency": "LKR", "Vendor Reference": "26OCT_BR03_1", "Description": "Refined edible oil"},
        {"Vendor Code": "V002", "Vendor Name": "Office Services Ltd", "Supplier TIN": "555555555", "Vendor Address": "Colombo", "VAT No": "555555555-7000", "Invoice No": "26OCT_OS01_10", "Invoice Date": "10/10/2026", "Posting Date": "10/11/2026", "Document Number": "190035", "PO Number": "PO2000", "Total Amount": 5900.00, "VAT Amount": 900.00, "Currency": "LKR", "Vendor Reference": "26OCT_OS01_10", "Description": "Services"},
    ])
    sap.to_excel(OUT / "sap_ap_export_sample.xlsx", index=False)
    print(f"Generated sample data in {OUT}")


if __name__ == "__main__":
    main()

