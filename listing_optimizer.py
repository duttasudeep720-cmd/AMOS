"""LISTING OPTIMIZATION orchestration (session 6, brief sections 1-2, 13-21, 30, 40-43).

This is the one place that wires together competitive_search.py (top-ASIN discovery),
keywords.py (extraction/scoring/gap analysis), listing_safety.py (product-fact + generated-content
validation) and ai_provider.py (copy generation) into a single AMOS Finding per SKU - the same
`skills.base.Finding` shape every other AMOS skill already returns, with
`recommendation.action_type = "UPDATE_LISTING_CONTENT"` (an action type that already existed:
base autonomy level 2, dry-run/SP-API-stub connector already registered - see
amos/engines/policy.py BASE_LEVEL and amos/engines/execution.py LIVE_CONNECTORS). Everything after
the Finding is returned - exception dedup, priority, task creation, human approval, execution,
verification, measurement, learning - is 100% the existing AMOS pipeline. Nothing below this
module talks to the database except to read the listing itself and cache the competitive set
(amos/competitive_search.py).
"""
from . import ai_provider, competitive_search, ingest, keywords as kw, listing_safety
from .skills.base import Finding, clamp, units_share

HIGH_VALUE_SCORE_THRESHOLD = 5.0   # heuristic cut, tuned against MockCompetitiveSearchProvider's output range


class ListingOptimizationError(Exception):
    code = "LISTING_OPTIMIZATION_ERROR"


class NoCompetitiveAsinsFound(ListingOptimizationError):
    code = "NO_COMPETITIVE_ASINS_FOUND"


class InsufficientCompetitiveData(ListingOptimizationError):
    code = "INSUFFICIENT_COMPETITIVE_DATA"


class CategoryNotIdentified(ListingOptimizationError):
    code = "CATEGORY_NOT_IDENTIFIED"


class CurrentListingUnavailable(ListingOptimizationError):
    code = "CURRENT_LISTING_UNAVAILABLE"


class NoValidKeywordOpportunities(ListingOptimizationError):
    code = "NO_VALID_KEYWORD_OPPORTUNITIES"


class AIGenerationFailed(ListingOptimizationError):
    code = "AI_GENERATION_FAILED"


class ContentValidationFailed(ListingOptimizationError):
    code = "CONTENT_VALIDATION_FAILED"


# ------------------------------------ shared analysis core -----------------------------------------
def _current_listing_text(listing):
    title = (listing.get("title") or "").strip()
    bullets = ingest.split_bullets(listing.get("bullets_text"))
    description = (listing.get("description") or "").strip()
    return title, bullets, description


def _competitive_evidence(conn, account, listing, cfg):
    """-> (attrs, category, title, bullets, description, competitive, scored, covered, candidates,
    before_pct). Raises CurrentListingUnavailable / CategoryNotIdentified / NoCompetitiveAsinsFound /
    InsufficientCompetitiveData. Shared by analyze_sku() and skills/metrics.py::keyword_coverage()
    so "what % of the competitive keyword universe does this listing cover right now" is computed
    exactly the same way whether it's driving a fresh proposal or re-measuring one 7/14/30 days later.
    """
    lo_cfg = cfg["listing_optimization"]
    title, bullets, description = _current_listing_text(listing)
    if not title:
        raise CurrentListingUnavailable(f"{listing['sku']}: no title on file - cannot analyze a listing with no content")

    attrs = ingest.parse_attrs(listing)
    category = listing.get("category") or attrs.get("product_type")
    if not category:
        raise CategoryNotIdentified(f"{listing['sku']}: no category/product_type on file")

    competitive = competitive_search.find_competitive_asins(conn, account, listing, cfg_section=lo_cfg)
    if not competitive:
        raise NoCompetitiveAsinsFound(f"{listing['sku']}: no sufficiently relevant top-ranking ASIN found for '{category}'")
    if len(competitive) < lo_cfg["min_competitive_set"]:
        raise InsufficientCompetitiveData(
            f"{listing['sku']}: only {len(competitive)} relevant competitor(s) found (need >= {lo_cfg['min_competitive_set']})")

    client_phrases = set(kw.extract_listing_phrases(title, bullets, description))
    competitor_phrase_sets = [
        {"rank": c["rank"], "asin": c["asin"], "phrases": kw.extract_listing_phrases(c["title"], c["bullets"], c["description"])}
        for c in competitive]
    scored = kw.score_keywords(competitor_phrase_sets, lo_cfg["keyword_score_weights"], search_context_terms=[category])
    covered, candidates = kw.client_coverage(client_phrases, scored, lo_cfg["min_competitor_coverage"])
    before_pct = round(len(covered) / len(scored), 4) if scored else 0.0
    return attrs, category, title, bullets, description, competitive, scored, covered, candidates, before_pct


def current_keyword_coverage_pct(conn, account, listing, cfg):
    """Used by skills/metrics.py's `keyword_coverage` metric so the existing Learning engine
    (amos/engines/learning.py::measure_due) can compare before-vs-after coverage automatically,
    with zero new measurement code. Never raises - returns None if it cannot be computed."""
    try:
        *_rest, before_pct = _competitive_evidence(conn, account, listing, cfg)
        return before_pct
    except ListingOptimizationError:
        return None


