# Data-update contracts

## Authoritative paths

- Operations contract: `data/data_update_registry.json`
- Historical baseline contract: `data/sp500_history_registry.json`
- Current dated universe: `data/processed/universes/sp500/current_constituents.csv`
- Approved current/history prices: `data/processed/daily/equities/`
- Shadow updates: `data/processed/updates/sp500_shadow/`
- Archived public responses: `data/raw/public/market_data_updates/`
- Purchased intake registry: `data/purchased_import_registry.json`
- Immutable purchased intake: `data/raw/purchased/`
- Nasdaq-100 candidate contract: `data/nasdaq100_history_registry.json`
- Nasdaq-100 pending-review products: `data/processed/universes/nasdaq100/pending_review/`

## Capability boundary

Treat `data/data_update_registry.json.capabilities` as authoritative. The S&P 500 point-in-time baseline, current-member shadow update, provider replay, and purchased intake are ready. Nasdaq-100 point-in-time membership is implemented as `candidate_pending_review`, not approved: use its frozen manifest and hashes only for explicitly authorized exploratory research, preserve the one-XNYS-session membership lag, and block strategy promotion while provenance, authorization, identity, adjustment, and early-history reviews remain open. Approved-price promotion and the scheduler remain disabled; QQQ/SPY shadow updates and a hedge-asset universe are not implemented. Report these boundaries explicitly instead of implying that the current SPY-constituent updater or Nasdaq candidate covers every strategy input.

## Status meanings

- `ready_candidate`: overlap, date coverage, and adjusted-return continuity passed; still shadow-only.
- `ready_bootstrap_candidate`: dated official identity plus independent security evidence allow a post-baseline shadow candidate; it is not production approval and pre-baseline history is not spliced.
- `bootstrap_needs_identity_review`: public prices exist for a missing current constituent, but security identity/history must be approved before any merge.
- `review`: one or more date, overlap, freshness, or cross-source gates need inspection.
- `fetch_or_analysis_failed`: the provider or parser failed for that symbol; other symbols may still be valid.
- `pending_review`: a purchased batch was copied and hash-verified but is not an approved input.
- `candidate_pending_review`: a reproducible derived candidate passed machine parsing and hash gates but still has unresolved human-review fields. It may support explicitly authorized exploratory research with frozen provenance; it is not an approved production input and cannot support promotion claims.

## Non-negotiable boundaries

- A shadow candidate is not an approved database row.
- A successful Tiingo/Twelve comparison does not prove historical security identity.
- Price level differences caused by different adjustment bases require an anchor; compare overlapping daily returns before scaling new rows.
- A historical adjustment-basis jump requires registered public corporate-action evidence, a stable recent return tail, and same-run Tiingo coverage; an undocumented jump blocks the run.
- Volume is warning-only because provider semantics differ materially.
- Never fill missing bars, splice ticker histories, or merge purchased files silently.
- Never expose credentials in argv, reports, source files, or chat.

## Free-tier behavior

- Twelve Data: 8 credits/minute and 800/day; one time-series symbol consumes one credit. The CLI waits across minute windows and uses a single adjusted-price request per symbol.
- Tiingo: 500 unique symbols/month, 50 requests/hour, and 1,000/day. The configured 480-symbol monthly budget leaves headroom; the monthly offset rotates the otherwise omitted current constituents over time.
- Eastmoney: opportunistic validator only until repeated transport-stability runs pass.

## Five-session campaign

- A qualifying day covers all 503 dated current equities, has no primary-provider failure, produces only allowed shadow statuses, passes at least 30 Tiingo cross-checks, and writes zero production rows.
- Every bootstrap candidate and every symbol with a documented historical adjustment-basis warning must be included in that day's Tiingo sample.
- Progress is the latest consecutive XNYS-session streak. Weekends and exchange holidays do not break it; a missing or failed trading session restarts the streak.

## Purchased intake phases

1. Preview external source, inventory hashes, and inspect supported containers.
2. Explicitly apply a reviewed source into immutable raw storage.
3. Review provider schema, ticker identity, adjustment semantics, coverage, and licensing.
4. Build a separate candidate transformation and compare against approved overlap.
5. Promote only through a separately implemented, tested, explicitly authorized gate.
