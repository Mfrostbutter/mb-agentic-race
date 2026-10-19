# Agentic Race handoff

State as of 2026-10-19. Lab build, synthetic data, every MB system mocked.
Read with `02-technical-spec.md` (what and why), `04-workflow-catalogue.md`
(what runs), `07-runbook.md` (how to operate) and `05-test-plan.md` (how we
know it works).

## 1. What was built

Nineteen n8n workflows that take a Genesys call from the in-car button to a
booked workshop appointment or a warm handover. The voice gateway (LiveKit,
outside n8n) handles SIP, speech-to-text and text-to-speech and calls four
n8n endpoints: start, turn, end, transfer result. n8n holds the dialogue
state, identifies the customer from the SIP UUI, opens and closes the
Sirius case, finds dealers and slots, books, sends the confirmation, and
builds the return UUI for the handover. A structured-output agent proposes
each reply; deterministic guards decide what is actually said and whether
the call is handed over. Nothing the model says can create a booking: only
the BookAppointment tool can, and only after the slot was read back and the
caller said yes in a later turn.

## 2. Architecture

```
in-car call -> Genesys Cloud (BYOC trunk, UUI) -> LiveKit sip -> LiveKit agent (STT, TTS)
                                                                 |  HTTPS bearer
                                                                 v
                                                   n8n  AR-01 start / AR-02 turn / AR-03 end / AR-04 transfer-result / AR-05 health+lookup
                                                   |         |            |              |
                                                   v         v            v              v
                                             AR-10 CRM   AR-11/12/13 DSD  AR-15 Sirius  AR-16 comms      (lab: AR-20..23 mocks)
                                                   Data Tables ar_sessions, ar_events, ar_markets, ar_codes; Postgres ar_chat_memory
Handover: n8n builds Refer-To with hex UUI -> LiveKit sends REFER -> Genesys routes to the queue, agent desktop reads the UUI
```

Environments: lab (n8n 2.35.5, localhost, LM Studio model) now; MB dev and
MB prod instances through the customer's promotion path. Queue mode on MB
with at least two workers; a turn is one execution, so concurrency equals
simultaneous turns, not calls.

External dependencies: Genesys Cloud trunk and queue, Nexus gateway to the
Model Garden model, CRM lookup, DSD Book through the Sirius API, Salesforce
case REST, SMS and e-mail service, STT and TTS providers for PL, ES, DE, EN.

## 3. Credentials and secrets

Table in `07-runbook.md`. Nothing is hard-coded; the lab bearer lives in the
gitignored `.env.lab`, the lab API key in Infisical. Two lab-only values
were exposed in a session transcript on 2026-10-19 (the lab gateway bearer
and two pg-lab role passwords); both are being rotated and neither reaches
MB.

## 4. Promotion

Steps in `07-runbook.md`. The gates (validate, lint with the lab host
allowances, layout) and `drift-check` before `export-all` apply as on the
MB-DEMO family. Mocks are never promoted.

## 5. Runbook

`07-runbook.md`.

## 6. Evals

`scripts/ar-golden-run.py` runs five scripted calls through the API and
scores final action, booking, case status, handoff reason, transfer retry,
false-confirmation hits, handoff context and memory purge. 5/5 on the lab
model on 2026-10-19. `05-test-plan.md` maps the set to the Genesys
acceptance tests and the nine race criteria and adds the red-team and load
scripts for W6. A failure prints the scenario, the transcript and the
failed checks; the guards column shows which deterministic rule fired.

## 7. Known limits and deferred items

| Item | Owner | When |
|---|---|---|
| Lab model (qwen3-coder-30b) has weak Polish grammar; the MB model replaces it | Frosty | W3 |
| Mocks stand in for CRM, DSD, Sirius, comms; real endpoints need test accounts | MB system owners | W5 |
| Return UUI in hex carries 64 bytes: caseId is dropped unless ASCII encoding is agreed with Genesys | Vivek Saraswat | W3 |
| Voice gateway not yet built; decision LiveKit, fallback AudioCodes | Frosty | W3 |
| Error workflow needs the UI assignment on each workflow and a Teams step on MB | Frosty | W5 |
| RESCHEDULE and CANCEL intents hand over to an agent in this build | Frosty | after W4 if in scope |
| Three recordings in the shared folder were empty stubs; Nexus and DSD-book meeting content still needed | Frosty to ask Daniele | W2 |
| Lab exposure rotation (bearer, pg-lab roles) | Frosty | W2 |

## 8. Escalation

See `07-runbook.md`. Attach `correlationId`, `sessionId`, the `ar_events`
rows and the UTC timestamp.
