"""DATA INGESTION (V1 = files; the same `ingest_rows` will be fed by SP-API / Ads API later).

Each upload is a snapshot of one KIND of report for one account. Column names are matched loosely
(case/spacing/punctuation ignored, common Amazon report headers accepted). Unknown columns on a
listings file become listing ATTRIBUTES (color, size, fabric ...) used by the compliance validator.
"""
import csv
import datetime as dt
import io
import re

from . import core

KINDS = ["listings", "inventory", "campaigns", "search_terms", "orders", "returns", "daily_performance", "health_metrics"]

# canonical column -> accepted (normalised) header names. Value type in TYPES.
ALIASES = {
    "listings": {
        "sku": ["sku", "seller_sku", "item_sku", "merchant_sku"], "asin": ["asin", "asin1"],
        "title": ["title", "item_name", "product_name"], "brand": ["brand", "brand_name"],
        "category": ["category", "product_type", "item_type", "category_name"],
        "bullet_count": ["bullet_count", "bullets", "no_of_bullets"], "description_len": ["description_len", "description_length"],
        "bullets_text": ["bullets_text", "bullet_points", "key_features", "bullet_points_text"],
        "description": ["description", "product_description", "long_description"],
        "image_count": ["image_count", "images", "no_of_images"], "has_aplus": ["has_aplus", "a_plus", "aplus"],
        "price": ["price", "selling_price", "your_price"], "mrp": ["mrp", "maximum_retail_price"], "cost": ["cost", "cogs", "unit_cost"],
        "fee_pct": ["fee_pct", "referral_fee_pct"], "shipping_cost": ["shipping_cost", "fulfilment_cost"],
        "hsn": ["hsn", "hsn_code"], "status": ["status", "listing_status"], "status_reason": ["status_reason", "suppression_reason"],
        "buy_box_pct": ["buy_box_pct", "buy_box_percentage", "featured_offer_percentage"],
        "buy_box_pct_prev": ["buy_box_pct_prev", "buy_box_previous"], "buybox_price": ["buybox_price", "buy_box_price"],
        "sessions": ["sessions"], "conversion_pct": ["conversion_pct", "conversion", "unit_session_percentage"],
        "units_30d": ["units_30d", "units_ordered", "units_sold"],
    },
    "inventory": {
        "sku": ["sku", "seller_sku"], "asin": ["asin"], "stock": ["stock", "quantity", "available", "afn_fulfillable_quantity"],
        "inbound": ["inbound", "inbound_qty", "afn_inbound_working_quantity"], "avg_daily_sales": ["avg_daily_sales", "daily_sales"],
        "lead_time_days": ["lead_time_days", "lead_time"], "snapshot_date": ["snapshot_date", "date"],
    },
    "campaigns": {
        "campaign": ["campaign", "campaign_name"], "daily_budget": ["daily_budget", "budget"], "impressions": ["impressions"],
        "clicks": ["clicks"], "spend": ["spend", "cost"], "sales": ["sales", "7_day_total_sales", "14_day_total_sales"],
        "orders": ["orders", "7_day_total_orders", "14_day_total_orders"], "days": ["days"], "report_date": ["report_date", "date"],
    },
    "search_terms": {
        "campaign": ["campaign", "campaign_name"], "ad_group": ["ad_group", "ad_group_name"],
        "search_term": ["search_term", "customer_search_term"], "match_type": ["match_type"], "impressions": ["impressions"],
        "clicks": ["clicks"], "spend": ["spend", "cost"], "sales": ["sales", "7_day_total_sales", "14_day_total_sales"],
        "orders": ["orders", "7_day_total_orders", "14_day_total_orders"], "report_date": ["report_date", "date"],
    },
    "orders": {
        "order_id": ["order_id", "amazon_order_id"], "sku": ["sku", "seller_sku"], "status": ["status", "order_status"],
        "order_date": ["order_date", "purchase_date"], "ship_by": ["ship_by", "latest_ship_date", "ship_by_date"],
        "shipped_date": ["shipped_date", "ship_date"],
    },
    "returns": {"sku": ["sku", "seller_sku"], "order_id": ["order_id"], "reason": ["reason", "return_reason"],
                "qty": ["qty", "quantity"], "return_date": ["return_date", "date"]},
    "daily_performance": {"date": ["date"], "sales": ["sales", "ordered_product_sales"], "orders": ["orders", "units_ordered"],
                          "sessions": ["sessions"], "ad_spend": ["ad_spend"], "ad_sales": ["ad_sales"]},
    "health_metrics": {"metric": ["metric", "name"], "value": ["value", "current"], "limit_value": ["limit_value", "limit", "target"],
                       "direction": ["direction"], "snapshot_date": ["snapshot_date", "date"]},
}
FRACTIONS = {"fee_pct", "buy_box_pct", "buy_box_pct_prev", "conversion_pct", "value", "limit_value"}
INTS = {"bullet_count", "description_len", "image_count", "sessions", "units_30d", "stock", "inbound", "lead_time_days",
        "impressions", "clicks", "orders", "days", "qty"}
