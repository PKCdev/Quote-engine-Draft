from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Dict, Optional, List, Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from urllib.parse import quote_plus, urlparse

from ..importers import wos_xlsx, parts_csv, products_csv, buyout_xlsx
from ..calculators import (
    materials as calc_materials,
    edgeband as calc_edgeband,
    hardware as calc_hardware,
    cnc as calc_cnc,
    assembly as calc_assembly,
    install as calc_install,
    overhead as calc_overhead,
    pricing as calc_pricing,
)

import yaml


BASE_DIR = Path(__file__).resolve().parents[2]
PROJECTS_DIR = BASE_DIR / "projects"
CONFIGS_DIR = BASE_DIR / "configs"
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="PKC Quote Engine UI")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Add a simple urlencode filter for links with query params
templates.env.filters["urlencode"] = lambda v: quote_plus(str(v)) if v is not None else ""

# Serve static assets if present
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _load_yaml(path: Path) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def _resolve_project_slug(request: Request) -> Optional[str]:
    slug = request.query_params.get("project") if hasattr(request, "query_params") else None
    if slug:
        return slug
    path_params = getattr(request, "path_params", None)
    if isinstance(path_params, dict):
        slug = path_params.get("slug")
        if slug:
            return slug
    referer = request.headers.get("referer") if hasattr(request, "headers") else None
    if referer:
        try:
            path = urlparse(referer).path
        except Exception:
            path = ""
        match = re.match(r"/projects/([^/]+)", path or "")
        if match:
            return match.group(1)
    return None


def _safe_redirect(target: Optional[str], fallback: str) -> str:
    if not target:
        return fallback
    try:
        parsed = urlparse(target)
    except Exception:
        return fallback
    if parsed.scheme or parsed.netloc:
        return fallback
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path.lstrip("/")
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if parsed.fragment:
        path = f"{path}#{parsed.fragment}"
    return path


def _ensure_projects_dir() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_overhead_config() -> Dict[str, Any]:
    path = CONFIGS_DIR / "overhead.yaml"
    if not path.exists():
        return {"internal_hours": 160, "categories": {}}
    data = _load_yaml(path)
    data.setdefault("internal_hours", 160)
    data.setdefault("categories", {})
    for cat in data["categories"].values():
        cat.setdefault("items", [])
        for item in cat["items"]:
            item.setdefault("optional", False)
            item.setdefault("enabled", True)
            item.setdefault("notes", "")
            item.setdefault("monthly_aud", 0.0)
    return data


def _calculate_overhead_summary(oh_cfg: Dict[str, Any]) -> Dict[str, Any]:
    categories = oh_cfg.get("categories", {})
    summary_categories: Dict[str, Any] = {}
    enabled_total = 0.0
    optional_enabled_total = 0.0
    optional_possible_total = 0.0

    for key, cat in categories.items():
        cat_total_enabled = 0.0
        cat_total_all = 0.0
        items = []
        for item in cat.get("items", []):
            monthly = float(item.get("monthly_aud", 0.0) or 0.0)
            enabled = bool(item.get("enabled", True)) if item.get("optional") else True
            optional = bool(item.get("optional", False))
            if enabled:
                enabled_total += monthly
                cat_total_enabled += monthly
            cat_total_all += monthly
            if optional:
                optional_possible_total += monthly
                if enabled:
                    optional_enabled_total += monthly
            items.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "monthly_aud": round(monthly, 2),
                    "notes": item.get("notes", ""),
                    "optional": optional,
                    "enabled": enabled,
                }
            )
        summary_categories[key] = {
            "label": cat.get("label", key.title()),
            "items": items,
            "enabled_total": round(cat_total_enabled, 2),
            "all_total": round(cat_total_all, 2),
        }

    total_all = 0.0
    for cat in categories.values():
        for item in cat.get("items", []):
            total_all += float(item.get("monthly_aud", 0.0) or 0.0)

    return {
        "categories": summary_categories,
        "enabled_total": round(enabled_total, 2),
        "all_total": round(total_all, 2),
        "optional_enabled_total": round(optional_enabled_total, 2),
        "optional_possible_total": round(optional_possible_total, 2),
        "internal_hours": float(oh_cfg.get("internal_hours", 160) or 160),
    }


def _sync_overhead_into_rates(total_monthly: float, internal_hours: float) -> None:
    path = CONFIGS_DIR / "rates.yaml"
    data = _load_yaml(path)
    oh = data.setdefault("overhead", {})
    oh["monthly_aud"] = float(round(total_monthly, 2))
    oh["internal_hours"] = float(round(internal_hours, 2))
    _save_yaml(path, data)


def _overhead_redirect(project: Optional[str] = None) -> RedirectResponse:
    url = "/overhead"
    if project:
        url = f"{url}?project={project}"
    return RedirectResponse(url=url, status_code=303)


def _recompute_project(slug: Optional[str]) -> None:
    if not slug:
        return
    proj_dir = PROJECTS_DIR / slug
    if proj_dir.exists():
        _compute_project(proj_dir)


def _slugify(name: str) -> str:
    s = name.strip().lower().replace(" ", "-")
    return "".join(ch for ch in s if ch.isalnum() or ch in "-_")[:60] or "project"


def _find_file(project_dir: Path, keyword: str, exts=(".xlsx", ".xlsm", ".csv")) -> Optional[Path]:
    for p in project_dir.iterdir():
        if p.suffix.lower() in exts and keyword.lower() in p.name.lower():
            return p
    return None


