"""AMOS core: configuration defaults, database schema, small helpers.

Everything is centred on ACCOUNT (client -> account/marketplace/store -> listing/sku/asin).
"""
import copy
import datetime as dt
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("AMOS_DB", os.path.join(ROOT, "data", "amos.db"))

# --------------------------------------------------------------------------------------
# Configuration. Every account can override any key through accounts.config_json.
# All money is in the account currency (INR for Amazon India). Percentages are fractions.
# --------------------------------------------------------------------------------------
DEFAULT_CONFIG = {
    # System phase = how much autonomy the OS is allowed (see HANDOVER.md, section "Phases")
    #   1 monitoring + tasks | 2 recommendations | 3 semi-autonomous (level<=1 auto)
    #   4 autonomous within guardrails (level 2 actions whitelisted in autonomy.l2_auto_actions)
    "phase": 1,
    "autonomy": {"l2_auto_actions": []},
    "execution": {"mode": "dry_run"},            # dry_run | live (live needs real connectors)
    "targets": {"acos": 0.25, "tacos": 0.12, "target_margin": 0.15, "min_conversion": 0.08},
    "ads": {
        "wasted_min_clicks": 12, "wasted_min_spend": 150, "max_auto_negatives": 25,
        "bid_change_limit_pct": 10, "high_acos_factor": 1.3, "min_campaign_spend": 500,
        "budget_util_threshold": 0.9, "harvest_min_orders": 2, "budget_increase_pct": 25,
        "protected_terms": [],                    # brand terms that must never be negated
    },
    "catalogue": {
        "title_min": 80, "title_max": 200, "bullets_min": 5, "images_min": 7,
        "description_min": 300, "aplus_for_hero": True, "min_sessions_for_conv": 150,
        "hero_share": 0.6,
    },
    "inventory": {
        "target_cover_days": 30, "min_buffer_days": 7, "default_lead_time_days": 10,
        "overstock_days": 120, "overstock_min_units": 30,
    },
    "operations": {"at_risk_hours": 24},
    "returns": {"return_rate_threshold": 0.10, "min_returned_units": 5, "pattern_share": 0.35},
    "buybox": {"drop_pts": 0.20, "min_pct": 0.60},
    "pricing": {"default_fee_pct": 0.18, "max_auto_price_change_pct": 5, "max_discount_pct": 0.65},
    "analytics": {"sales_drop_pct": 0.25},
    "verification": {"measure_days": 7},
    # Session 6: Listing Optimization (keyword intelligence from top-ranking Amazon.in ASINs).
    # See amos/listing_optimizer.py / amos/keywords.py / amos/competitive_search.py.
    "listing_optimization": {
        "top_asins_limit": 10,                 # configurable N (brief section 3) - never hard-coded elsewhere
        "min_competitor_relevance": 0.5,       # 0-1 relevance score floor (section 4) below which an ASIN is dropped
        "min_competitive_set": 3,              # fewer relevant ASINs than this -> INSUFFICIENT_COMPETITIVE_DATA
        "min_competitor_coverage": 0.2,        # a keyword needs >= this fraction of the competitive set using it
        "good_enough_coverage_pct": 0.75,      # stop proposing once client coverage of scored keywords reaches this
        "max_new_keywords_per_proposal": 12,   # cap on how many opportunities one proposal tries to place
        "keyword_stuffing_max_repeat": 3,      # a phrase repeated more than this many times in generated copy fails validation
        "ai_provider": "template",             # template (offline, deterministic) | anthropic (needs ANTHROPIC_API_KEY)
        "measurement_windows_days": [7, 14, 30],
        "relevance_weights": {                 # section 4: competitive-set relevance scoring
            "product_type": 3.0, "category": 3.0, "gender": 2.0, "material": 1.0,
            "use_case": 1.0, "attribute": 0.5,
        },
        "keyword_score_weights": {             # section 7: keyword scoring model
            "frequency": 0.8, "competitor_coverage": 2.0, "ranking_position": 1.5,
            "relevance": 1.0, "attribute_match": 0.5, "search_context": 0.3,
        },
    },
    "data_freshness": {
        # max age (days) of a kind's latest data date before amos/coverage.py flags it 'stale'
        # instead of 'available'. Override per-account, e.g. set_account_config(conn, aid,
        # {"data_freshness": {"campaigns": 3}}). Kinds with no per-row date (listings) are
        # intentionally absent - see coverage.py's TABLES for why.
        "inventory": 3, "campaigns": 7, "search_terms": 7, "orders": 3,
        "returns": 14, "daily_performance": 3, "health_metrics": 7,
    },
    "compliance": {
        "hsn_required": True,
        # Example rules only - confirm real mandatory attributes / HSN with your CA and Amazon templates.
        "category_rules": {
            "Kurta Sets": {
                "mandatory_attrs": ["color", "size", "fabric", "pattern", "sleeve_type",
                                    "neck_style", "occasion", "bottom_type"],
                "allowed_values": {
                    "size": ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "Free Size"],
                    "fabric": ["Cotton", "Rayon", "Cotton Blend", "Silk Blend", "Polyester", "Georgette"],
                    "sleeve_type": ["Sleeveless", "Short Sleeve", "3/4 Sleeve", "Long Sleeve"],
                    "bottom_type": ["Palazzo", "Pants", "Churidar", "Salwar", "Skirt"],
                },
                "hsn_prefixes": ["6104", "6204", "6211"],
            },
            "Kurtis": {
                "mandatory_attrs": ["color", "size", "fabric", "pattern", "sleeve_type"],
                "allowed_values": {"size": ["XS", "S", "M", "L", "XL", "XXL", "3XL", "Free Size"]},
                "hsn_prefixes": ["6104", "6204", "6206"],
            },
        },
    },
}


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def now():
    return dt.datetime.now().isoformat(timespec="microseconds")


