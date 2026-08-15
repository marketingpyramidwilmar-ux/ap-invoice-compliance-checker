from __future__ import annotations

import base64
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from modules.database import ScanDatabase
from modules.duplicate import detect_duplicate
from modules.excel import CANONICAL_COLUMNS, DISPLAY_NAMES, SAPDataIndex, apply_mapping, load_mapping, read_excel_bytes, resolve_supplier_identity, save_mapping, suggest_mapping
from modules.extraction import FIELD_LABELS, extract_fields
from modules.ocr import extract_document
from modules.utils import PROJECT_ROOT, ensure_directories, load_json, save_json, sha256_bytes
from modules.validation import CheckResult, summarise_checks, validate_arithmetic, validate_invoice, validate_supplier_master


st.set_page_config(page_title="AP Invoice Compliance & Duplicate Control", page_icon="🧾", layout="wide")
ensure_directories()
CONFIG_PATH = PROJECT_ROOT / "config" / "user_config.json"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
MAPPING_PATH = PROJECT_ROOT / "config" / "sap_mapping.json"
DB = ScanDatabase(PROJECT_ROOT / "database" / "ap_invoice_checker.sqlite3")


def get_config() -> dict[str, Any]:
    return load_json(CONFIG_PATH, load_json(DEFAULT_CONFIG_PATH, {}))


