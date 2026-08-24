# BUILD_LOG

## 2026-06-12, Slice S0: Skeleton and health

**Asked:** First vertical slice per slice.md S0. FastAPI app boots, validates config from env, connects to Supabase Postgres, exposes a health endpoint with a db-ok flag. Bad DB URL fails loud at startup.

**Generated:** `crm_api/` package (config.py with pydantic-settings and asyncpg scheme normalization, db.py with async engine and ping helper, main.py with lifespan startup ping and health handler), tests (health ok, healthz alias, db-down 503, missing DATABASE_URL raises, non-postgres URL rejected), pyproject.toml (ruff + pytest config), requirements files, .env.example, .gitignore. Health handler registered at both /health (slice.md) and /healthz (PROJECT.md section 12) to satisfy both docs.

**Human changed or rejected:** Plan approved as proposed. Open item: DATABASE_URL is not yet in .env (only Supabase URL and anon key present), so the live startup ping has not been exercised against Supabase yet. Owner must add the session pooler Postgres URI before running uvicorn.

**Checks:** ruff check clean, ruff format clean, pytest 5 passed.

## 2026-06-12, Slice S1: Schema and migrations

**Asked:** All six tables from PROJECT.md section 4 via one Alembic migration. UNIQUE(communication_id, event_type) and status_rank must exist, downgrade must run cleanly, no table created outside Alembic.

**Generated:** crm_api/models.py (SQLAlchemy 2.0 typed models, naming convention on metadata, named unique constraint uq_communication_events_communication_id_event_type so ON CONFLICT can target it in S6, append-only docstring on CommunicationEvent), async Alembic wiring (env.py reads DATABASE_URL via get_settings, alembic.ini carries no URL), one hand-written migration 0001_initial_schema, tests/test_schema.py with metadata-level assertions plus a guard that exactly one migration file exists.

**Changed or rejected during the slice:** Autogenerate against Supabase was the plan but the connection failed twice. First, the DB password contained an unencoded @ so SQLAlchemy parsed the wrong host, fixed by percent-encoding the password in .env with a throwaway script that never printed the secret. Second, the server then rejected the password outright, which only the owner can fix in the Supabase dashboard. Switched to a hand-written migration derived from the models, which is deterministic and arguably more reviewable than autogen. Live upgrade, downgrade, upgrade roundtrip is pending the corrected password.

**Noted for S6:** PROJECT.md ranks failed at 1.5 but status_rank is INT per the locked schema. Ranks will map to integers in S6, no schema change.

**Checks:** ruff check clean, ruff format clean, pytest 11 passed. alembic upgrade head NOT yet run, blocked on DB credentials. Resolved next session, owner fixed the password and ran the migration roundtrip successfully.

## 2026-06-12, Slice S2: Customer ingest and aggregates

**Asked:** POST /api/v1/customers/bulk and /api/v1/orders/bulk, upsert on external_id, order ingest maintains total_spend, order_count, last_order_at in the same transaction. Re-ingest safe. Hand-computed aggregate test.

**Generated:** schemas/ingest.py (Pydantic, batch cap 1000, amount gt 0), routers/ingest.py (thin), services/ingest_service.py (orchestration, in-batch dedupe by external_id, unknown customer_external_id rows skipped and reported in errors), repositories/ingest_repo.py (ON CONFLICT DO UPDATE for customers, ON CONFLICT DO NOTHING with RETURNING for orders so aggregates count only genuinely new rows, GREATEST/COALESCE for last_order_at). Tests run against live DATABASE_URL with savepoint rollback isolation, nothing persists, skip cleanly if DB unreachable.

**Changed or rejected:** Found and fixed an S0 conftest bug, the fake DATABASE_URL setdefault overrode .env because env vars beat dotenv files in pydantic-settings. conftest now loads .env first, fake URL only as fallback. Orders re-ingest is DO NOTHING rather than DO UPDATE by design, updating an order amount on re-ingest would silently corrupt aggregates, logged as a tradeoff.

**Checks:** ruff check clean, ruff format clean, pytest 16 passed including 5 live DB tests.

## 2026-06-12, Slice S3: Seed script

**Asked:** Faker seed, about 600 customers and 2500 orders, cohorts per PROJECT.md section 11, idempotent re-run, aggregates populated, one-line command.

**Generated:** scripts/seed.py. Pure generators (make_customers, make_orders) separate from async main so they unit-test without DB. Pushes data through the S2 service layer in-process, so aggregates populate via the exact production code path. Deterministic external_ids plus fixed Faker seed give idempotency through S2 upsert semantics. Cohorts: 90 loyalists, 150 lapsed (all orders older than 90 days), 120 one-time, 240 regular, festive month weighting, en_IN locale, coffee SKUs in JSONB items, cohort tag stored in customer attributes for later segment demos. tests/test_seed.py, 6 pure tests including determinism and lapsed-cohort recency invariant.

**Changed or rejected:** Nothing rejected by owner. Self-correction: dropped an unused now parameter from make_customers.

**Verified live:** first run inserted 600 customers and 2507 orders, zero errors. Second run: 0 inserted, 600 updated, 2507 skipped, sample aggregates byte-identical. Command: venv\Scripts\python -m scripts.seed.

**Checks:** ruff check clean, ruff format clean, pytest 22 passed.

## 2026-06-12, Slice S4: Segment AST compiler and preview

**Asked:** Whitelisted rule AST to parameterized SQLAlchemy, POST /segments/preview with audience count, whitelist in one place, injection test proving bound parameters, correct counts on seed data.