def today():
    v = os.environ.get("AMOS_TODAY")
    return dt.date.fromisoformat(v) if v else dt.date.today()


SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL, marketplace TEXT NOT NULL DEFAULT 'Amazon India',
  store TEXT, currency TEXT DEFAULT 'INR', config_json TEXT DEFAULT '{}', created_at TEXT);

-- ---- source data (each ingest is a snapshot) ------------------------------------------
CREATE TABLE IF NOT EXISTS listings(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, sku TEXT NOT NULL, asin TEXT, title TEXT,
  brand TEXT, category TEXT, bullet_count INTEGER DEFAULT 0, description_len INTEGER DEFAULT 0,
  image_count INTEGER DEFAULT 0, has_aplus INTEGER DEFAULT 0, price REAL, mrp REAL, cost REAL,
  fee_pct REAL, shipping_cost REAL DEFAULT 0, hsn TEXT, attrs_json TEXT DEFAULT '{}',
  status TEXT DEFAULT 'active', status_reason TEXT, buy_box_pct REAL, buy_box_pct_prev REAL,
  buybox_price REAL, sessions INTEGER DEFAULT 0, conversion_pct REAL, units_30d INTEGER DEFAULT 0,
  updated_at TEXT, UNIQUE(account_id, sku));
CREATE TABLE IF NOT EXISTS inventory(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, sku TEXT NOT NULL, asin TEXT,
  stock INTEGER DEFAULT 0, inbound INTEGER DEFAULT 0, avg_daily_sales REAL DEFAULT 0,
  lead_time_days INTEGER, snapshot_date TEXT, UNIQUE(account_id, sku));
CREATE TABLE IF NOT EXISTS ad_campaigns(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, campaign TEXT NOT NULL, daily_budget REAL,
  impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, spend REAL DEFAULT 0, sales REAL DEFAULT 0,
  orders INTEGER DEFAULT 0, days INTEGER DEFAULT 7, report_date TEXT, UNIQUE(account_id, campaign));
CREATE TABLE IF NOT EXISTS ad_search_terms(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, campaign TEXT, ad_group TEXT, search_term TEXT,
  match_type TEXT, impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, spend REAL DEFAULT 0,
  sales REAL DEFAULT 0, orders INTEGER DEFAULT 0, report_date TEXT);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, order_id TEXT NOT NULL, sku TEXT, status TEXT,
  order_date TEXT, ship_by TEXT, shipped_date TEXT, UNIQUE(account_id, order_id, sku));
