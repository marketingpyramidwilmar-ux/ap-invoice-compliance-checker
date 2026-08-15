# AP Invoice Compliance & Duplicate Control System

Version 1 is a local-only internal Finance/AP control application for Pyramid Wilmar Oils & Fats (Pvt) Ltd. It reads an SAP Business One **Excel export** and supplier invoice files. It does not connect to SAP and cannot post, modify, or delete SAP data.

The result is configured invoice-format validation—not legal certification. Finance/Tax retains the final tax and processing decision.

## Included capabilities

- Upload and map an SAP AP Excel export without assuming column names.
- Keep the mapped data and four duplicate-search indexes in memory for the session.
- Upload 1–20 JPG, PNG, or PDF invoices.
- Extract selectable PDF text directly; use local Tesseract OCR only when needed.
- Preprocess images with OpenCV/Pillow and process batches with a bounded worker pool.
- Extract and manually correct invoice fields with a reasoned audit trail.
- Learn supplier-specific OCR character corrections from repeated AP edits without retraining or using AI.
- Run configurable mandatory, conditional, and optional invoice-format checks.
- Calculate VAT, totals, and quantity × unit price deterministically.
- Select VAT rates by invoice date from a configurable effective-date table.
- Score exact and fuzzy duplicates from 0–100 and show the matched SAP row.
- Store scan history, results, corrections, and manual decisions in local SQLite.
- Export the dashboard and scan history to Excel.
- Continue with REVIEW results when OCR or expected fields are unavailable.

## Important 2026 rule-date note

Gazette Extraordinary **2481/22** originally specified an effective date of 1 July 2026. Gazette Extraordinary **2500/106**, dated 6 August 2026, amended that effective date to **1 October 2026**, leaving the other specifications unchanged. Version 1 therefore defaults the configured Gazette-format date to 1 October 2026. Finance/Tax should verify and update the configuration when requirements change.

Official sources:

