# HANDOVER — Desiretech Account Management OS (AMOS)

> Status: **V1 COMPLETE** — builds, all tests pass, CLI + dashboard verified end-to-end.

## What this is

A hierarchical Account Management Operating System for Amazon (India first), modelled on how a
real e-commerce agency works — see `ARCHITECTURE.md` for the full brief. In one line:

    Management -> Account Director -> Department Managers -> Task Agents (skills)
      -> Detect -> Analyze -> Diagnose -> Recommend -> Approve -> Execute -> Verify -> Measure -> Learn

## What happened this session

The previous build session produced working code but the hand-off zip lost its directory
structure — every file (including three different `__init__.py`s meant for three different
folders) ended up flattened into one folder, and `SKILL.md` had been overwritten with an unrelated
Anthropic frontend-design skill file. This session:

1. Reconstructed the intended package layout from each file's own relative imports
   (`from .base import ...` vs `from ..core import ...` tells you exactly which subpackage a file
   belongs in) — see the tree in `README.md`.
2. Rewrote the two missing `__init__.py` files (`amos/__init__.py`, `amos/skills/__init__.py`) —
   the third (`amos/engines/__init__.py`) had survived intact and just needed moving back.
3. Replaced the corrupted `SKILL.md` with `ARCHITECTURE.md`, carrying the real 25-section brief.
4. Ran the full test suite (29/29 pass) and manually exercised the whole lifecycle: seed -> cycle
   (25 issues/tasks detected from demo data) -> approve -> execute -> demo-fix -> re-cycle
   (issues verified as cleared, learning recorded) -> dashboard API -> web UI -> precheck.
5. Added `README.md` with setup/usage instructions that weren't written before.

No application logic changed — every `.py` file's contents are exactly as the prior session left
them; only the file layout, the two missing `__init__.py`s, and the docs were fixed.

## Build checklist

- [x] `amos/core.py` (db, schema, config)
- [x] `amos/skills/*` (detection skills) — advertising, catalogue, compliance, health_pricing_cx,
      inventory_ops, metrics, base (Finding/Ctx/NoData/@skill)
- [x] `amos/engines/*` (exception, priority, policy, tasks, execution, verification, learning)
- [x] `amos/org.py` + `director.py`
- [x] `amos/ingest.py`
- [x] `amos/reporting.py`
- [x] `amos/cli.py` + `server.py` + `web/index.html`
- [x] `amos/demo.py` (seed + simulated fixes)
- [x] `tests/` — 29 tests, all passing
- [x] `README.md`, final `HANDOVER.md`, `ARCHITECTURE.md`

## Verified working (this session)

```
python -m amos seed                    -> account #1 "Krishna Enterprises" + sample CSVs
python -m amos cycle --account 1       -> 25 new issues, 25 tasks created (P1..P4 buckets correct)
python -m amos brief --account 1       -> exception-driven Markdown brief, matches ARCHITECTURE.md's
                                           dashboard mock-up (Critical/High/Medium/Monitor)
python -m amos approve 15 --account 1  -> pending_approval -> ready
python -m amos execute 19 --account 1  -> L1 task executes (dry-run)
python -m amos execute 15 --account 1  -> L2 task executes once approved
python -m amos demo-fix --account 1    -> simulates the marketplace reacting to both
python -m amos cycle --account 1       -> re-run: 3 issues cleared, 2 tasks verified, 0 failed
python -m amos precheck samples/listings.csv --account 1
                                        -> 16 PASS / 2 FAIL with real HSN/attribute/value errors
python -m amos serve                   -> dashboard HTML (200) + /api/dashboard + /api/org verified
python -m pytest tests/                -> 29 passed
```

## Departments implemented vs planned (org.py)

Active (V1, have at least one real skill): Account Health, Catalogue & Content, Advertising,
Pricing, Inventory, Orders & Operations, Returns & CX, Analytics & BI, Compliance & Marketplace
Admin. Each has additional agents listed with no skill yet (`Agent.implemented == False`) — those
are the placeholders for V2, e.g. Title Agent, Bullet Agent, Competitor Price Agent, most of
Promotions & Growth. Planned-phase departments with zero agents: Expansion, Brand & Creative,
Finance/Reconciliation, Competitor Intelligence.

## Session 2 — Account Data Coverage layer

Added `amos/coverage.py`: for each of the 8 ingest kinds, works out `available` / `stale` /
`missing`, `row_count`, `latest_date` (where a kind has one meaningful date column), `age_days`,
`last_ingested_at`, and which skills (`used_by`) depend on it — purely informational, never
touches the exception/priority/task/policy lifecycle, never fabricates an issue for missing data.

**No new tables.** Coverage is derived live from each kind's own data table
(`COUNT(*)`/`MAX(date_col)`) plus the `data_sources` table `ingest.touch_source()` already
maintained (kind, rows, ingested_at) — that table existed but nothing read it for anything beyond
the dashboard's raw `data_sources` list, so this reuses it rather than duplicating it.

