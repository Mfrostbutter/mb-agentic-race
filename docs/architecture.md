# Architecture

What the build does, the contract the voice gateway calls, the dialogue
design, the data it keeps and the security position. Workflow by workflow:
`workflow-catalogue.md`.

## 1. What the MVP does

A caller reaches the assistant from the car. The assistant discloses that it
is an AI, identifies the caller from the vehicle identifier and phone number
the call carries, asks for consent, learns what is needed (service type,
mobility), agrees a dealer and a slot, reads the slot back, books on an
explicit yes, reads the confirmation code back and sends a confirmation. At
any point the caller can ask for a person; the call is then transferred with
the case id, intent, reason and a summary attached so the agent does not
start from zero.

Out of scope here: the voice layer itself (telephony, speech recognition and
synthesis), the real backend connectors (every backend is a mock on the
same instance) and outbound calling.

## 2. Design position

- The gateway is thin: telephony and speech only. n8n holds every fact and
  every decision.
- The model proposes, deterministic code decides. The prompt is rebuilt each
  turn from the session row; the reply is parsed into a fixed schema; guards
  can override the model's state, intent and wording.
- One booking path. Only the `BookAppointment` tool creates a booking, only
  for a slot that was offered in this call, read back in one turn and
  confirmed in the next. A reply that claims a booking without a code is
  rewritten.
- No audio, no transcript on the n8n side. The session row holds ids, states
  and codes; chat memory is purged at session end; the event log holds
  structured fields.
- Mocks swap for real systems node by node. Each backend sits behind one HTTP
  Request node in one sub-workflow, with a typed `{ok, ...}` contract.

## 3. API contract (v1)

Every call carries `Authorization: Bearer <token>` (n8n header credential)
and `Content-Type: application/json`. Responses are JSON. Errors from the
caller's side are 4xx with `{ok:false, error}`; platform errors are 5xx and
also produce a handoff with reason `PLATFORM_ERROR` where a session exists.

| Endpoint | Request | Response |
|---|---|---|
| `POST ar/v1/session/start` | `correlationId` (the call id, idempotency key), `uui {version, vin, msisdn, language, raw}`, `sip {from, to}`, `gateway {id, version}` | `sessionId`, `state`, `speak` (disclosure plus greeting), `customer {known, greetingName, vehicleLabel}`, `language`; or `action: handoff` with the handoff block |
| `POST ar/v1/session/turn` | `sessionId`, `correlationId`, `transcript`, `asrConfidence`, `language` | `speak`, `action` (`continue`, `handoff`, `end`), `state`, `intent`, `handoff {referTo, uuiHex, uuiAscii, handoffReason, summary}` when handing over |
| `POST ar/v1/session/end` | `sessionId`, `correlationId`, `endReason`, `durationMs`, `turns` | `caseStatus`, `confirmationSent` |
| `POST ar/v1/session/transfer-result` | `sessionId`, `correlationId`, `referAccepted`, `notifyStatus`, `at` | `action` (`release`, `speak_and_retry_once`, `speak_and_end`), `speak` |
| `GET ar/v1/health` | | `ok`, `service`, `version`, `at` |
| `GET ar/v1/handoff?correlationId=` | | `found`, `state`, `intent`, `handoffReason`, `handoffResult`, `confirmationCode`, `caseId`, `summary` |

The inbound UUI is positional: `version;vin;msisdn;language` with the raw
string kept for audit. The return UUI (set on the SIP REFER) is
`1;<correlationId>;<sessionId>;<intent>;<reason>;<lang>`, hex-encoded with a
64-byte budget, ASCII with 128; the payload is truncated from the summary
end, never from the ids.

## 4. Dialogue design

States: IDENTIFY, CONSENT, NEED, DEALER, SLOT, CONFIRM, BOOKED, WRAPUP, and
HANDOFF from any of them. Intents: BOOK, RESCHEDULE, CANCEL, INFO, AGENT.
RESCHEDULE and CANCEL hand over to a person in this build.