def _deep_update(dst: Dict, src: Dict):
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def _compute_project(project_dir: Path, overrides: Optional[Dict] = None) -> Dict:
    # Load configs
    rates = _load_yaml(CONFIGS_DIR / "rates.yaml")
    policy = _load_yaml(CONFIGS_DIR / "policy.yaml")
    materials_cat = _load_yaml(CONFIGS_DIR / "materials.yaml")
    materials_attr = _load_yaml(CONFIGS_DIR / "materials_pricing.yaml") if (CONFIGS_DIR / "materials_pricing.yaml").exists() else {}
    bands_cat = _load_yaml(CONFIGS_DIR / "edgeband.yaml")
    hardware_cat = _load_yaml(CONFIGS_DIR / "hardware.yaml")
    hardware_aliases = _load_yaml(CONFIGS_DIR / "hardware_aliases.yaml") if (CONFIGS_DIR / "hardware_aliases.yaml").exists() else {}
    assembly_rules = _load_yaml(CONFIGS_DIR / "assembly_rules.yaml")

    # Load persisted overrides if not provided
    ov_path = project_dir / "overrides.json"
    file_overrides: Dict = {}
    if ov_path.exists():
        try:
            file_overrides = json.loads(ov_path.read_text(encoding="utf-8")) or {}
        except Exception:
            file_overrides = {}
    # Merge overrides into configs
    overhead_cfg = _load_overhead_config()
    overhead_summary = _calculate_overhead_summary(overhead_cfg)
    rates.setdefault("overhead", {})["monthly_aud"] = overhead_summary["enabled_total"]
    rates.setdefault("overhead", {})["internal_hours"] = overhead_summary["internal_hours"]

    overrides = overrides or file_overrides or {}
    if "defaults" in overrides:
        rates.setdefault("defaults", {}).update(overrides["defaults"])
    if "extra_sheet_waste" in overrides:
        policy["extra_sheet_waste"] = overrides["extra_sheet_waste"]
    # Deep merge rates/policy/assembly_rules overrides if present
    if overrides.get("rates"):
        _deep_update(rates, overrides["rates"])
    if overrides.get("policy"):
        _deep_update(policy, overrides["policy"])
    if overrides.get("assembly_rules"):
        _deep_update(assembly_rules, overrides["assembly_rules"])
    # Enforce overhead from central config even if overrides tried to adjust it
    rates.setdefault("overhead", {})["monthly_aud"] = overhead_summary["enabled_total"]
    rates["overhead"]["internal_hours"] = overhead_summary["internal_hours"]

    # Locate inputs (support flexible names)
    wos = _find_file(project_dir, "Work Order Summary") or project_dir / "WorkOrderSummary.xlsx"
    parts = _find_file(project_dir, "Processing Station Parts") or project_dir / "ProcessingStationParts.csv"
    # Accept either Product List or Delivery Product Check List
    products = (
        _find_file(project_dir, "Product List")
        or _find_file(project_dir, "Delivery Product Check List")
        or project_dir / "ProductList.csv"
    )
    buyout = _find_file(project_dir, "Buyout") or project_dir / "Buyout.xlsx"

    wos_data = wos_xlsx.parse(wos if wos.exists() else None)
    parts_data = parts_csv.parse(parts if parts.exists() else None)
    products_data = products_csv.parse(products if products.exists() else None)
    if isinstance(products_data, dict):
        products_data["hardware"] = wos_data.get("hardware", [])
    buyout_data = buyout_xlsx.parse(buyout if buyout.exists() else None)

    mat_bd = calc_materials.breakdown(wos_data.get("sheets", []), materials_cat, policy, materials_attr)
    # Augment material breakdown with edgeband LM per material (best-effort mapping by spec phrase → material name)
    def _eb_phrase(spec: str) -> str:
        s = (spec or "").strip()
        parts = []
        for w in s.split():
            wl = w.lower()
            if "mm" in wl:
                continue
            if any(ch.isdigit() for ch in w):
                continue
            if w.lower() in {"x", "\u00d7"}:
                continue
            parts.append(w)
        return " ".join(parts)

    eb_map: Dict[str, float] = {}
    for b in (wos_data.get("edgeband", []) or []):
        phrase = _eb_phrase(b.get("spec", ""))
        lm = float(b.get("lm", 0.0) or 0.0)
        if not phrase:
            continue
        pl = phrase.lower()
        # Attribute LM to any material whose name contains the phrase
        for s in (wos_data.get("sheets", []) or []):
            mat = str(s.get("material", ""))
            if pl in mat.lower():
                eb_map[mat] = eb_map.get(mat, 0.0) + lm

    eb_total = 0.0
    for it in mat_bd.get("items", []):
        lm = round(float(eb_map.get(it.get("material"), 0.0)), 2)
        it["eb_lm"] = lm
        eb_total += lm
    mat_bd["eb_lm_total"] = round(eb_total, 2)
    mat_cost = mat_bd["total"]
    eb_result = calc_edgeband.compute(wos_data.get("edgeband", []), bands_cat, policy, rates)
    hw_cost = calc_hardware.compute(wos_data.get("hardware", []), hardware_cat, hardware_aliases)
    cnc_result = calc_cnc.estimate_from_materials(mat_bd, rates, (overrides or {}).get("materials", {}))
    product_overrides = (overrides or {}).get("products", {})
    asm_result = calc_assembly.estimate(products_data, assembly_rules, rates, product_overrides)
    inst_result = calc_install.estimate(products_data, rates, assembly_rules, product_overrides)
    # Finger Pull (FP) estimation
    def _count_fp(products: Dict[str, Any], base_only: bool = True) -> Dict[str, int]:
        import re as _re
        doors = 0
        drawers = 0
        for p in (products.get("products", []) or []):
            desc = str(p.get("description", "") or "")
            if base_only and not desc.lower().startswith("base"):
                continue
            m_draw = _re.search(r"base\s+(\d+)\s+drawer", desc, flags=_re.IGNORECASE)
            if m_draw:
                try:
                    drawers += int(m_draw.group(1))
                except Exception:
                    pass
            m_door = _re.search(r"base\s+(\d+)\s+door", desc, flags=_re.IGNORECASE)
            if m_door:
                try:
                    doors += int(m_door.group(1))
                except Exception:
                    pass
        return {"doors": doors, "drawers": drawers}

    fp_defaults = (rates.get("finger_pull", {}) or {})
    fp_ov = (overrides or {}).get("finger_pull", {})
    fp_apply_doors = bool(fp_ov.get("apply_doors", True))
    fp_apply_drawers = bool(fp_ov.get("apply_drawers", True))
    fp_base_only = bool(fp_ov.get("base_only", True))
    per_part_fee = float(fp_ov.get("per_part_fee", fp_defaults.get("per_part_fee", 15.5)) or 15.5)
    pickup_fee = float(fp_ov.get("pickup_fee", fp_defaults.get("pickup_fee", 35.0)) or 35.0)
    computed_fp = _count_fp(products_data, base_only=fp_base_only)
    doors_count = int(fp_ov.get("override_doors", computed_fp.get("doors", 0)) or 0)
    drawers_count = int(fp_ov.get("override_drawers", computed_fp.get("drawers", 0)) or 0)
    doors_count = max(0, doors_count - int(fp_ov.get("subtract_doors", 0) or 0))
    drawers_count = max(0, drawers_count - int(fp_ov.get("subtract_drawers", 0) or 0))
    applied_parts = (doors_count if fp_apply_doors else 0) + (drawers_count if fp_apply_drawers else 0)
    fp_cost = (applied_parts * per_part_fee) + (pickup_fee if applied_parts > 0 else 0.0)
    # Add assembly/install time for FP
    fp_asm_min = float(fp_defaults.get("assembly_minutes_per_part", 10) or 10)
    fp_inst_min = float(fp_defaults.get("install_minutes_per_part", 2) or 2)
    fp_asm_hours = (applied_parts * fp_asm_min) / 60.0
    fp_inst_hours = (applied_parts * fp_inst_min) / 60.0
    # Increase assembly result
    try:
        shop_rate = float((rates.get("labor_rates", {}) or {}).get("shop", 110) or 110)
    except Exception:
        shop_rate = 110.0
    asm_result["hours"] = round(float(asm_result.get("hours", 0.0) or 0.0) + fp_asm_hours, 2)
    asm_result["cost"] = round(float(asm_result.get("cost", 0.0) or 0.0) + fp_asm_hours * shop_rate, 2)
    # Increase install result using blended rate
    blended_rate = float(inst_result.get("blended_rate", 0.0) or 0.0)
    if not blended_rate:
        tpf = float((rates.get("install_team", {}) or {}).get("two_person_fraction", 0.8) or 0.8)
        opf = float((rates.get("install_team", {}) or {}).get("one_person_fraction", 0.2) or 0.2)
        tr = float((rates.get("install_team", {}) or {}).get("two_person_rate", 190) or 190)
        or1 = float((rates.get("install_team", {}) or {}).get("one_person_rate", (rates.get("labor_rates", {}) or {}).get("installer_billed", 95)) or 95)
        # normalize
        s = max(tpf + opf, 0.0001)
        tpf, opf = tpf / s, opf / s
        blended_rate = tpf * tr + opf * or1
    inst_result["hours"] = round(float(inst_result.get("hours", 0.0) or 0.0) + fp_inst_hours, 2)
    inst_result["cost"] = round(float(inst_result.get("cost", 0.0) or 0.0) + fp_inst_hours * blended_rate, 2)
    # Loading & Delivery estimation from products CBM
    def _prod_volume_m3(p: Dict[str, Any]) -> float:
        try:
            w = float(p.get("width_mm", 0) or 0) / 1000.0
            h = float(p.get("height_mm", 0) or 0) / 1000.0
            d = float(p.get("depth_mm", 0) or 0) / 1000.0
            v = max(w, 0.0) * max(h, 0.0) * max(d, 0.0)
            q = int(p.get("qty", 1) or 1)
            return v * q
        except Exception:
            return 0.0

    pov = (overrides or {}).get("products", {})
    total_cbm = 0.0
    for p in (products_data.get("products", []) or []):
        item_id = p.get("item")
        ov = pov.get(item_id, {}) if item_id else {}
        if ov.get("exclude"):
            continue
        total_cbm += _prod_volume_m3(p)
    delivery_cfg = (rates.get("delivery", {}) or {})
    cap = float(delivery_cfg.get("truck_capacity_cbm", 15.0) or 15.0)
    load_full_h = float(delivery_cfg.get("load_hours_per_full", 3.0) or 3.0)
    unload_h = float(delivery_cfg.get("unload_hours_per_trip", 0.5) or 0.5)
    travel_h = float(delivery_cfg.get("travel_hours_per_trip", 1.0) or 1.0)
    admin_h = float(delivery_cfg.get("rental_admin_hours", 1.0) or 1.0)
    scale_load = bool(delivery_cfg.get("scale_load_with_fill", True))
    trips = int((total_cbm / cap) + (0 if total_cbm % cap == 0 else 1)) if cap > 0 else 0
    load_h = (total_cbm / cap) * load_full_h if (scale_load and cap > 0) else (trips * load_full_h)
    unload_total_h = trips * unload_h
    travel_total_h = trips * travel_h
    admin_total_h = admin_h
    delivery_hours = load_h + unload_total_h + travel_total_h + admin_total_h
    # Cost using handling rate by default (or explicit delivery_rate)
    delivery_rate = float(delivery_cfg.get("delivery_rate", rates.get("labor_rates", {}).get("handling", 110)) or 110)
    delivery_cost = round(delivery_hours * delivery_rate, 2)
    delivery_result = {
        "cbm": round(total_cbm, 3),
        "capacity_cbm": cap,
        "trips": trips,
        "hours": {
            "load": round(load_h, 2),
            "unload": round(unload_total_h, 2),
            "travel": round(travel_total_h, 2),
            "admin": round(admin_total_h, 2),
            "total": round(delivery_hours, 2),
        },
        "rate": delivery_rate,
        "cost": delivery_cost,
    }
    totals = {
        "materials": mat_cost,
        "edgeband": eb_result.get("cost", 0.0) or 0.0,
        "hardware": hw_cost,
        "cnc": cnc_result["cnc"]["cost"],
        "panel_saw": cnc_result["panel_saw"]["cost"],
        "assembly": asm_result["cost"],
        "install": inst_result["cost"],
        "finger_pull": round(fp_cost, 2),
        "delivery": delivery_result["cost"],
    }
    oh_cost = calc_overhead.allocate(
        drafting_hours=rates.get("defaults", {}).get("drafting_hours", 10),
        pm_hours=rates.get("defaults", {}).get("pm_hours", 10),
        assembly_hours=asm_result.get("hours", 0.0),
        rates=rates,
    )
    totals["overhead"] = oh_cost

    # Prepare edgeband summary (by spec) for display
    # Use per-edge model from parts for time; subtract one edge per FP part; add machine cost into totals
    eb_hours_parts = calc_edgeband.time_from_parts(parts_data, rates)
    minutes_per_edge = float(((rates.get("edgeband", {}) or {}).get("minutes_per_edge", 1.0)) or 1.0)
    fp_edge_count = applied_parts
    eb_result["hours"] = round(max(0.0, eb_hours_parts - (fp_edge_count * minutes_per_edge) / 60.0), 2)
    try:
        edb_rate = float((rates.get("machine_rental", {}) or {}).get("edgebander", (rates.get("machine_rates", {}) or {}).get("edgebander", 0.0)) or 0.0)
    except Exception:
        edb_rate = 0.0
    edgeband_machine_cost = round(float(eb_result.get("hours", 0.0) or 0.0) * edb_rate, 2)
    totals["edgeband"] = round((totals.get("edgeband", 0.0) or 0.0) + edgeband_machine_cost, 2)
    # Recompute price with updated edgeband total including machine cost
    price_summary = calc_pricing.price(totals, rates, policy)
    eb_items = eb_result.get("items", [])
    eb_total_lm = sum(i.get("lm", 0.0) for i in eb_items)
    eb_total_cost = (eb_result.get("cost", 0.0) or 0.0) + edgeband_machine_cost
    eb_summary = {"items": eb_items, "total_lm": round(eb_total_lm, 2), "total_cost": round(eb_total_cost, 2), "machine_cost": edgeband_machine_cost}

    # Add allowances and surcharges into totals before pricing, so client Price lines add up
    allow_flat = float(((policy.get("allowances", {}) or {}).get("flat_aud_ex_gst", 0.0)) or 0.0)
    if allow_flat:
        totals["allowances"] = round(allow_flat, 2)
    sc = (policy.get("surcharges", {}) or {})
    surcharge_pct = float(sc.get("warranty_percent", 0.0) or 0.0) + float(sc.get("contingency_percent", 0.0) or 0.0) + float(sc.get("merchant_percent", 0.0) or 0.0)
    surcharge_cost = 0.0
    if surcharge_pct:
        base_for_sc = sum(float(v or 0.0) for v in totals.values())
        surcharge_cost = round(base_for_sc * (surcharge_pct / 100.0), 2)

    # Final pricing: compute margin on core totals, then add pass-through surcharge
    price_summary = calc_pricing.price(totals, rates, policy)
    if surcharge_cost:
        totals["surcharges"] = surcharge_cost
        price_summary["subtotal_ex_gst"] = round(price_summary["subtotal_ex_gst"] + surcharge_cost, 2)
        price_summary["price_ex_gst"] = round(price_summary["price_ex_gst"] + surcharge_cost, 2)
        gst_rate = float(rates.get("taxes", {}).get("gst", 0.10))
        price_summary["gst"] = round(price_summary["price_ex_gst"] * gst_rate, 2)
        price_summary["total_inc_gst"] = round(price_summary["price_ex_gst"] + price_summary["gst"], 2)
    else:
        totals.pop("surcharges", None)

    out_dir = project_dir / "out"
    out_dir.mkdir(exist_ok=True)
    # Save internal JSON
    (out_dir / "internal_breakdown.json").write_text(
        json.dumps(
            {
                "totals": totals,
                "price": price_summary,
                "overrides": overrides,
                "wos": wos_data,
                "parts": parts_data,
                "products": products_data,
                "buyout": buyout_data,
                "assembly": asm_result,
                "install": inst_result,
                "overhead_summary": overhead_summary,
                "materials_breakdown": mat_bd,
                "edgeband_summary": eb_summary,
                "time": {
                    "cnc": cnc_result["cnc"]["hours"],
                    "panel_saw": cnc_result["panel_saw"]["hours"],
                    "edgeband": eb_result["hours"],
                    "assembly": asm_result["hours"],
                    "install": inst_result["hours"],
                    "delivery": delivery_result["hours"]["total"],
                },
                "delivery": delivery_result,
                "finger_pull": {
                    "computed": computed_fp,
                    "applied": {"doors": (doors_count if fp_apply_doors else 0), "drawers": (drawers_count if fp_apply_drawers else 0)},
                    "per_part_fee": per_part_fee,
                    "pickup_fee": pickup_fee if applied_parts > 0 else 0.0,
                    "total_parts": applied_parts,
                    "cost": round(fp_cost, 2),
                    "edges_removed": fp_edge_count,
                    "asm_hours": round(fp_asm_hours, 2),
                    "inst_hours": round(fp_inst_hours, 2),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Render client HTML
    from ..output.exporters.html import render_client_quote

    # Optional meta including client/company
    meta = _read_meta(project_dir)
    # Render client HTML (Cambridge-style)
    html = render_client_quote(
        project_name=project_dir.name,
        materials=wos_data.get("sheets", []),
        edgeband=wos_data.get("edgeband", []),
        hardware=wos_data.get("hardware", []),
        assembly=asm_result,
        install=inst_result,
        price=price_summary,
        rates=rates,
        policy=policy,
        category_totals=totals,
        materials_breakdown=mat_bd,
        products=products_data,
        buyout=buyout_data,
        hardware_catalog=hardware_cat,
        hardware_aliases=hardware_aliases,
        meta=meta,
    )
    (out_dir / "quote.html").write_text(html, encoding="utf-8")

    return {"totals": totals, "price": price_summary, "asm": asm_result, "inst": inst_result}


def _read_meta(project_dir: Path) -> Dict:
    meta_path = project_dir / "meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    # Fallback: minimal meta with company from configs if present
    company_cfg = {}
    try:
        comp_path = CONFIGS_DIR / "company.yaml"
        if comp_path.exists():
            with open(comp_path, "r", encoding="utf-8") as f:
                import yaml as _yaml

                company_cfg = _yaml.safe_load(f) or {}
    except Exception:
        company_cfg = {}
    return {"name": project_dir.name, "company": company_cfg}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    _ensure_projects_dir()
    projects = []
    for d in sorted(PROJECTS_DIR.glob("*")):
        if d.is_dir():
            meta = _read_meta(d)
            has_quote = (d / "out" / "quote.html").exists()
            projects.append({"slug": d.name, "name": meta.get("name", d.name), "has_quote": has_quote})
    return templates.TemplateResponse("dashboard.html", {"request": request, "projects": projects})


@app.get("/projects/new", response_class=HTMLResponse)
def new_project_form(request: Request):
    return templates.TemplateResponse("new_project.html", {"request": request})


@app.post("/projects/new")
async def create_project(
    request: Request,
    name: str = Form(...),
    wos: UploadFile = File(...),
    parts: UploadFile = File(...),
    products: UploadFile = File(...),
    buyout: Optional[UploadFile] = File(None),
):
    _ensure_projects_dir()
    slug = _slugify(name)
    proj_dir = PROJECTS_DIR / slug
    if proj_dir.exists():
        # Avoid accidental overwrite by appending a suffix
        idx = 2
        while (PROJECTS_DIR / f"{slug}-{idx}").exists():
            idx += 1
        slug = f"{slug}-{idx}"
        proj_dir = PROJECTS_DIR / slug
    proj_dir.mkdir(parents=True)

    # Save files with stable names
    def save_up(upload: UploadFile, filename: str):
        with open(proj_dir / filename, "wb") as f:
            shutil.copyfileobj(upload.file, f)

    save_up(wos, "WorkOrderSummary.xlsx")
    save_up(parts, "ProcessingStationParts.csv")
    save_up(products, "ProductList.csv")
    if buyout is not None:
        save_up(buyout, "Buyout.xlsx")

    # Save meta
    (proj_dir / "meta.json").write_text(json.dumps({"name": name}), encoding="utf-8")

    # Compute once with defaults
    _compute_project(proj_dir)

    return RedirectResponse(url=f"/projects/{slug}", status_code=303)


@app.get("/projects/{slug}", response_class=HTMLResponse)
def view_project(request: Request, slug: str):
    proj_dir = PROJECTS_DIR / slug
    if not proj_dir.exists():
        return HTMLResponse("Project not found", status_code=404)
    meta = _read_meta(proj_dir)
    summary_path = proj_dir / "out" / "internal_breakdown.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    # Auto-heal: if older project lacks new fields, recompute once
    if (
        not summary
        or "assembly" not in summary
        or "price" not in summary
        or "totals" not in summary
        or "materials_breakdown" not in summary
        or "edgeband_summary" not in summary
    ):
        _compute_project(proj_dir)
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
    # Ensure new fields (delivery) exist; recompute if missing
    if summary and ("delivery" not in summary or "delivery" not in summary.get("totals", {})):
        _compute_project(proj_dir)
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
    # Compute unknown catalog items for convenience
    materials_cat = _load_yaml(CONFIGS_DIR / "materials.yaml")
    hardware_cat = _load_yaml(CONFIGS_DIR / "hardware.yaml")
    hardware_aliases = _load_yaml(CONFIGS_DIR / "hardware_aliases.yaml") if (CONFIGS_DIR / "hardware_aliases.yaml").exists() else {}
    unknown_materials = []
    for s in summary.get("wos", {}).get("sheets", []):
        name = (s.get("material") or "").strip()
        if name and name not in materials_cat and name not in unknown_materials:
            unknown_materials.append(name)
    unknown_hardware = []
    for h in summary.get("wos", {}).get("hardware", []):
        name = (h.get("description") or "").strip()
        # Respect existing aliases
        if name in hardware_aliases:
            continue
        if name and name not in hardware_cat and name not in unknown_hardware:
            unknown_hardware.append(name)

    # Suggestions for unknown hardware via fuzzy matching
    suggestions = {}
    if unknown_hardware:
        from ..normalize.hardware import best_matches
        cat_keys = list(hardware_cat.keys())
        for uh in unknown_hardware:
            if cat_keys:
                matches = best_matches(uh, cat_keys, limit=3)
            else:
                matches = []
            enriched = [
                {
                    "key": k,
                    "score": score,
                    "price": hardware_cat.get(k, {}).get("unit_price_aud_ex_gst"),
                }
                for k, score in matches
            ]
            suggestions[uh] = enriched
    # Unknown edgeband specs (not in catalog)
    eb_cat = _load_yaml(CONFIGS_DIR / "edgeband.yaml")
    known_specs = set(eb_cat.keys())
    unknown_eb = []
    for b in summary.get("wos", {}).get("edgeband", []) or []:
        spec = (b.get("spec") or "").strip()
        if spec and spec not in known_specs and spec not in unknown_eb:
            unknown_eb.append(spec)

    # Build hardware summary with pricing for display
    hw_items_src = summary.get("wos", {}).get("hardware", []) or []
    hw_items: List[Dict] = []
    hw_total = 0.0
    for h in hw_items_src:
        desc = (h.get("description") or "").strip()
        qty = int(h.get("qty", 0) or 0)
        key = hardware_aliases.get(desc, desc)
        cat = hardware_cat.get(key, {})
        unit = float(cat.get("unit_price_aud_ex_gst", 0.0) or 0.0)
        pack = int(cat.get("pack_size", 1) or 1)
        if pack <= 0:
            pack = 1
        packs = (qty + pack - 1) // pack if qty > 0 else 0
        line_total = unit * pack * packs
        hw_total += line_total
        hw_items.append({
            "key": key,
            "description": desc,
            "qty": qty,
            "unit_price": round(unit, 2),
            "pack_size": pack,
            "packs": packs,
            "line_total": round(line_total, 2),
        })
    hardware_summary = {"items": hw_items, "total": round(hw_total, 2)}

    # Build merged views for rates and policy so Advanced Tuning reflects persisted overrides
    base_rates = _load_yaml(CONFIGS_DIR / "rates.yaml")
    base_policy = _load_yaml(CONFIGS_DIR / "policy.yaml")
    # Merge persisted file overrides first (source of truth), then any in-memory summary overrides
    ov_path = proj_dir / "overrides.json"
    file_ov: Dict = {}
    if ov_path.exists():
        try:
            file_ov = json.loads(ov_path.read_text(encoding="utf-8")) or {}
        except Exception:
            file_ov = {}
    _deep_update(base_rates, (file_ov.get("rates") or {}))
    _deep_update(base_policy, (file_ov.get("policy") or {}))
    _deep_update(base_rates, (summary.get("overrides", {}) or {}).get("rates", {}))
    _deep_update(base_policy, (summary.get("overrides", {}) or {}).get("policy", {}))
    overhead_cfg = _load_overhead_config()
    overhead_summary = _calculate_overhead_summary(overhead_cfg)
    base_rates.setdefault("overhead", {})["monthly_aud"] = overhead_summary["enabled_total"]
    base_rates["overhead"]["internal_hours"] = overhead_summary["internal_hours"]

    return templates.TemplateResponse(
        "project_view.html",
        {
            "request": request,
            "project": {"slug": slug, "name": meta.get("name", slug)},
            "summary": summary,
            "unknown_materials": unknown_materials,
            "unknown_hardware": unknown_hardware,
            "hw_suggestions": suggestions,
            "products_by_room": _build_products_by_room(summary),
            "product_overrides": (summary.get("overrides", {}) or {}).get("products", {}),
            # Pass merged current rates/policy so UI shows persisted overrides
            "rates": base_rates,
            "policy": base_policy,
            "assembly_rules": (lambda arules: (_deep_update(arules, (summary.get("overrides", {}) or {}).get("assembly_rules", {})) or arules))(_load_yaml(CONFIGS_DIR / "assembly_rules.yaml")),
            "current_project": slug,
            "unknown_eb": unknown_eb,
            "material_overrides": (summary.get("overrides", {}) or {}).get("materials", {}),
            "hardware_summary": hardware_summary,
            "overhead_summary": summary.get("overhead_summary") or overhead_summary,
        },
    )


def _build_products_by_room(summary: Dict) -> Dict[str, Dict]:
    """Build grouped products with per-room and grand totals for asm/install hours.

    Returns: { 'rooms': {room: [rows...]}, 'room_totals': {room:{asm_h,inst_h}}, 'grand': {asm_h,inst_h} }
    """
    # Accept either new location (derived.products.products) or legacy (products.products)
    prods = (
        (summary.get("derived", {}) or {}).get("products", {}).get("products")
        or (summary.get("products", {}) or {}).get("products")
        or []
    )
    asm_list = (summary.get("assembly", {}) or {}).get("products", [])
    inst_list = (summary.get("install", {}) or {}).get("products", [])
    asm_map = {p.get("item"): p.get("hours", 0) for p in asm_list}
    inst_map = {p.get("item"): p.get("hours", 0) for p in inst_list}
    rooms: Dict[str, list] = {}
    room_totals: Dict[str, Dict[str, float]] = {}
    grand_asm = 0.0
    grand_inst = 0.0
    for p in prods:
        room = p.get("room") or "(Unassigned)"
        w = p.get("width_mm")
        h = p.get("height_mm")
        d = p.get("depth_mm")
        try:
            size = f"{int(round(float(w or 0)))}×{int(round(float(h or 0)))}×{int(round(float(d or 0)))}"
        except Exception:
            size = f"{w}×{h}×{d}"
        item_id = p.get("item")
        asm_h = float(asm_map.get(item_id, 0) or 0)
        inst_h = float(inst_map.get(item_id, 0) or 0)
        row = {
            "item": item_id,
            "description": p.get("description"),
            "size": size,
            "asm_h": asm_h,
            "inst_h": inst_h,
        }
        rooms.setdefault(room, []).append(row)
        rt = room_totals.setdefault(room, {"asm_h": 0.0, "inst_h": 0.0})
        rt["asm_h"] += asm_h
        rt["inst_h"] += inst_h
        grand_asm += asm_h
        grand_inst += inst_h
    # sort rows by item id for readability
    for room in rooms:
        rooms[room] = sorted(rooms[room], key=lambda r: r.get("item") or "")
    return {"rooms": rooms, "room_totals": room_totals, "grand": {"asm_h": round(grand_asm, 2), "inst_h": round(grand_inst, 2)}}


@app.post("/projects/{slug}/recalc")
async def recalc_project(request: Request, slug: str):
    proj_dir = PROJECTS_DIR / slug
    if not proj_dir.exists():
        return HTMLResponse("Project not found", status_code=404)
    # Start from existing overrides to avoid wiping unrelated settings
    existing: Dict = {}
    ov_path = proj_dir / "overrides.json"
    if ov_path.exists():
        try:
            existing = json.loads(ov_path.read_text(encoding="utf-8")) or {}
        except Exception:
            existing = {}

    overrides: Dict = {}
    form = await request.form()
    def getf(name):
        val = form.get(name)
        try:
            return float(val) if val not in (None, "") else None
        except Exception:
            return None
    def geti(name):
        val = form.get(name)
        try:
            return int(val) if val not in (None, "") else None
        except Exception:
            return None
    def getb(name):
        try:
            vals = form.getlist(name)  # type: ignore[attr-defined]
        except Exception:
            v = form.get(name)
            vals = [v] if v is not None else []
        if not vals:
            return None
        svals = [str(v).strip().lower() for v in vals if v is not None]
        # Treat any truthy marker as True (handles hidden 0 + checkbox 1 pattern)
        return any(s in ("1", "true", "on", "yes") for s in svals)

    # Rates overrides
    rates_ov: Dict = {}
    lr = {k: getf(k) for k in ["shop", "drafting", "pm", "installer_billed", "my_rate_per_hour"]}
    lr = {k: v for k, v in lr.items() if v is not None}
    if lr:
        rates_ov.setdefault("labor_rates", {}).update(lr)
    mr = {k: getf(k) for k in ["cnc", "edgebander", "panel_saw"]}
    mr = {k: v for k, v in mr.items() if v is not None}
    if mr:
        rates_ov.setdefault("machine_rental", {}).update(mr)
    # Machine adders (client-facing uplift per hour; internal planning only)
    mad = {"cnc": getf("cnc_adder"), "edgebander": getf("edb_adder")}
    mad = {k: v for k, v in mad.items() if v is not None}
    if mad:
        rates_ov.setdefault("machine_adder", {}).update(mad)
    # Delivery tuning
    delivery_ov = {
        "truck_capacity_cbm": getf("delivery_truck_capacity_cbm"),
        "load_hours_per_full": getf("delivery_load_hours_per_full"),
        "unload_hours_per_trip": getf("delivery_unload_hours_per_trip"),
        "travel_hours_per_trip": getf("delivery_travel_hours_per_trip"),
        "rental_admin_hours": getf("delivery_rental_admin_hours"),
        "delivery_rate": getf("delivery_rate"),
    }
    delivery_ov = {k: v for k, v in delivery_ov.items() if v is not None}
    if delivery_ov:
        rates_ov.setdefault("delivery", {}).update(delivery_ov)
    cnc_model_min = {k: getf(f"cnc_{k}") for k in ["a", "b", "c"]}
    cnc_model_min = {k: v for k, v in cnc_model_min.items() if v is not None}
    if cnc_model_min:
        # Convert minutes to hours for storage
        cnc_model_hr = {k: (v / 60.0) for k, v in cnc_model_min.items()}
        rates_ov.setdefault("cnc_model", {}).update(cnc_model_hr)
    # Area-based CNC and panel saw overrides
    cnc_sqm = getf("cnc_sqm_per_hour")
    if cnc_sqm is not None:
        rates_ov.setdefault("cnc_area", {})["sqm_per_hour"] = cnc_sqm
    ps_min = getf("panel_saw_minutes_per_sheet")
    if ps_min is not None:
        rates_ov.setdefault("panel_saw", {})["minutes_per_sheet"] = ps_min
    asm = {
        "base_minutes_per_m2": getf("asm_base_m2"),
        "min_minutes_per_product": getf("asm_min"),
        "setout_minutes_per_product": getf("asm_setout_min"),
    }
    asm = {k: v for k, v in asm.items() if v is not None}
    if asm:
        rates_ov.setdefault("assembly", {}).update(asm)
    inst = {"base_minutes_per_m2": getf("inst_base_m2"), "min_minutes_per_product": getf("inst_min")}
    inst = {k: v for k, v in inst.items() if v is not None}
    if inst:
        rates_ov.setdefault("install", {}).update(inst)
    edb = {"minutes_per_edge": getf("edb_min_per_edge"), "minutes_per_m": getf("edb_min_per_m"), "setup_minutes": getf("edb_setup_min")}
    edb = {k: v for k, v in edb.items() if v is not None}
    if edb:
        rates_ov.setdefault("edgeband", {}).update(edb)
    team = {
        "two_person_fraction": getf("team_two_frac"),
        "one_person_fraction": getf("team_one_frac"),
        "two_person_rate": getf("team_two_rate"),
        "one_person_rate": getf("team_one_rate"),
    }
    team = {k: v for k, v in team.items() if v is not None}
    team = {k: v for k, v in team.items() if v is not None}
    if team:
        rates_ov.setdefault("install_team", {}).update(team)
    subs = {"markup_percent": getf("subs_markup")}
    subs = {k: v for k, v in subs.items() if v is not None}
    if subs:
        rates_ov.setdefault("subcontractors", {}).update(subs)
    # Contingency factor for dynamic personal rates
    cf = getf("contingency_factor")
    if cf is not None:
        rates_ov["contingency_factor"] = cf
    # Finger pull rate tuning (minutes per part)
    fp_rate = {
        "assembly_minutes_per_part": getf("fp_asm_min"),
        "install_minutes_per_part": getf("fp_inst_min"),
    }
    fp_rate = {k: v for k, v in fp_rate.items() if v is not None}
    if fp_rate:
        rates_ov.setdefault("finger_pull", {}).update(fp_rate)
    if rates_ov:
        overrides["rates"] = rates_ov

    # Assembly rules overrides (minutes adders)
    ar_ov: Dict = {}
    madd = {
        "drawer": getf("add_drawer"),
        "inner_drawer": getf("add_inner_drawer"),
        "hinge": getf("add_hinge"),
        "foot": getf("add_foot"),
        "bin": getf("add_bin"),
        "hinges_per_door": geti("hinges_per_door"),
    }
    madd = {k: v for k, v in madd.items() if v is not None}
    if madd:
        ar_ov.setdefault("minutes_adders", {}).update(madd)
    imadd = {
        "drawer": getf("i_add_drawer"),
        "inner_drawer": getf("i_add_inner_drawer"),
        "hinge": getf("i_add_hinge"),
        "foot": getf("i_add_foot"),
        "bin": getf("i_add_bin"),
    }
    imadd = {k: v for k, v in imadd.items() if v is not None}
    if imadd:
        ar_ov.setdefault("install_minutes_adders", {}).update(imadd)
    if ar_ov:
        overrides["assembly_rules"] = ar_ov
    # Policy overrides: allowances & surcharges
    allow_flat = getf("allowances_flat")
    war_p = getf("warranty_percent")
    cont_p = getf("contingency_percent")
    merch_p = getf("merchant_percent")
    tgt_margin = getf("target_margin")
    tax_wedge = getf("income_tax_percent")
    pol_ov: Dict = {}
    if allow_flat is not None:
        pol_ov.setdefault("allowances", {})["flat_aud_ex_gst"] = allow_flat
    if tgt_margin is not None:
        pol_ov["target_margin"] = tgt_margin
    if tax_wedge is not None:
        pol_ov["income_tax_percent"] = tax_wedge
    sur: Dict = {}
    if war_p is not None:
        sur["warranty_percent"] = war_p
    if cont_p is not None:
        sur["contingency_percent"] = cont_p
    if merch_p is not None:
        sur["merchant_percent"] = merch_p
    if sur:
        pol_ov["surcharges"] = sur
    if pol_ov:
        overrides["policy"] = pol_ov
    # Finger pull overrides
    # Only update boolean flags if the inputs are present in this form submit
    fp_ov: Dict = {}
    val = getb("fp_apply_doors")
    if val is not None:
        fp_ov["apply_doors"] = val
    val = getb("fp_apply_drawers")
    if val is not None:
        fp_ov["apply_drawers"] = val
    # Base-only toggle not exposed yet; default True unless provided later
    od = geti("fp_override_doors")
    if od is not None:
        fp_ov["override_doors"] = od
    odr = geti("fp_override_drawers")
    if odr is not None:
        fp_ov["override_drawers"] = odr
    sd = geti("fp_subtract_doors")
    if sd is not None:
        fp_ov["subtract_doors"] = sd
    sdr = geti("fp_subtract_drawers")
    if sdr is not None:
        fp_ov["subtract_drawers"] = sdr
    ppf = getf("fp_per_part_fee")
    if ppf is not None:
        fp_ov["per_part_fee"] = ppf
    pkf = getf("fp_pickup_fee")
    if pkf is not None:
        fp_ov["pickup_fee"] = pkf
    if fp_ov:
        overrides["finger_pull"] = fp_ov
    # Deep-merge with existing overrides to persist prior customizations
    merged = existing.copy()
    _deep_update(merged, overrides)
    # Persist overrides for the project
    (proj_dir / "overrides.json").write_text(json.dumps(merged, indent=2), encoding="utf-8")
    _compute_project(proj_dir, overrides=merged)
    return RedirectResponse(url=f"/projects/{slug}", status_code=303)


@app.post("/projects/{slug}/product/update")
async def update_product_override(
    slug: str,
    item: str = Form(...),
    buyout: Optional[str] = Form(None),
    complexity: Optional[float] = Form(None),
):
    proj_dir = PROJECTS_DIR / slug
    if not proj_dir.exists():
        return HTMLResponse("Project not found", status_code=404)
    ov_path = proj_dir / "overrides.json"
    overrides = {}
    if ov_path.exists():
        try:
            overrides = json.loads(ov_path.read_text(encoding="utf-8")) or {}
        except Exception:
            overrides = {}
    products = overrides.setdefault("products", {})
    entry = products.setdefault(item, {})
    # Checkbox comes as 'on' when checked; set explicitly to current value (instant toggle)
    entry["exclude"] = bool(buyout)
    if complexity is not None:
        try:
            entry["complexity"] = float(complexity)
        except Exception:
            pass
    ov_path.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    _compute_project(proj_dir, overrides=overrides)
    return RedirectResponse(url=f"/projects/{slug}", status_code=303)


@app.post("/projects/{slug}/materials/update")
async def update_material_override(
    slug: str,
    material: str = Form(...),
    panel_saw: Optional[str] = Form(None),
):
    proj_dir = PROJECTS_DIR / slug
    if not proj_dir.exists():
        return HTMLResponse("Project not found", status_code=404)
    ov_path = proj_dir / "overrides.json"
    overrides = {}
    if ov_path.exists():
        try:
            overrides = json.loads(ov_path.read_text(encoding="utf-8")) or {}
        except Exception:
            overrides = {}
    mats = overrides.setdefault("materials", {})
    entry = mats.setdefault(material, {})
    entry["panel_saw"] = bool(panel_saw)
    ov_path.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    _compute_project(proj_dir, overrides=overrides)
    return RedirectResponse(url=f"/projects/{slug}", status_code=303)


@app.get("/projects/{slug}/quote", response_class=HTMLResponse)
def quote_preview(slug: str, request: Request):
    proj_dir = PROJECTS_DIR / slug
    html_path = proj_dir / "out" / "quote.html"
    regen = request.query_params.get("regen")
    if regen == "1" or not html_path.exists():
        # Generate on-demand (always when regen=1 or if missing)
        try:
            _compute_project(proj_dir)
        except Exception as e:
            return HTMLResponse(f"Failed to generate quote: {e}", status_code=500)
        # Recheck after generation
        if not html_path.exists():
            return HTMLResponse("No quote generated yet", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/catalogs/materials", response_class=HTMLResponse)
def catalog_materials(request: Request):
    materials = _load_yaml(CONFIGS_DIR / "materials.yaml")
    rates = _load_yaml(CONFIGS_DIR / "rates.yaml")
    # sort by key for display
    ordered = dict(sorted(materials.items(), key=lambda x: x[0].lower()))
    # Optional prefill & parse
    name = request.query_params.get("name")
    prefill = None
    if name:
        from ..normalize.materials import parse_material_name

        prefill = parse_material_name(name)
        prefill["name"] = name
    current_project = _resolve_project_slug(request)
    return_url = request.url.path
    if request.url.query:
        return_url = f"{return_url}?{request.url.query}"
    return templates.TemplateResponse(
        "catalog_materials.html",
        {
            "request": request,
            "materials": ordered,
            "prefill": prefill,
            "rates": rates,
            "current_project": current_project,
            "return_url": return_url,
        },
    )


@app.post("/catalogs/materials/add")
async def add_material(
    name: str = Form(...),
    price_per_m2: float = Form(...),
    sheet_w: float = Form(0),
    sheet_h: float = Form(0),
    project: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    return_url: Optional[str] = Form(None),
):
    path = CONFIGS_DIR / "materials.yaml"
    data = _load_yaml(path)
    rec = {"price_per_m2_aud_ex_gst": float(price_per_m2)}
    if sheet_w and sheet_h:
        rec["sheet_size_mm"] = [int(sheet_w), int(sheet_h)]
    data[name] = rec
    _save_yaml(path, data)
    _recompute_project(project)
    if return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/materials"), status_code=303)
    if project:
        return RedirectResponse(url=f"/catalogs/materials?project={project}", status_code=303)
    return RedirectResponse(url="/catalogs/materials", status_code=303)


@app.post("/catalogs/materials/add_attr")
async def add_material_attr(
    name: str = Form(""),
    supplier: str = Form(...),
    finish: str = Form(...),
    thickness_mm: float = Form(...),
    substrate: str = Form(...),
    price_per_m2: float = Form(...),
    project: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    return_url: Optional[str] = Form(None),
):
    path = CONFIGS_DIR / "materials_pricing.yaml"
    data = _load_yaml(path) if path.exists() else {}
    entries = data.get("entries", [])
    entries.append(
        {
            "supplier": supplier.upper(),
            "finish": finish.lower(),
            "substrate": substrate.upper(),
            "thickness_mm": float(thickness_mm),
            "price_per_m2_aud_ex_gst": float(price_per_m2),
        }
    )
    data["entries"] = entries
    _save_yaml(path, data)
    # Optionally create a named alias with sheet size omitted
    if name:
        mat_path = CONFIGS_DIR / "materials.yaml"
        mats = _load_yaml(mat_path)
        mats.setdefault(name, {"price_per_m2_aud_ex_gst": float(price_per_m2)})
        _save_yaml(mat_path, mats)
    _recompute_project(project)
    if return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/materials"), status_code=303)
    if project:
        return RedirectResponse(url=f"/catalogs/materials?project={project}", status_code=303)
    return RedirectResponse(url="/catalogs/materials", status_code=303)


@app.post("/catalogs/materials/edgeband-price")
async def update_edgeband_price(price_per_m: float = Form(...)):
    path = CONFIGS_DIR / "rates.yaml"
    data = _load_yaml(path)
    data.setdefault("edgeband", {})["price_per_m"] = float(price_per_m)
    _save_yaml(path, data)
    return RedirectResponse(url="/catalogs/materials", status_code=303)


@app.get("/overhead", response_class=HTMLResponse)
def overhead_panel(request: Request):
    overhead_cfg = _load_overhead_config()
    summary = _calculate_overhead_summary(overhead_cfg)
    core_total = summary["enabled_total"] - summary["optional_enabled_total"]
    current_project = _resolve_project_slug(request)
    return templates.TemplateResponse(
        "overhead.html",
        {
            "request": request,
            "overhead": overhead_cfg,
            "summary": summary,
            "core_total": round(core_total, 2),
            "current_project": current_project,
        },
    )


@app.post("/overhead/update-item")
async def overhead_update_item(
    category: str = Form(...),
    item_id: str = Form(...),
    name: str = Form(...),
    monthly_aud: float = Form(...),
    notes: str = Form(""),
    enabled: Optional[str] = Form(None),
    project: Optional[str] = Form(None),
):
    overhead_cfg = _load_overhead_config()
    cat = (overhead_cfg.get("categories") or {}).get(category)
    if not cat:
        return _overhead_redirect(project)
    for item in cat.get("items", []):
        if str(item.get("id")) == item_id:
            item["name"] = name.strip() or item.get("name", "")
            item["monthly_aud"] = float(monthly_aud)
            item["notes"] = notes.strip()
            if item.get("optional"):
                item["enabled"] = bool(enabled in {"1", "true", "on", "yes"})
            break
    _save_yaml(CONFIGS_DIR / "overhead.yaml", overhead_cfg)
    summary = _calculate_overhead_summary(overhead_cfg)
    _sync_overhead_into_rates(summary["enabled_total"], summary["internal_hours"])
    _recompute_project(project)
    return _overhead_redirect(project)


@app.post("/overhead/add")
async def overhead_add_item(
    category: str = Form("other"),
    name: str = Form(...),
    monthly_aud: float = Form(...),
    notes: str = Form(""),
    optional: Optional[str] = Form(None),
    enabled: Optional[str] = Form(None),
    project: Optional[str] = Form(None),
):
    overhead_cfg = _load_overhead_config()
    cats = overhead_cfg.setdefault("categories", {})
    cat_key = category if category in cats else "other"
    if cat_key in cats:
        cat = cats[cat_key]
    else:
        cat = {"label": category.title(), "items": []}
        cats[cat_key] = cat
    existing_ids = {str(item.get("id")) for item in cat.get("items", [])}
    base_id = _slugify(name) or "overhead-item"
    unique_id = base_id
    i = 1
    while unique_id in existing_ids:
        unique_id = f"{base_id}-{i}"
        i += 1
    item = {
        "id": unique_id,
        "name": name.strip(),
        "monthly_aud": float(monthly_aud),
        "notes": notes.strip(),
        "optional": bool(optional in {"1", "true", "on", "yes"}),
        "enabled": bool(enabled in {"1", "true", "on", "yes"}),
    }
    if not item["optional"]:
        item["enabled"] = True
    cat.setdefault("items", []).append(item)
    _save_yaml(CONFIGS_DIR / "overhead.yaml", overhead_cfg)
    summary = _calculate_overhead_summary(overhead_cfg)
    _sync_overhead_into_rates(summary["enabled_total"], summary["internal_hours"])
    _recompute_project(project)
    return _overhead_redirect(project)


@app.post("/overhead/update-settings")
async def overhead_update_settings(
    internal_hours: float = Form(...),
    project: Optional[str] = Form(None),
):
    overhead_cfg = _load_overhead_config()
    overhead_cfg["internal_hours"] = max(float(internal_hours), 1.0)
    _save_yaml(CONFIGS_DIR / "overhead.yaml", overhead_cfg)
    summary = _calculate_overhead_summary(overhead_cfg)
    _sync_overhead_into_rates(summary["enabled_total"], summary["internal_hours"])
    if project:
        proj_dir = PROJECTS_DIR / project
        if proj_dir.exists():
            _compute_project(proj_dir)
    return _overhead_redirect(project)


@app.post("/catalogs/materials/set_price")
async def set_material_price(
    name: str = Form(...),
    price_per_m2: Optional[float] = Form(None),
    unit_price: Optional[float] = Form(None),
    project: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    return_url: Optional[str] = Form(None),
):
    path = CONFIGS_DIR / "materials.yaml"
    data = _load_yaml(path)
    entry = data.get(name, {})
    if price_per_m2 is not None:
        entry["price_per_m2_aud_ex_gst"] = float(price_per_m2)
    if unit_price is not None:
        entry["unit_cost_aud_ex_gst"] = float(unit_price)
    data[name] = entry
    _save_yaml(path, data)
    _recompute_project(project)
    # Redirect back to project if provided
    if origin == "catalog" and return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/materials"), status_code=303)
    if project:
        return RedirectResponse(url=f"/projects/{project}#adjustments", status_code=303)
    if return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/materials"), status_code=303)
    return RedirectResponse(url="/catalogs/materials", status_code=303)


@app.post("/catalogs/hardware/set_price")
async def set_hardware_price(
    name: str = Form(...),
    unit_price: float = Form(...),
    project: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    return_url: Optional[str] = Form(None),
):
    path = CONFIGS_DIR / "hardware.yaml"
    data = _load_yaml(path)
    entry = data.get(name, {})
    entry["unit_price_aud_ex_gst"] = float(unit_price)
    data[name] = entry
    _save_yaml(path, data)
    _recompute_project(project)
    if origin == "catalog" and return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/hardware"), status_code=303)
    if project:
        return RedirectResponse(url=f"/projects/{project}#adjustments", status_code=303)
    if return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/hardware"), status_code=303)
    return RedirectResponse(url="/catalogs/hardware", status_code=303)


@app.get("/catalogs/hardware", response_class=HTMLResponse)
def catalog_hardware(request: Request):
    hardware = _load_yaml(CONFIGS_DIR / "hardware.yaml")
    ordered = dict(sorted(hardware.items(), key=lambda x: x[0].lower()))
    current_project = _resolve_project_slug(request)
    return_url = request.url.path
    if request.url.query:
        return_url = f"{return_url}?{request.url.query}"
    return templates.TemplateResponse(
        "catalog_hardware.html",
        {
            "request": request,
            "hardware": ordered,
            "current_project": current_project,
            "return_url": return_url,
        },
    )


@app.get("/catalogs/hardware/import", response_class=HTMLResponse)
def import_hardware_form(request: Request):
    current_project = _resolve_project_slug(request)
    return_url = request.url.path
    if request.url.query:
        return_url = f"{return_url}?{request.url.query}"
    return templates.TemplateResponse(
        "import_hardware.html",
        {
            "request": request,
            "current_project": current_project,
            "return_url": return_url,
        },
    )


@app.post("/catalogs/hardware/import")
async def import_hardware_upload(request: Request, html_file: UploadFile = File(...)):
    from ..importers.vendors.lincoln_sentry import parse_html

    html = (await html_file.read()).decode("utf-8", errors="ignore")
    items = parse_html(html)
    # Merge into hardware.yaml converting to ex-GST (site likely inc-GST)
    path = CONFIGS_DIR / "hardware.yaml"
    data = _load_yaml(path)
    added = 0
    for it in items:
        name = it.get("description")
        if not name:
            continue
        price_inc = float(it.get("unit_price_aud_inc_gst", 0) or 0)
        price_ex = round(price_inc / 1.10, 2)
        pack = int(it.get("pack_size", 1) or 1)
        # Upsert
        data[name] = {"unit_price_aud_ex_gst": price_ex, "pack_size": pack}
        added += 1
    _save_yaml(path, data)

    current_project = _resolve_project_slug(request)
    _recompute_project(current_project)
    return_url = request.url.path
    if request.url.query:
        return_url = f"{return_url}?{request.url.query}"
    return templates.TemplateResponse(
        "import_hardware.html",
        {
            "request": request,
            "imported": added,
            "items": items[:50],
            "current_project": current_project,
            "return_url": return_url,
        },
    )


@app.post("/catalogs/hardware/add")
async def add_hardware(
    name: str = Form(...),
    unit_price: float = Form(...),
    pack_size: int = Form(1),
    project: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    return_url: Optional[str] = Form(None),
):
    path = CONFIGS_DIR / "hardware.yaml"
    data = _load_yaml(path)
    data[name] = {
        "unit_price_aud_ex_gst": float(unit_price),
        "pack_size": int(pack_size) if pack_size else 1,
    }
    _save_yaml(path, data)
    _recompute_project(project)
    if return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/hardware"), status_code=303)
    if project:
        return RedirectResponse(url=f"/catalogs/hardware?project={project}", status_code=303)
    return RedirectResponse(url="/catalogs/hardware", status_code=303)


@app.post("/projects/{slug}/catalogs/hardware/map-alias")
async def map_hardware_alias(slug: str, source: str = Form(...), target: str = Form(...)):
    path = CONFIGS_DIR / "hardware_aliases.yaml"
    data = _load_yaml(path) if path.exists() else {}
    data[source] = target
    _save_yaml(path, data)
    proj_dir = PROJECTS_DIR / slug
    _compute_project(proj_dir)
    return RedirectResponse(url=f"/projects/{slug}", status_code=303)


@app.get("/catalogs/edgeband", response_class=HTMLResponse)
def catalog_edgeband(request: Request):
    eb = _load_yaml(CONFIGS_DIR / "edgeband.yaml")
    ordered = dict(sorted(eb.items(), key=lambda x: x[0].lower()))
    current_project = _resolve_project_slug(request)
    return_url = request.url.path
    if request.url.query:
        return_url = f"{return_url}?{request.url.query}"
    return templates.TemplateResponse(
        "catalog_edgeband.html",
        {
            "request": request,
            "edgebands": ordered,
            "current_project": current_project,
            "return_url": return_url,
        },
    )


@app.post("/catalogs/edgeband/add")
async def add_edgeband(
    spec: str = Form(...),
    price_per_m: float = Form(...),
    setup_cost: float = Form(0),
    project: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    return_url: Optional[str] = Form(None),
):
    path = CONFIGS_DIR / "edgeband.yaml"
    data = _load_yaml(path)
    data[spec] = {"price_per_m": float(price_per_m), "setup_cost": float(setup_cost or 0)}
    _save_yaml(path, data)
    if return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/edgeband"), status_code=303)
    if project:
        return RedirectResponse(url=f"/catalogs/edgeband?project={project}", status_code=303)
    return RedirectResponse(url="/catalogs/edgeband", status_code=303)


@app.post("/catalogs/edgeband/update")
async def update_edgeband(
    spec: List[str] = Form(...),
    price_per_m: List[float] = Form(...),
    setup_cost: List[float] = Form(...),
    project: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    return_url: Optional[str] = Form(None),
):
    path = CONFIGS_DIR / "edgeband.yaml"
    data = _load_yaml(path)
    for i, s in enumerate(spec):
        s = (s or "").strip()
        if not s:
            continue
        try:
            ppm = float(price_per_m[i]) if i < len(price_per_m) else 0.0
        except Exception:
            ppm = 0.0
        try:
            sc = float(setup_cost[i]) if i < len(setup_cost) else 0.0
        except Exception:
            sc = 0.0
        if s not in data:
            data[s] = {}
        data[s]["price_per_m"] = ppm
        data[s]["setup_cost"] = sc
    _save_yaml(path, data)
    # Optionally recompute a project if provided
    if project:
        proj_dir = PROJECTS_DIR / project
        if proj_dir.exists():
            _compute_project(proj_dir)
            if origin != "catalog":
                return RedirectResponse(url=f"/projects/{project}", status_code=303)
    if return_url:
        return RedirectResponse(url=_safe_redirect(return_url, "/catalogs/edgeband"), status_code=303)
    if project:
        return RedirectResponse(url=f"/catalogs/edgeband?project={project}", status_code=303)
    return RedirectResponse(url="/catalogs/edgeband", status_code=303)


@app.post("/projects/{slug}/catalogs/edgeband/add-inline")
async def add_edgeband_inline(slug: str, spec: str = Form(...), price_per_m: float = Form(...), setup_cost: float = Form(0)):
    path = CONFIGS_DIR / "edgeband.yaml"
    data = _load_yaml(path)
    data[spec] = {"price_per_m": float(price_per_m), "setup_cost": float(setup_cost or 0)}
    _save_yaml(path, data)
    proj_dir = PROJECTS_DIR / slug
    _compute_project(proj_dir)
    return RedirectResponse(url=f"/projects/{slug}", status_code=303)


# Inline add from project page (materials)
@app.post("/projects/{slug}/catalogs/materials/add-inline")
async def add_material_inline(
    slug: str,
    name: str = Form(...),
    price_per_m2: float = Form(...),
    sheet_w: float = Form(0),
    sheet_h: float = Form(0),
):
    mat_path = CONFIGS_DIR / "materials.yaml"
    mats = _load_yaml(mat_path)
    rec = {"price_per_m2_aud_ex_gst": float(price_per_m2)}
    if sheet_w and sheet_h:
        rec["sheet_size_mm"] = [int(sheet_w), int(sheet_h)]
    mats[name] = rec
    _save_yaml(mat_path, mats)

    # Add attribute-based price from parsed name if available
    from ..normalize.materials import parse_material_name

    attrs = parse_material_name(name)
    attr_path = CONFIGS_DIR / "materials_pricing.yaml"
    attr = _load_yaml(attr_path) if attr_path.exists() else {}
    entries = attr.get("entries", [])
    if all(attrs.get(k) for k in ("supplier", "finish", "thickness_mm", "substrate")):
        entries.append(
            {
                "supplier": str(attrs["supplier"]).upper(),
                "finish": str(attrs["finish"]).lower(),
                "substrate": str(attrs["substrate"]).upper(),
                "thickness_mm": float(attrs["thickness_mm"]),
                "price_per_m2_aud_ex_gst": float(price_per_m2),
            }
        )
        attr["entries"] = entries
        _save_yaml(attr_path, attr)

    # Recompute project and redirect back
    proj_dir = PROJECTS_DIR / slug
    _compute_project(proj_dir)
    return RedirectResponse(url=f"/projects/{slug}", status_code=303)


# Inline add from project page (hardware)
@app.post("/projects/{slug}/catalogs/hardware/add-inline")
async def add_hardware_inline(
    slug: str,
    name: str = Form(...),
    unit_price: float = Form(...),
    pack_size: int = Form(1),
):
    path = CONFIGS_DIR / "hardware.yaml"
    data = _load_yaml(path)
    data[name] = {
        "unit_price_aud_ex_gst": float(unit_price),
        "pack_size": int(pack_size) if pack_size else 1,
    }
    _save_yaml(path, data)

    proj_dir = PROJECTS_DIR / slug
    _compute_project(proj_dir)
    return RedirectResponse(url=f"/projects/{slug}", status_code=303)


# Bulk add materials and hardware from project page
@app.post("/projects/{slug}/catalogs/bulk_add")
async def bulk_add_catalog(
    slug: str,
    m_name: list[str] = Form(default_factory=list),
    m_price: list[float] = Form(default_factory=list),
    m_w: list[float] = Form(default_factory=list),
    m_h: list[float] = Form(default_factory=list),
    h_name: list[str] = Form(default_factory=list),
    h_price: list[float] = Form(default_factory=list),
    h_pack: list[int] = Form(default_factory=list),
):
    # Materials
    mat_path = CONFIGS_DIR / "materials.yaml"
    mats = _load_yaml(mat_path)
    attr_path = CONFIGS_DIR / "materials_pricing.yaml"
    attr = _load_yaml(attr_path) if attr_path.exists() else {}
    entries = attr.get("entries", [])

    from ..normalize.materials import parse_material_name

    for i, name in enumerate(m_name):
        name = (name or "").strip()
        if not name:
            continue
        try:
            price = float(m_price[i]) if i < len(m_price) else 0.0
        except Exception:
            price = 0.0
        if price <= 0:
            continue
        rec = {"price_per_m2_aud_ex_gst": price}
        try:
            w = float(m_w[i]) if i < len(m_w) else 0
            h = float(m_h[i]) if i < len(m_h) else 0
        except Exception:
            w = h = 0
        if w and h:
            rec["sheet_size_mm"] = [int(w), int(h)]
        mats[name] = rec
        # attribute pricing
        attrs = parse_material_name(name)
        if all(attrs.get(k) for k in ("supplier", "finish", "thickness_mm", "substrate")):
            entries.append(
                {
                    "supplier": str(attrs["supplier"]).upper(),
                    "finish": str(attrs["finish"]).lower(),
                    "substrate": str(attrs["substrate"]).upper(),
                    "thickness_mm": float(attrs["thickness_mm"]),
                    "price_per_m2_aud_ex_gst": float(price),
                }
            )
    _save_yaml(mat_path, mats)
    if entries:
        attr["entries"] = entries
        _save_yaml(attr_path, attr)

    # Hardware
    hw_path = CONFIGS_DIR / "hardware.yaml"
    hw = _load_yaml(hw_path)
    for i, name in enumerate(h_name):
        name = (name or "").strip()
        if not name:
            continue
        try:
            up = float(h_price[i]) if i < len(h_price) else 0.0
        except Exception:
            up = 0.0
        if up <= 0:
            continue
        try:
            pack = int(h_pack[i]) if i < len(h_pack) else 1
        except Exception:
            pack = 1
        hw[name] = {"unit_price_aud_ex_gst": up, "pack_size": pack}
    _save_yaml(hw_path, hw)

    proj_dir = PROJECTS_DIR / slug
    _compute_project(proj_dir)
    return RedirectResponse(url=f"/projects/{slug}", status_code=303)
    # Top-level adjustments (only set if present in the submitted form)
    esw = getf("extra_sheet_waste")
    if esw is not None:
        overrides["extra_sheet_waste"] = esw
    dhrs = getf("drafting_hours")
    pmhrs = getf("pm_hours")
    ihours = getf("install_hours")
    defaults_block = {k: v for k, v in {
        "drafting_hours": dhrs,
        "pm_hours": pmhrs,
        "install_hours": ihours,
    }.items() if v is not None}
    if defaults_block:
        overrides.setdefault("defaults", {}).update(defaults_block)
