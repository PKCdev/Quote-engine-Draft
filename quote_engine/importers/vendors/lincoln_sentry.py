from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from bs4 import BeautifulSoup


def _pack_size_from_text(text: str) -> int:
    t = text.lower()
    # heuristics: pair -> 2, box of N, pack of N
    if "pair" in t:
        return 2
    m = re.search(r"(box|pack) of\s*(\d+)", t)
    if m:
        try:
            return int(m.group(2))
        except Exception:
            pass
    return 1


def parse_html(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    products: List[Dict[str, Any]] = []

    # Try JSON-LD first
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "{}")
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if n.get("@type") == "Product" or (isinstance(n.get("@type"), list) and "Product" in n.get("@type")):
                name = (n.get("name") or "").strip()
                if not name:
                    continue
                price = None
                offers = n.get("offers")
                if isinstance(offers, dict):
                    price = offers.get("price") or offers.get("highPrice") or offers.get("lowPrice")
                elif isinstance(offers, list) and offers:
                    o0 = offers[0]
                    if isinstance(o0, dict):
                        price = o0.get("price") or o0.get("highPrice") or o0.get("lowPrice")
                try:
                    price = float(str(price)) if price is not None else None
                except Exception:
                    price = None
                pack = _pack_size_from_text(name)
                if price is not None:
                    products.append({"description": name, "unit_price_aud_inc_gst": price, "pack_size": pack})

    # Fallback: parse Algolia search result cards seen in saved pages
    if not products:
        items = soup.select("li.ais-InfiniteHits-item .product-result-item")
        for card in items:
            # Name
            a = card.select_one(".product-pdp-name a")
            name = (a.get_text(" ", strip=True) if a else "").strip()
            if not name:
                # try any anchor text as fallback
                for _a in card.find_all("a"):
                    txt = (_a.get_text(" ", strip=True) or "").strip()
                    if len(txt) > len(name):
                        name = txt
            # Price (promo-price or similar, includes GST)
            price_el = card.select_one(".product-price .promo-price, .product-price [class*=price]")
            price = None
            if price_el:
                m = re.search(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)", price_el.get_text(" ", strip=True))
                if m:
                    try:
                        price = float(m.group(1))
                    except Exception:
                        price = None
            if name and price is not None:
                pack = _pack_size_from_text(name)
                products.append({"description": name, "unit_price_aud_inc_gst": price, "pack_size": pack})

    return products