FLOATS = {"price", "mrp", "cost", "shipping_cost", "buybox_price", "avg_daily_sales", "daily_budget", "spend", "sales",
          "ad_spend", "ad_sales"}
DATES = {"snapshot_date", "report_date", "order_date", "ship_by", "shipped_date", "return_date", "date"}
BOOLS = {"has_aplus"}
REQUIRED = {"listings": "sku", "inventory": "sku", "campaigns": "campaign", "search_terms": "search_term",
            "orders": "order_id", "returns": "sku", "daily_performance": "date", "health_metrics": "metric"}


def norm_key(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[₹,\s]|rs\.?|inr", "", str(v), flags=re.I).replace("%", "")
    if s in ("", "-", "--", "n/a", "na", "none", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def frac(v):
    if v is None or str(v).strip() == "":
        return None
    n = num(v)
    if n is None:
        return None
    if isinstance(v, str) and v.strip().endswith("%"):
        return n / 100
    return n / 100 if n > 1 else n


def date_str(v):
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, (dt.datetime, dt.date)):
        return v.date().isoformat() if isinstance(v, dt.datetime) else v.isoformat()
    s = str(v).strip()[:19]
    for f in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return s


def truthy(v):
    return 1 if str(v).strip().lower() in ("1", "yes", "y", "true", "t") else 0


def split_bullets(text):
    """Bullets are stored as one delimited TEXT field (bullets_text) - split on '|' or newlines,
    whichever the source used; each Amazon bullet point becomes one list entry."""
    if not text:
        return []
    parts = text.split("|") if "|" in text else text.splitlines()
    return [p.strip() for p in parts if p.strip()]


def parse_attrs(listing_row):
    return core.loads(listing_row.get("attrs_json"), {}) or {}


# ------------------------------------ reading ------------------------------------------------------
def read_table(path):
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError as e:
            raise RuntimeError("Reading .xlsx needs 'pip install openpyxl' (or export the sheet as CSV)") from e
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
        it = ws.iter_rows(values_only=True)
        header = None
        out = []
        for r in it:
            if header is None:
                if r and any(c not in (None, "") for c in r):
                    header = [str(c) if c is not None else "" for c in r]
                continue
            if r and any(c not in (None, "") for c in r):
                out.append(dict(zip(header, r)))
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_csv_text(text):
    return list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))


# ------------------------------------ canonicalising -----------------------------------------------
def canon(kind, raw):
    alias_map = {a: canonical for canonical, al in ALIASES[kind].items() for a in al}
    out, attrs = {}, {}
    for k, v in raw.items():
        nk = norm_key(k)
        if not nk:
            continue
        c = alias_map.get(nk)
        if c and c not in out:
            out[c] = v
        elif kind == "listings" and not c:
            if v not in (None, ""):
                attrs[nk] = str(v).strip()
    res = {}
    for c, v in out.items():
        if c in FRACTIONS:
            res[c] = frac(v)
        elif c in INTS:
            n = num(v)
            res[c] = int(n) if n is not None else None
        elif c in FLOATS:
            res[c] = num(v)
        elif c in DATES:
            res[c] = date_str(v)
        elif c in BOOLS:
            res[c] = truthy(v)
        else:
            res[c] = None if v is None else str(v).strip()
    if kind == "listings":
        res["_attrs"] = attrs
    return res


def listing_for_validation(raw, default_category=None):
    c = canon("listings", raw)
    return {"sku": c.get("sku"), "category": c.get("category") or default_category, "hsn": c.get("hsn"), "attrs": c.get("_attrs", {})}


# ------------------------------------ writing ------------------------------------------------------
def _upsert(conn, table, keys, data):
    cols = list(data)
    upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
    sql = (f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
           f"ON CONFLICT({','.join(keys)}) DO UPDATE SET {upd}")
    conn.execute(sql, [data[c] for c in cols])


