import io

import pandas as pd

from modules.excel import SAPDataIndex, apply_mapping, read_excel_bytes, resolve_supplier_identity, suggest_mapping


def test_mapping_and_index():
    source = pd.DataFrame([{"CardName": "ABC Oils", "NumAtCard": "INV1", "DocTotal": 1180}])
    mapping = suggest_mapping(list(source.columns))
    mapped = apply_mapping(source, mapping)
    index = SAPDataIndex(mapped)
    assert mapping["supplier_name"] == "CardName"
    assert len(index.by_invoice["INV1"]) == 1


def test_excel_read_once_compatible():
    data = io.BytesIO()
    pd.DataFrame([{"Supplier": "A"}]).to_excel(data, index=False)
    assert len(read_excel_bytes(data.getvalue())) == 1


def test_learning_supplier_identity_uses_exact_tin():
    frame = pd.DataFrame([{"supplier_name": "ABC Oils (Pvt) Ltd", "supplier_tin": "123456789"}])
    identity = resolve_supplier_identity({"supplier_name": "ABC OiIs Pvt Ltd", "supplier_tin": "123456789"}, SAPDataIndex(frame))
    assert identity == "ABC Oils (Pvt) Ltd"