- [IRD Gazette 2481/22](https://www.ird.gov.lk/en/publications/Gazette_Documents/2026_2481-22_E.pdf)
- [IRD Gazette 2500/106 amendment](https://www.ird.gov.lk/en/publications/Gazette_Documents/2026_2500_106_E.pdf)

## Folder structure

```text
ap_invoice_checker/
├── .streamlit/config.toml
├── app.py
├── start_windows.bat
├── requirements.txt
├── pytest.ini
├── README.md
├── config/
│   ├── default_config.json
│   ├── user_config.json          # created after first save
│   └── sap_mapping.json          # created after first mapping
├── data/
├── database/
│   └── ap_invoice_checker.sqlite3 # created on first launch
├── modules/
│   ├── __init__.py
│   ├── database.py
│   ├── duplicate.py
│   ├── excel.py
│   ├── extraction.py
│   ├── ocr.py
│   ├── utils.py
│   └── validation.py
├── scripts/
│   └── generate_sample_data.py
├── sample_data/
│   ├── sap_ap_export_sample.xlsx
│   ├── 01_correct_invoice.pdf ... 14_same_amount_new_invoice.pdf
│   └── 15_batch_20/
└── tests/
    ├── test_duplicate.py
    ├── test_excel.py
    └── test_validation.py
```

## Windows installation

Use 64-bit Python 3.11 or 3.12.

```powershell
cd C:\path\to\ap_invoice_checker
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation for the current window:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

### Install Tesseract OCR

1. Install a current Windows Tesseract build, normally into `C:\Program Files\Tesseract-OCR`.
2. During installation, include English language data.
3. Add `C:\Program Files\Tesseract-OCR` to the Windows PATH, or define:

```powershell
$env:TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
```

Verify it:

```powershell
tesseract --version
```

Selectable-text PDFs do not require OCR. Scanned PDFs require PyMuPDF plus Tesseract; both are included in the requirements.

## Generate sample data

Sample files are included. To regenerate them:

```powershell
python scripts\generate_sample_data.py
```

The 14 individual cases cover correct invoice, exact duplicate, wrong purchaser TIN/name/address, incorrect VAT/total, missing invoice number/VAT/date, supplier mismatch, OCR-style character mistake, similar invoice number, and same amount on a different supplier. `15_batch_20` contains a 20-invoice performance batch.

## Launch

Exact command:

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Then open `http://localhost:8501`. After installation, `start_windows.bat` provides the same launch.

The supplied configuration binds Streamlit to `127.0.0.1`, so it is accessible only from the same laptop. Do not change the address to `0.0.0.0`, do not configure router port forwarding, and do not expose port 8501 through a public tunnel.

## First-use workflow

1. Open **Configuration** and enter the Finance/Tax-approved Pyramid Wilmar TIN, VAT number, registered address, telephone, and approved aliases. The real TIN and address are intentionally not hard-coded.
2. Open **1 · SAP Excel**, upload the export, map the columns, and build the in-memory indexes.
3. Open **2 · Check invoices**, upload 1–20 files, and click **CHECK INVOICES**.
4. Review PASS/REVIEW/FAIL results and open flagged invoices.
5. Correct OCR fields only with a reason; the change is written to the audit log.
6. Record the manual decision and download results or scan history.

## Tests

```powershell
pytest -q
```

The tests cover VAT-rate selection, VAT and total arithmetic, optional-field treatment, material TIN mismatch, missing fields, extraction, exact/fuzzy duplicate detection, same-amount false-positive control, Excel mapping, and Excel loading.

For a manual performance benchmark, upload `01_correct_invoice.pdf`, then all files in `15_batch_20`. Processing seconds are shown per invoice and for the batch. Expected time varies with CPU, storage, OCR need, page count, and image quality; the requested 3–8 / 10–30 / 20–60 second ranges are targets, not guarantees.

## Troubleshooting

- **Tesseract not found:** run `tesseract --version`; set `TESSERACT_CMD` to the full executable path and restart Streamlit.
- **PDF cannot be read:** confirm it opens locally. The app converts the failure to REVIEW. Re-save or export the PDF and retry.
- **OCR is poor:** use a straight, sharp 200–300 DPI scan with good contrast. Manually correct fields and record the reason.
- **Excel column missing:** leave it unmapped; controls using that data become REVIEW where appropriate. Map supplier name and invoice number at minimum.
- **Old mapping points to renamed columns:** select the current columns and save the mapping again.
- **Wrong date interpretation:** use `MM/DD/YYYY` for the configured Gazette format; the parser also accepts common local and ISO forms.
- **No duplicate found:** verify supplier, invoice number, amount, date, and PO columns were mapped and that the relevant SAP period was exported.
- **Port already in use:** close the other Streamlit process or launch with a different localhost port.
- **Database locked:** close duplicate application windows/processes. SQLite WAL mode supports normal app activity but not uncontrolled multi-instance use.
- **Reset local history:** close the app and archive or remove `database\ap_invoice_checker.sqlite3`; a fresh database is created next launch. Treat the old file as finance data.

## Security and data handling

All document extraction and matching run locally. No invoice, SAP export, or master data is sent to an API or cloud service. No passwords or external credentials are stored. SQLite and configuration files remain in the project folder; protect the Windows account, disk, backups, and folder permissions according to company policy.

The extraction layer is modular, so an optional future AI extractor can implement the same field dictionary without changing deterministic VAT, duplicate, compliance, database, or audit logic.

## Supplier-specific correction learning

The application learns controlled OCR substitutions from explicit manual corrections. For example, when AP repeatedly changes `INV4582I` to `INV45821` for ABC Oils, the system records:

- Supplier: ABC Oils
- Field: Invoice number
- Observed character: `I`
- Corrected character: `1`
- Position: last character
- Evidence count and confidence

The default activation threshold is 10 matching corrections at 90% confidence. Both values are configurable. A rule is restricted to the same normalized supplier, field, observed character and position from the end. This means a learned final-character rule does not replace every `I` in the invoice number.

Default recognized confusion pairs are `I ↔ 1` and `O ↔ 0`. Finance can add approved pairs in Configuration. Automatic substitutions are accepted only when the result matches the field profile, such as nine numeric digits for a TIN or permitted invoice-number characters.

Every automatic correction is shown on the invoice detail page and stored in the automatic-correction application log. Finance can inspect, enable or disable individual learned rules under **History & audit**. Manual corrections remain in the original audit log with user, time, reason, old value and new value.
