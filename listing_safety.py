"""LISTING SAFETY (Listing Optimization, session 6, brief sections 11, 19, 35, 38).

Two quality gates:
  validate_keyword_against_facts()  - BEFORE generation: is a candidate keyword actually true of
                                       this product? (the "cotton kurta" vs "polyester kurta" example)
  validate_generated_content()      - AFTER generation: does the proposed title/bullets/description
                                       avoid rejected/unsupported terms, competitor brand names and
                                       keyword stuffing, and stay within Amazon field-length limits?

Neither function calls an AI provider or touches the database - both are pure, so they are cheap to
unit-test and cannot themselves be a source of hallucinated facts.
"""
import re

from . import keywords as kw

# Attribute vocabulary: known values -> which attribute family they belong to. Sourced from the
# account's OWN compliance category rules (core.py DEFAULT_CONFIG["compliance"]["category_rules"] -
# already-declared allowed_values per category, e.g. fabric/size/sleeve_type) plus a small, generic
# extra list for families the compliance rules don't cover (material synonyms, colour, gender) -
# this is intentionally NOT a big category-specific rules system living in the core engine (brief:
# "do not hard-code Amazon category-specific rules into the core engine") - it is only ever used to
# ask "does this one word conflict with a fact we already know?", never to invent new rules.
_EXTRA_VOCAB = {
    "material": ["cotton", "polyester", "rayon", "silk", "linen", "denim", "wool", "nylon",
                 "viscose", "georgette", "chiffon", "leather", "velvet", "khadi", "modal"],
    "gender": ["women", "men", "unisex", "kids", "boys", "girls"],
    "color": ["red", "blue", "green", "black", "white", "yellow", "pink", "purple", "orange",
              "grey", "gray", "brown", "beige", "maroon", "navy", "teal", "gold", "silver"],
}


def _build_vocab(cfg, category):
    vocab = {}
    for fam, vals in _EXTRA_VOCAB.items():
        for v in vals:
            vocab[v] = fam
    rules = ((cfg.get("compliance") or {}).get("category_rules") or {}).get(category, {})
    for fam, vals in (rules.get("allowed_values") or {}).items():
        for v in vals:
            vocab[str(v).lower()] = fam
    return vocab


def _attr_actual(attrs, family):
    # attrs_json keys are whatever the seller's sheet called them; try the obvious aliases.
    aliases = {"material": ("material", "fabric"), "color": ("color", "colour"),
               "gender": ("gender",), "size": ("size",)}
    for key in aliases.get(family, (family,)):
        if attrs.get(key):
            return str(attrs[key]).strip().lower()
    return None


def validate_keyword_against_facts(keyword, attrs, cfg, category):
    """-> (eligible: bool, reason: str|None). Section 11: never add a keyword merely because
    competitors use it if it conflicts with (or is unsupported by) the product's own facts."""
    vocab = _build_vocab(cfg, category)
    tokens = keyword.split(" ")
    for tok in tokens:
        family = vocab.get(tok)
        if not family:
            continue
        actual = _attr_actual(attrs, family)
        if actual and actual != tok:
            return False, f"product {family} is '{actual}', keyword implies '{tok}'"
        # actual is None (attribute not on file) -> can't disprove it; allow, but this is exactly
        # the PRODUCT_FACTS_INCOMPLETE situation the caller may want to note in the report.
    return True, None


def filter_opportunities(candidates, attrs, cfg, category):
    """-> (eligible: list[dict], rejected: list[dict with 'reason']). Applied once per candidate
    keyword; never mutates the scored keyword dicts, only annotates copies."""
    eligible, rejected = [], []
    for c in candidates:
        ok, reason = validate_keyword_against_facts(c["keyword"], attrs, cfg, category)
        if ok:
            eligible.append(c)
        else:
            rejected.append({**c, "status": "rejected", "reason": reason})
    return eligible, rejected


# ------------------------------------ generated-content validation (section 38) --------------------
def _brand_mentions(text, competitor_brands):
    low = (text or "").lower()
    return [b for b in competitor_brands if b and b.lower() in low]


def _stuffing_hits(text, max_repeat):
    low = kw.normalize(text)
    counts = {}
    words = low.split(" ")
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            counts[phrase] = counts.get(phrase, 0) + 1
    return [p for p, n in counts.items() if n > max_repeat]


def validate_generated_content(title, bullets, description, cfg, rejected_keywords, competitor_brands):
    """-> list[str] of problems (empty = passes every gate). Never raises - the caller decides
    whether to fall back to the deterministic template on failure."""
    problems = []
    cat_cfg = cfg["catalogue"]
    if title and not (cat_cfg["title_min"] <= len(title) <= cat_cfg["title_max"]):
        problems.append(f"title length {len(title)} outside [{cat_cfg['title_min']},{cat_cfg['title_max']}]")

    full_text = " ".join([title or ""] + list(bullets or []) + [description or ""])
    rejected_words = {r["keyword"] for r in (rejected_keywords or [])}
    low_full = kw.normalize(full_text)
    for r in rejected_words:
        if r and r in low_full:
            problems.append(f"generated content contains rejected/unsupported keyword '{r}'")

    hit = _brand_mentions(full_text, competitor_brands or [])
    if hit:
        problems.append(f"generated content mentions competitor brand(s): {', '.join(hit)}")

    stuffing = _stuffing_hits(full_text, cfg["listing_optimization"]["keyword_stuffing_max_repeat"])
    if stuffing:
        problems.append(f"possible keyword stuffing: {', '.join(stuffing[:3])}")

    return problems
