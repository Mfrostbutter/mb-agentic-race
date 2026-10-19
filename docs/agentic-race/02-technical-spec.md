# Agentic Appointment Booking on n8n: technical specification

Version 0.1, 2026-10-19. Author: Frosty (n8n FDE). Status: draft for the n8n
race team. Built from the race documents in `00-sources-read.md`; every
customer fact is traced in `01-source-notes.md`. Items marked **OPEN** need a
customer answer and are collected in `08-open-questions.md`.

## 1. What the MVP does

A customer presses the appointment option in the in-car IVR. Genesys Cloud
transfers the call to the n8n team's voice endpoint over a dedicated SIP trunk,
with the car's VIN, MSISDN, language and a correlation ID in the SIP UUI. The
voice gateway answers, speaks the market's AI disclosure, and from then on
every utterance becomes a turn against n8n. n8n identifies the customer and
vehicle, opens a Sirius case, finds the preferred dealer, offers real DSD Book
slots, books one, confirms by SMS or email, writes the confirmation code into
the case, and closes it. At any point the caller or the agent can hand the call
back to a human in Genesys with the correlation ID, session ID, intent, reason
and case ID in the REFER.

Scope for the W4 jury demo (2026-11-02 to 11-06): the happy path end to end
plus handover from every state, Poland and Spain, Polish, Spanish, English and
German. Reschedule and cancel are in the agent's intent set and route to the
same DSD tools; retailer callback for non-DSD markets routes to handoff. The
optional check-out call (journey step 6) is specified in section 12 and is not
in the W4 scope.

## 2. Design position

**n8n is the brain, not a tool behind someone else's brain.** The voice
gateway does audio only: SIP, UUI, REFER, speech to text, text to speech,
barge-in, silence timers. It has no prompt, no tools and no state beyond the
live call. Every decision, every system call, every log line and the handoff
payload come out of n8n. This is what puts criteria 1, 2, 3, 5, 6 and 7 of
the scorecard inside n8n workflows that the jury can open on a canvas, and it
is what makes the gateway replaceable in a day if the chosen product fails the
W6 security review.

Consequences accepted: one HTTPS round trip per turn (budget in section 9),
and the gateway must support an external "dialogue engine" over HTTP rather
than only its own built-in LLM loop. Section 4 states the contract; the
product choice is LiveKit self-hosted with AudioCodes VoiceAI Connect as the
fallback, argued in `03-voice-layer.md`.

## 3. Architecture

```
 car ──PSTN──> Genesys Cloud (IVR, Sirius routing)
                  │ SIP INVITE + UUI(version, correlationId, vin, msisdn, language)
                  ▼
          Voice gateway (SIP/SBC + STT/TTS)          ← MB cloud, EU, no recording
                  │ HTTPS, bearer auth, JSON            ▲ SIP REFER (Refer-To with UUI)
                  ▼                                      │
          n8n Enterprise at MB  ──────────────────────────┘
            AR-01 session start   AR-02 turn   AR-03 end   AR-04 transfer result
            AI Agent (model via Nexus / Model Garden) + tools:
              AR-10 IdentifyCustomer (CRM / Customer 360)
              AR-11 FindDealers (GIS / preferred retailer)
              AR-12 FindSlots (DSD Book)
              AR-13 BookAppointment (DSD Book)
              AR-14 RequestHandoff (builds the return UUI)
            AR-15 Sirius case (Salesforce REST)   AR-16 confirmations (SMS/email)
            AR-17 event log   AR-30 error workflow   AR-31 golden test runner
```

Components:

| Component | Where | Role |
|---|---|---|
| Genesys Cloud | MB CAC | IVR, trunk, return flow, recording, agent routing. Unchanged. |
| Voice gateway | MB cloud (AWS Helios or the n8n cluster's network) | SIP termination, UUI read, REFER with Refer-To UUI, NOTIFY handling, STT, TTS, VAD. Calls n8n. |
| n8n Enterprise | existing MB instance (int/prod requested by the platform team) | Everything in the box above. Queue mode for concurrency. |
| Model Garden via Nexus | MB | LLM for the dialogue agent and the parser fixer. STT/TTS if the Garden offers them, else the gateway's approved engines. **OPEN** which voice models are approved. |
| CRM / Customer 360 | MB | Customer and vehicle lookup by VIN and MSISDN. Read only. |
| DSD Book | MB, through the Sirius DSD booking API (Race Control preference) or direct API or MCP | Dealers, slots, booking, confirmation code. |
| Sirius (Salesforce) | MB | Case create, update, close. Standard REST. |
| Communication services | MB | SMS and email confirmation. |
| Session store | n8n Data Table `ar_sessions` (or Postgres on the MB instance) | One row per call. No transcript. |
| Chat memory | Postgres Chat Memory table, keyed by sessionId, purged at session end | Turn context for the agent. |
| Event log | n8n Data Table `ar_events` plus n8n executions, log streaming | Every event with correlationId, sessionId, UTC timestamp. No PII. |

## 4. Voice gateway to n8n contract (API v1)

Base path on the n8n instance: `/webhook/ar/v1/`. Auth: header auth
credential (`Authorization: Bearer <gateway token>`), token in the MB secret
store, rotated by the platform team. TLS terminated by the n8n ingress. Allow
list the gateway's egress IPs on the ingress. All timestamps UTC ISO 8601.

### 4.1 POST session/start

Called once the INVITE is answered, before any speech.

```json
{
  "correlationId": "7f0c0b4e-3f7e-4a2e-9b0f-0b8d6a1a1c22",
  "uui": { "version": "1", "vin": "W1K2970461A123456", "msisdn": "+48601234567", "language": "pl", "raw": "..." },
  "sip": { "callId": "a84b4c76e66710@gw", "from": "sip:+48601234567@gw", "to": "sip:111101@gw" },
  "gateway": { "vendor": "livekit", "sessionRef": "rm_8f2..." }
}
```

Response 200:

```json
{
  "sessionId": "S7K2M9Q4",
  "correlationId": "7f0c0b4e-...",
  "caseId": "CS-00481234",
  "market": "PL", "language": "pl",
  "state": "IDENTIFY",
  "customer": { "known": true, "greetingName": "Pani Kowalska", "vehicle": "GLC 300" },
  "speak": "<AI disclosure + greeting in session language>",
  "listen": true
}
```

Errors: 400 `validation_error` (missing correlationId or version, unknown
version), 401, 503 `service_unavailable` when CRM and case creation both
fail. On any non-200 the gateway speaks the market's fallback line and REFERs
with `intent=unknown, handoffReason=BACKEND_UNAVAILABLE` using a UUI it builds
from the inbound correlationId alone (the only time the gateway builds UUI).

### 4.2 POST session/turn

One call per final STT result.

```json
{ "sessionId": "S7K2M9Q4", "correlationId": "7f0c...", "turn": 3,
  "transcript": "chciałabym umówić przegląd w przyszłym tygodniu",
  "language": "pl", "asrConfidence": 0.91, "receivedAt": "2026-11-03T09:14:02.120Z" }
```

Response 200:

```json
{
  "sessionId": "S7K2M9Q4",
  "speak": "Mam wolne terminy ... Który pasuje?",
  "action": "continue",
  "state": "SLOT",
  "handoff": null,
  "endReason": null,
  "latencyMs": 1180
}
```

`action` is one of `continue` (speak, then listen), `handoff` (speak, then
REFER using `handoff`), `end` (speak, then hang up). `handoff`:

```json
{ "intent": "BOOK", "handoffReason": "CALLER_REQUEST", "caseId": "CS-00481234",
  "uuiPayload": "1;7f0c0b4e-3f7e-4a2e-9b0f-0b8d6a1a1c22;S7K2M9Q4;B;CR;pl;CS-00481234",
  "uuiEncoded": "<hex or ascii per agreed format>", "referTo": "sip:111102@<genesys-host>;...",
  "summary": "Customer wants a service appointment next week, preferred dealer Warszawa Okęcie, no slot chosen yet." }
```

The gateway sends the REFER exactly as given. `summary` is for the API-based
context exchange (section 6.4), never for the UUI.

The transcript is held in memory for the session and in the chat memory
table until session end. It is never written to `ar_events`, `ar_sessions`
or the case.

### 4.3 POST session/transfer-result

After a REFER: `{ "sessionId", "correlationId", "referAccepted": true,
"notifyStatus": "200 OK" | "486 Busy" | "timeout", "at": "..." }`. Response:
`{ "action": "release" }` on success, `{ "action": "speak_and_retry_once",
"speak": "..." }` on a first failure, `{ "action": "speak_and_end", "speak":
"..." }` on a second. The session records `handoffResult`. One retry, never a
loop (Genesys requirement).

### 4.4 POST session/end

`{ "sessionId", "correlationId", "endReason": "completed" | "caller_hangup" |
"handoff_completed" | "handoff_failed" | "gateway_error", "durationMs",
"turns" }`. n8n closes or delegates the case, sends the confirmation if the
booking exists and no confirmation was sent, purges chat memory, writes the
final event. Idempotent.

### 4.5 Health

`GET /webhook/ar/v1/health` returns 200 `{ "ok": true, "version": "..." }`
and is what the gateway and the load balancer probe. The gateway refuses new
INVITEs with 503 when n8n is unhealthy, which Genesys handles in its fallback
flow.

## 5. Dialogue design

### 5.1 States

```
IDENTIFY → CONSENT → NEED → DEALER → SLOT → CONFIRM → BOOKED → WRAPUP
                       └─ RESCHEDULE / CANCEL → (existing booking) → CONFIRM
HANDOFF reachable from every state. END reachable from BOOKED, WRAPUP, HANDOFF.
```

| State | Agent goal | Exit |
|---|---|---|
| IDENTIFY | Confirm the customer and vehicle returned by `IdentifyCustomer` ("Am I speaking with ... about your GLC?"). Never read back VIN or phone number. | confirmed → CONSENT; unknown VIN or denial → HANDOFF `IDENTIFICATION_FAILED` |
| CONSENT | Market disclosure was spoken at start; confirm the caller accepts continuing with the assistant. | yes → NEED; no → HANDOFF `POLICY` |
| NEED | Classify BOOK, RESCHEDULE, CANCEL, OTHER. Collect service type, urgency, mobility need. | BOOK → DEALER; RESCHEDULE/CANCEL → lookup; OTHER → HANDOFF `SCENARIO_REQUIRES_AGENT` |
| DEALER | Offer the preferred retailer from CRM; allow another by city. Non-DSD market or dealer → HANDOFF `SCENARIO_REQUIRES_AGENT` (the as-is retailer-callback path). | dealer chosen → SLOT |
| SLOT | Offer at most three slots from `FindSlots`. Caller picks by day and time. No slot fits → widen window once, then HANDOFF `NO_SLOT`. | slot chosen → CONFIRM |
| CONFIRM | Read back dealer, date, time, service, mobility. Explicit yes required. | yes → `BookAppointment` → BOOKED; no → SLOT or DEALER |
| BOOKED | Speak the DSD confirmation code, confirm the SMS/email channel. | → WRAPUP → END |
| HANDOFF | Speak the handover line, return the REFER payload. | gateway REFERs |

Two low-confidence turns in a row (ASR confidence below 0.5 or the agent
reports it could not map the utterance) in the same state → HANDOFF
`NOT_UNDERSTOOD`. Any tool returning `ok:false, error: backend_unavailable`
→ HANDOFF `BACKEND_UNAVAILABLE`. Silence handling (no speech for 8 s, two
prompts, then handoff) lives in the gateway with the thresholds in its config.

### 5.2 Intent and handoff-reason codes (proposal for Genesys agreement)

Intent: `BOOK`, `RESCHEDULE`, `CANCEL`, `OTHER`, `UNKNOWN`. Handoff reason:
`CALLER_REQUEST`, `SCENARIO_REQUIRES_AGENT`, `NOT_UNDERSTOOD`,
`BACKEND_UNAVAILABLE`, `IDENTIFICATION_FAILED`, `NO_SLOT`, `POLICY`,
`PLATFORM_ERROR`. In the compact UUI they travel as one-letter and two-letter
codes (section 6.3); the mapping table is a Data Table row set so Genesys can
re-code without a deploy.

### 5.3 Guardrails (deterministic, outside the model)

- **No booking without a code.** The spoken confirmation comes from a
  template filled with the `BookAppointment` result. The `FalseConfirmationGuard`
  node rejects any model reply that contains a confirmation phrase while the
  session is not in BOOKED and replaces it with a neutral line. This is the
  jury's "false confirmation rate" metric, enforced in code.
- **Slots are facts.** `BookAppointment` accepts only a `slotId` that
  `FindSlots` stored on the session in this call. The model cannot invent a
  slot.
- **Identity is plumbed, not spoken.** VIN and MSISDN come from the UUI into
  the tools as workflow context. The model never sees the raw VIN or the
  phone number and cannot be talked into changing them.
- **One case per call.** Created at session start, keyed by correlationId;
  every later write is an update. Re-delivery of the same correlationId
  returns the existing session (idempotent start).
- **Language lock.** The reply language is the session language from the
  UUI or market default; the model is instructed to answer in it and the
  guard checks the response is non-empty and under 400 characters (voice
  pacing).
- **Structured output or nothing.** The agent must return the JSON schema in
  section 7; parsing failures go through the auto-fixer once, then a
  deterministic apology and handoff `PLATFORM_ERROR`.

### 5.4 Prompts

System prompt (English, with "Reply in {{language}}") states the persona,
the current state and its goal, the facts on the session (customer greeting
name, vehicle, dealer, offered slots with ids), the rules above, and the
output schema. Per-tool guidance lives on the tools. Market texts
(disclosure, greeting, closing, handover line) come from `ar_markets` so a
new market is a row, not a prompt edit. The full prompt text is in
`workflows/agentic-race/README.md` next to the JSON.

## 6. Genesys integration

### 6.1 Inbound UUI parsing

The gateway passes the decoded UUI string and the parsed fields; AR-01
re-parses from `raw` as the source of truth so the parsing rules are in one
place and testable. Format to agree with Genesys (**OPEN**, our proposal):
positional, semicolon separated, version first:
`1;<correlationId>;<vin>;<msisdn>;<language>`; empty fields allowed
(`1;abc;;+48601234567;pl`). Version `1` is the only accepted value; any other
→ 400 and handoff `PLATFORM_ERROR`.

### 6.2 Correlation

`correlationId` is stored verbatim, returned verbatim in every response and
in the REFER, and is the first column of every `ar_events` row. The AI
`sessionId` is 8 characters from a 32-symbol alphabet (no vowels, no 0/1),
generated by n8n, unique per instance.

### 6.3 Return UUI

Genesys reads the return UUI only from the Refer-To URI:
`<sip:<returnAddress>?User-to-User=<payload>%3Bencoding%3Dhex>` with the
protocol discriminator either as the first octet or as a `pd=XX` parameter,
depending on the trunk's header type (`03-voice-layer.md`). Genesys
recommends 128 characters or fewer, and hex doubles the length, so the hex
budget is 64 bytes of payload.

Payload: `1;<correlationId>;<sessionId>;<I>;<RR>;<lang>` where I is B/R/C/O/U
and RR is CR/SA/NU/BU/IF/NS/PO/PE. With a 36-character UUID correlation ID
and an 8-character session ID that is 55 bytes, inside the hex budget. The
case ID does not fit in hex; it travels when the trunk is configured for
ASCII (`1;<correlationId>;<sessionId>;<I>;<RR>;<lang>;<caseId>`, 67 bytes),
otherwise Genesys gets it from the handoff lookup in 6.4 or from Salesforce
by correlation ID. AR-14 emits both encodings, enforces the agreed maximum,
logs `uui_truncated` if it had to drop the case ID, and never truncates the
correlation ID. **OPEN**: header type (`User-To-User PD Attribute` proposed),
encoding, `pd` value, Take Back and Transfer enabled on the trunk.

### 6.4 Larger context exchange

`GET /webhook/ar/v1/handoff?correlationId=<id>` (header auth, Genesys side)
returns `{ caseId, intent, handoffReason, state, summary, dealer, slot,
language, at }` for the agent desktop or a Genesys data action. No
transcript. Summary is generated by the agent at handoff time and stored on
the session for 24 hours, then purged.

### 6.5 Failure behaviour (our side of the table to agree)

| Case | Behaviour |
|---|---|
| Gateway cannot accept the call | SIP 503; Genesys fallback flow |
| n8n unhealthy at INVITE | Gateway answers, speaks fallback line, REFER `U;BU`; if REFER fails, releases the call so Genesys handles it |
| Tool failure mid-call | Agent apologises once, handoff `BACKEND_UNAVAILABLE` |
| Genesys rejects the REFER or NOTIFY fails | One retry after 3 s; then "a colleague will call you back", case set to `Delegated` with reason, call ends |
| Caller hangs up | session/end `caller_hangup`; case closed or delegated depending on state |
| Turn over 6 s | Gateway plays a filler; over 12 s, treats as tool failure |

## 7. Agent output schema

```json
{ "type": "object", "required": ["speak", "action", "state", "intent"],
  "properties": {
    "speak": { "type": "string" },
    "action": { "enum": ["continue", "handoff", "end"] },
    "state": { "enum": ["IDENTIFY","CONSENT","NEED","DEALER","SLOT","CONFIRM","BOOKED","WRAPUP","HANDOFF"] },
    "intent": { "enum": ["BOOK","RESCHEDULE","CANCEL","OTHER","UNKNOWN"] },
    "handoffReason": { "enum": ["CALLER_REQUEST","SCENARIO_REQUIRES_AGENT","NOT_UNDERSTOOD","BACKEND_UNAVAILABLE","IDENTIFICATION_FAILED","NO_SLOT","POLICY","PLATFORM_ERROR", null] },
    "slots": { "type": "object", "properties": { "serviceType": {}, "mobility": {}, "dealerId": {}, "slotId": {}, "notes": {} } },
    "confidence": { "type": "number" },
    "summary": { "type": "string" } } }
```

## 8. Data

### 8.1 `ar_sessions` (Data Table)

sessionId, correlationId, caseId, market, language, state, intent,
customerRef (CRM id, not name), vehicleRef, dealerId, dealerName,
offeredSlots (JSON string of `[{slotId, startsAt, dealerId}]`), slotId,
bookingRef, confirmationCode, confirmationChannel, confirmationSent,
handoffReason, handoffResult, handoffSummary, turns, startedAt, endedAt,
endReason, lastLatencyMs. Rows older than 30 days are deleted by AR-32
(retention job). No free text from the caller.

### 8.2 `ar_events` (Data Table, append only)

at (UTC), correlationId, sessionId, caseId, workflow, event, state, tool,
latencyMs, outcome, errorCode, detail (short, no PII). Events: session_start,
identify, consent, tool_call, tool_result, guard_triggered, handoff_requested,
transfer_result, booking_created, confirmation_sent, case_updated,
session_end, error.

### 8.3 `ar_markets` (Data Table, config)

market, languages, defaultLanguage, dsdEnabled, disclosureText per language,
greeting, closing, handoverLine, confirmationChannels, returnNumber,
slotWindowDays, maxSlotsOffered.

### 8.4 `ar_codes` (Data Table, config)

kind (intent|reason), code, short, genesysValue, description.

### 8.5 PII and retention

Held during the call only: transcript turns (chat memory table), names and
vehicle description in the session row. At session end: chat memory rows for
the sessionId deleted, `customer` fields on the session reduced to the CRM
reference, summary kept 24 hours for the handoff lookup then blanked. Nothing
is written to n8n execution data beyond what the nodes carry: the production
setting is `saveDataSuccessExecution: none` for AR-02 with a sampled debug
toggle, and `saveDataErrorExecution: all` with the transcript field redacted
by the `RedactForLog` node before any error path. Recording is never done.
Consent text and AI disclosure per market come from MB Legal (**OPEN**).

## 9. Non-functional

| Item | Target | How |
|---|---|---|
| Turn latency, n8n side | p50 ≤ 1.2 s, p95 ≤ 2.5 s | small fast model for turns, tools cached per session, queue mode workers warm, `maxIterations` 6 per turn |
| Concurrency | 20 concurrent calls per market at W7, 50 at scale | queue mode, 2 workers, webhook processors separate; load test in W6 with AR-31 at 20 parallel simulated calls |
| Availability | gateway and n8n health probes, Genesys fallback covers outage | section 4.5 |
| Observability | every event correlatable in under a minute | `ar_events`, n8n executions by correlationId tag, log streaming to the MB SIEM, OpenTelemetry if enabled on the instance |
| Versioning | workflow JSON in Git, promoted dev → int → prod with `@n8n/cli` | `n8n-promotion-pipeline` skill; tags `AR` and `AR-vX.Y` |
| Secrets | all credentials in n8n credential store backed by MB's secret manager | no tokens in nodes |

## 10. Security and governance

- Gateway to n8n: bearer token, IP allow list, TLS 1.2+.
- n8n to systems: technical users per system through n8n credentials;
  Salesforce via OAuth2 client credentials; CRM and DSD per their standard.
- Model calls: only through the Nexus gateway credential; model name and
  version recorded per execution for the TCO and the audit trail.
- Prompt injection: the model has no tool that can change identity, amount
  or target; tools validate every id against the session. Red-team script in
  `05-test-plan.md`.
- Data classification, PII document and MASSP4AI: started W1 per the race
  rules; the data flows in section 8 are the input. W6 security review slot
  to be booked by the tech lead (**OPEN** who).
- EU AI Act: disclosure at call start, handover on request, no emotion
  recognition, no biometric use. Consent per market via MB Legal.

## 11. Environments and promotion

| Env | Purpose | n8n | Gateway | Systems |
|---|---|---|---|---|
| lab (n8n FDE) | reference build, synthetic data, this repo | FDE lab | simulated by AR-31 | mocks AR-20 to AR-23 |
| dev (MB) | team build | MB n8n dev | dev gateway, own SIP trunk for self-test | sandboxes or mocks |
| int / pre-prod (MB) | race scoring, 200 golden calls | MB n8n int | Genesys test trunk | pre-prod systems, live data |
| prod (MB) | W7 pilot calls, PL and ES | MB n8n prod | prod trunk | prod |

Promotion by reviewed commit: `scripts/promote.py` today, `@n8n/cli`
promotion connections once the MB instance exposes them. Credential ids and
Data Table ids are bound per environment in the promotion map, never edited
in the JSON by hand.

## 12. Beyond W4

- W5: multilingual test set, handover from every state verified against the
  Genesys return flow, reschedule and cancel paths with live DSD, agent CSAT
  fields on the case.
- W6: the change task (new step or new market) is designed to be a Data
  Table row plus at most one new tool sub-workflow; load test; security
  review; red teaming.
- Check-out call (step 6): AR-40 schedules an outbound call after the
  appointment through the gateway's outbound API, asks three questions,
  writes the answers to the case. Spec only until W5.
- Proactive reminders by SMS the day before, same AR-16 path.

## 13. Risks specific to the n8n entry

| Risk | Mitigation |
|---|---|
| No approved voice model in the Garden by W3 | Gateway supports the Garden's STT/TTS when they exist and an approved external engine otherwise; raise at SteCo in W2 as the deck's risk 3 anticipates |
| Gateway fails the W6 review | Thin contract in section 4; second product kept warm in dev |
| DSD connector decision slips | Tools AR-11 to AR-13 are the only code that knows DSD; mock keeps the dialogue work moving |
| Turn latency over budget | Streaming TTS of the first sentence, filler audio, small model for turns |
| Hex UUI breaks the 128-char guidance | Section 6.3 fallback, decided with Genesys in W2 |
| Vendor hours cap | FDE time spent on the reference build and reviews, not on the MB build itself |