CREATE TABLE IF NOT EXISTS returns(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, sku TEXT, order_id TEXT, reason TEXT,
  qty INTEGER DEFAULT 1, return_date TEXT);
CREATE TABLE IF NOT EXISTS daily_performance(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, date TEXT NOT NULL, sales REAL DEFAULT 0,
  orders INTEGER DEFAULT 0, sessions INTEGER DEFAULT 0, ad_spend REAL DEFAULT 0, ad_sales REAL DEFAULT 0,
  UNIQUE(account_id, date));
CREATE TABLE IF NOT EXISTS health_metrics(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, metric TEXT NOT NULL, value REAL,
  limit_value REAL, direction TEXT DEFAULT 'max', snapshot_date TEXT, UNIQUE(account_id, metric));
CREATE TABLE IF NOT EXISTS data_sources(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, kind TEXT NOT NULL, rows INTEGER,
  ingested_at TEXT, UNIQUE(account_id, kind));

-- ---- engine tables ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, account_id INTEGER, topic TEXT, payload_json TEXT, source TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS issues(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, fingerprint TEXT NOT NULL, skill TEXT,
  department TEXT, type TEXT, kind TEXT DEFAULT 'issue', entity_type TEXT, entity_id TEXT, title TEXT,
  evidence_json TEXT, diagnosis TEXT, recommendation_json TEXT, metric_json TEXT,
  impact REAL, urgency REAL, confidence REAL, effort REAL, score REAL, priority INTEGER,
  status TEXT DEFAULT 'open', first_seen TEXT, last_seen TEXT, cleared_at TEXT,
  UNIQUE(account_id, fingerprint));
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, issue_id INTEGER, department TEXT, title TEXT,
  description TEXT, action_type TEXT, action_payload_json TEXT, priority INTEGER, score REAL,
  stage TEXT, status TEXT, autonomy_level INTEGER, autonomy_reason TEXT, assignee TEXT, due_date TEXT,
  attempts INTEGER DEFAULT 0, baseline_json TEXT, measure_due TEXT, outcome TEXT,
  created_at TEXT, updated_at TEXT, executed_at TEXT, verified_at TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS task_history(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, stage TEXT, actor TEXT, note TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY, task_id INTEGER, account_id INTEGER, connector TEXT, action_type TEXT,
  payload_json TEXT, result_json TEXT, mode TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS measurements(
  id INTEGER PRIMARY KEY, task_id INTEGER, metric TEXT, before_value REAL, after_value REAL,
  delta REAL, verdict TEXT, measured_at TEXT);
CREATE TABLE IF NOT EXISTS learning(
  id INTEGER PRIMARY KEY, account_id INTEGER, action_type TEXT, department TEXT, outcome TEXT, created_at TEXT);
-- Evidence layer (amos/evidence.py): structured, traceable facts behind an issue. Rows are
-- replaced (not appended) each time an OPEN issue's evidence is refreshed - see evidence.py's
-- save_issue_evidence() docstring for why that still satisfies "historical evidence must survive
-- current-data changes" (an issue that clears simply stops being refreshed; its last evidence sticks).
CREATE TABLE IF NOT EXISTS evidence(
  id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL, source_kind TEXT, source_table TEXT,
  source_label TEXT, entity_type TEXT, entity_id TEXT, field TEXT, actual_value TEXT,
  expected_value TEXT, operator TEXT, observation TEXT, data_date TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_evidence_issue ON evidence(issue_id);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, account_id INTEGER, actor TEXT, action TEXT, entity_type TEXT, entity_id TEXT,
  detail_json TEXT, at TEXT);
CREATE INDEX IF NOT EXISTS ix_issues_acct ON issues(account_id, status);
CREATE INDEX IF NOT EXISTS ix_tasks_acct ON tasks(account_id, status);
-- Session 6: Listing Optimization. Caches the top-ranking-ASIN competitive set discovered per SKU
-- (amos/competitive_search.py) so a cycle is deterministic/fast and there is a durable record of
-- "who we compared against" for a given proposal - not a data warehouse, just the last discovery.
CREATE TABLE IF NOT EXISTS competitive_asins(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, sku TEXT NOT NULL, asin TEXT, rank INTEGER,
  title TEXT, bullets_text TEXT, description TEXT, brand TEXT, category TEXT, relevance_score REAL,
  source TEXT DEFAULT 'mock', fetched_at TEXT);
CREATE INDEX IF NOT EXISTS ix_competitive_asins_sku ON competitive_asins(account_id, sku);
"""


def _ensure_columns(conn, table, columns):
    """Additive-only schema migration: add any column in `columns` (name -> SQL type/clause) that a
    pre-existing on-disk database's `table` does not already have. SCHEMA above only ever runs
    CREATE TABLE IF NOT EXISTS, which does nothing for a table that already exists - this is what
    lets Session 6 add `listings.description` / `listings.bullets_text` (full listing copy; the
    older `description_len` / `bullet_count` columns stay exactly as they were) without breaking
    or migrating anyone's existing data.db. Never drops or renames a column.
    """
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def connect(path=None):
    path = path or DB_PATH
    if path != ":memory:":
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "listings", {"description": "TEXT", "bullets_text": "TEXT"})
    return conn


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def execute(conn, sql, params=()):
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid


def dumps(obj):
    return json.dumps(obj, default=str, ensure_ascii=False)


def loads(s, default=None):
    if s in (None, ""):
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def audit(conn, account_id, actor, action, entity_type=None, entity_id=None, detail=None):
    execute(conn,
            "INSERT INTO audit_log(account_id,actor,action,entity_type,entity_id,detail_json,at) VALUES(?,?,?,?,?,?,?)",
            (account_id, actor, action, entity_type, None if entity_id is None else str(entity_id),
             dumps(detail or {}), now()))


def get_account(conn, account_id):
    a = row(conn, "SELECT a.*, c.name AS client_name FROM accounts a JOIN clients c ON c.id=a.client_id WHERE a.id=?",
            (account_id,))
    if not a:
        raise KeyError(f"account {account_id} not found")
    a["config"] = deep_merge(DEFAULT_CONFIG, loads(a.get("config_json"), {}))
    return a


def create_client_account(conn, client_name, marketplace="Amazon India", store=None, config=None, currency=None):
    """Reuses (never duplicates) a client by name, then always inserts a new account row - a client
    can legitimately have several accounts (e.g. Amazon India + Amazon UAE). `currency` is the
    account's reporting/display currency only (accounts.currency) - there is no conversion anywhere
    in AMOS; if omitted the schema's own default ('INR') applies, same as before this parameter existed."""
    c = row(conn, "SELECT id FROM clients WHERE name=?", (client_name,))
    cid = c["id"] if c else execute(conn, "INSERT INTO clients(name,created_at) VALUES(?,?)", (client_name, now()))
    if currency:
        aid = execute(conn,
            "INSERT INTO accounts(client_id,marketplace,store,currency,config_json,created_at) VALUES(?,?,?,?,?,?)",
            (cid, marketplace, store or client_name, currency, dumps(config or {}), now()))
    else:
        aid = execute(conn, "INSERT INTO accounts(client_id,marketplace,store,config_json,created_at) VALUES(?,?,?,?,?)",
                      (cid, marketplace, store or client_name, dumps(config or {}), now()))
    audit(conn, aid, "system", "account_created", "account", aid, {"client": client_name})
    return aid


def set_account_config(conn, account_id, patch):
    a = row(conn, "SELECT config_json FROM accounts WHERE id=?", (account_id,))
    cur = loads(a["config_json"], {})
    execute(conn, "UPDATE accounts SET config_json=? WHERE id=?", (dumps(deep_merge(cur, patch)), account_id))
    audit(conn, account_id, "human", "config_updated", "account", account_id, patch)


# --------------------------------------------------------------------------------------
# Account creation & configuration (Session 5). This is the single entry point the CLI
# (`python -m amos accounts create`) and the dashboard (`POST /api/accounts`) both call -
# it is a thin orchestration over create_client_account()/set_account_config() above, not a
# second account system. No new tables, no new identity or phase/mode rules.
# --------------------------------------------------------------------------------------

# Initial marketplace list for dropdowns/help text only - marketplace itself stays free text in
# the schema (see accounts.marketplace), so adding one more later is just adding it here (and, if
# it needs a sensible default currency, to the map below); nothing else has to change.
MARKETPLACES = ["Amazon India", "Amazon UAE", "Amazon USA", "Amazon UK", "Amazon Saudi Arabia", "Amazon Germany"]

MARKETPLACE_DEFAULT_CURRENCY = {
    "Amazon India": "INR", "Amazon UAE": "AED", "Amazon USA": "USD",
    "Amazon UK": "GBP", "Amazon Saudi Arabia": "SAR", "Amazon Germany": "EUR",
}

# Reporting/display currency only - see the DEFAULT_CONFIG module docstring: no conversion anywhere.
VALID_CURRENCIES = {"INR", "AED", "USD", "GBP", "SAR", "EUR"}

# Same phase system as DEFAULT_CONFIG["phase"] / engines/policy.py - not a second phase system.
VALID_PHASES = {1, 2, 3, 4}

# Same execution modes as DEFAULT_CONFIG["execution"]["mode"] / engines/execution.py.
VALID_MODES = {"dry_run", "live"}


class AccountCreationError(ValueError):
    """Any invalid input or duplicate-account problem when creating a real account. Subclasses
    ValueError so amos/server.py's existing route dispatch (which already maps ValueError -> HTTP
    400) handles it with no extra wiring; amos/cli.py catches it to print a readable CLI error."""


def create_account(conn, client_name, marketplace, store=None, currency=None, phase=1, mode="dry_run"):
    """Create one real account through the existing account system. Validates input, refuses a
    duplicate (client, marketplace, store) combination, then calls create_client_account() and
    set_account_config() exactly as amos/demo.py::seed() already does. Returns the new account
    (see get_account) on success; raises AccountCreationError otherwise."""
    client_name = (client_name or "").strip()
    marketplace = (marketplace or "").strip()
    store_name = (store or "").strip() or client_name

    if not client_name:
        raise AccountCreationError("client name is required")
    if not marketplace:
        raise AccountCreationError("marketplace is required")

    if phase is None:
        phase = 1
    try:
        phase = int(phase)
    except (TypeError, ValueError):
        raise AccountCreationError(f"invalid phase '{phase}' (must be an integer, one of {sorted(VALID_PHASES)})")
    if phase not in VALID_PHASES:
        raise AccountCreationError(f"invalid phase {phase} (must be one of {sorted(VALID_PHASES)})")

    mode = (mode or "dry_run").strip()
    if mode not in VALID_MODES:
        raise AccountCreationError(f"invalid mode '{mode}' (must be one of {sorted(VALID_MODES)})")

    currency = (currency or MARKETPLACE_DEFAULT_CURRENCY.get(marketplace) or "INR").strip().upper()
    if currency not in VALID_CURRENCIES:
        raise AccountCreationError(f"invalid currency '{currency}' (must be one of {sorted(VALID_CURRENCIES)})")

    dup = row(conn,
        "SELECT a.id FROM accounts a JOIN clients c ON c.id=a.client_id WHERE c.name=? AND a.marketplace=? AND a.store=?",
        (client_name, marketplace, store_name))
    if dup:
        raise AccountCreationError(
            f"an account already exists for client '{client_name}', marketplace '{marketplace}', "
            f"store '{store_name}' (account #{dup['id']}) - not creating a duplicate")

    try:
        aid = create_client_account(conn, client_name, marketplace, store_name, currency=currency)
        set_account_config(conn, aid, {"phase": phase, "execution": {"mode": mode}})
    except sqlite3.Error as e:
        raise AccountCreationError(f"database error creating account: {e}") from e

    return get_account(conn, aid)