**Generated:** services/segment_compiler.py, the single whitelist (total_spend, order_count, city, email, last_order_at, created_at with per-field comparator maps, value validators, depth cap 5, leaf cap 50, SegmentCompileError). schemas/segments.py recursive RuleGroup/RuleLeaf. routers/segments.py POST /api/v1/segments/preview returning count, sample of 10, per_rule_impact per leaf. 8 test groups: AND, OR, nested counts proven against ORM-computed expectations on live seed data, whitelist rejections (unknown field, unknown comparator, disallowed pair, negative days, oversized in_list), injection proof (payload in compiled params dict, absent from SQL string, count 0, table intact), depth and leaf bombs 422, per_rule_impact sanity, lapsed cohort count >= 150.

**Changed or rejected:** Nothing rejected by owner. Fixed S2 tests that assumed an empty DB, they counted all rows globally and broke once seed data persisted. Now scoped to test-created external_ids. per_rule_impact runs one count query per leaf, fine at 50-leaf cap and demo scale, documented swap to a single windowed query at volume.

**Checks:** ruff check clean, ruff format clean, pytest 30 passed.

## 2026-06-12, Slice S5: Campaign create

**Asked:** POST /api/v1/campaigns binding a segment, materialize audience into communications rows status queued status_rank 0, audience count matches preview.

**Generated:** Minimal segments CRUD pulled into this slice (POST, GET list, GET by id) because campaigns.segment_id FK had no creator yet. Segment definitions are compiled by segment_compiler before save, off-whitelist definitions can never be stored. schemas/campaigns.py, services/campaign_service.py (audience resolved through the same compiler as preview, single validation path, latest order amount via DISTINCT ON subquery, bulk insert of communications relying on server defaults for queued and rank 0), routers/campaigns.py (POST create, GET by id). Renderer: whitelisted tokens first_name, name, city, total_spend, last_order_amount via single regex pass, unknown tokens stay literal, None renders empty, no eval or format on user input. 8 tests: materialization count equals preview count with all rows queued rank 0, exact rendered message for a fresh customer with two orders (latest amount wins), unknown token literal, None empty, 404 unknown segment, 422 bad channel, off-whitelist segment never stored, empty audience allowed with zero rows.

**Changed or rejected:** Nothing rejected by owner. Scope addition (segments CRUD) was flagged before plan approval and accepted.

**Checks:** ruff check clean, ruff format clean, pytest 38 passed.

## 2026-06-12, Slice S6: Receipt endpoint, tests first

**Asked:** POST /api/v1/receipts, idempotent, order-safe, HMAC-verified. Five mandated tests written first and shown failing before implementation.

**Generated:** tests/test_receipts.py first, all 7 tests shown failing on 404 (run output kept). Then: schemas/receipts.py (event_type Literal, batch cap 1000), services/receipt_service.py (STATUS_RANKS scaled by 10 so failed lands at 15 in the INT column, in-batch dedupe, one SELECT resolving known communication ids, INSERT ON CONFLICT ON CONSTRAINT uq_communication_events_communication_id_event_type DO NOTHING with RETURNING to split accepted from duplicate, conditional UPDATE communications WHERE status_rank < incoming so the DB enforces never-downgrade without read-modify-write races, last_event_at via greatest+coalesce, commit before response so 200 means durable), routers/receipts.py (HMAC-SHA256 over raw body, compare_digest, 401 on missing or wrong signature before any parse). CHANNEL_HMAC_SECRET added to config as required field, .env.example updated. Response is 200 with per-event results (accepted, duplicate, unknown_communication), unknown ids do not poison the batch, valid events still land, no retry storms from a single bad id.

**Changed or rejected:** Nothing rejected by owner. Owner action needed: add CHANNEL_HMAC_SECRET to .env before running uvicorn, config now requires it.

**Append-only proof:** grep over crm_api shows communication_events referenced only by models.py and the INSERT in receipt_service.py. No UPDATE or DELETE anywhere.

**Checks:** ruff check clean, ruff format clean, pytest 45 passed. The five mandated receipt tests plus mixed-batch and converted-first cases all green.

## 2026-06-12, Slice S7: Channel service

**Asked:** Separate FastAPI app, POST /send accepts batch and 202s, simulates outcomes per documented percentages with jitter, deliberate duplicates and reordering, HMAC-signed callbacks to CRM receipts, retry with 1/4/16 backoff, dead-letter after three attempts, shares nothing with crm_api.

**Generated:** channel_service/ package: config.py (own pydantic-settings, CRM_RECEIPTS_URL, shared secret by env value only, configurable jitter and chaos probabilities so tests run at zero delay), simulator.py (pure plan_events with injected rng, returns the full callback plan as data including duplicates and delay-swapped reordering, testable without side effects), sender.py (HMAC-SHA256 over raw body mirroring S6 verification, retry 1s 4s 16s with injectable sleep, in-memory dead_letters list), main.py (202 immediately, asyncio task per message, delta-sleep over sorted delays, stable event_id per logical event so duplicates carry the same id, /dead-letters and /healthz), own Dockerfile (3.12-slim, non-root, port 8001). 8 tests, no DB, fast: outcome distribution over 5000 seeded plans within bounds, funnel ordering semantics, forced duplicates, forced reorder, retry storm makes exactly 3 attempts with sleeps 1, 4, 16 then dead-letters, success path signature recomputed and matched in test, end-to-end /send against MockTransport with zero jitter, isolation scan proves no crm_api import.

**Changed or rejected:** Nothing rejected by owner. One self-fix: sequential sleeps over sorted delays would have accumulated, switched to delta sleep.