# ------------------------------------ proposal generation -------------------------------------------
def _generate_and_validate(product, current, eligible, rejected, cfg, competitor_brands):
    lo_cfg = cfg["listing_optimization"]
    context = {
        "product": product, "current_listing": current, "keyword_opportunities": eligible,
        "rejected_keywords": rejected,
        "constraints": {"title_min": cfg["catalogue"]["title_min"], "title_max": cfg["catalogue"]["title_max"],
                        "max_new_keywords": lo_cfg["max_new_keywords_per_proposal"]},
    }
    provider = ai_provider.get_provider(lo_cfg)
    try:
        generated = provider.generate(context)
    except Exception:
        generated = ai_provider.TemplateProvider().generate(context)   # always-safe fallback (section 19)
        provider = ai_provider.TemplateProvider()

    problems = listing_safety.validate_generated_content(
        generated.get("title"), generated.get("bullet_points"), generated.get("description"),
        cfg, rejected, competitor_brands)
    if problems and provider.name != "template":
        generated = ai_provider.TemplateProvider().generate(context)
        problems = listing_safety.validate_generated_content(
            generated.get("title"), generated.get("bullet_points"), generated.get("description"),
            cfg, rejected, competitor_brands)
    if problems and generated.get("title") and len(generated["title"]) > cfg["catalogue"]["title_max"]:
        generated["title"] = generated["title"][:cfg["catalogue"]["title_max"]].rsplit(" ", 1)[0]
        problems = listing_safety.validate_generated_content(
            generated.get("title"), generated.get("bullet_points"), generated.get("description"),
            cfg, rejected, competitor_brands)
    if problems:
        raise ContentValidationFailed("; ".join(problems))
    return generated


def analyze_sku(ctx, listing):
    """-> Finding | None. None means "no actionable, fact-safe opportunity right now" (already
    good-enough coverage, or every candidate got rejected on product facts) - a normal, non-error
    outcome, not a failure. Raises a ListingOptimizationError subclass for the genuinely exceptional
    cases (section 30) - the calling skill (amos/skills/catalogue.py::optimize_listing_keywords)
    catches those per-SKU so one bad SKU never stops the rest of the account's analysis."""
    conn, account, cfg = ctx.conn, ctx.account, ctx.cfg
    (attrs, category, title, bullets, description, competitive,
     scored, covered, candidates, before_pct) = _competitive_evidence(conn, account, listing, cfg)
    lo_cfg = cfg["listing_optimization"]

    if before_pct >= lo_cfg["good_enough_coverage_pct"]:
        return None   # already covers most of the relevant competitive keyword universe

    eligible, rejected = listing_safety.filter_opportunities(candidates, attrs, cfg, category)
    if not eligible:
        raise NoValidKeywordOpportunities(
            f"{listing['sku']}: {len(candidates)} candidate keyword(s) found but all rejected on product facts")
    eligible = eligible[:lo_cfg["max_new_keywords_per_proposal"]]
    for o in eligible:
        o["gap_type"] = kw.gap_type(o, is_multi_word=(" " in o["keyword"]), high_value_threshold=HIGH_VALUE_SCORE_THRESHOLD)

    product = {"sku": listing["sku"], "product_type": category, "attrs": attrs}
    current = {"title": title, "bullets": bullets, "description": description}
    competitor_brands = [c.get("brand") for c in competitive if c.get("brand")]
    try:
        generated = _generate_and_validate(product, current, eligible, rejected, cfg, competitor_brands)
    except ContentValidationFailed:
        raise

    full_generated_text = kw.normalize(" ".join([generated["title"]] + generated["bullet_points"] + [generated["description"]]))
    added = [o["keyword"] for o in eligible if o["keyword"] in full_generated_text]
    after_pct = round(min(1.0, (len(covered) + len(added)) / len(scored)), 4) if scored else before_pct

    share = units_share(ctx, listing.get("units_30d"))
    high_value_n = sum(1 for o in eligible if o["gap_type"] == "MISSING_HIGH_VALUE_KEYWORD")
    diagnosis = (f"Current listing covers {before_pct * 100:.0f}% of the keyword vocabulary used by "
                 f"{len(competitive)} relevant top-ranking Amazon.in ASIN(s) for '{category}'. "
                 f"{len(candidates)} relevant keyword gap(s) found, {len(eligible)} usable after product-fact "
                 f"validation ({len(rejected)} rejected as unsupported by this product's own attributes), "
                 f"{high_value_n} of them high-value. Proposed content would raise coverage to ~{after_pct * 100:.0f}%.")

    return Finding(
        type="listing_keyword_optimization", entity_type="sku", entity_id=listing["sku"], kind="opportunity",
        title=f"{listing['sku']}: {len(eligible)} keyword opportunity(ies) vs {len(competitive)} top-ranking ASIN(s)",
        diagnosis=diagnosis,
        recommendation={
            "action_type": "UPDATE_LISTING_CONTENT",
            "summary": f"Add {len(eligible)} evidence-backed keyword(s) to title/bullets/description",
            "params": {
                "sku": listing["sku"], "fields": ["title", "bullets", "description"],
                "current_title": title, "current_bullets": bullets, "current_description": description,
                "proposed_title": generated["title"], "proposed_bullets": generated["bullet_points"],
                "proposed_description": generated["description"],
                "keywords_added": added,
            },
        },
        evidence={
            "category": category, "competitive_asin_count": len(competitive),
            "competitive_asins": [{"asin": c["asin"], "rank": c["rank"], "relevance_score": c["relevance_score"],
                                    "source": c.get("source", "mock")} for c in competitive],
            "keyword_opportunities": eligible,
            "rejected_keywords": rejected,
            "keyword_coverage_before_pct": before_pct, "keyword_coverage_after_pct": after_pct,
        },
        impact=clamp(2.5 + share * 30 + high_value_n * 0.8 + (1 - before_pct) * 5),
        urgency=5 if high_value_n else 3, confidence=0.7 if not rejected else 0.6,
        effort=clamp(2 + len(eligible) * 0.3),
        metric={"name": "keyword_coverage", "value": before_pct, "better": "higher", "entity": listing["sku"]},
    )


