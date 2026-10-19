# Agentic Race, n8n entry

Agentic workshop-appointment booking from an in-car call. A Mercedes-Benz
driver presses the call button, Genesys Cloud routes the call over a SIP
trunk, and n8n identifies the customer, opens the Sirius case, finds a slot
at the dealer, books it, confirms it, or hands the caller to a person with
the context attached. Four platforms compete on the same brief; this is the
n8n build.

Status 2026-10-19: lab build complete, synthetic data, every Mercedes-Benz
system mocked. Golden set 5/5 on two consecutive runs, zero false
confirmations, all gates clean. Next: the voice gateway on the Genesys test
trunk (W3), the jury demo (2026-11-02 to 06), final on 2026-12-04.

Private, internal to n8n. Customer-named by necessity; do not fork publicly.

## How it works

```
in-car call -> Genesys Cloud (BYOC trunk, SIP UUI) -> voice gateway (LiveKit: SIP, STT, TTS)
                                                              | HTTPS, bearer
                                                              v
      n8n   POST ar/v1/session/start   POST ar/v1/session/turn   POST ar/v1/session/end
            POST ar/v1/session/transfer-result   GET ar/v1/health   GET ar/v1/handoff?correlationId=
              |                |                    |                   |
              v                v                    v                   v
        AR-10 identify   AR-11/12/13 dealers,   AR-15 Sirius case   AR-16 confirmation
        (CRM)            slots, booking (DSD)   AR-14 handover payload (hex UUI for SIP REFER)
        Data Tables: ar_sessions, ar_events, ar_markets, ar_codes   Postgres: ar_chat_memory (purged per call)
```

The gateway is thin: telephony and speech only. n8n is the brain. Every turn
rebuilds the system prompt from facts held in n8n (state, customer, vehicle,
dealer, offered slots), the AI Agent proposes a structured reply, and
deterministic guards decide what is said and whether the call is handed
over. A booking can only be created by the `BookAppointment` tool, and only
after the slot was read back in one turn and confirmed in the next.

Dialogue states: IDENTIFY, CONSENT, NEED, DEALER, SLOT, CONFIRM, BOOKED,
WRAPUP, with HANDOFF reachable from any of them. Handoff reasons:
CALLER_REQUEST, SCENARIO_REQUIRES_AGENT, NOT_UNDERSTOOD,
BACKEND_UNAVAILABLE, IDENTIFICATION_FAILED, NO_SLOT, POLICY,
PLATFORM_ERROR. Return UUI payload
`1;<correlationId>;<sessionId>;<intent>;<reason>;<lang>`.

## Repository layout

| Path | What |
|---|---|
| `docs/agentic-race/00-sources-read.md` | Which source documents were read, when, and which were empty |
| `docs/agentic-race/01-source-notes.md` | Distilled facts: timeline, criteria, rules, journey, Genesys contract, people |
| `docs/agentic-race/02-technical-spec.md` | The technical specification (API, dialogue contract, UUI, data, security) |
| `docs/agentic-race/03-voice-layer.md` | Voice gateway decision: LiveKit primary, AudioCodes fallback |
| `docs/agentic-race/04-workflow-catalogue.md` | Every workflow, its inputs, outputs and the guards |
| `docs/agentic-race/05-test-plan.md` | Golden set, results, criteria mapping, red-team and load plans |
| `docs/agentic-race/06-sip-trunk-form-draft.md` | Draft answers for the Genesys SIP trunk form |
| `docs/agentic-race/07-runbook.md` | Health, failures, credentials, promotion, escalation |
| `docs/agentic-race/08-open-questions.md` | Open questions with owners and dates |
| `docs/agentic-race/09-race-plan.md` | W2 to W8 plan with owners and blocking dependencies |
| `docs/agentic-race/10-handoff.md` | Handoff: what was built, architecture, limits, deferred items |
| `workflows/agentic-race/` | 19 workflow JSON files, the generator (`build/gen.py`), the deploy script, lab ids, README |
| `scripts/ar-golden-run.py` | Scripted calls through the API; writes a scored report per run |

## Workflows