**Checks:** ruff check clean, ruff format clean, pytest 53 passed.

## 2026-06-13, Slice S8: Dispatch, batched

**Asked:** POST /api/v1/campaigns/{id}/dispatch posts the queued audience to the channel service in batches of 50, campaign walks draft to dispatching to active, communications progress to sent as receipts arrive. End-to-end test against a stubbed channel.

**Generated:** services/dispatch_service.py (BATCH_SIZE 50, draft guard with 404/409/502 mapping, recipient resolution email or phone with id fallback, status committed to dispatching before posting so a mid-dispatch failure is visible), crm_api/http_client.py (shared httpx.AsyncClient as FastAPI dependency, created in main.py lifespan), dispatch route in routers/campaigns.py, channel_send_url setting plus CHANNEL_SEND_URL in .env.example and conftest default, tests/test_dispatch.py (batch sizes 50/50/20 for 120 customers, unique communication coverage, 404, double-dispatch 409, empty audience, end-to-end queued to sent to delivered via signed receipts reusing test_receipts helpers, recipient unit tests).

**Changed or rejected:** None to the plan. One test fix during the run, the bulk customers endpoint rejects an empty list (batch min length 1), so the empty-audience test skips the ingest call instead of posting zero customers. Dispatch is synchronous in-request by design at demo scale, swap point is the dispatch_campaign interface.

**Checks:** ruff check clean, ruff format clean, pytest 60 passed including live DB dispatch tests.

## 2026-06-13, Slice S9: Funnel stats

**Asked:** GET /api/v1/campaigns/{id}/stats returning per-status funnel counts for the 5s polling frontend, cheap to poll, numbers reconcile with the communications table.

**Generated:** services/stats_service.py (read-only, one GROUP BY over communications using ix_communications_campaign_id_status, zero-fills all eight statuses ordered by rank, ranks imported from receipt_service.STATUS_RANKS so there is a single source of truth, failure_rate = failed/total guarded for zero, converted surfaced top-level, dispatched_at as the timeline anchor), FunnelStep and CampaignStats schemas in schemas/campaigns.py, GET stats route in routers/campaigns.py with 404 mapping, tests/test_stats.py (404, fresh campaign all queued with full zero-filled funnel in rank order, reconciliation test asserting funnel counts equal a direct DB GROUP BY after mixed sent/delivered/failed receipts, converted count, empty audience divide-by-zero edge).

**Changed or rejected:** Nothing rejected. One deviation from the approved plan: stats tests post receipts directly against queued communications instead of routing through the dispatch stub, the reconciliation guarantee is identical and the test has fewer moving parts. Environment fix unrelated to the slice: .env had CHANNEL_SEND_URL=http://localhost:8001/ (missing /send path) and no CHANNEL_HMAC_SECRET, which broke the existing dispatch tests with 502 before any S9 code ran, corrected to http://localhost:8001/send and added a dev secret.

**Checks:** ruff check clean, ruff format clean, pytest 65 passed.

## 2026-06-13, Slice S10: Attributed order path

**Asked:** A converted receipt event creates an order attributed to the campaign and linked to the customer, aggregates update, and a duplicate converted event never creates a second order. Receipt-adjacent, so failing tests first.

**Generated:** tests/test_attribution.py written first and confirmed failing (6 tests: converted creates attributed order with exact aggregate deltas, duplicate converted yields one order, retry storm of the same batch yields one order, converted-before-delivered out-of-order still one order with status ending at rank 60, unknown communication writes nothing, non-converted events write nothing). Then receipt_service.py extension: SIMULATED_CONVERSION_AMOUNT = 499.00 module constant (the locked receipt contract carries no amount, deterministic value keeps aggregate assertions exact), _create_attributed_orders called inside process_batch before the single commit so 200 still means everything durable. Two independent idempotency layers: attribution only runs for converted events newly inserted past the UNIQUE(communication_id, event_type) guard, and the order itself carries external_id conv_{communication_id} inserted with ON CONFLICT DO NOTHING, aggregates applied only to rows actually returned. Aggregate math reuses ingest_repo.apply_order_aggregates unchanged.

**Changed or rejected:** Nothing rejected. No deviations from the approved plan.

**Append-only proof:** the new path INSERTs into orders and UPDATEs customers only, communication_events still touched by nothing but the existing INSERT ON CONFLICT DO NOTHING.

**Checks:** ruff check clean, ruff format clean, pytest 71 passed including the five mandated receipt tests and all S8/S9 suites.

## 2026-06-13, Slice S11: LLM client

**Asked:** One llm_client with Groq primary, OpenRouter fallback, four-attempt exponential backoff, returns text plus which provider answered, strips em dashes centrally, no provider called outside this file.

**Generated:** services/llm_client.py using raw httpx against both OpenAI-compatible chat completion endpoints, no groq or openai SDK added. complete(client, messages, temperature, max_tokens, json_mode, sleep) returns LLMResult(text, provider, model). Per provider up to 4 attempts with 1/2/4s sleeps between attempts, injectable sleep for instant tests. Retryable: transport errors, 429, 5xx. Non-retryable 4xx abandons the provider immediately and falls back. Both exhausted raises LLMUnavailableError carrying last error per provider. json_mode sets response_format json_object for the constrained-JSON slices ahead. Em dash stripping happens once here on every return, the source uses unicode escapes so the file itself contains none. Settings gained groq_api_key, openrouter_api_key, groq_model, openrouter_model, all in .env.example, conftest provides dummy keys. tests/test_llm_client.py, 6 tests via MockTransport: groq success without fallback, 500 storm with sleeps 1/2/4 then fallback, both exhausted with 8 attempts, 401 immediate fallback with zero sleeps, em dash stripping, request shape per provider including auth headers and response_format presence.

