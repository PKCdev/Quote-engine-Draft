from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


def _detect_header(df) -> int:
    # choose row with most non-null in first 30 rows
    head = df.iloc[:30].notna().sum(axis=1)
    return int(head.idxmax())


def _to_int(x) -> int:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return 0
        # handle strings like "1"
        return int(float(x))
    except Exception:
        return 0


def _to_float(x) -> float:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def parse(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {"buyouts": []}
    xls = pd.read_excel(path, sheet_name=0, header=None)
    header_row = _detect_header(xls)
    cols = [str(c).strip() if pd.notna(c) else "" for c in xls.iloc[header_row].tolist()]
    data = xls.iloc[header_row + 1 :].copy()
    data.columns = cols

    # Normalize common headers
    colmap = {}
    for c in data.columns:
        lc = c.lower()
        if "qty" in lc and "unit" not in lc:
            colmap[c] = "qty"
        elif "desc" in lc:
            colmap[c] = "description"
        elif "length" in lc:
            colmap[c] = "length"
        elif "width" in lc:
            colmap[c] = "width"
        elif "height" in lc:
            colmap[c] = "height"
        elif "material" in lc:
            colmap[c] = "material"
    data = data.rename(columns=colmap)

    records: List[Dict[str, Any]] = []
    for _, row in data.iterrows():
        raw_desc = row.get("description", None)
        # Skip empty/NaN descriptions
        if raw_desc is None or (isinstance(raw_desc, float) and pd.isna(raw_desc)):
            continue
        desc = str(raw_desc).strip()
        if not desc or desc.lower() == "nan":
            continue

        rec = {
            "description": desc,
            "qty": _to_int(row.get("qty", 0)),
            "length": _to_float(row.get("length", 0)),
            "width": _to_float(row.get("width", 0)),
            "height": _to_float(row.get("height", 0)),
            "material": str(row.get("material", "") or "").strip(),
        }
        records.append(rec)
    return {"buyouts": records}
