from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import csv


@dataclass
class PartRow:
    room: str
    product: str
    part_name: str
    qty: int
    width_mm: float
    length_mm: float
    material: str
    eb_flags: Dict[str, str]
    machine_flags: Dict[str, bool]


def _to_float(x: str) -> float:
    x = (x or "").strip().replace(",", "")
    try:
        return float(x)
    except Exception:
        return 0.0


def parse(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {"parts": [], "summary": {}}
    parts: List[PartRow] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parts.append(
                PartRow(
                    room=row.get("Room Name", ""),
                    product=row.get("Product Name", ""),
                    part_name=row.get("Part Name", ""),
                    qty=int(_to_float(row.get("Quantity", "0"))),
                    width_mm=_to_float(row.get("Width", "0")),
                    length_mm=_to_float(row.get("Length", "0")),
                    material=row.get("Material Name", ""),
                    eb_flags={
                        "EBW1": row.get("EB Width 1", ""),
                        "EBL1": row.get("EB Length 1", ""),
                        "EBW2": row.get("EB Width 2", ""),
                        "EBL2": row.get("EB Length 2", ""),
                    },
                    machine_flags={
                        "Weeke": bool(row.get("Weeke")),
                        "SinglePart": bool(row.get("Weeke Single Part")),
                    },
                )
            )

    # Derive summary
    total_parts = sum(p.qty for p in parts)
    total_perimeter_m = sum(
        p.qty * (2 * (p.width_mm + p.length_mm)) / 1000.0 for p in parts
    )
    total_edged_parts = sum(
        p.qty for p in parts if any(v for v in p.eb_flags.values())
    )
    return {
        "parts": [p.__dict__ for p in parts],
        "summary": {
            "total_parts": total_parts,
            "total_perimeter_m": round(total_perimeter_m, 3),
            "total_edged_parts": total_edged_parts,
        },
    }