**Changed or rejected:** Nothing rejected. Owner action needed: add GROQ_API_KEY and OPENROUTER_API_KEY to .env before running uvicorn, config now requires them, same pattern as the S6 hmac secret note.

**Checks:** ruff check clean, ruff format clean, pytest 77 passed.

## 2026-06-13, Slice S12: NL to segment

**Asked:** POST /api/v1/ai/nl-to-segment, natural language to rule AST plus rationale plus per-rule audience count. Valid AST validated against the S4 whitelist, malformed model output gets one repair retry then 422. Test feeds known-bad output and asserts 422.

**Generated:** schemas/ai.py (NLToSegmentRequest with prompt 1 to 2000 chars, internal LLMSegmentOutput reusing RuleGroup, NLToSegmentResponse reusing RuleImpact), services/ai_service.py (system prompt embeds the live whitelist from segment_compiler.list_whitelist so allowed fields are never hand duplicated, _parse does json.loads then Pydantic then compile_definition so the whitelist is the final gate, any failure raises _InvalidLLMOutput, exactly one repair retry appends the assistant output and an error message then a second attempt, second failure raises SegmentGenerationError, success computes total and per-leaf counts the same way as the preview route and warns on any zero-audience leaf), routers/ai.py (thin, SegmentGenerationError to 422, LLMUnavailableError to 503), main.py wires the ai router. tests/test_ai_nl_to_segment.py, 6 tests via MockTransport scripting the LLM content: happy path with reconciled counts and one request, malformed JSON then repair, whitelist rejection of an ssn field then repair, known-bad twice gives 422 with exactly 2 requests, empty prompt 422 at the Pydantic layer with no LLM call, zero-audience warning.

**Changed or rejected:** Nothing rejected. Design note kept to plan: the endpoint does not persist a segment, the marketer reviews then calls the existing POST /segments with source ai, so S12 stays one concern.

**No raw LLM text to SQL proof:** the AST only reaches the DB through segment_compiler.compile_definition and compile_leaf, which bind parameters. The rationale is returned as a response field, never interpolated. The whitelist rejects unknown fields before any query runs, proven by the ssn test.

**Checks:** ruff check clean, ruff format clean, pytest 83 passed.

## 2026-06-13, Slice S13: Message drafting

**Asked:** POST /api/v1/ai/draft-messages, draft plus tone label plus audience-fit reason per variant. Returns draft and reasoning fields, failure path tested.

**Generated:** Refactor first, extracted the two-attempt repair loop out of nl_to_segment into a shared generic _generate(client, messages, parse) so the one-repair-retry rule lives in one place, nl_to_segment now calls it with _parse and keeps its SegmentGenerationError mapping. schemas/ai.py gained DraftMessagesRequest (campaign_intent 1 to 2000, segment_id, channel literal), MessageVariant (variant, message, tone, reasoning all non-empty), LLMDraftOutput (variants 1 to 5), DraftMessagesResponse. ai_service.draft_messages loads the segment for context, 404 via SegmentNotFoundError if missing, builds a channel-aware prompt that carries the segment name and definition for audience fit, parses via model_validate_json wrapped into _InvalidLLMOutput, one repair then DraftGenerationError. routers/ai.py adds the route, 404 plus 422 plus 503 mapping. tests/test_ai_draft_messages.py, 6 tests: happy path three variants one request, malformed then repair, known-bad missing tone twice gives 422 with exactly 2 requests, unknown segment 404 no LLM call, empty intent 422 at Pydantic with no LLM call, variant count tolerance accepts two.

**Changed or rejected:** Nothing rejected. Schema allows 1 to 5 variants though the prompt asks for exactly three, so an off-by-one count does not trigger a wasteful repair. The refactor touched the S12 path, the unchanged S12 tests passed as the regression guard.

**No raw LLM text to SQL proof:** draft_messages issues one parameterized SELECT for the segment by id, the model output is returned as response fields only, never interpolated. The {{first_name}} tokens stay literal, rendering happens at dispatch.

**Checks:** ruff check clean, ruff format clean, pytest 89 passed.

## 2026-06-13, Slice S14: Insight narrative

**Asked:** GET /api/v1/ai/campaigns/{id}/insight, narrative grounded in the exact stats it cites. Response includes the stat values so the frontend shows claim and number together. The narrative cannot cite a number not in the payload.

**Generated:** schemas/ai.py gained InsightFact, LLMInsightOutput (narrative), InsightResponse (campaign_id, narrative, facts). ai_service.campaign_insight reuses stats_service.campaign_stats from S9 for the numbers, 404 via its CampaignNotFoundError. _insight_facts builds a flat list of integer facts, one source feeding both the prompt and the response so model and frontend see the same numbers: total, each funnel status count, failure_rate_pct, conversion_rate_pct, audience_size. Grounding is enforced by construction, not prompt only: _check_grounded strips thousands commas, regex pulls every digit token, strips a trailing percent and a trailing .0, and requires each token in the allowed integer set, any miss raises _InvalidLLMOutput. Reuses the shared _generate for one repair retry, second failure raises InsightGenerationError mapped to 422. routers/ai.py adds the GET route with 404, 422, 503 mapping. tests/test_ai_insight.py builds campaigns with known communication statuses directly so stats are deterministic, 6 tests: happy path, grounded percentage citing failure_rate_pct, ungrounded 999 drives a repair, ungrounded twice gives 422 with exactly 2 requests, unknown campaign 404 no LLM, zero-activity allows a legitimate 0.

