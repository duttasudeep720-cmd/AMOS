"""COMPETITIVE SEARCH (Listing Optimization, session 6, brief sections 3, 4, 22, 23).

Keeps "find relevant top-ranking ASINs" (this file) completely separate from "what do we do with
their keywords" (amos/keywords.py) and from Amazon API transport (there isn't a live SP-API /
Product Advertising API connector in this codebase yet - see amos/engines/execution.py's stub
connectors - so this module's only implementation today is an explicitly-labelled mock).

    CompetitiveSearchProvider.search(marketplace, query, limit) -> list[dict] (raw candidates)
    MockCompetitiveSearchProvider                                - deterministic, offline, source="mock"
    relevance_score()                              section 4  - product-type/category/gender/etc weighting
    find_competitive_asins(conn, account, listing) section 3  - the actual entry point a skill calls
"""
import hashlib
import random

from . import core, ingest

# A small, deliberately generic phrase bank the mock provider draws from. This is NOT category-
# specific business logic baked into the engine (brief section: "do not hard-code Amazon category-
# specific rules into the core engine") - it is a stand-in for what a real Search/Advertising API
# would return, clearly labelled source="mock" everywhere it is used, per section 22.
_MOCK_QUALIFIERS = ["premium", "stylish", "comfortable", "everyday", "trendy", "classic", "designer",
                    "printed", "solid", "regular fit", "relaxed fit"]
_MOCK_USE_CASES = ["festive wear", "casual wear", "office wear", "party wear", "daily wear", "wedding wear"]


class CompetitiveSearchProvider:
    name = "base"

    def search(self, marketplace, query, attributes, limit):
        """-> list[dict]: {asin, rank, title, bullets(list[str]), description, brand, category,
        attributes(dict)}. `rank` is 1-based search/category rank as this provider understands it."""
        raise NotImplementedError


class MockCompetitiveSearchProvider(CompetitiveSearchProvider):
    """Deterministic, offline stand-in (brief section 22: "For development/demo mode, use mock
    data explicitly marked as mock"). Seeded from (marketplace, query) so the same SKU produces
    the same competitive set across cycles - exactly what real top-ranking-ASIN data would also
    do most of the time, and what keeps the exception engine's per-cycle output stable/testable.
    """
    name = "mock"

    def search(self, marketplace, query, attributes, limit):
        seed = int(hashlib.sha1(f"{marketplace}|{query}".encode()).hexdigest(), 16) % (2 ** 32)
        rng = random.Random(seed)
        attrs = attributes or {}
        product_type = query.strip()
        out = []
        for rank in range(1, limit + 1):
            qualifier = rng.choice(_MOCK_QUALIFIERS)
            use_case = rng.choice(_MOCK_USE_CASES)
            # Vary a couple of attributes across the set so relevance_score() has something real to
            # discriminate on (brief section 4's kurta/saree/lehenga example) - most of the set
            # matches the client's own attributes; a minority intentionally does not.
            comp_attrs = dict(attrs)
            if rng.random() < 0.25 and attrs.get("material"):
                comp_attrs["material"] = rng.choice(["cotton", "rayon", "polyester", "silk blend"])
            if rng.random() < 0.15 and attrs.get("gender"):
                comp_attrs["gender"] = rng.choice(["women", "men", "kids"])
            bits = [b for b in (comp_attrs.get("material"), product_type, qualifier) if b]
            title = " ".join(w.capitalize() for w in " ".join(bits).split()) + f" for {use_case.title()}"
            bullets = [
                f"{comp_attrs.get('material', 'Quality').title()} construction, {qualifier} fit for {use_case}.",
                f"Designed for {use_case} - {product_type} with a {rng.choice(['modern', 'traditional', 'contemporary'])} look.",
                f"Available in multiple sizes; care instructions on the {product_type} label.",
            ]
            description = (f"This {product_type} is built for {use_case}, combining a {qualifier} silhouette "
                            f"with everyday comfort. A popular choice among {product_type} shoppers on Amazon.in.")
            out.append({
                "asin": f"MOCK{seed % 100000:05d}{rank:02d}", "rank": rank, "title": title,
                "bullets": bullets, "description": description, "brand": f"Brand{rank}",
                "category": attrs.get("category", product_type), "attributes": comp_attrs, "source": "mock",
            })
        return out


