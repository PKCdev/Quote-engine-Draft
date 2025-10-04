from __future__ import annotations

import re
from typing import Dict, List, Tuple

from rapidfuzz import fuzz, process


STOPWORDS = {
    "kit", "set", "drawer", "drawers", "finista", "swift",
    "matt", "matte", "white", "black", "grey", "gray",
    "pair", "ea", "each", "unit", "uom", "standard",
}


def normalize(s: str) -> str:
    s = s.lower()
    s = s.replace("-", " ")
    # normalize units
    s = re.sub(r"mm\b", " ", s)
    s = re.sub(r"kg\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokens(s: str) -> List[str]:
    s = normalize(s)
    toks = re.split(r"[^a-z0-9]+", s)
    toks = [t for t in toks if t and t not in STOPWORDS]
    return toks


def best_matches(query: string, choices: List[str], limit: int = 3) -> List[Tuple[str, int]]:
    """Return best matches using token_set_ratio with basic normalization."""
    # RapidFuzz process.extract works on raw strings; apply normalize to both sides
    norm_query = " ".join(tokens(query))
    norm_choices = {c: " ".join(tokens(c)) for c in choices}
    results = process.extract(norm_query, norm_choices, scorer=fuzz.token_set_ratio, limit=limit)
    # results: list of (matched_norm, score, original_key)
    out: List[Tuple[str, int]] = []
    for norm, score, original in results:
        out.append((original, int(score)))
    return out

