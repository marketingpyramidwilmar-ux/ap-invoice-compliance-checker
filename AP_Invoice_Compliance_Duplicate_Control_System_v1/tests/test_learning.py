from pathlib import Path

from modules.database import ScanDatabase


def learning_config(minimum: int = 3):
    return {
        "enabled": True,
        "min_occurrences": minimum,
        "min_confidence": 0.90,
        "confusion_pairs": ["I:1", "O:0"],
        "learnable_fields": ["invoice_number", "supplier_tin"],
    }


def test_supplier_specific_rule_activates_after_threshold(tmp_path: Path):
    database = ScanDatabase(tmp_path / "learning.sqlite3")
    fields = {"supplier_name": "ABC Oils (Pvt) Ltd", "invoice_number": "INV4582I"}
    before, applications = database.apply_learning(fields, learning_config())
    assert before["invoice_number"] == "INV4582I"
    assert applications == []

    for _ in range(3):
        database.learn_from_correction(
            "ABC Oils (Pvt) Ltd", "invoice_number", "INV4582I", "INV45821",
            ["I:1", "O:0"], ["invoice_number"],
        )

    corrected, applications = database.apply_learning(fields, learning_config())
    assert corrected["invoice_number"] == "INV45821"
    assert len(applications) == 1
    assert applications[0].occurrences == 3
    assert applications[0].confidence == 1.0


def test_rule_does_not_cross_suppliers(tmp_path: Path):
    database = ScanDatabase(tmp_path / "learning.sqlite3")
    for _ in range(5):
        database.learn_from_correction("ABC Oils", "invoice_number", "INV10I", "INV101", ["I:1"], ["invoice_number"])
    fields = {"supplier_name": "Different Supplier", "invoice_number": "INV10I"}
    corrected, applications = database.apply_learning(fields, learning_config())
    assert corrected["invoice_number"] == "INV10I"
    assert applications == []


def test_position_specific_rule_does_not_replace_all_characters(tmp_path: Path):
    database = ScanDatabase(tmp_path / "learning.sqlite3")
    for _ in range(3):
        database.learn_from_correction("ABC Oils", "invoice_number", "INV10I", "INV101", ["I:1"], ["invoice_number"])
    fields = {"supplier_name": "ABC Oils", "invoice_number": "IINV10I"}
    corrected, _ = database.apply_learning(fields, learning_config())
    assert corrected["invoice_number"] == "IINV101"


def test_tin_rule_applies_only_when_result_matches_numeric_profile(tmp_path: Path):
    database = ScanDatabase(tmp_path / "learning.sqlite3")
    for _ in range(3):
        database.learn_from_correction("ABC Oils", "supplier_tin", "98765432I", "987654321", ["I:1"], ["supplier_tin"])
    corrected, applications = database.apply_learning(
        {"supplier_name": "ABC Oils", "supplier_tin": "98765432I"}, learning_config()
    )
    assert corrected["supplier_tin"] == "987654321"
    assert len(applications) == 1


def test_disabled_rule_is_not_applied(tmp_path: Path):
    database = ScanDatabase(tmp_path / "learning.sqlite3")
    for _ in range(3):
        database.learn_from_correction("ABC Oils", "invoice_number", "INV10I", "INV101", ["I:1"], ["invoice_number"])
    rule_id = int(database.learned_rules().iloc[0]["id"])
    database.set_rule_enabled(rule_id, False)
    corrected, applications = database.apply_learning(
        {"supplier_name": "ABC Oils", "invoice_number": "INV10I"}, learning_config()
    )
    assert corrected["invoice_number"] == "INV10I"
    assert applications == []

