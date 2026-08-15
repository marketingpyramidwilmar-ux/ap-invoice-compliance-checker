from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from modules.learning import DEFAULT_LEARNABLE_FIELDS, apply_rules_to_fields, correction_differences, parse_confusion_pairs
from modules.utils import normalize_name


class ScanDatabase:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scanned_at TEXT NOT NULL, user_name TEXT, filename TEXT NOT NULL,
                    file_hash TEXT, supplier TEXT, invoice_number TEXT, invoice_date TEXT,
                    amount TEXT, vat TEXT, duplicate_result TEXT, duplicate_score INTEGER,
                    compliance_result TEXT, overall_result TEXT, processing_seconds REAL,
                    extracted_fields TEXT, check_results TEXT, matched_sap_row TEXT,
                    learned_corrections TEXT,
                    manual_decision TEXT, manual_decision_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_scans_supplier_invoice ON scans(supplier, invoice_number);
                CREATE INDEX IF NOT EXISTS idx_scans_file_hash ON scans(file_hash);
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER,
                    changed_at TEXT NOT NULL, user_name TEXT, field_name TEXT,
                    original_value TEXT, changed_value TEXT, reason TEXT,
                    FOREIGN KEY(scan_id) REFERENCES scans(id)
                );
                CREATE TABLE IF NOT EXISTS correction_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    supplier_key TEXT NOT NULL, supplier_name TEXT NOT NULL,
                    field_name TEXT NOT NULL, observed_char TEXT NOT NULL,
                    corrected_char TEXT NOT NULL, position_from_end INTEGER NOT NULL,
                    occurrences INTEGER NOT NULL DEFAULT 1, first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(supplier_key, field_name, observed_char, corrected_char, position_from_end)
                );
                CREATE INDEX IF NOT EXISTS idx_patterns_supplier_field
                    ON correction_patterns(supplier_key, field_name, enabled);
                CREATE TABLE IF NOT EXISTS learning_applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER,
                    applied_at TEXT NOT NULL, supplier_name TEXT, field_name TEXT,
                    original_value TEXT, corrected_value TEXT, rule_id INTEGER,
                    occurrences INTEGER, confidence REAL,
                    FOREIGN KEY(scan_id) REFERENCES scans(id),
                    FOREIGN KEY(rule_id) REFERENCES correction_patterns(id)
                );
            """)
            columns = {row[1] for row in con.execute("PRAGMA table_info(scans)")}
            if "learned_corrections" not in columns:
                con.execute("ALTER TABLE scans ADD COLUMN learned_corrections TEXT")

    def save_scan(self, record: dict[str, Any]) -> int:
        columns = [
            "scanned_at", "user_name", "filename", "file_hash", "supplier", "invoice_number",
            "invoice_date", "amount", "vat", "duplicate_result", "duplicate_score",
            "compliance_result", "overall_result", "processing_seconds", "extracted_fields",
            "check_results", "matched_sap_row", "learned_corrections", "manual_decision", "manual_decision_reason",
        ]
        payload = dict(record)
        payload.setdefault("scanned_at", datetime.now(timezone.utc).isoformat())
        for key in ("extracted_fields", "check_results", "matched_sap_row", "learned_corrections"):
            if not isinstance(payload.get(key), str):
                payload[key] = json.dumps(payload.get(key), default=str)
        with self.connect() as con:
            cursor = con.execute(
                f"INSERT INTO scans ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [payload.get(column) for column in columns],
            )
            return int(cursor.lastrowid)

    def log_change(self, scan_id: int | None, user: str, field: str, original: Any, changed: Any, reason: str) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO audit_log (scan_id,changed_at,user_name,field_name,original_value,changed_value,reason) VALUES (?,?,?,?,?,?,?)",
                (scan_id, datetime.now(timezone.utc).isoformat(), user, field, str(original), str(changed), reason),
            )

    def learn_from_correction(
        self, supplier_name: str, field: str, original: Any, changed: Any,
        confusion_pairs: list[str], learnable_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        allowed_fields = set(learnable_fields or DEFAULT_LEARNABLE_FIELDS)
        supplier_key = normalize_name(supplier_name)
        if field not in allowed_fields or not supplier_key:
            return []
        differences = correction_differences(original, changed, parse_confusion_pairs(confusion_pairs))
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            for item in differences:
                con.execute("""
                    INSERT INTO correction_patterns
                        (supplier_key,supplier_name,field_name,observed_char,corrected_char,position_from_end,occurrences,first_seen,last_seen,enabled)
                    VALUES (?,?,?,?,?,?,1,?,?,1)
                    ON CONFLICT(supplier_key,field_name,observed_char,corrected_char,position_from_end)
                    DO UPDATE SET occurrences=occurrences+1,last_seen=excluded.last_seen,supplier_name=excluded.supplier_name
                """, (supplier_key, supplier_name, field, item["observed_char"], item["corrected_char"], item["position_from_end"], now, now))
        return differences

    def learned_rules(self, supplier_name: str | None = None) -> pd.DataFrame:
        query = "SELECT * FROM correction_patterns"
        params: tuple[Any, ...] = ()
        if supplier_name:
            query += " WHERE supplier_key=?"
            params = (normalize_name(supplier_name),)
        query += " ORDER BY occurrences DESC, id DESC"
        with self.connect() as con:
            frame = pd.read_sql_query(query, con, params=params)
        if frame.empty:
            frame["confidence"] = pd.Series(dtype=float)
            return frame
        totals = frame.groupby(["supplier_key", "field_name", "observed_char", "position_from_end"])["occurrences"].transform("sum")
        frame["confidence"] = frame["occurrences"] / totals
        return frame

    def apply_learning(self, fields: dict[str, Any], learning_config: dict[str, Any], supplier_name: str | None = None) -> tuple[dict[str, Any], list[Any]]:
        if not learning_config.get("enabled", True):
            return dict(fields), []
        supplier_name = str(supplier_name or fields.get("supplier_name") or "")
        frame = self.learned_rules(supplier_name)
        if frame.empty:
            return dict(fields), []
        minimum = int(learning_config.get("min_occurrences", 10))
        confidence = float(learning_config.get("min_confidence", 0.90))
        active = frame[(frame["enabled"] == 1) & (frame["occurrences"] >= minimum) & (frame["confidence"] >= confidence)]
        return apply_rules_to_fields(fields, supplier_name, active.to_dict("records"))

    def log_learning_applications(self, scan_id: int, applications: list[Any]) -> None:
        if not applications:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            con.executemany("""
                INSERT INTO learning_applications
                    (scan_id,applied_at,supplier_name,field_name,original_value,corrected_value,rule_id,occurrences,confidence)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, [(scan_id, now, app.supplier, app.field_name, app.original_value, app.corrected_value, app.rule_id, app.occurrences, app.confidence) for app in applications])

    def learning_application_history(self, limit: int = 5000) -> pd.DataFrame:
        with self.connect() as con:
            return pd.read_sql_query("SELECT * FROM learning_applications ORDER BY id DESC LIMIT ?", con, params=(limit,))

    def set_rule_enabled(self, rule_id: int, enabled: bool) -> None:
        with self.connect() as con:
            con.execute("UPDATE correction_patterns SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))

    def update_scan_results(self, scan_id: int, fields: dict[str, Any], checks: list[Any], duplicate: Any, compliance: str, overall: str) -> None:
        with self.connect() as con:
            con.execute("""
                UPDATE scans SET supplier=?,invoice_number=?,invoice_date=?,amount=?,vat=?,
                    duplicate_result=?,duplicate_score=?,compliance_result=?,overall_result=?,
                    extracted_fields=?,check_results=?,matched_sap_row=? WHERE id=?
            """, (
                fields.get("supplier_name"), fields.get("invoice_number"), fields.get("invoice_date"),
                fields.get("total_including_vat"), fields.get("vat_amount"), duplicate.band,
                duplicate.score, compliance, overall, json.dumps(fields, default=str),
                json.dumps([item.to_dict() for item in checks], default=str),
                json.dumps(duplicate.matched_row, default=str), scan_id,
            ))

    def history(self, limit: int = 5000) -> pd.DataFrame:
        with self.connect() as con:
            return pd.read_sql_query("SELECT * FROM scans ORDER BY id DESC LIMIT ?", con, params=(limit,))

    def audit_history(self, limit: int = 5000) -> pd.DataFrame:
        with self.connect() as con:
            return pd.read_sql_query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", con, params=(limit,))

    def update_decision(self, scan_id: int, decision: str, reason: str) -> None:
        with self.connect() as con:
            con.execute("UPDATE scans SET manual_decision=?, manual_decision_reason=? WHERE id=?", (decision, reason, scan_id))