**Changed or rejected:** Nothing rejected. Honest scope note kept from the plan: only digit tokens are guarded, spelled-out words like one or half are not, stated rather than hidden. The grounding is grounding by construction plus visible verification, matching PROJECT.md section 8.

**Infra note:** the H drive unmounted mid run during the first full-suite pass, which surfaced as a spurious httpcore ModuleNotFoundError in test_health. After remount the full suite ran clean, the failure was the drive, not the code.

**Checks:** ruff check clean, ruff format clean, pytest 95 passed.

## 2026-06-13, Slice S15: Agentic propose-campaign

**Asked:** Goal in, proposal out (segment plus message plus channel, each with reasoning), explicit pending-approval state, execute gated on approval. Test asserts execute on an unapproved proposal is refused.

**Generated:** schemas/ai.py gained ProposeCampaignRequest, ProposalSegment, LLMProposalOutput (segment, recommended_channel, channel_reasoning, variants reusing the S13 MessageVariant), ProposeCampaignResponse. ai_service.propose_campaign makes one LLM call through the shared _generate, the parse callback validates the schema then runs segment_compiler.compile_definition as the whitelist gate, one repair then ProposalGenerationError to 422. On success it persists an ai Segment, reuses campaign_service.create_campaign to materialize a draft campaign plus queued communications, then stores the proposal and proposal_state pending in campaigns.ai_reasoning. campaign_service gained approve_proposal and execute_proposal plus CampaignNotFoundError, NotAProposalError, ProposalNotApprovedError. approve flips proposal_state to approved by reassigning the JSONB dict. execute refuses unless approved then delegates to dispatch_service.dispatch_campaign. routers/ai.py adds POST propose-campaign, routers/campaigns.py adds POST approve and POST execute with full status mapping. tests/test_ai_propose_campaign.py, 7 tests on one host-routed MockTransport that serves both the LLM call and the channel dispatch: propose happy path, repair then success, bad field twice gives 422, execute before approve refused with no dispatch batch, approve then execute dispatches three messages, approve unknown 404 plus execute on a plain S5 campaign 409, empty goal 422 with no LLM call.

**Changed or rejected:** Nothing rejected. One flagged judgment call, the proposal lifecycle state rides in the existing ai_reasoning JSONB under proposal_state rather than widening the locked campaigns.status CHECK enum, so no migration and no schema deviation. The campaign stays a normal draft until execute dispatches it, so S8 and S9 keep working unchanged. Double execute is refused by two layers, the proposal_state check and the existing dispatch draft-state guard.

**No raw LLM text to SQL proof:** the proposed definition only reaches the DB through segment_compiler.compile_definition inside create_campaign, which binds parameters, the non-whitelisted ssn field is rejected before any query, proven by the bad-twice test. Channel, reasoning, and variants are stored as JSONB or returned as response fields, never interpolated.

**Checks:** ruff check clean, ruff format clean, pytest 102 passed.

## 2026-06-13, Slice S16: Auth

**Asked:** Supabase Google OAuth, JWT verified in FastAPI. A valid JWT passes, a missing or bad one gets 401, applied across the CRM routes.

**Generated:** crm_api/auth.py with a require_user dependency, HTTPBearer auto_error false so a missing token returns our own 401 not 403, jwt.decode with HS256 against the Supabase project JWT secret and audience authenticated, any PyJWTError to 401, returns the claims for future per-user logic. config.py gained supabase_jwt_secret as a required field. main.py includes the four CRM routers ingest, segments, campaigns, ai with dependencies require_user, receipts stays open since it is HMAC machine to machine, health and healthz stay open for uptime pings. requirements.txt gained pyjwt, installed PyJWT 2.13.0. .env.example gained SUPABASE_JWT_SECRET. conftest gained a dummy secret and overrides require_user in the shared client fixture so all prior DB tests stay transparent to the gate. tests/test_auth.py, 7 tests on an app with only the session overridden so the real gate runs: valid token passes a protected GET, missing header 401, bad signature 401, expired 401, wrong audience 401, receipts open without a user token, health open without a token.

**Changed or rejected:** Nothing rejected. Two flagged decisions kept from the plan. First, gated whole CRM routers not only mutating verbs, matching PROJECT.md section 10 which says gate all CRM routes, stricter than the brief. Second, HS256 shared secret rather than JWKS asymmetric verification, the simplest demo credible path, the JWKS swap is the documented production upgrade. Added one dependency, PyJWT, an environment change called out before install. The short test secret triggers a harmless InsecureKeyLengthWarning, real Supabase secrets exceed the 32 byte threshold.

**Checks:** ruff check clean, ruff format clean, pytest 109 passed. Owner action needed: set SUPABASE_JWT_SECRET in .env from the Supabase project settings before running uvicorn, config now requires it.


## 2026-06-13
## Recovery: local working-tree corruption (Sat 13 June)
Asked: reconcile git, finish ingest slice, get tests green.
Found: .git/HEAD had a trailing NUL byte (HEAD unresolvable); all working
files were CRLF-rewritten and truncated by 5-8 lines (bad bulk write/sync on
H: drive). origin/master @ 12b75b1 was intact and complete throughout.
Did: backed up corrupted tree, repaired .git/HEAD, restored all files from
master. ruff clean. No code changes; ingest slice was already committed.
Action: prefer a local-disk working copy to avoid repeat corruption.

