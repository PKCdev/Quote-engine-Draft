from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import csv


def _to_float(x: str) -> float:
    x = (x or "").strip().replace(",", "")
    try:
        return float(x)
    except Exception:
        return 0.0


@dataclass
class ProductRow:
    item: str
    room: str
    description: str
    qty: int
    width_mm: float
    height_mm: float
    depth_mm: float

    @property
    def area_m2(self) -> float:
        return (self.width_mm * self.height_mm) / 1_000_000.0


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def parse(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {"products": []}

    # Heuristic: detect Delivery Product Check List format (multi-line header with specific tokens)
    with open(path, newline="", encoding="utf-8") as f:
        sample = f.read(2048)
    is_delivery = ("Delivery Product Check List" in sample) and ("Product Name" in sample)

    rows: List[ProductRow] = []
    if is_delivery:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header_row = None
            header_idx = -1
            current_room = None
            for i, row in enumerate(reader):
                # Track room context
                if row and isinstance(row[0], str) and row[0].strip().startswith("Room Name:"):
                    try:
                        current_room = row[0].split(":", 1)[1].strip()
                    except Exception:
                        current_room = row[0].strip()
                # Find header row containing required tokens
                joined = ",".join(row)
                if ("Product Name" in joined) and ("Item Number" in joined) and ("Width" in joined):
                    header_row = row
                    header_idx = i
                    break
            if header_row is None:
                # fallback to generic parser
                pass
            else:
                # Map important columns by name -> index
                def index_of(name: str) -> int:
                    for idx, val in enumerate(header_row):
                        if (val or "").strip().lower() == name.lower():
                            return idx
                    return -1

                idx_qty = index_of("Qty")
                idx_name = index_of("Product Name")
                idx_item = index_of("Item Number")
                idx_w = index_of("Width")
                idx_h = index_of("Height")
                idx_d = index_of("Depth")

                # Continue reading data rows
                with open(path, newline="", encoding="utf-8") as f2:
                    reader2 = csv.reader(f2)
                    for j, row in enumerate(reader2):
                        if j <= header_idx:
                            continue
                        if not row or all(not (c or "").strip() for c in row):
                            continue
                        # Skip recurring summary/placeholder lines like "0×0×0 0.17 0.08"
                        joined_lower = ",".join((c or "") for c in row).strip().lower()
                        if "0×0×0" in joined_lower or "0x0x0" in joined_lower:
                            continue
                        # Reset room context if a new "Room Name:" line occurs
                        if row and isinstance(row[0], str) and row[0].strip().startswith("Room Name:"):
                            try:
                                current_room = row[0].split(":", 1)[1].strip()
                            except Exception:
                                current_room = row[0].strip()
                            continue
                        name = (row[idx_name] if 0 <= idx_name < len(row) else "").strip()
                        item = (row[idx_item] if 0 <= idx_item < len(row) else "").strip().lstrip("#")
                        qty = _to_float(row[idx_qty] if 0 <= idx_qty < len(row) else "0")
                        if not name:
                            continue
                        width = _to_float(row[idx_w] if 0 <= idx_w < len(row) else "0")
                        height = _to_float(row[idx_h] if 0 <= idx_h < len(row) else "0")
                        depth = _to_float(row[idx_d] if 0 <= idx_d < len(row) else "0")
                        # Skip if all dimensions are zero (placeholder rows)
                        if not any([width, height, depth]):
                            continue
                        rows.append(
                            ProductRow(
                                item=item,
                                room=current_room or "",
                                description=name,
                                qty=int(qty or 1),
                                width_mm=width,
                                height_mm=height,
                                depth_mm=depth,
                            )
                        )

    if not rows:
        # Fallback to generic DictReader for standard Product List CSV
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = {h: _norm(h) for h in reader.fieldnames or []}
            inv = {v: k for k, v in headers.items()}

            def get(row: Dict[str, str], key_variants: List[str]) -> str:
                for k in key_variants:
                    if k in inv:
                        return row.get(inv[k], "")
                return ""

            for row in reader:
                rows.append(
                    ProductRow(
                        item=get(row, ["item", "itemnumber", "productcode", "code"]),
                        room=get(row, ["roomname", "room"]),
                        description=get(row, ["description", "productname", "product", "name"]),
                        qty=int(_to_float(get(row, ["quantity", "qty", "qty.", "q"])) or 1),
                        width_mm=_to_float(get(row, ["width", "w"])),
                        height_mm=_to_float(get(row, ["height", "h"])),
                        depth_mm=_to_float(get(row, ["depth", "d"])),
                    )
                )

    return {
        "products": [
            {
                **r.__dict__,
                "area_m2": round(r.area_m2, 4),
            }
            for r in rows
        ]
    }