def ingest_rows(conn, account_id, kind, raw_rows):
    if kind not in KINDS:
        raise ValueError(f"unknown kind '{kind}'. Use one of {KINDS}")
    key = REQUIRED[kind]
    n, skipped = 0, 0
    if kind in ("campaigns", "search_terms", "returns"):
        conn.execute(f"DELETE FROM {'ad_campaigns' if kind == 'campaigns' else 'ad_search_terms' if kind == 'search_terms' else 'returns'} WHERE account_id=?", (account_id,))
    table = {"listings": "listings", "inventory": "inventory", "campaigns": "ad_campaigns", "search_terms": "ad_search_terms",
             "orders": "orders", "returns": "returns", "daily_performance": "daily_performance", "health_metrics": "health_metrics"}[kind]
    today = core.today().isoformat()
    for raw in raw_rows:
        c = canon(kind, raw)
        if not c.get(key):
            skipped += 1
            continue
        c["account_id"] = account_id
        if kind == "listings":
            attrs = c.pop("_attrs", {})
            c["attrs_json"] = core.dumps(attrs)
            c["updated_at"] = core.now()
            c["status"] = (c.get("status") or "active").lower()
            # Session 6: if the file carries full bullet/description text but not their counts,
            # derive bullet_count/description_len so the existing catalogue audit (which only
            # ever looks at the counts) keeps working unchanged either way.
            if c.get("bullets_text") and c.get("bullet_count") is None:
                c["bullet_count"] = len(split_bullets(c["bullets_text"]))
            if c.get("description") and c.get("description_len") is None:
                c["description_len"] = len(c["description"])
            ex = core.row(conn, "SELECT buy_box_pct FROM listings WHERE account_id=? AND sku=?", (account_id, c["sku"]))
            if c.get("buy_box_pct_prev") is None and ex:
                c["buy_box_pct_prev"] = ex["buy_box_pct"]
            _upsert(conn, table, ["account_id", "sku"], c)
        elif kind == "inventory":
            c.setdefault("snapshot_date", today)
            _upsert(conn, table, ["account_id", "sku"], c)
        elif kind == "orders":
            c["sku"] = c.get("sku") or ""
            _upsert(conn, table, ["account_id", "order_id", "sku"], c)
        elif kind == "daily_performance":
            _upsert(conn, table, ["account_id", "date"], c)
        elif kind == "health_metrics":
            c["direction"] = (c.get("direction") or "max").lower()
            c.setdefault("snapshot_date", today)
            _upsert(conn, table, ["account_id", "metric"], c)
        elif kind == "campaigns":
            c.setdefault("days", 7)
            c["days"] = c.get("days") or 7
            c.setdefault("report_date", today)
            _upsert(conn, table, ["account_id", "campaign"], c)
        else:   # search_terms / returns are plain snapshot inserts
            c.setdefault("return_date" if kind == "returns" else "report_date", today)
            c.setdefault("qty", 1) if kind == "returns" else None
            cols = list(c)
            conn.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [c[k] for k in cols])
        n += 1
    conn.commit()
    touch_source(conn, account_id, kind, n)
    core.audit(conn, account_id, "human", "data_ingested", "data_source", kind, {"rows": n, "skipped": skipped})
    return {"kind": kind, "rows": n, "skipped": skipped}


def touch_source(conn, account_id, kind, rows=None):
    core.execute(conn, """INSERT INTO data_sources(account_id,kind,rows,ingested_at) VALUES(?,?,?,?)
        ON CONFLICT(account_id,kind) DO UPDATE SET rows=COALESCE(excluded.rows, rows), ingested_at=excluded.ingested_at""",
                 (account_id, kind, rows, core.now()))


def ingest_file(conn, account_id, kind, path):
    return ingest_rows(conn, account_id, kind, read_table(path))


def ingest_csv_text(conn, account_id, kind, text):
    return ingest_rows(conn, account_id, kind, read_csv_text(text))


# ------------------------------------ pre-upload validator ----------------------------------------
def precheck_rows(raw_rows, cfg, default_category=None):
    """PASS/FAIL every catalogue row BEFORE upload. Returns {'pass': bool, 'checked': n, 'failed': n, 'rows': [...]}"""
    from .skills.compliance import validate_listing
    results = []
    for raw in raw_rows:
        r = listing_for_validation(raw, default_category)
        if not r["sku"]:
            continue
        errs = validate_listing(r, cfg)
        results.append({"sku": r["sku"], "category": r["category"], "status": "FAIL" if errs else "PASS", "errors": errs})
    failed = sum(1 for r in results if r["status"] == "FAIL")
    return {"pass": failed == 0, "checked": len(results), "failed": failed, "rows": results}