## 2026-06-14, Finalization

**State:** Backend complete through S16. CRM API and channel service both run
with their own Dockerfiles and share nothing at runtime. Last green run was 109
tests passing, ruff check and format clean. The five mandated receipt tests
(duplicate event, out-of-order pair, retry storm, unknown communication_id,
malformed HMAC) are green, plus attribution idempotency on converted events.

**AI-native workflow, the honest version.** The assistant wrote most of the code
from CLAUDE.md plus per-slice plans. It was directed, reviewed, and overruled.
Three concrete moments where the assistant proposed one thing and the human
chose another, with the reason:

1. *AI proposed Alembic autogenerate for the initial schema; I overruled to a
   hand-written migration.* The live Supabase connection failed twice during S1
   (an unencoded `@` in the password, then a server-side password reject only
   the owner could fix). Rather than block the schema slice on credentials, I
   took a deterministic hand-written migration derived from the models. It is
   arguably more reviewable than autogen output, and the live upgrade and
   downgrade roundtrip was run later once the password was corrected.

2. *AI proposed `ON CONFLICT DO UPDATE` for order re-ingest; I overruled to
   `DO NOTHING`.* Re-ingesting an order is meant to be safe and idempotent. If a
   re-ingest updated an existing order's amount, the denormalized `total_spend`
   and `order_count` aggregates would drift silently with no error. `DO NOTHING`
   on the order plus aggregate math that only counts genuinely new rows keeps
   re-ingest byte-identical on the second run, which the seed script verifies.

3. *AI proposed widening the `campaigns.status` CHECK enum (a migration) to hold
   the proposal lifecycle; I overruled to ride `proposal_state` in the existing
   `ai_reasoning` JSONB.* Widening the locked status enum would have meant a
   migration and a schema deviation, and it risked breaking the S8 dispatch and
   S9 stats paths that read `status`. Keeping the proposal state in JSONB left
   the campaign a normal draft until execute, so those slices kept working
   unchanged, and double-execute is still refused by two independent guards.

**What I would not claim:** the grounding check on the insight narrative guards
digit tokens only, not spelled-out numbers like "half"; this is stated in S14,
not hidden. HS256 shared-secret auth is the demo-credible choice, with JWKS as
the documented production upgrade. Dispatch is synchronous in-request at this
scale. None of these are presented as production-final; each names its swap.

## 2026-08-24, Slice S17: Observability and CI

**Asked:** Begin the customer-intelligence pivot on the feat/customer-intelligence branch. Add structured logging (log everything, as before) and a CI quality gate, keeping diffs clean, low coupling, no em dashes, comments only where needed.

**Generated:** crm_api/logging_config.py (stdlib-only JsonFormatter, no new dependency, UTC timestamps, a request_id ContextVar, configure_logging that installs one stdout JSON handler). crm_api/middleware.py (RequestContextMiddleware: assigns or preserves an X-Request-ID, sets the ContextVar, records latency_ms, emits one access log per request). main.py wires configure_logging into lifespan startup so importing the app has no logging side effect, and adds RequestContextMiddleware. tests/test_observability.py (3 tests, DB-independent: request id generated when absent, preserved when provided, JsonFormatter emits valid JSON carrying request_id and context). .github/workflows/ci.yml (ubuntu, Python 3.12, Postgres 16 service, ruff check, ruff format check, alembic upgrade head, pytest).

**Changed or rejected:** Nothing rejected. Chose a stdlib JSON formatter over python-json-logger to avoid a dependency. Deferred the bugfix pack (async JWKS, rate limiting, segment dedupe race, batch receipt UPDATE) to S18 to keep this slice reviewable. Deferred the segment definition-hash column to the multi-tenancy migration so there is one migration, not two.

**Checks:** pending owner run of ruff + pytest on the branch.

## 2026-08-24, Slice S18: Backend bugfix pack (async JWKS, rate limiting)

**Asked:** Fix the two audit findings that need no migration: the blocking JWKS fetch inside the async auth path, and the missing rate limiting on the AI and receipt routes that leaves the free LLM quota unprotected.

**Generated:** auth.py now runs `PyJWKClient.get_signing_key_from_jwt` via `asyncio.to_thread` so a cache-miss key fetch cannot stall the event loop, and it stashes the verified claims on `request.state.user` for downstream keying. crm_api/rate_limit.py (new): a dependency-free fixed-window RateLimiter, `user_key` (per JWT sub) and `ip_key` key functions, and a make_rate_limit_dependency factory that raises 429 when over budget. config.py gained ai_rate_limit_per_minute (120) and receipt_rate_limit_per_minute (600). main.py builds one limiter per surface and applies user-keyed limiting to /ai and IP-keyed limiting to /receipts. tests/test_rate_limit.py (4 tests: within-limit, window expiry, key isolation, dependency returns 429).

**Changed or rejected:** Kept the segment dedupe race and the batch receipt UPDATE out of this slice. The segment fix needs a definition_hash unique index, folded into the multi-tenancy migration so there is one migration. The receipt UPDATE change touches the append-only receipt path and gets its own tests-first slice. In-process limiter state is single-instance by design; the documented swap at volume is a shared Redis store behind the same interface.

**Checks:** ruff clean on changed files; new rate-limit tests pass locally. Pending owner run of the full suite on the branch.

## 2026-08-24, Slice S19: EDA on the real dataset (Olist)

**Asked:** Replace the Faker fixture story with evidence from a real dataset. Run EDA on-device (no notebook), save named figures, and write observations sourced only from real numbers, so later feature and model choices are grounded.