def init_state() -> None:
    defaults = {
        "sap_raw": None, "sap_mapped": None, "sap_index": None, "sap_filename": "",
        "invoice_results": [], "selected_result": None, "user_name": os.getenv("USERNAME", "AP User"),
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


init_state()

st.markdown("""
<style>
    .block-container {padding-top: 1.25rem; padding-bottom: 3rem;}
    .app-title {font-size: 1.72rem; font-weight: 760; color:#172033; margin:0;}
    .subtitle {color:#64748b; margin:.15rem 0 1rem 0;}
    div[data-testid="stMetric"] {background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:14px; box-shadow:0 1px 2px rgba(15,23,42,.04)}
    .status-pass {color:#067647;font-weight:700}.status-review {color:#b54708;font-weight:700}.status-fail {color:#b42318;font-weight:700}
    .notice {background:#f8fafc;border-left:4px solid #334155;padding:.8rem 1rem;border-radius:6px;color:#334155}
</style>
""", unsafe_allow_html=True)


def status_icon(status: str) -> str:
    return {"PASS": "🟢", "REVIEW": "🟠", "FAIL": "🔴"}.get(status, "⚪")


def excel_download(frame: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Results")
    return output.getvalue()


def process_one(file_record: dict[str, Any], config: dict[str, Any], sap_index: SAPDataIndex | None, user_name: str) -> dict[str, Any]:
    started = time.perf_counter()
    ocr = extract_document(file_record["data"], file_record["name"])
    extracted = extract_fields(ocr.text)
    ocr_fields = dict(extracted.fields)
    learning_supplier = resolve_supplier_identity(ocr_fields, sap_index)
    corrected_fields, learned_applications = DB.apply_learning(ocr_fields, config.get("learning", {}), learning_supplier)
    extracted.fields = corrected_fields
    checks = validate_invoice(corrected_fields, extracted.raw_text, config)
    checks.extend(validate_supplier_master(corrected_fields, sap_index))
    vat_checks = validate_arithmetic(corrected_fields, config)
    checks.extend(vat_checks)
    compliance, counts = summarise_checks(checks)
    duplicate = detect_duplicate(extracted.fields, sap_index)
    overall = "FAIL" if duplicate.band == "HIGH PROBABILITY DUPLICATE" or compliance == "FAIL" else "REVIEW" if duplicate.band == "REVIEW" or compliance == "REVIEW" else "PASS"
    result = {
        "filename": file_record["name"], "file_bytes": file_record["data"], "file_type": file_record["type"],
        "file_hash": sha256_bytes(file_record["data"]), "ocr_method": ocr.method, "ocr_warning": ocr.warning,
        "preview": ocr.preview, "raw_text": extracted.raw_text, "ocr_fields": ocr_fields,
        "fields": corrected_fields, "learned_applications": learned_applications,
        "checks": checks, "compliance": compliance, "counts": counts, "duplicate": duplicate,
        "overall": overall, "processing_seconds": time.perf_counter() - started,
    }
    result["scan_id"] = DB.save_scan({
        "user_name": user_name, "filename": result["filename"],
        "file_hash": result["file_hash"], "supplier": result["fields"].get("supplier_name"),
        "invoice_number": result["fields"].get("invoice_number"), "invoice_date": result["fields"].get("invoice_date"),
        "amount": result["fields"].get("total_including_vat"), "vat": result["fields"].get("vat_amount"),
        "duplicate_result": duplicate.band, "duplicate_score": duplicate.score, "compliance_result": compliance,
        "overall_result": overall, "processing_seconds": result["processing_seconds"], "extracted_fields": result["fields"],
        "check_results": [c.to_dict() for c in checks], "matched_sap_row": duplicate.matched_row,
        "learned_corrections": [item.to_dict() for item in learned_applications],
    })
    DB.log_learning_applications(result["scan_id"], learned_applications)
    return result


def rerun_result(result: dict[str, Any], new_fields: dict[str, Any], reason: str) -> None:
    old_fields = result["fields"]
    config = get_config()
    learning = config.get("learning", {})
    evidence: list[dict[str, Any]] = []
    supplier_name = resolve_supplier_identity(new_fields, st.session_state.sap_index)
    for field, changed in new_fields.items():
        original = old_fields.get(field, "")
        if str(original) != str(changed):
            DB.log_change(result.get("scan_id"), st.session_state.user_name, field, original, changed, reason)
            evidence.extend(DB.learn_from_correction(
                supplier_name, field, original, changed,
                learning.get("confusion_pairs", ["I:1", "O:0"]),
                learning.get("learnable_fields"),
            ))
    result["fields"] = new_fields
    result["last_learning_evidence"] = evidence
    result["checks"] = validate_invoice(new_fields, result["raw_text"], config) + validate_supplier_master(new_fields, st.session_state.sap_index) + validate_arithmetic(new_fields, config)
    result["compliance"], result["counts"] = summarise_checks(result["checks"])
    result["duplicate"] = detect_duplicate(new_fields, st.session_state.sap_index)
    result["overall"] = "FAIL" if result["duplicate"].band == "HIGH PROBABILITY DUPLICATE" or result["compliance"] == "FAIL" else "REVIEW" if result["duplicate"].band == "REVIEW" or result["compliance"] == "REVIEW" else "PASS"
    DB.update_scan_results(result["scan_id"], new_fields, result["checks"], result["duplicate"], result["compliance"], result["overall"])


def page_upload_sap() -> None:
    st.header("Step 1 · Upload SAP AP Excel")
    st.caption("The Excel export is read once and retained in memory for this session. No SAP connection is made.")
    upload = st.file_uploader("SAP Business One AP export", type=["xlsx", "xlsm"], key="sap_upload")
    if upload is not None and (st.session_state.sap_raw is None or upload.name != st.session_state.sap_filename):
        try:
            st.session_state.sap_raw = read_excel_bytes(upload.getvalue())
            st.session_state.sap_filename = upload.name
            st.success(f"Loaded {len(st.session_state.sap_raw):,} rows from {upload.name}")
        except Exception as exc:
            st.error(f"Excel could not be read: {exc}")
            return
    frame = st.session_state.sap_raw
    if frame is None:
        st.info("Upload an Excel file to create the column mapping.")
        return
    st.dataframe(frame.head(10), width="stretch")
    remembered = load_mapping(MAPPING_PATH)
    suggestions = {**suggest_mapping([str(c) for c in frame.columns]), **remembered}
    options = ["— Not mapped —", *[str(c) for c in frame.columns]]
    st.subheader("Column mapping")
    mapping: dict[str, str] = {}
    columns = st.columns(3)
    for pos, canonical in enumerate(CANONICAL_COLUMNS):
        current = suggestions.get(canonical)
        index = options.index(current) if current in options else 0
        selected = columns[pos % 3].selectbox(DISPLAY_NAMES[canonical], options, index=index, key=f"map_{canonical}")
        if selected != options[0]:
            mapping[canonical] = selected
    if st.button("Save mapping and build indexes", type="primary"):
        if not mapping.get("supplier_name") or not mapping.get("invoice_number"):
            st.warning("Map at least Supplier Name and Invoice Number. Total Amount is strongly recommended.")
        else:
            mapped = apply_mapping(frame, mapping)
            st.session_state.sap_mapped = mapped
            st.session_state.sap_index = SAPDataIndex(mapped)
            save_mapping(MAPPING_PATH, mapping)
            st.success(f"Ready: {len(mapped):,} SAP rows indexed in memory.")


def page_check_invoices() -> None:
    st.header("Step 2–3 · Upload and check invoices")
    if st.session_state.sap_index is None:
        st.warning("No SAP export is indexed. Invoices can still be checked, but duplicate matching will show REVIEW.")
    uploads = st.file_uploader("Upload 1–20 invoices", type=["jpg", "jpeg", "png", "pdf"], accept_multiple_files=True, key="invoice_uploads")
    if uploads and len(uploads) > 20:
        st.error("A maximum of 20 invoices can be processed in one batch.")
        return
    if st.button("CHECK INVOICES", type="primary", disabled=not uploads):
        config = get_config()
        records = [{"name": f.name, "data": f.getvalue(), "type": f.type} for f in uploads]
        progress = st.progress(0, text=f"Processing 0 / {len(records)} invoices")
        status = st.empty()
        results: list[dict[str, Any]] = []
        workers = max(1, min(int(config.get("performance", {}).get("max_workers", 4)), len(records)))
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            user_name = st.session_state.get("user_name", "AP User")
            futures = {pool.submit(process_one, record, config, st.session_state.sap_index, user_name): record["name"] for record in records}
            for completed, future in enumerate(as_completed(futures), start=1):
                try:
                    results.append(future.result())
                except Exception as exc:
                    name = futures[future]
                    status.error(f"{name}: processing error converted to REVIEW — {exc}")
                progress.progress(completed / len(records), text=f"Processing {completed} / {len(records)} invoices")
        results.sort(key=lambda item: item["filename"])
        st.session_state.invoice_results = results
        elapsed = time.perf_counter() - started
        status.success(f"{len(results)} invoices processed in {elapsed:.1f} seconds.")
    if st.session_state.invoice_results:
        render_dashboard(st.session_state.invoice_results)


def render_dashboard(results: list[dict[str, Any]]) -> None:
    st.divider()
    st.subheader("Compliance dashboard")
    counts = {status: sum(r["overall"] == status for r in results) for status in ("PASS", "REVIEW", "FAIL")}
    duplicates = sum(r["duplicate"].score >= 70 for r in results)
    for column, (label, value) in zip(st.columns(5), [("Invoices scanned", len(results)), ("PASS", counts["PASS"]), ("REVIEW", counts["REVIEW"]), ("FAIL", counts["FAIL"]), ("Potential duplicates", duplicates)]):
        column.metric(label, value)
    rows = []
    for i, result in enumerate(results):
        fields = result["fields"]
        rows.append({
            "#": i + 1, "Invoice number": fields.get("invoice_number") or "FIELD NOT DETECTED",
            "Supplier": fields.get("supplier_name") or "FIELD NOT DETECTED", "Invoice date": fields.get("invoice_date"),
            "Amount": fields.get("total_including_vat"), "VAT": fields.get("vat_amount"),
            "Duplicate": f"{result['duplicate'].band} ({result['duplicate'].score}%)",
            "Compliance": result["compliance"], "Overall": result["overall"], "Seconds": round(result["processing_seconds"], 2),
        })
    frame = pd.DataFrame(rows)
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button("Download batch results", excel_download(frame), "invoice_check_results.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    choices = {f"{i+1}. {r['filename']} — {r['overall']}": i for i, r in enumerate(results)}
    selected = st.selectbox("Open invoice detail", list(choices))
    if st.button("Open selected invoice"):
        st.session_state.selected_result = choices[selected]
        st.session_state.nav_page = "Invoice detail"
        st.rerun()


def render_document(result: dict[str, Any]) -> None:
    if result["file_type"] == "application/pdf" or result["filename"].lower().endswith(".pdf"):
        encoded = base64.b64encode(result["file_bytes"]).decode()
        st.components.v1.html(f'<iframe src="data:application/pdf;base64,{encoded}" width="100%" height="720"></iframe>', height=740)
    else:
        st.image(result["file_bytes"], width="stretch")


def page_invoice_detail() -> None:
    results = st.session_state.invoice_results
    if not results:
        st.info("Process invoices first.")
        return
    index = st.session_state.selected_result if st.session_state.selected_result is not None else 0
    result = results[index]
    st.header(f"Invoice detail · {result['filename']}")
    top = st.columns(4)
    top[0].metric("Overall", result["overall"])
    top[1].metric("Compliance", result["compliance"])
    top[2].metric("Duplicate score", f"{result['duplicate'].score}%")
    top[3].metric("Processing time", f"{result['processing_seconds']:.2f}s")
    if result.get("learned_applications"):
        st.info(f"{len(result['learned_applications'])} supplier-specific learned OCR correction(s) were applied before validation. Review them below.")
        st.dataframe(pd.DataFrame([item.to_dict() for item in result["learned_applications"]]), width="stretch", hide_index=True)
    if result.get("last_learning_evidence"):
        st.success(f"Learning evidence captured from {len(result['last_learning_evidence'])} character correction(s). The rule activates only after the configured evidence and confidence thresholds are reached.")
    left, right = st.columns([1.05, 1])
    with left:
        st.subheader("Original invoice")
        render_document(result)
    with right:
        st.subheader("Extracted fields · editable")
        with st.form("field_editor"):
            updated = {key: st.text_input(label, value=str(result["fields"].get(key, "")), key=f"edit_{index}_{key}") for key, label in FIELD_LABELS.items()}
            reason = st.text_input("Reason for correction", placeholder="Example: OCR read 1 as I")
            submitted = st.form_submit_button("Save corrections and rerun validation", type="primary")
            if submitted:
                changed = any(str(result["fields"].get(k, "")) != str(v) for k, v in updated.items())
                if changed and not reason.strip():
                    st.error("Enter a reason so the audit trail is complete.")
                else:
                    rerun_result(result, updated, reason or "Validation rerun; no field change")
                    st.success("Corrections saved and controls rerun.")
                    st.rerun()
    st.subheader("Control results")
    categories: dict[str, list[CheckResult]] = {}
    for check in result["checks"]:
        categories.setdefault(check.category, []).append(check)
    for category, checks in categories.items():
        with st.expander(f"{category} · {sum(c.status == 'FAIL' for c in checks)} fail · {sum(c.status == 'REVIEW' for c in checks)} review", expanded=any(c.status != "PASS" for c in checks)):
            for check in checks:
                st.markdown(f"**{status_icon(check.status)} {check.label} — {check.status}**  \n{check.message}")
                if check.actual or check.expected:
                    a, e = st.columns(2)
                    a.caption(f"Invoice: {check.actual or 'FIELD NOT DETECTED'}")
                    e.caption(f"Expected: {check.expected or '—'}")
    duplicate = result["duplicate"]
    st.subheader("Duplicate check")
    st.markdown(f"**{status_icon('FAIL' if duplicate.score >= 90 else 'REVIEW' if duplicate.score >= 70 else 'PASS')} {duplicate.band} · {duplicate.score}%**")
    for reason in duplicate.reasons:
        st.write(f"• {reason}")
    if duplicate.matched_row:
        st.dataframe(pd.DataFrame([duplicate.matched_row]), width="stretch", hide_index=True)
    st.subheader("Manual review decision")
    decision = st.selectbox("Decision", ["Pending", "Approved to process", "Hold", "Rejected"])
    decision_reason = st.text_area("Decision notes")
    if st.button("Save manual decision"):
        DB.update_decision(result["scan_id"], decision, decision_reason)
        st.success("Manual decision saved.")


def page_configuration() -> None:
    st.header("Configuration")
    st.caption("Enter only Finance/Tax-approved master data. Tax and legal determination remains with Finance/Tax.")
    config = get_config()
    company = config.setdefault("company", {})
    rules = config.setdefault("rules", {})
    learning = config.setdefault("learning", {})
    with st.form("config_form"):
        st.subheader("Pyramid Wilmar master data")
        legal_name = st.text_input("Legal company name", company.get("legal_name", ""))
        tin = st.text_input("TIN", company.get("tin", ""))
        vat_reg = st.text_input("VAT registration number", company.get("vat_registration_number", ""))
        address = st.text_area("Registered address", company.get("address", ""))
        telephone = st.text_input("Telephone", company.get("telephone", ""))
        aliases = st.text_area("Approved aliases · one per line", "\n".join(company.get("aliases", [])))
        st.subheader("Configured invoice-format validation")
        gazette_date = st.date_input("Gazette format effective date", value=pd.to_datetime(rules.get("gazette_effective_date", "2026-10-01")).date())
        tin_regex = st.text_input("TIN validation regex", rules.get("tin_regex", r"^\d{9}$"))
        serial_regex = st.text_input("Invoice serial-number regex", rules.get("invoice_serial_regex", r"^\d{2}[A-Z]{3}_(?=[A-Z0-9]*\d)[A-Z0-9]{1,15}_[0-9]+$"))
        tolerance = st.number_input("Rounding tolerance (LKR)", min_value=0.0, value=float(rules.get("rounding_tolerance", 0.05)), step=0.01)
        name_threshold = st.slider("Name fuzzy-match threshold", 50, 100, int(rules.get("name_match_threshold", 88)))
        address_threshold = st.slider("Address fuzzy-match threshold", 50, 100, int(rules.get("address_match_threshold", 70)))
        st.subheader("Supplier-specific correction learning")
        learning_enabled = st.checkbox("Enable learned OCR corrections", value=bool(learning.get("enabled", True)))
        min_occurrences = st.number_input("Corrections required before automatic use", min_value=2, max_value=100, value=int(learning.get("min_occurrences", 10)), step=1)
        min_confidence = st.slider("Minimum rule confidence", 0.50, 1.00, float(learning.get("min_confidence", 0.90)), step=0.01)
        confusion_pairs = st.text_area("Allowed OCR confusion pairs · one per line", "\n".join(learning.get("confusion_pairs", ["I:1", "O:0"])), help="Examples: I:1 and O:0. Both directions are recognized, but only the direction confirmed by AP corrections is learned.")
        st.subheader("VAT rates")
        vat_frame = st.data_editor(pd.DataFrame(config.get("vat_rates", [])), num_rows="dynamic", width="stretch")
        submitted = st.form_submit_button("Save configuration", type="primary")
    if submitted:
        config["company"] = {"legal_name": legal_name, "tin": tin, "vat_registration_number": vat_reg, "address": address, "telephone": telephone, "aliases": [v.strip() for v in aliases.splitlines() if v.strip()]}
        config["rules"].update({"gazette_effective_date": gazette_date.isoformat(), "tin_regex": tin_regex, "invoice_serial_regex": serial_regex, "rounding_tolerance": str(tolerance), "name_match_threshold": name_threshold, "address_match_threshold": address_threshold})
        config["learning"].update({"enabled": learning_enabled, "min_occurrences": int(min_occurrences), "min_confidence": float(min_confidence), "confusion_pairs": [value.strip() for value in confusion_pairs.splitlines() if value.strip()]})
        config["vat_rates"] = vat_frame.fillna("").to_dict("records")
        save_json(CONFIG_PATH, config)
        st.success("Configuration saved locally.")


def page_history() -> None:
    st.header("Scan history and audit trail")
    history = DB.history()
    st.dataframe(history.drop(columns=[c for c in ("extracted_fields", "check_results", "matched_sap_row") if c in history], errors="ignore"), width="stretch", hide_index=True)
    st.download_button("Export scan history to Excel", excel_download(history), "ap_scan_history.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.subheader("Field-change audit log")
    audit = DB.audit_history()
    st.dataframe(audit, width="stretch", hide_index=True)
    st.subheader("Supplier-specific correction learning")
    st.caption("Rules are learned only from explicit AP corrections. Automatic use requires the configured evidence and confidence thresholds.")
    rules = DB.learned_rules()
    config = get_config().get("learning", {})
    if rules.empty:
        st.info("No character-confusion patterns have been learned yet.")
    else:
        rules["active_now"] = (
            (rules["enabled"] == 1)
            & (rules["occurrences"] >= int(config.get("min_occurrences", 10)))
            & (rules["confidence"] >= float(config.get("min_confidence", 0.90)))
            & bool(config.get("enabled", True))
        )
        st.dataframe(rules, width="stretch", hide_index=True)
        rule_id = st.selectbox("Rule ID to manage", rules["id"].astype(int).tolist())
        left, right = st.columns(2)
        if left.button("Enable selected rule"):
            DB.set_rule_enabled(int(rule_id), True)
            st.success("Rule enabled."); st.rerun()
        if right.button("Disable selected rule"):
            DB.set_rule_enabled(int(rule_id), False)
            st.success("Rule disabled."); st.rerun()
    st.subheader("Automatic correction application log")
    st.dataframe(DB.learning_application_history(), width="stretch", hide_index=True)


st.markdown('<p class="app-title">AP Invoice Compliance & Duplicate Control System</p><p class="subtitle">Internal finance control · local processing only · no SAP connection</p>', unsafe_allow_html=True)
st.markdown('<div class="notice"><b>Configured invoice-format validation.</b> This tool does not provide legal certification. Final tax and processing decisions remain with Finance/Tax. The system never posts, modifies or deletes SAP data.</div>', unsafe_allow_html=True)
st.sidebar.text_input("Current user", key="user_name")
pages = ["1 · SAP Excel", "2 · Check invoices", "Invoice detail", "Configuration", "History & audit"]
st.session_state.setdefault("nav_page", pages[0])
page = st.sidebar.radio("Workflow", pages, key="nav_page")
st.sidebar.caption("Run locally at http://localhost:8501")

if page == pages[0]:
    page_upload_sap()
elif page == pages[1]:
    page_check_invoices()
elif page == pages[2]:
    page_invoice_detail()
elif page == pages[3]:
    page_configuration()
else:
    page_history()