# ------------------------------------ human-readable report (section 20) ---------------------------
def build_report(conn, account, listing, cfg):
    """-> dict for CLI `listing optimize`/`listing report` and for tests. Raises the same
    ListingOptimizationError subclasses as analyze_sku(); does not require a Ctx (no ctx.publish/
    hero-share needed for a read-only preview report)."""
    (attrs, category, title, bullets, description, competitive,
     scored, covered, candidates, before_pct) = _competitive_evidence(conn, account, listing, cfg)
    lo_cfg = cfg["listing_optimization"]
    eligible, rejected = listing_safety.filter_opportunities(candidates, attrs, cfg, category)
    eligible = eligible[:lo_cfg["max_new_keywords_per_proposal"]]
    report = {
        "sku": listing["sku"], "category": category, "competitive_asin_count": len(competitive),
        "keyword_coverage_before_pct": before_pct, "candidates_found": len(candidates),
        "eligible_opportunities": eligible, "rejected_keywords": rejected,
        "current": {"title": title, "bullets": bullets, "description": description},
    }
    if not eligible:
        report["proposed"] = None
        report["keyword_coverage_after_pct"] = before_pct
        return report
    product = {"sku": listing["sku"], "product_type": category, "attrs": attrs}
    current = {"title": title, "bullets": bullets, "description": description}
    competitor_brands = [c.get("brand") for c in competitive if c.get("brand")]
    generated = _generate_and_validate(product, current, eligible, rejected, cfg, competitor_brands)
    full_generated_text = kw.normalize(" ".join([generated["title"]] + generated["bullet_points"] + [generated["description"]]))
    added = [o["keyword"] for o in eligible if o["keyword"] in full_generated_text]
    report["proposed"] = generated
    report["keyword_coverage_after_pct"] = round(min(1.0, (len(covered) + len(added)) / len(scored)), 4) if scored else before_pct
    return report


def format_report(report):
    lines = ["LISTING OPTIMIZATION REPORT", "", f"SKU: {report['sku']}", f"Category: {report['category']}",
              f"Competitive ASINs: {report['competitive_asin_count']}", "",
              f"Current keyword coverage: {report['keyword_coverage_before_pct'] * 100:.0f}%",
              f"Potential relevant keywords: {report['candidates_found']}",
              f"New keyword opportunities: {len(report['eligible_opportunities'])}",
              f"Rejected keywords: {len(report['rejected_keywords'])}",
              f"Keyword Coverage Improvement: {report['keyword_coverage_before_pct'] * 100:.0f}% -> "
              f"{report['keyword_coverage_after_pct'] * 100:.0f}%", ""]
    if report["rejected_keywords"]:
        lines.append("REJECTED (unsupported by product facts):")
        for r in report["rejected_keywords"][:10]:
            lines.append(f"  - {r['keyword']}: {r['reason']}")
        lines.append("")
    if not report["proposed"]:
        lines.append("No actionable opportunity right now (coverage already good, or nothing usable survived "
                      "product-fact validation).")
        return "\n".join(lines)

    lines.append("TITLE")
    lines.append(f"Current:\n  {report['current']['title']}")
    lines.append(f"Proposed:\n  {report['proposed']['title']}")
    lines.append("")
    lines.append("BULLET POINTS")
    lines.append("Current:")
    lines += [f"  - {b}" for b in report["current"]["bullets"]] or ["  (none on file)"]
    lines.append("Proposed:")
    lines += [f"  - {b}" for b in report["proposed"]["bullet_points"]]
    lines.append("")
    lines.append("DESCRIPTION")
    lines.append(f"Current:\n  {report['current']['description'] or '(none on file)'}")
    lines.append(f"Proposed:\n  {report['proposed']['description']}")
    lines.append("")
    lines.append("Keywords added: " + ", ".join(o["keyword"] for o in report["eligible_opportunities"]))
    return "\n".join(lines)