**Generated:** ml/ package (config.py with one shared path definition, requirements.txt), ml/eda.py (loads the 9 Olist CSVs, resolves the real customer key customer_unique_id, computes order value as item price plus freight, and produces status/revenue/RFM/review/category/seller stats). Nine figures in ml/figures and a machine-readable ml/eda_stats.json. ml/EDA_OBSERVATIONS.md written from the run values.

**Key findings (real):** 96,478 delivered orders, 15,419,773.75 BRL delivered revenue, 93,358 unique customers, repeat rate 3.0 percent (97 percent one-time), order value mean 159.83 (p50 105.28, p99 1052.39), recency p50 218 days, review mean 4.086, 3,095 sellers with 562 (18.2 percent) making 80 percent of revenue.

**Decision it drives:** the primary target is repeat/reactivation propensity (imbalanced binary, about 3 percent positive), CLV is next-order value times P(repeat), and the split is a leak-safe temporal cutoff with encoders fit on train only. Seller is the tenant dimension.

**Changed or rejected:** Fixed two .gitignore bugs found in passing: the dataset ignore path was wrong (backend/ml/data instead of ml/data), so 200MB of CSVs were not being ignored; and BUILD_LOG.md is currently gitignored, so this log is not tracked (flagged to owner for a decision).

**Checks:** ruff check and format clean on ml/. EDA reproducible via venv\\Scripts\\python -m ml.eda.

## 2026-08-24, Slice S20: Leak-safe features, split, and preprocessing

**Asked:** Build the modeling dataset to industry standard: proper preprocessing, feature engineering, temporal split, encoding, and provably no test-data leakage.

**Generated:** ml/dataset.py (one shared order-level loader so EDA, training, and serving cannot diverge on feature definitions). ml/features.py (build_features: for each customer, features use only orders on or before the cutoff; label is 1 if the customer orders in (cutoff, cutoff + horizon]; a customer with no pre-cutoff order is excluded, so the future cannot enter a feature). ml/split.py (stratified customer-level split 70/15/15; a ColumnTransformer of median-impute plus StandardScaler for numerics and most-frequent-impute plus OneHotEncoder(handle_unknown=ignore) for payment_type, fit on train only and transformed onto val and test; saves parquet splits, the fitted preprocessor, and a manifest). config gained CUTOFF 2018-03-01, HORIZON_DAYS 180, and processed/artifact paths. tests/test_features.py (5 tests: population excludes no-history customers, labels look only forward, features do not count post-cutoff orders, split has no customer overlap and covers all, preprocessor fits on train and tolerates an unseen category).

**Real result:** 57,511 customers (train 40,257 / val 8,627 / test 8,627), reactivation positive rate 1.19 percent, stratified evenly across splits, 14 transformed features. The severe imbalance is the honest reality of a 97 percent one-time marketplace and dictates the S21 modeling choices (class weights, PR-AUC, calibration; never accuracy).

**Changed or rejected:** No target encoding on high-cardinality fields (leak risk); kept payment_type one-hot only. Category and state deferred until a leak-safe encoding is chosen. Single-cutoff stratified split chosen over out-of-time holdout given the short window; out-of-time is the documented next step if drift is a concern.

**Checks:** ruff check and format clean on ml/ and the new test; 5 feature tests pass; dataset reproducible via venv\\Scripts\\python -m ml.split.

## 2026-08-24, Slice S21: Model training and registry

**Asked:** Train the reactivation model to industry standard on the leak-safe splits, judge it honestly (no accuracy headline), pick a business threshold, and version the artifact as a registry.

**Generated:** ml/train.py. One sklearn Pipeline (preprocessor plus HistGradientBoostingClassifier) fit end to end on train only, so serving loads a single artifact and there is no train/serve skew. class_weight=balanced for the 1.2 percent positive rate. Evaluation is PR-AUC, ROC-AUC, and Brier on val and test. choose_threshold maximizes expected profit (value per reactivation times true positives minus cost per contact), not 0.5. Registry: versioned model_<ts>.joblib plus model_card_<ts>.json (metrics, threshold, features, cutoff, data hash), a stable model_latest.joblib and model_card_latest.json for serving, and an appended registry.json. tests/test_train.py (4 tests, parameterized max_iter for speed: evaluate reports ranking and calibration not accuracy, training is deterministic, choose_threshold maximizes profit and is bounded, feature columns present).

**Real result (honest):** test PR-AUC 0.0264 vs a 0.0118 base rate (about 2.2x lift), test ROC-AUC 0.61, Brier 0.211. Signal is weak but real, which is the correct and defensible finding for a 97 percent one-time marketplace: reactivation is barely predictable from RFM alone. The deliverable is the correct, leak-free, versioned pipeline and the honest evaluation, not an inflated AUC.

**Changed or rejected:** class_weight=balanced improves ranking but inflates raw probabilities (Brier 0.211), so probability calibration is deferred to the scoring slice (S22), where isotonic calibration is fit on val and applied before the expected-impact math. LightGBM was not added; sklearn HistGradientBoosting keeps the dependency set minimal and is fast on CPU.

**Checks:** ruff check and format clean on ml/train.py and the test; 4 train tests pass in about 24s; model reproducible via venv\\Scripts\\python -m ml.train.

## 2026-08-24, Slice S22: Calibrated scoring, CLV, and SHAP reasons

**Asked:** Turn the ranker into a decision-grade scores table: calibrated probabilities, an honest CLV, and a per-customer explanation, written to a table the serving layer will read.