Each turn:

1. Load the session and market rows. Build the system prompt from facts:
   state, what is known about the customer and vehicle, the agreed dealer,
   the slots offered so far, the market's wording rules.
2. The AI Agent answers with a structured object: `speak`, `nextState`,
   `intent`, `handoffReason`, `pendingSlotId`, `confirmedSlotId`. Three tools:
   `FindDealers`, `FindSlots`, `BookAppointment`. Identifiers are plumbed into
   the tools from the session, never passed through the model.
3. Guards run on the parsed object. They advance a state the model forgot to
   advance after a clear yes, decline on a clear no, force a handoff when the
   caller asks for a person in any supported language, strip any booking
   claim without a confirmation code, replace the reply with the market's
   handover line on a handoff, and refuse a `SCENARIO_REQUIRES_AGENT` the
   model raised right after the caller agreed to a bookable dealer.
4. The session row is updated, the event is logged, and the reply goes back.

Handoff reasons: CALLER_REQUEST, SCENARIO_REQUIRES_AGENT, NOT_UNDERSTOOD,
BACKEND_UNAVAILABLE, IDENTIFICATION_FAILED, NO_SLOT, POLICY, PLATFORM_ERROR.
Each maps to a short code for the UUI and a contact-center routing value in
`ar_codes`.

Transfer result: a refused or failed REFER is retried once, then the
assistant speaks a closing line and ends; the case stays delegated so a
person follows up.

## 5. Data

| Store | Holds | Notes |
|---|---|---|
| `ar_sessions` (Data Table) | One row per call: ids, market, language, state, intent, customer and vehicle references, dealer, offered slots, booking and confirmation, handoff fields, counters, timestamps | No transcript, no free text from the caller |
| `ar_events` (Data Table, append only) | One row per step: correlation id, session, case, workflow, event, state, tool, latency, outcome, error code, detail | Structured fields only |
| `ar_markets` (Data Table, config) | Per market: languages, bookable flag, disclosure texts per language, handover and closing lines, confirmation channels, return address, slot window | Seeded by AR-90 |
| `ar_codes` (Data Table, config) | Intent and handoff reason codes with their short form, contact-center value and description | Seeded by AR-90 |
| `ar_prompts` (Data Table, config) | Everything the assistant says or is told: persona, rules, the goal per state, greeting, apology and confirmation lines, transfer lines; one row per key, a column per language, English fallback | Seeded by AR-90; a wording change is one cell |
| `ar_chat_memory` (Postgres) | The agent's conversation memory, keyed by session | Deleted at session end |
| `ar_mock_bookings`, `ar_mock_cases`, `ar_mock_customers`, `ar_mock_dealers` (Data Tables) | State for the mocks | Lab only |

## 6. Security and privacy

- Secrets only through n8n credentials: the gateway bearer, the Postgres
  connection, the model key. No value in a node parameter.
- API workflows keep success execution data off, so request headers never
  land in the execution store.
- The model never sees the VIN or the phone number; tools receive them from
  the session row.
- Chat memory is purged at session end; the memory-purge check is part of
  the scripted test set.
- Prompt injection: no tool can change identity or target; every tool
  validates its ids against the session. The false-confirmation guard is
  code, not prompt.
- AI disclosure at call start, handover on request, no emotion recognition,
  no biometric use. Disclosure wording per market is a placeholder until
  Legal supplies it.

## 7. Non-functional

- Turn latency is the model plus its tool calls. Non-model work per turn
  (two Data Table reads, guards, two writes, one log) is under 250 ms.
- Idempotency: `session/start` on a known correlation id returns the
  existing session; the booking key is session plus slot.
- One retry on transient HTTP failures inside the sub-workflows; callers
  branch on `ok:false`, they do not catch.
- Observability: every event in `ar_events` is correlatable by call id in
  under a minute; the error workflow AR-30 logs any failed execution.
