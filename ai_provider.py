"""AI PROVIDER abstraction (Listing Optimization, session 6, brief sections 18, 19).

There is no pre-existing AI provider abstraction anywhere else in this codebase (checked before
writing this - AMOS has never called an LLM before this session), so this is the first one. It is
deliberately small and swappable, per the brief: the deterministic keyword engine (amos/keywords.py
+ amos/listing_safety.py) decides WHICH keywords are opportunities/prohibited; a provider here only
turns that decision into natural-language copy - it never invents the keyword strategy itself.

    AIProvider.generate(context: dict) -> {"title": str, "bullet_points": [str,...], "description": str}
    TemplateProvider   - default. Deterministic, offline, zero dependencies, always succeeds.
    AnthropicProvider  - optional. Only used if cfg says "anthropic" AND ANTHROPIC_API_KEY is set;
                         uses stdlib urllib only (no new dependency), and on ANY failure the caller
                         (amos/listing_optimizer.py) falls back to TemplateProvider rather than
                         failing the whole proposal - see AIGenerationFailed there.
"""
import json
import os
import textwrap
import urllib.error
import urllib.request

SYSTEM_PROMPT = textwrap.dedent("""\
    You are writing an Amazon.in product listing. Use only the verified product facts you are given.
    Do not invent attributes (material, color, size, pattern, brand, certifications, occasion).
    Do not copy competitor sentences verbatim. Do not mention competitor brand names.
    Do not make unsupported or misleading claims. Do not keyword-stuff - use keywords naturally,
    each at most once or twice across the whole listing. Preserve factual accuracy above all else.
    Prioritize readability. Respond with ONLY a JSON object shaped exactly like:
    {"title": "...", "bullet_points": ["...", "...", "...", "...", "..."], "description": "..."}
    No preamble, no markdown fences, JSON only.""")


class AIProvider:
    name = "base"

    def generate(self, context):
        raise NotImplementedError


class TemplateProvider(AIProvider):
    """Deterministic, offline generation - places eligible keyword opportunities into the title,
    bullets and description using simple, readable templates (brief sections 13-16). This is the
    default and the guaranteed-safe fallback: it can only ever use facts/keywords it was handed, so
    validate_generated_content() in amos/listing_safety.py always passes against its own output."""
    name = "template"

    def generate(self, context):
        product = context["product"]
        current = context["current_listing"]
        opps = context["keyword_opportunities"]
        max_new = context["constraints"].get("max_new_keywords", 12)
        title_max = context["constraints"].get("title_max", 200)

        # ---- title: current title + up to 2 highest-value phrases not already present ----
        title = (current.get("title") or product.get("product_type") or "").strip()
        added_title = []
        for o in opps:
            if len(added_title) >= 2:
                break
            candidate = f"{title} {o['keyword'].title()}".strip()
            if len(candidate) <= title_max and o["keyword"].lower() not in title.lower():
                title = candidate
                added_title.append(o["keyword"])

        # ---- bullets: keep existing bullets, add/extend up to 5 total, weaving in opportunities ----
        bullets = list(current.get("bullets") or [])
        remaining = [o for o in opps if o["keyword"] not in added_title][:max_new]
        templates = [
            "{kw} - a key reason shoppers pick this {pt}.",
            "Made with attention to {kw} for everyday reliability.",
            "Great choice for {kw}.",
            "Designed with {kw} in mind, without compromising on comfort.",
            "Trusted {pt} feature: {kw}.",
        ]
        pt = product.get("product_type") or "product"
        bi = 0
        while len(bullets) < 5 and remaining:
            o = remaining.pop(0)
            bullets.append(templates[bi % len(templates)].format(kw=o["keyword"], pt=pt))
            bi += 1
        # if we already have 5+ bullets, still try to naturally extend the last couple with any
        # remaining high-value phrases rather than dropping them entirely
        for o in remaining[:2]:
            bullets.append(templates[bi % len(templates)].format(kw=o["keyword"], pt=pt))
            bi += 1

        # ---- description: current description + a closing paragraph weaving remaining phrases ----
        description = (current.get("description") or "").strip()
        left = [o["keyword"] for o in opps if o["keyword"] not in added_title][:max_new]
        if left:
            extra = ("This " + pt + " is also a strong fit for shoppers searching for " +
                     ", ".join(left[:6]) + ", combining practicality with everyday value.")
            description = (description + " " + extra).strip() if description else extra

        return {"title": title, "bullet_points": bullets[:8], "description": description}


class AnthropicProvider(AIProvider):
    """Optional. Calls the Anthropic Messages API with stdlib urllib only (no new dependency).
    Never used unless the account config explicitly opts in AND ANTHROPIC_API_KEY is set; any
    failure (network, auth, bad JSON) raises so the caller can fall back to TemplateProvider."""
    name = "anthropic"
    API_URL = "https://api.anthropic.com/v1/messages"
    MODEL = "claude-sonnet-4-6"

    def generate(self, context):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        body = json.dumps({
            "model": self.MODEL, "max_tokens": 1200, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": json.dumps(context, default=str)}],
        }).encode()
        req = urllib.request.Request(self.API_URL, data=body, method="POST", headers={
            "Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        out = json.loads(text)
        if not out.get("title") or not out.get("bullet_points"):
            raise ValueError("AI response missing required fields")
        return out


PROVIDERS = {"template": TemplateProvider(), "anthropic": AnthropicProvider()}


def get_provider(cfg):
    return PROVIDERS.get((cfg or {}).get("ai_provider", "template"), PROVIDERS["template"])