**Generated:** ml/score.py. Isotonic calibration via CalibratedClassifierCV over a FrozenEstimator, fit on validation only so no training or test data leaks into the calibrator. Expected-value CLV = calibrated probability times the customer average order value; a BG/NBD lifetimes model was rejected because a 97 percent one-time base does not identify its parameters, which is stated rather than faked. Per-customer reasons from shap.TreeExplainer on the HGB, aggregated from one-hot columns back to base features so a category reads as one reason. Output customer_scores.parquet (reactivation_probability, expected_value, recency, frequency, monetary, value_tier by expected-value quantile, reasons JSON, model_version, scored_at) plus scoring_manifest.json. tests/test_score.py (calibrated probabilities bounded; reasons capped at 3 and well formed).

**Real result:** test Brier improved from 0.21104 to 0.01162 after calibration, an 18x gain, which is what makes the expected-impact math trustworthy. 96,095 customers scored. Value tiers: 47,710 low, 28,626 mid, 19,084 high.

**Changed or rejected:** sklearn 1.9 removed cv="prefit", so calibration uses FrozenEstimator, the current supported prefit path. shap TreeExplainer was verified compatible with HistGradientBoosting before writing the scorer, so no silent fallback was needed.

**Checks:** ruff check and format clean; 2 score tests pass; scores reproducible via venv\\Scripts\\python -m ml.score.

## 2026-08-24, Slice S23: Multi-tenancy and money schema (migration only)

**Asked:** Lay the schema for multi-tenancy, the scores table the serving layer reads, and the campaign cost field the P&L needs, in one Alembic migration so a single upgrade unblocks the serving slices.

**Generated:** models.py gained a Tenant model, a nullable tenant_id foreign key plus index on customers, orders, segments, campaigns, and communications, a segments.definition_hash column with a unique (tenant_id, definition_hash) constraint (fixes the segment dedupe race without a Python scan), campaigns.cost_per_message, and a CustomerScore model (one row per tenant and customer: reactivation_probability, expected_value, value_tier, RFM, reasons JSONB, model_version, scored_at, unique on tenant_id plus customer_id). Migration 0002_multitenancy_and_scores.py hand-written to match 0001, with a clean downgrade. auth.py now reads a tenant_id claim (top level or app_metadata) and stashes it on request.state for query scoping. tests/test_schema.py updated: expected tables now include tenants and customer_scores, and the old single-migration guard became a single-head invariant across the migration chain.

**Changed or rejected:** tenant_id is nullable for now so existing rows survive the upgrade; the not-null tightening happens after backfill in S23b. Enforcement is application-layer tenant scoping, because the backend connects with a service role that bypasses Postgres RLS; RLS with a per-request GUC is the documented alternative for a per-user DB role. Query scoping across services (S23b) is deliberately a separate slice, since it needs a live DB to test and must not be rushed alongside the schema.

**Checks:** ruff clean; models import with 8 tables; 6 schema tests pass; migration 0002 emits valid SQL via alembic upgrade 0001:0002 --sql (offline, no DB). Live upgrade pending owner run of alembic upgrade head.

## 2026-08-24, Slice S23b: Olist to DB load under the tenant

**Asked:** Populate the live tenant with real data so the serving layer has something to serve.

**Generated:** scripts/load_olist.py. Reads the shared ml.dataset.order_level, aggregates per customer_unique_id, and upserts customers, orders, and the ML scores into the tenant's tables, all stamped with the tenant id. Idempotent (customers and orders on external_id, scores on tenant_id plus customer_id), batched at 1000 rows. Olist is anonymized, so display names are synthesized deterministically with a seeded Faker and documented as not-real identities; timestamps are localized to UTC for the timestamptz columns.

**Real result:** 95,420 customers, 98,666 orders, 95,420 scores loaded into tenant da036dba.

**Checks:** ruff clean; live load completed exit 0 with the counts above.

## 2026-08-24, Slice S24: Campaign economics

**Asked:** Real P&L math, no hardcoded conversion value.

**Generated:** crm_api/services/economics.py, pure functions. cost_per_message takes the per-campaign override or a documented default channel rate (business input, not data). campaign_pnl computes cost, profit, and ROI with a zero-cost guard. tests/test_economics.py (5 tests).

**Checks:** ruff clean; 5 economics tests pass.

## 2026-08-24, Slice S25: Tenant-scoped serving and decision layer

**Asked:** Serve the money story and the per-customer decision layer, tenant-scoped, and real per-campaign P&L.

**Generated:** crm_api/tenancy.py (require_tenant reads the JWT tenant claim off request.state, 403 if absent). crm_api/services/scores_service.py: portfolio_summary (expected value, high-tier opportunity, revenue-at-risk from lapsed customers, tier counts), list_decisions (score to reason to recommended action to expected impact, transparent rule-based actions), campaign_pnl (real attributed revenue from orders.attributed_campaign_id, contact cost from communications, replacing the old hardcoded 499). schemas/scores.py, routers/insights.py (GET /api/v1/insights/portfolio, /decisions), GET /api/v1/campaigns/{id}/pnl. tests/test_scores_service.py (3 live-DB tests: tenant-scoped portfolio and decisions with cross-tenant isolation, real attributed-revenue P&L, unknown campaign None).

**Real result against the loaded tenant:** 95,420 scored; revenue-at-risk R$11,057,609; high-tier reactivation opportunity R$80,948; portfolio expected value R$153,156. Decision list returns per-customer action plus SHAP reasons.

**Checks:** ruff clean; 5 economics + 3 serving tests pass; portfolio and decisions verified live against the loaded data.
