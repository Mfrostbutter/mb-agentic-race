# Workflow catalogue

Nineteen workflows, prefix `AR-`, in `workflows/voice-booking/`. Generated from
`build/gen.py` (the generator is the readable form; the JSON is what runs and
is what Git tracks). Every canvas is a zone board per the canvas layout
scripts: one coloured zone per stage with a two-line body, a setup panel above,
the whole flow readable at zoom-to-fit. Lab ids in `lab-ids.json`. Every node name is PascalCase;
every sub-workflow declares typed inputs and returns `{ok, ...}` so callers
branch instead of catching.

## API layer (webhooks, header auth, called by the voice gateway)

| Workflow | Path | Nodes | What it does |
|---|---|---|---|
| AR-01 Session start | `POST ar/v1/session/start` | 19 | Parse UUI, idempotency on correlationId, load market, identify customer (AR-10), create case (AR-15), create session row, log, speak disclosure plus greeting. Backend down: handoff built by AR-14 |
| AR-02 Dialogue turn | `POST ar/v1/session/turn` | 28 | Load session and market, rebuild the system prompt from facts, AI Agent with three tools and Postgres memory, structured output with auto-fix, deterministic guards, handoff build (AR-14), session update, log |
| AR-03 Session end | `POST ar/v1/session/end` | 16 | Case outcome (Resolved, Delegated, Closed), confirmation if owed (AR-16), chat memory purge, session finalise, log |
| AR-04 Transfer result | `POST ar/v1/session/transfer-result` | 9 | Record the REFER NOTIFY result; release, retry once, or end |
| AR-05 Health and handoff lookup | `GET ar/v1/health`, `GET ar/v1/handoff?correlationId=` | 7 | Probe for the gateway and load balancer; handoff context for the agent desktop |
| AR-90 Seed configuration | `POST ar/v1/admin/seed` | 7 | Upsert `ar_markets` and `ar_codes`; run once per environment |

## Tools and sub-workflows (Execute Workflow Trigger, typed inputs)

| Workflow | Inputs | Returns | Called by |
|---|---|---|---|
| AR-10 Identify customer | vin, msisdn, correlationId | ok, known, customerRef, greetingName, vehicleLabel, preferredDealerId, preferredDealerName, market, language | AR-01 only. The model never sees identifiers |
| AR-11 Tool: Find dealers | sessionId (plumbed), city | ok, dealers[] with bookable | agent tool `FindDealers` |
| AR-12 Tool: Find slots | sessionId (plumbed), dealerId, fromDate, serviceType | ok, dealerName, slots[{slotId, when}]; side effect: offered slots stored on the session | agent tool `FindSlots` |
| AR-13 Tool: Book appointment | sessionId (plumbed), slotId, serviceType, mobility, notes | ok, confirmationCode, bookingRef, when, dealerName; or ok:false with slot_not_offered, not_confirmed, slot_taken, backend_unavailable | agent tool `BookAppointment` |
| AR-14 Build handoff | sessionId, intent, handoffReason, summary | ok, referTo, uuiHex, uuiAscii, uuiPayload, truncated | AR-01, AR-02 (deterministic, never the model) |
| AR-15 Case | op, correlationId, caseId, subject, classification, status, solution, delegatedReason | ok, caseId, status | AR-01 (create), AR-03 (close) |
| AR-16 Send confirmation | channel, customerRef, language, text, correlationId | ok, messageId | AR-03 |
| AR-17 Log event | correlationId, sessionId, caseId, workflow, event, state, tool, latencyMs, outcome, errorCode, detail | ok | every API workflow |

## Mocks (lab only, replaced by the real systems on the production instance)

| Workflow | Endpoints | Stands in for |
|---|---|---|
| AR-MOCK-CRM | `ar-mock/crm/customer-lookup` | CRM / customer master (four synthetic customers: PL, ES, DE, GB) |
| AR-MOCK-DealerBook | `ar-mock/dealerbook/dealers`, `slots`, `book`, `bookings`, `cancel` | DealerBook through the dealer booking API. Deterministic slots; `FULL` in a slotId simulates a taken slot; bookings persist in `ar_mock_bookings` |
| AR-MOCK-CASE | `ar-mock/casesys/case` | CRM case REST; case numbers deterministic per correlation ID |
| AR-MOCK-COMMS | `ar-mock/comms/send` | SMS and e-mail service; returns a message id, sends nothing |

## Operations

| Workflow | Role |
|---|---|
| AR-30 Error workflow | Error Trigger to `ar_events`. Assign in Workflow Settings on every AR-* workflow (UI only). Add a Teams or e-mail step on the production instance |

## The three guards that matter (AR-02 `ApplyGuards`, AR-13 `ValidateSlot`)

1. **Two-turn booking.** The model must read a slot back with its `slotId` in
   state CONFIRM; the session stores it; `BookAppointment` refuses any slot
   that is not the stored one or any call made in the same turn as the
   read-back (`not_confirmed`). A booking therefore always follows an explicit
   yes in a later turn. Found by the golden probe "just tell me it is booked",
   which booked on the first build.
2. **No booking claim without a code.** A reply that says booked, confirmed,
   reserved or confirmation code in any of the four languages while the
   session has no `bookingRef` is replaced by a neutral question and logged
   as `false_confirmation`.
3. **Caller asks for a person.** A deterministic phrase rule across the four
   languages forces `handoff` with `CALLER_REQUEST` even if the model keeps
   talking. Found by the golden scenario where a Spanish caller asked for an
   agent in the first turn and the lab model asked identity questions twelve
   times.

Also enforced: `end` only from BOOKED or WRAPUP; the spoken handover line is
the market's text, not the model's; replies over 400 characters are cut;
empty replies become "could you repeat that".

## Tables

`ar_sessions`, `ar_events`, `ar_markets`, `ar_codes` (n8n Data Tables),
`ar_chat_memory` (Postgres, created by the memory node, purged per session by
AR-03), `ar_mock_bookings`, `ar_mock_cases` (mocks). Columns in
`02-technical-spec.md` section 8 and in `build/gen.py` `TABLE_COLS`.