| Id | Name | Role |
|---|---|---|
| AR-01 | Session start | Parse UUI, idempotency, market, identify, case, greeting |
| AR-02 | Dialogue turn | Prompt from facts, agent with three tools, guards, handover build |
| AR-03 | Session end | Case outcome, confirmation, memory purge |
| AR-04 | Transfer result | REFER result, retry once, then end |
| AR-05 | Health and handoff lookup | Probe; agent-desktop context by correlation id |
| AR-10 to AR-17 | Sub-workflows and tools | Identify, dealers, slots, booking, handover, Sirius case, confirmation, log |
| AR-20 to AR-23 | Mocks (lab only) | CRM, DSD Book, Sirius case, comms |
| AR-30 | Error workflow | Failures to `ar_events` |
| AR-90 | Seed configuration | Markets and codes, once per environment |

Details, inputs and outputs: `docs/agentic-race/04-workflow-catalogue.md`.

## Quick start on a lab instance

Requirements: n8n 2.35 or later with Data Tables, a Postgres database for
chat memory, an OpenAI-compatible model endpoint, Python 3.9+.

```bash
# 1. ids: edit workflows/agentic-race/lab-ids.json with your credential and Data Table ids
# 2. deploy (two passes: sub-workflows first, then callers)
export N8N_LOCAL_WORKFLOW_LAB_API_KEY=...   # from your secrets manager, never in a file
cd workflows/agentic-race && python3 build/deploy.py && cd ../..
# 3. seed markets and codes
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:5678/webhook/ar/v1/admin/seed
# 4. run the golden set
cp .env.example .env.lab && $EDITOR .env.lab
python3 scripts/ar-golden-run.py
```

Edit `build/gen.py`, never the JSON. `deploy.py --only <key,key>` redeploys a
subset. Gates before a commit: validate, lint (allow `localhost` and
`host.docker.internal` on the lab), layout check. See
`workflows/agentic-race/README.md`.

## API

| Endpoint | Body | Returns |
|---|---|---|
| `POST ar/v1/session/start` | `correlationId`, `uui{version,vin,msisdn,language,raw}`, `sip`, `gateway` | `sessionId`, `speak` (disclosure plus greeting), `customer.known`, or a handover |
| `POST ar/v1/session/turn` | `sessionId`, `transcript`, `asrConfidence` | `speak`, `action` (continue, handoff, end), `state`, `intent`, `handoff{referTo,uuiHex,...}` |
| `POST ar/v1/session/end` | `sessionId`, `reason` | `caseStatus`, `confirmationSent` |
| `POST ar/v1/session/transfer-result` | `sessionId`, `result` | `action` (release, speak_and_retry_once, speak_and_end) |
| `GET ar/v1/health` | | `ok` |
| `GET ar/v1/handoff?correlationId=` | | session summary for the agent desktop |

All endpoints take `Authorization: Bearer <token>` through an n8n header
credential. Full contract: `docs/agentic-race/02-technical-spec.md`.

## Testing

`scripts/ar-golden-run.py` drives five scripted calls (PL happy path, ES
caller asks for a person, EN unknown VIN, DE false-confirmation probe, ES
transfer fails twice) and scores final action, booking, case status, handoff
reason, transfer retries, false-confirmation hits, handoff context and
memory purge. Reports land in `data/agentic-race/runs/` (gitignored). Pass
bar before any promotion: 5/5 twice in a row. Results and the mapping to
the Genesys acceptance tests and the nine race criteria:
`docs/agentic-race/05-test-plan.md`.

## Security and privacy

No secrets in workflows; values come through the n8n credential system.
API workflows keep success execution data off so bearer headers never land
in the execution store. No call audio and no transcript are retained on the
n8n side: chat memory is deleted at session end, `ar_events` holds
structured fields only. Identifiers (VIN, phone number) are plumbed into
tools and never shown to the model.

## Open questions

The full list with owners and dates is `docs/agentic-race/08-open-questions.md`.
The ones that change the build:

1. DSD connector decision (2026-10-21): direct DSD Book API or through the
   Sirius API. AR-21 stands in until then.
2. Return UUI encoding: hex (64 bytes, no case id in the payload) or ASCII
   agreed with Genesys.
3. Model access: the Model Garden model through Nexus, credential and
   endpoint shape.
4. Test queue with "Take Back and Transfer" enabled, and the return numbers
   per market.
5. Test customer records in CRM for PL, ES, DE and GB.

## Source of truth and sync

This repository mirrors the `agentic-race` paths of the engagement repo.
Edit there, then `scripts/sync-from-engagement.sh` and commit here. Lab run
reports, `.env.lab` and anything under `data/` never enter either repo.