**"Required data" per skill already existed** — every `@skill(..., sources=[...])` in
`amos/skills/*.py` already declares the kinds it reads (see `skills/base.py`'s `SkillDef.sources`).
`coverage.py` just reads that instead of inventing a second, parallel declaration, so a new skill's
`sources=[...]` is picked up automatically with no change to `coverage.py` needed.

Files changed/added:
- **New** `amos/coverage.py` — `get_account_coverage(conn, account_id)`, `limitations(coverage)`,
  `summary_lines(coverage)`.
- `amos/core.py` — added `DEFAULT_CONFIG["data_freshness"]` (per-kind max-age-in-days before
  `available` becomes `stale`; override per account the normal way via `set_account_config`).
  No other config keys touched.
- `amos/director.py` — `run_cycle()` now sets `s["data_coverage"] = coverage.get_account_coverage(...)`
  as its first step, before skills run. Every other key in the cycle result is unchanged.
- `amos/reporting.py` — `dashboard()` gained two new keys, `data_coverage` and
  `coverage_limitations`; the existing `data_sources` key is untouched. `daily_brief_md()` appends
  a `## Data coverage` section only when `coverage_limitations` is non-empty.
- `amos/cli.py` — new `python -m amos coverage --account <id>` command (human-readable, matches
  the brief's mock-up: `Search Terms      MISSING     0 rows` + an "Analysis limitations" list).
- `amos/server.py` — new `GET /api/coverage?account_id=<id>` route, `{"coverage": {...}, "limitations": [...]}`.

Verified this session (in addition to re-running the full existing suite, 29/29 still pass):
- `amos coverage --account 1` on freshly seeded data → all 8 kinds `AVAILABLE` with correct row counts.
- Manually cleared `ad_search_terms` + `health_metrics` for the demo account → coverage correctly
  reports both `MISSING`, and prints exactly:
  `Account Health analysis is limited because Health Metrics data is not available.` /
  `Advertising analysis is limited because Search Terms data is not available.`
- `amos cycle --account 1` in that same missing-data state → `data_coverage` present in the JSON,
  `skipped` shows the same three skills the limitations pointed at (`check_account_health_metrics`,
  `detect_wasted_ad_spend`, `harvest_converting_terms`) — **no fabricated issue was created** for
  the missing data itself, confirming it never entered the issue/task lifecycle.
- Pushed `daily_performance` dates back 30 days → status correctly flips to `STALE` with an
  `age_days`/`freshness_expectation_days` pair; a 2-day-old date correctly stayed `AVAILABLE`
  against the default 3-day threshold.
- `dashboard()` / `daily_brief_md()` / `GET /api/coverage` all confirmed showing the same data.
- `python -m py_compile` across every module — no syntax errors introduced.

Backward compatibility: every pre-existing CLI command, the dashboard's `data_sources` key, and
the task/exception/priority/policy lifecycle are byte-for-byte unchanged in behaviour; coverage is
additive everywhere it's exposed.

## Session 3 — Evidence Layer

**Feature:** every Issue (and therefore every Task) can now answer *"Why did AMOS create this?"*
and *"What actual seller data supports it?"* — as structured, queryable rows instead of only the
opaque `issues.evidence_json` blob. Built additively: `evidence_json` and every existing field on
issues/tasks/dashboard are unchanged; the new structured data sits alongside them.

**Build approach for this session: BUILD FIRST → TEST LATER.** Comprehensive testing is
**deferred to the later testing phase** (see "Verification" below for exactly what has and hasn't
been checked).

### Files

**New**
- `amos/evidence.py` — the whole layer. Public API:
  `build_items(finding)` (pure, no DB) · `save_issue_evidence(conn, issue_id, finding)` (write,
  replace-per-issue) · `get_issue_evidence(conn, issue_id)` (read) · `format_evidence(items, header=None)`
  (human-readable text).

**Modified** (all changes additive)
- `amos/core.py` — new `evidence` table + `ix_evidence_issue` index in `SCHEMA` (see below). Uses
  `CREATE TABLE IF NOT EXISTS` like the rest of the schema and `connect()` runs the schema every
  time, so existing databases pick the table up automatically — no migration step.
- `amos/engines/exception.py` — calls `evidence.save_issue_evidence()` in exactly the three places it
  already (re)writes `issues.evidence_json`: new issue, refresh of an *open* issue, and reopen of a
  *cleared* issue. Deliberately **not** called on clear (or for `ignored` issues) — see snapshot behaviour.
- `amos/engines/tasks.py` — `task_detail()` now includes `issue["evidence_items"]`, read live from the
  issue's evidence rows (the task stores no evidence of its own).
- `amos/reporting.py` — `issues_list(conn, account_id, status="open", with_evidence=True)` adds
  `evidence_items` to each issue. The existing `evidence` key (parsed `evidence_json`) is untouched.
  `dashboard()` was **not** changed.
- `amos/cli.py` — new `evidence` subcommand.
- `amos/server.py` — new evidence route.
- `amos/skills/catalogue.py` — `listing_gaps()` now also emits `actual` / `expected` / `operator` on
  each gap dict (e.g. images: actual 2, expected 7, operator `<`), alongside the existing human
  `issue` text. No detection logic or thresholds changed.

### New `evidence` table

```
evidence(id, issue_id NOT NULL, source_kind, source_table, source_label, entity_type, entity_id,
         field, actual_value, expected_value, operator, observation, data_date, created_at)
INDEX ix_evidence_issue ON evidence(issue_id)
```

One row per fact. It holds references/snapshots (field, actual, expected, …) — never a copy of a
whole report, so there is no duplicate data warehouse. `actual_value` / `expected_value` are stored
as TEXT (values are `str()`-ed on write).

### `EvidenceItem` structure (dataclass in `evidence.py`)

| Field | Meaning |
|---|---|
| `source_kind` | the skill's own logical data kind, e.g. `listings` |
| `source_table` | actual DB table behind that kind (may differ, e.g. `search_terms` → `ad_search_terms`) |
| `source_label` | human label shown to users, e.g. "Amazon Category Listings Report" |
| `entity_type` / `entity_id` | what it's about: `sku`, `campaign`, `account`, … |
| `field` | the measured field, e.g. `images`, `hsn`, `late_dispatch_rate` |
| `actual` / `expected` / `operator` | observed value, threshold/target, comparison (`<`, `>`, `==`, or a rule code) |
| `observation` | plain-language sentence for humans |
| `data_date` | report/snapshot date the numbers came from, only when the finding carried one |

### How evidence is generated

`build_items(finding)` derives everything from data the Finding already has — **it never fabricates
a value**:

1. **Source info** comes from the skill's own declared `sources=[...]` (`skills/base.SkillDef`),
   mapped through `coverage.TABLES` / `coverage.LABELS`. New skills get sensible source labels with
   zero evidence-specific wiring.
2. **List-shaped evidence keys with per-item structure explode into one item per entry:**
   `gaps` (field / issue / actual / expected / operator — used by catalogue) and `errors`
   (field / message / code / suggestion — used by compliance validation).
3. **Every other key in `finding.evidence`** becomes one item (`field=key`, `actual=value`;
   non-scalar values are JSON-dumped).
4. **Date keys** (`date`, `report_date`, `snapshot_date`, `return_date`, `order_date`) become
   `data_date` on the items rather than items of their own; never guessed if absent.
5. **Fallback:** if a finding has no structured evidence dict, a single item is built from its
   `metric` / `diagnosis` / `title` — nothing invented.

### Relationships

- **Issue → Evidence:** one-to-many via `evidence.issue_id`. Each save *replaces* the issue's rows
  (DELETE + INSERT), it does not append.
- **Task → Issue → Evidence:** a task already links to its issue (`tasks.issue_id`). The task never
  duplicates evidence; `task_detail()` follows Task → Issue → Evidence live.

### Historical evidence snapshot behaviour

Evidence rows are only written while an issue is open (created / refreshed / reopened). When an
issue **clears**, the exception engine simply stops touching it, so its evidence rows **freeze at
whatever was last true while it was open** — later changes to the source tables never rewrite them.
No rows are ever deleted by AMOS. This satisfies "historical evidence must survive changes to
current source data" with no extra bookkeeping. `ignored` issues are likewise left untouched.

### Clear / reopen behaviour

- **Clear:** evidence retained as-is (frozen).
- **Reopen** (a cleared issue's fingerprint is detected again): status returns to `open` and the
  evidence rows are **replaced** with those from the new finding.
- **Known limit:** this is a *last-known* snapshot per issue, not a per-cycle history — a reopen
  overwrites the earlier frozen set, and open-issue refreshes overwrite each cycle. If full
  evidence history over time is ever needed, that would be a separate design decision.

### CLI

```
python -m amos evidence --account <id> --issue <id>
```
Prints `AMOS EVIDENCE`, the issue id/title, and the formatted evidence list. Exits 1 with a message
if the issue doesn't exist **for that account**.

### API

`GET /api/issues/<issue_id>/evidence` → `{id, title, diagnosis, evidence: [ ...rows... ]}`
(HTTP 404 if the issue id doesn't exist). Evidence is also embedded as `evidence_items`
in `issues_list()` output and in `task_detail()`'s `issue` object.

### Data Coverage integration

Evidence reuses, rather than duplicates, the Session 2 coverage layer: `source_table` and
`source_label` are looked up in `coverage.TABLES` / `coverage.LABELS`, keyed by the skill's
existing `sources=[...]` declaration. `coverage.py` itself was **not modified**. Coverage remains
informational only: missing data still never becomes an issue, and therefore never produces evidence.

### Verification performed (minimal — comprehensive testing is deferred)

Reported from the build session's log (not re-run since):
- Existing test suite: **29/29 pass**, unchanged (no new tests were added for the Evidence Layer).
- `python -m py_compile` clean across all modules.
- Fresh `seed` → `cycle`: **25 issues** created, matching prior behaviour exactly; **101 evidence rows**
  written.
- `python -m amos evidence --account 1 --issue 23` (catalogue validation): `errors` list exploded
  correctly into per-field items (HSN length, missing `fabric`, `XXXL` → allowed `3XL`).
- Structured evidence observed for catalogue (title/images/bullets gaps), compliance, inventory
  (stockout) and account-health metrics.

Done in the packaging step (this session's housekeeping):
- **File-placement fix:** the hand-off zip (`Amazon_AM_OS-v2.zip`) had the module at
  `amos/skills/evidence.py`, but its own imports (`from . import core, coverage`) and every importer
  (`cli`, `server`, `reporting`, `engines/exception`, `engines/tasks`) expect `amos/evidence.py`, so
  the package raised `ImportError` on load. The file was moved to `amos/evidence.py` — **contents
  unchanged**. (Same class of hand-off problem as Session 1's flattened zip.)
- One import-only check afterwards (`amos`, `amos.evidence`, `amos.cli`, `amos.server`,
  `amos.reporting`, `amos.engines.exception/tasks`) — all import cleanly.

**NOT yet verified:**
- The clear/reopen **snapshot check** (approve → execute → `demo-fix` → re-`cycle`, then confirm the
  cleared issues' evidence rows are unchanged) was started but **not completed** — the freeze
  behaviour above is from reading the code, not from an observed run.
- Reopen-replaces-evidence behaviour.
- `GET /api/issues/<id>/evidence`, `issues_list()` / `task_detail()` `evidence_items` payloads, and
  the `evidence` CLI's account-mismatch error path.
- The 29-test suite has not been re-run since the file move (it's a path-only change).

**Testing notes for the later phase:** `GET /api/issues/<id>/evidence` looks the issue up by id
only (no `account_id` check, unlike the CLI). `r_task_detail` behaves the same way, so this is
consistent with existing routes, but worth a look if multi-account/client access is ever exposed.
Also, `_source_info()` uses only the *first* entry of a skill's `sources=[...]`, so evidence from
skills reading several kinds is labelled with just the first. `README.md` does not yet list the
`evidence` CLI command.

### Next planned feature (not started)

**Account Dashboard + Daily AM Work Queue.** Nothing for it has been built or designed yet.

## Session 4 — Account Dashboard + Daily AM Work Queue

**Feature:** the existing dashboard is now the Account Manager's daily operating screen. Open AMOS,
pick an account, and see: what is happening (overview), what state it is in, what data is missing,
what work to do today (ordered), what is waiting for approval, *why* each task exists and what
evidence backs it, and what just happened.

**Build approach: BUILD FIRST → TEST LATER.** The full test suite was **not** run this session;
only the minimal sanity checks listed under "Verification" were done.

**Rules the build followed** (please keep them when extending this):
- **One task system.** The work queue is the existing `tasks` table read through `engines/tasks.py`.
  No second queue, no new ranking formula: order is the existing priority bucket → due date → existing score.
- **The UI decides nothing.** Every approve / reject / execute goes through the existing endpoints →
  `AccountDirector` → policy/execution engines. Buttons only mirror which statuses the engine accepts;
  the backend re-checks on every call and its refusal reason is shown verbatim.
- **No new tables, no schema change.** Evidence, coverage and recent activity all come from tables that already exist.
- **Presentation logic lives in Python once**, not in the browser: evidence text is produced by the same
  `evidence.format_evidence` the CLI uses; coverage comes from `coverage.py`; the JS only renders.

### Files

**New**
- `amos/workqueue.py` — the service layer: `get_work_queue(conn, account_id, status="attention")`,
  `task_view(conn, task_id)`, `format_queue_text(queue)`, plus small helpers (`evidence_summary`,
  `evidence_display`, `recent_activity`). Layering: `server → workqueue → reporting/engines/coverage/evidence → DB`.

**Modified**
- `amos/server.py` — new consolidated route; `GET /api/tasks/<id>` gains `am_view`; account guard on
  approve/reject/execute; cleaner 404 text (details below).
- `amos/reporting.py` — currency-aware formatting (`_fmt_money`); `dashboard()["account"]` gains `currency`.
  Nothing else in `dashboard()` changed (its `tiles`, `buckets`, `tasks`, `data_coverage`, `data_sources`, … are as before).
- `amos/cli.py` — new `queue` command.
- `amos/web/index.html` — rebuilt in place (same single-file, dependency-free, dark-teal page; same header,
  account selector, Run cycle and brief buttons).

**Not touched:** every engine (`exception`, `priority`, `tasks`, `policy`, `execution`, `verification`,
`learning`), every skill, `core.py`, `coverage.py`, `evidence.py`, `director.py`, `org.py`, `ingest.py`,
`demo.py`, and `tests/`.

### APIs

| | Endpoint | Notes |
|---|---|---|
| **new** | `GET /api/account/<id>/work-queue[?status=…]` | The one consolidated payload (below). `status` = `attention` (default), `pending_approval`, `pending_decision`, `human_only`, `ready`, `executed`, `verified`, `closed`. Unknown account → 404, non-numeric id / unknown status → 400. |
| changed | `GET /api/tasks/<id>` | **Additive:** new key `am_view` (the enriched task: evidence lines, coverage, approval, execution, issue detail). All previous keys identical. If `?account_id=` is supplied it must match the task's account (404 otherwise); still optional, so old callers work. |
| changed | `POST /api/tasks/<id>/approve\|reject\|execute` | Now 404 `task N not found for account M` if the task belongs to a different account than `?account_id=`. Before, `tasks.approve/reject` and `execute_task` looked tasks up by id alone, so a wrong `account_id` silently acted on another account's task. Engines were **not** changed — the guard is in `server.py`. |
| changed | 404 bodies | `{"error": …}` no longer wraps the message in Python `KeyError` quotes. |
| changed | `GET /api/dashboard` | `account` gains `currency`. Nothing removed. |

`work-queue` payload: `account` (id, client, marketplace, store, phase, mode, currency, today) ·
`overview` (`as_of`, `has_data`, `metrics[]`: sales/orders/sessions/ad_spend/ad_sales with latest-day `value` and 7-day `window_total`,
plus `open_issues`, `open_tasks`) · `state` (counts) · `departments` (= existing `dashboard()` tiles) ·
`coverage` + `limitations` (= existing coverage layer, unmodified) · `buckets`/`bucket_labels` (attention tasks by priority) ·
`filter`, `status_counts`, `status_labels` · `work_queue[]` · `approvals[]` · `recent_activity[]` · `generated_at`.

Each task item carries: id, priority + label, title, department, status + label, autonomy level, `reason`
(+ `policy_reason`), due date + `due_label` ("Due tomorrow", "Overdue by 2 days"), score, action type,
`recommended_action`, account, issue `{id,title,type,kind,status}`, `evidence` `{available,count,summary,sources}`,
`approval` `{required,state,label}`, `execution` `{mode,phase,agent_may_execute,can_execute,text}`, `can` `{approve,reject,execute}`.
`am_view` (detail) additionally has the full issue (diagnosis, first/last seen), `evidence.items[]`, and `coverage[]`.

### CLI

`python -m amos queue --account <id> [--status attention|pending_approval|…|closed]` — plain-text version of the
same payload (overview, state, work grouped by priority, approvals, limitations, recent activity). Output layout
is ASCII; issue titles keep their `₹`, and the command sets stdout `errors="replace"` so a legacy Windows console
cannot crash it. Exit code 1 with a message for an unknown account.

### Dashboard sections (top to bottom)

1. **Account line** — client, marketplace, currency, autonomy phase, Dry-run/Live badge.
2. **Account overview** — Sales, Orders, Sessions, Ad spend, Ad sales for the latest day in the data
   (date shown), with a "N days: total" line. Missing data shows `—`, never a made-up number.
3. **Account state** — factual counts (no health score): open issues (+ opportunities), critical, high,
   awaiting your decision, open tasks, data limitations; then the existing department status tiles
   (hover for detail; the Opportunities tile is folded into the open-issues count).
4. **Today's work** (left column) — priority counts, status filters, then tasks grouped Critical → High → Medium → Monitor.
5. **Approvals required** (right column) — tasks in `pending_approval` / `pending_decision`, with reason and
   View evidence / Approve / Reject. First 6 shown, "Show all N" expands.
6. **Data coverage** — the 8 report kinds (Available / Stale, Nd old / Missing, row count, latest data date) and
   an "Analysis limited" box using `coverage.limitations()` text verbatim.
7. **Recent activity**.
8. **Task detail modal** (click a title / View evidence / View issue) — sticky title bar and sticky action bar.

Under ~1040 px the right column stacks below the queue. Below 560 px the tile grid goes to 2 columns.

### Work queue behaviour

- **Default view = "Needs attention"** = statuses that need a person: `pending_approval`, `pending_decision`,
  `human_only`, `ready`. Executed / verified / closed work is behind filters and Recent activity, so finished
  work does not clutter the day.
- **Filters:** Needs attention · Pending approval · Pending decision · Human action (L4) · Ready ·
  Awaiting verification (`executed`) · Verified (`verified`, being measured) · Closed (`closed` + `rejected`,
  newest 50). Counts on each. There is **no "In progress" filter** because the task engine has no such status.
- **Order:** existing priority bucket → `due_date` → existing `score` (high first) → id. The Monitor group
  is collapsed by default in the attention view; open/closed state of groups is remembered while you work.
- **Due labels** come from the stored `due_date` vs today (`core.today()`, so `AMOS_TODAY` is respected).
  Note `priority.DUE_DAYS` gives P1 a 1-day due date, so a fresh Critical task reads "Due tomorrow".
- **Evidence line** per row e.g. `Images: 2 / target 7 (+3 more)`, built only from stored evidence rows;
  list-valued facts read "3 items". A task whose issue predates the Evidence Layer shows "not recorded yet
  (run a cycle to refresh)" — open issues refresh their evidence every cycle.
- A task that bounced back to a human (failed verification, or blocked by an agent guardrail) is flagged
  "Sent back to you" with the real reason from its history.
- "Account state → Critical/High" count **open issues** by priority; the Today's-work chips count **tasks in the
  attention queue** by priority. They usually agree but need not (e.g. an executed task whose issue is still open).
- "Awaiting your decision" = `pending_approval` + `pending_decision` (same definition as the older `awaiting_approval`).

### Approval / execution behaviour

- Buttons: **Approve** and **Reject** for pending tasks, **Execute** for `ready`/`human_only`, **View evidence**,
  **View issue**. Approve/Execute from the list send an empty note; from the modal they send the note box.
- **Reject asks for confirmation** and says what it does — the engine closes the task and sets its issue to
  `ignored`, so AMOS will not raise it again (existing engine behaviour, unchanged).
- **Execute** calls the existing manual path (`via=manual`): it records that a person did the work. In
  dry-run mode nothing is sent to Amazon. Agent execution stays where it always was (`run_agent_queue`,
  phase ≥ 3 and policy). The modal explains this per task using `policy.agent_may_execute`.
- Blocked actions (e.g. executing an unapproved task) come back from the backend as 400 with its own message,
  which is shown in the modal and as a toast. The list refreshes after any failure so stale rows disappear.
- L1–L4, phase gating, whitelist (`l2_auto_actions`), dry-run and guardrails are all untouched engine logic.

### Evidence integration

Queue rows show the summary; the modal's **Why AMOS created this** shows the issue diagnosis, and **Evidence**
shows every item as Source / SKU / field / Expected / … lines produced by `evidence.format_evidence`
(same text as `python -m amos evidence`). Facts stored as JSON lists (e.g. late orders) show "3 items" with an
expandable readable list. The Evidence Layer itself was not modified.

### Coverage integration

The panel renders `coverage.get_account_coverage()` and `limitations()` as-is (no recalculation in the browser).
The task modal lists coverage for the data kinds the detecting skill declared in `sources=[…]`
(same declaration coverage.py and evidence.py use), e.g. "Listings — Available".

### Account selection

Header selector lists every account (`/api/accounts`); nothing is hard-coded to a client. The choice is kept in
the URL (`?account=2`) and `localStorage`. Every account uses its own `accounts.currency`, formatted with
`Intl.NumberFormat` (INR uses Indian digit grouping); amounts are never converted. `reporting.py` now
formats brief/dashboard money with `_fmt_money(amount, currency)` — INR output is byte-identical to before,
other codes without a well-known symbol print as `AED 1,234`. An account with no data renders cleanly
(`—` metrics, "Nothing needs you right now", all 8 reports Missing plus the limitation messages).

### Recent activity

Read from the existing `audit_log` (no new activity system): task created / approved / rejected / executed,
cycle completed (with counts), data imported, agent blocked, connector/skill errors, account created, settings
updated. Task events are clickable. Only events AMOS already audits appear — e.g. "issue refreshed" is not
audited today, so it is not shown.

### Schema changes

None.

### Verification performed (minimal, on a throw-away temp database — nothing shipped)

- `py_compile` on the touched Python modules; `node --check` on the dashboard's JavaScript.
- Seeded demo → one cycle: 25 issues / 25 tasks, no skill errors. Today's-work buckets came out
  Critical 4 · High 6 · Medium 11 · Monitor 4.
- `python -m amos queue` on the seeded data (used to tighten the evidence summaries).
- HTTP checks against a running server: `work-queue` for account 1; a second, empty account with currency AED
  (null metrics, 0 tasks, 8 missing reports); 404/400 paths (unknown account, bad id, bad status); `am_view`
  contents; wrong-account approve → 404; executing a `pending_approval` task → backend 400 with its message.
- Headless DOM run (jsdom) of the real page against the live server — 28 assertions, 0 JS errors: every section
  renders; filter; evidence modal; approve from the Approvals panel then the row shows *Ready to execute*;
  execute moves it to *Awaiting verification*; reject confirmation + reject; account switch to the empty AED
  account; URL keeps the account.
- Chromium screenshots at 1440 px, 820 px and 390 px (no horizontal overflow at 390). The visual review found and
  fixed: Today's work starting too low, orphaned wrapped cards, engine boilerplate ("default policy for X") on
  every row, a sticky-header inset bug in the modal.
- Existing CLI smoke (exit 0): `accounts`, `dashboard`, `issues`, `tasks`, `brief` (daily + weekly), `coverage`,
  `evidence`, `org`, `cycle`; `approve`/`execute` via CLI earlier in the same DB.
- `_fmt_money` INR output compared against the old `₹` formatting (identical).

### Not yet verified (deferred to the testing phase)

- **The existing 29-test suite has not been run** since Sessions 3 and 4 — and **no automated tests were added**
  for the Evidence Layer, `workqueue.py` or the new routes. Suggested first tests: `get_work_queue` shape and
  ordering; filters; `_guard_task`; `am_view`; evidence summaries; `_fmt_money`; snapshot/reopen behaviour of evidence.
- The clear/reopen evidence-snapshot check from Session 3 is still unconfirmed.
- Real data for: `human_only` (L4) tasks; `verified` / `closed` views; a task "sent back to you"; **stale**
  coverage rows in the UI; a non-INR account **with** performance data; phase 3–4 wording (agent auto-execution).
- Browsers other than Chromium/jsdom; touch use; keyboard-only and screen-reader pass.
- Large accounts (hundreds of tasks): the payload does a few small queries per task per request — unmeasured.
- `python -m amos queue` on an actual Windows console; the whole UI on the real Windows install.
- Two people acting on the same task at once (backend refuses the second; UI refreshes — reasoned, not exercised).

### Known limitations / notes

- **Currency can only be set directly in the DB** (`accounts.currency`, default `INR`); `create_client_account`,
  the CLI and the API have no way to set it, and `python -m amos accounts` does not print it.
- There is still no way to create a second real account from the CLI (`amos accounts create` is in "Next up").
- `GET /api/issues/<id>/evidence` (Session 3) is still not account-scoped; the dashboard does not use it.
- Evidence "Source" text uses the coverage labels (`Listings`, `Orders`), not longer Amazon report names.
- `coverage.limitations()` only reports **missing** data; stale data is shown as a status but adds no limitation text.
- The old "Data sources" table (rows / last ingested) is no longer drawn; row count and latest data date are in the
  coverage panel and `data_sources` is still in `/api/dashboard`.
- `README.md` does not yet mention the `evidence` or `queue` commands or the new dashboard sections.

### Next recommended

1. **The testing phase** for Sessions 3 + 4 (list above), before adding more surface.
2. **`amos accounts create`** (client, marketplace, store, currency) — the dashboard is now multi-account
   ready but a second real account still has to be created in code.
3. Then the existing "Next up" list below (live connectors, remaining department agents, cross-department
   links, data-request tasks).

## Session 5 — Account Creation & Configuration

### What was built

A real account (a second Krishna/Yesocea-style client, not the demo) can now be created without
touching Python or the database directly:

- CLI: `python -m amos accounts create --client ... --marketplace ... [--store ...] [--currency ...]
  [--phase ...] [--mode ...]`. `python -m amos accounts` (unchanged) still lists accounts; it is
  now also `accounts list` under the hood, so both spellings work.
- Dashboard: a `+ Add Account` button next to the account selector opens a small modal (client
  name, marketplace/currency dropdowns, optional store, phase, mode) and POSTs to the new API.

Both interfaces call one new function, `core.create_account()` — the single place validation,
duplicate-checking and account creation happen. It does not replace or duplicate anything: it is a
thin orchestration over the *existing* `create_client_account()` and `set_account_config()` (the
exact two calls `demo.py::seed()` already made), plus the input checks the brief asked for. No new
table, no second phase/mode/config system, no new currency field.

### Files changed

- `amos/core.py` — `create_client_account()` gained an optional `currency=` kwarg (writes
  `accounts.currency`; omitted keeps the old default-`'INR'` behavior, so the one existing caller in
  `demo.py` is untouched). Added `MARKETPLACES` / `MARKETPLACE_DEFAULT_CURRENCY` / `VALID_CURRENCIES`
  / `VALID_PHASES` / `VALID_MODES`, `AccountCreationError` (a `ValueError` subclass), and
  `create_account(conn, client_name, marketplace, store=None, currency=None, phase=1, mode="dry_run")`.
- `amos/cli.py` — `accounts` is now a parser with two subcommands, `list` (= the old behaviour,
  still the default when no subcommand is given) and `create`; `cmd_accounts_create()` prints the
  same shape of confirmation the brief specified, or `Error: ...` on stderr with exit code 1.
- `amos/server.py` — `POST /api/accounts`, calling `core.create_account()` and returning
  `{"ok": true, "account": {...}}`.
- `amos/web/index.html` — `+ Add Account` button + `openAddAccount()`/`submitAddAccount()` modal
  (same modal/toast/`data-act` patterns already used for reject/brief); `loadAccounts()` now takes
  an optional account id to select (used right after a successful create) instead of always
  re-deriving the selection from the URL/localStorage.
- `HANDOVER.md`, `README.md` — this section / CLI usage.

Not touched, as instructed: `engines/`, `skills/`, `coverage.py`, `evidence.py`, `workqueue.py`,
`director.py`. No engine, phase, or task-lifecycle behavior changed.

### CLI

```
python -m amos accounts create --client "ABC Enterprises" --marketplace "Amazon India" \
  --store "ABC Store" --currency INR --phase 1 --mode dry_run
```
(Windows: replace the trailing `\` line-continuations with `^`, or put it all on one line — both
were tested.) `--store`, `--currency`, `--phase` (default `1`) and `--mode` (default `dry_run`) are
all optional. Success prints exactly the confirmation block the brief specified (`Created account
#N` + the six fields). A validation or duplicate problem prints `Error: <reason>` to stderr and
exits 1; argparse itself rejects an out-of-list `--currency`/`--phase`/`--mode` before that (exit 2,
its own usage message) since those are closed sets.

### API

`POST /api/accounts` — body `{"client", "marketplace", "store"?, "currency"?, "phase"?, "mode"?}` →
`{"ok": true, "account": {"id","client","marketplace","store","currency","phase","mode"}}` on
success, ready for the dashboard to select immediately. `core.AccountCreationError` subclasses
`ValueError`, so it rides the server's existing `ValueError` → HTTP 400 `{"error": "..."}` handling
with no new error-handling code in `server.py`.

### Dashboard

`+ Add Account` sits directly right of the account selector in the header. The modal collects all
six fields (marketplace and currency as dropdowns — picking a marketplace pre-fills its usual
currency, which the user can still override), defaults phase to 1 and mode to `dry_run`, and on
success: closes, toasts the new account id, reloads the account list *and selects the new account*
(`loadAccounts(newId)`), then reloads the dashboard for it — no manual refresh needed. A validation
or duplicate error from the API is shown inline in the modal (`modalError`, the same element the
reject/approve modals already use) rather than closing it.

### Configuration

Phase and mode are **not** new columns or a new table — they go through the existing
`accounts.config_json` via `set_account_config()`, called once right after `create_client_account()`,
exactly as `demo.py::seed()` already does it (`{"phase": phase, "execution": {"mode": mode}}`).
`set_account_config()`'s existing deep-merge means this only ever sets those two keys and leaves
every other `DEFAULT_CONFIG` key (targets, ads thresholds, compliance rules, etc.) at its default —
nothing about that merge behavior changed.

### Currency

Populates the existing `accounts.currency` column only — no new field, no conversion anywhere. If
`--currency`/`currency` is omitted, it defaults from the marketplace (`MARKETPLACE_DEFAULT_CURRENCY`:
India→INR, UAE→AED, USA→USD, UK→GBP, Saudi Arabia→SAR, Germany→EUR); the user can still pick any of
the six explicitly. This also closes the "currency can only be set directly in the DB" gap called
out in Session 4's Known limitations.

### Validation

`core.create_account()` (shared by both CLI and API, so the rules live in exactly one place):
client name required (non-empty after `.strip()`); marketplace required (non-empty; deliberately
**not** restricted to the six-item list — `MARKETPLACES` is only for the CLI help text and the
dashboard's dropdown, so adding a seventh marketplace later is a one-line addition, not a schema or
validation change); currency must be one of the six supported (defaults from marketplace if
omitted); phase must be an integer 1–4; mode must be `dry_run` or `live`; duplicate
(client name, marketplace, store) is rejected with the existing account's id in the message rather
than silently creating a second one (a client legitimately having several accounts — e.g. India +
UAE — is unaffected, since the identity check includes marketplace and store, not just the client).
A `sqlite3.Error` during the two writes is caught and re-raised as a readable
`AccountCreationError` instead of a raw traceback.

### What was minimally checked

Per this session's "minimal checks only" rule — no new automated test suite, existing 29-test suite
not run:

- `py_compile` on `amos/core.py`, `amos/cli.py`, `amos/server.py`; `node --check` on the dashboard's
  extracted `<script>` body — all clean.
- `python -m amos accounts --help` and `accounts create --help` — both render correctly, including
  the `list`/`create` split.
- On a throwaway temp DB (`AMOS_DB=/tmp/amos_test.db`, never `data/amos.db`): re-created accounts #1
  (Krishna Enterprises, Amazon India, INR) and #2 (Yesocea International, Amazon UAE, AED) the way
  `create_client_account`/`set_account_config` already would, then via the CLI created #3 (ABC
  Enterprises, Amazon India, explicit fields) and #4 (XYZ Traders, Amazon USA, all defaults —
  confirmed store defaulted to the client name and currency defaulted to USD from the marketplace).
  Confirmed a repeat of #3's (client, marketplace, store) is rejected (exit 1, naming account #3) and
  an empty `--client` is rejected (exit 1); confirmed argparse itself rejects `--currency XYZ`
  (exit 2). `python -m amos accounts` afterwards listed all four correctly and #1/#2 were unchanged.
- Ran `python -m amos serve` against that same DB: `GET /api/accounts` (4 accounts); `POST
  /api/accounts` creating #5 (Nova Retail, Amazon UK → currency auto-filled GBP) — `{"ok": true,
  "account": {...}}` with the id immediately usable; a repeat POST of the same account →
  400 `{"error": "...account #5..."}`; a POST missing `client` → 400 `{"error": "client name is
  required"}`; `GET /api/accounts` again showed #5 with everything else unchanged.
- `GET /api/account/5/work-queue?status=attention` (the brand-new, no-data account) returned cleanly
  — `overview.has_data: false`, all metrics `null`, `state.data_missing: 8`/`data_limitations: 9`,
  every department tile "No data"/"Healthy" — no exception, and confirmed no fake sales/tasks/issues
  were seeded (`SELECT COUNT(*) FROM tasks/issues WHERE account_id=5` → 0 each) and no cycle ran
  automatically.

### What remains untested

- The dashboard's `+ Add Account` modal itself was not exercised in a real/headless browser this
  session (only `node --check` syntax-checked the script) — worth a jsdom/Chromium pass alongside
  the rest of Session 4's UI in the upcoming testing phase.
- `--mode live` was accepted by validation but nothing was executed through it (no live connector
  exists yet — expected, out of scope for this session).
- Creating an account whose marketplace is *not* in `MARKETPLACES` (a free-text marketplace) was not
  explicitly tried, though nothing in `create_account()` restricts it — worth a quick check later.
- Concurrent create requests for the same (client, marketplace, store) racing each other (the
  duplicate check and the insert are not in one transaction) — low risk for this single-user
  internal tool, not exercised.
- The full 29-test suite (Sessions 1–4) is still unrun, per this session's brief.

### Known limitations

- Marketplace stays free text; there is no enum/validation against `MARKETPLACES` beyond
  non-emptiness, by design (see Validation above) — a typo'd marketplace name is not caught.
- No way to *edit* an existing account's marketplace/store/currency/phase/mode from the CLI or
  dashboard yet — only creation. Phase/mode changes still need `set_account_config()` directly;
  currency has no update path at all yet (this session only added the create-time write).
- No way to delete/archive an account.
- The dashboard's account dropdown has no visual "new" indicator beyond being selected once created.

## Session 6 — Listing Optimization Using Top-Ranking Amazon.in ASIN Keywords (IN PROGRESS)

*This section is being written incrementally as the session proceeds, so the work done so far is
never lost. It will be filled in properly (methodology, safety, integration, limitations) once the
build is complete; treat anything below as a running log until the "Status: COMPLETE" line appears.*

### Plan (see the session brief for full detail)

Reuse the existing skill -> Finding -> exception -> priority -> task -> policy -> execution ->
verification -> measurement -> learning pipeline end-to-end. Concretely:

- A new skill, `optimize_listing_keywords` (catalogue department, "Backend Keyword Agent"),
  produces one Finding per SKU with `recommendation.action_type = "UPDATE_LISTING_CONTENT"` -
  already a registered action type (base autonomy level 2, human-approval, `AmazonSPAPIConnector`
  stub already in `LIVE_CONNECTORS`) - so approval/execution/dry-run needed **zero** changes to
  `engines/policy.py` or `engines/execution.py`.
- New modules: `amos/competitive_search.py` (top-ASIN discovery abstraction + mock provider),
  `amos/keywords.py` (extraction/normalization/scoring/gap analysis), `amos/listing_safety.py`
  (product-fact validation + generated-content validation), `amos/ai_provider.py` (Template
  provider by default, optional Anthropic provider), `amos/listing_optimizer.py` (orchestration +
  human-readable report, mirrors what `demo.py` does for the rest of AMOS).
- Schema: additive-only. New `competitive_asins` cache table; `listings` gains nullable
  `description`/`bullets_text` columns via a new small migration helper (existing DBs never had a
  column-add path before - `core.connect()` only ever did `CREATE TABLE IF NOT EXISTS`).
- CLI: `python -m amos listing optimize --account N [--sku X]` (fresh preview, no DB write) and
  `listing report --account N --sku X` (prefers a persisted issue's evidence if one exists).
- Measurement: a new `keyword_coverage` metric function (`amos/skills/metrics.py`) plugs into the
  *existing* `learning.measure_due()` - no new measurement code needed.
- Dashboard: no new UI written yet - the existing generic issue/task/evidence rendering already
  shows this action type's proposal (`action_payload` JSON) and evidence, same as every other
  catalogue finding. Revisit if a dedicated card is wanted later.

### Status: build not yet complete - see file diff / commit-in-progress for exact state.

## Next up (not started)

- Live SP-API / Amazon Ads API connectors in `engines/execution.py` (currently dry-run/manual
  only — the interface is already shaped for this, see the module docstring).
- Editing/archiving an existing account (Session 5 only added creation — see its Known limitations).
- Fill in the "planned" agents in `org.py` department-by-department (each just needs one new
  skill function in `amos/skills/` plus its name added to that agent's tuple — no other wiring).
- Multi-department cross-links beyond the one already built (returns -> catalogue size-chart
  correction): e.g. Buy Box loss routing an investigation to Pricing per `ARCHITECTURE.md` §4.
- Coverage is informational only per this session's brief; a natural (not yet built) follow-up is
  a *separate* operational "data request" task type (e.g. "ask client for search term report") for
  when a department manager decides a persistent gap is worth chasing — deliberately not built now
  since the brief was explicit that missing data must not itself become a business issue.
- `web/index.html` doesn't render the new coverage data yet (API is live at `/api/coverage` and
  inside `/api/dashboard`'s `data_coverage`/`coverage_limitations` keys) — wire up a coverage tile
  next time the dashboard UI is touched.