PROVIDERS = {"mock": MockCompetitiveSearchProvider()}


def get_provider(cfg):
    return PROVIDERS.get(cfg.get("provider", "mock"), PROVIDERS["mock"])


# ------------------------------------ relevance (section 4) ---------------------------------------
def relevance_score(client_attrs, candidate, weights):
    """0-1 relevance of one candidate ASIN to the client's product. Weighted match on product
    type/category (from title/category text) + gender/material/use-case/other attributes - this is
    what stops "kurta" alone from pulling in Men's Kurta / Saree / Lehenga (brief section 4)."""
    cand_attrs = candidate.get("attributes") or {}
    total_w = sum(weights.values()) or 1.0
    score = 0.0

    cat_client = (client_attrs.get("category") or "").lower()
    cat_cand = (candidate.get("category") or "").lower()
    if cat_client and cat_cand:
        score += weights.get("category", 0) * (1.0 if cat_client == cat_cand else 0.3)
    else:
        score += weights.get("category", 0) * 0.5   # unknown either side -> neutral, not a penalty

    pt_client = (client_attrs.get("product_type") or "").lower()
    if pt_client and pt_client in (candidate.get("title") or "").lower():
        score += weights.get("product_type", 0) * 1.0
    elif pt_client:
        score += weights.get("product_type", 0) * 0.2
    else:
        score += weights.get("product_type", 0) * 0.5

    for family, w_key in (("gender", "gender"), ("material", "material")):
        cv, kv = (client_attrs.get(family) or "").lower(), (cand_attrs.get(family) or "").lower()
        if cv and kv:
            score += weights.get(w_key, 0) * (1.0 if cv == kv else 0.0)
        else:
            score += weights.get(w_key, 0) * 0.6   # can't disprove -> mild credit, not a hard fail

    # use_case / other attributes: generic partial credit (no per-category rules baked in here)
    score += weights.get("use_case", 0) * 0.6
    score += weights.get("attribute", 0) * 0.6

    return round(min(1.0, score / total_w), 3)


def _client_attrs(listing):
    attrs = ingest.parse_attrs(listing)
    return {
        "category": listing.get("category"), "product_type": attrs.get("product_type") or listing.get("category"),
        "gender": attrs.get("gender"), "material": attrs.get("material") or attrs.get("fabric"),
    }


def find_competitive_asins(conn, account, listing, limit=None, cfg_section=None):
    """The entry point: discover, score, filter and cache the competitive set for one listing.
    Always caches the result to `competitive_asins` (replacing any previous cache for this SKU) so
    the evidence behind a proposal is a durable, queryable record (brief section 36/37) - not just
    something that flashed by inside one cycle."""
    cfg = cfg_section or account["config"]["listing_optimization"]
    limit = limit or cfg["top_asins_limit"]
    client_attrs = _client_attrs(listing)
    query = client_attrs["product_type"] or listing.get("category") or listing.get("title") or listing["sku"]

    provider = get_provider(cfg)
    candidates = provider.search(account["marketplace"], query, client_attrs, max(limit * 2, limit))
    scored = []
    for c in candidates:
        rel = relevance_score(client_attrs, c, cfg["relevance_weights"])
        if rel < cfg["min_competitor_relevance"]:
            continue
        c["relevance_score"] = rel
        scored.append(c)
    scored.sort(key=lambda c: (-c["relevance_score"], c["rank"]))
    kept = scored[:limit]
    for i, c in enumerate(kept, start=1):
        c["rank"] = i   # re-rank within the KEPT relevant set, not the raw provider order

    conn.execute("DELETE FROM competitive_asins WHERE account_id=? AND sku=?", (account["id"], listing["sku"]))
    now = core.now()
    conn.executemany(
        """INSERT INTO competitive_asins(account_id,sku,asin,rank,title,bullets_text,description,brand,
           category,relevance_score,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(account["id"], listing["sku"], c["asin"], c["rank"], c["title"], " | ".join(c["bullets"]),
          c["description"], c.get("brand"), c.get("category"), c["relevance_score"], c.get("source", "mock"), now)
         for c in kept])
    conn.commit()
    return kept
