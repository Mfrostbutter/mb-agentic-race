# Race plan, W2 to W8

Dates from the race timeline (W1 starts 2026-10-12, finish 2026-12-04).
Owners marked `?` are proposed, not confirmed.

| Week | Dates | Deliverable | Owner |
|---|---|---|---|
| W2 | 2026-10-19 to 10-25 | Reference build on the lab (this repo), spec v0.1, SIP trunk form draft, open questions sent. File approvals and the trunk form by 2026-10-26 | Frosty; form sign-off n8n team lead ? |
| W3 | 2026-10-26 to 11-01 | Voice gateway up (LiveKit sip + agents, AudioCodes fallback), STT and TTS chosen per language, Nexus credential for the Model Garden model, first end-to-end call over the Genesys test trunk, return UUI seen on the agent desktop | Frosty (gateway, n8n), Vivek Saraswat (Genesys), Dawid Kacprzak (Orange) |
| W3 | same | Prompt and guards re-tuned on the real model; golden set 5/5 twice in a row | Frosty |
| W4 | 2026-11-02 to 11-06 | Jury demo: one scripted PL call, one ES call with handoff, one probe. Demo pack: architecture page, live `ar_events` view, the criteria map | Frosty, n8n team lead ? |
| W5 | 2026-11-09 to 11-15 | Real system calls behind AR-10 to AR-16 (CRM lookup, DSD dealers/slots/book, Sirius case, comms), DSD connector decision applied (pending 2026-10-21), error workflow with Teams alert | Frosty; MB system owners for endpoints and test accounts |
| W6 | 2026-11-16 to 11-22 | Load test (20 concurrent calls, p95 turn under 2.5 s on the MB model), red-team session with the jury script, retention and privacy review | Frosty, Christian Brueggemann ? (review), Marta Dias ? |
| W7 | 2026-11-23 to 11-29 | Hardening: retries, timeouts, queue mode sizing, dashboards, runbook and handoff finalised, second golden run on MB | Frosty |
| W8 | 2026-11-30 to 12-04 | Final presentation and scored run | Frosty, n8n team lead ? |

Dependencies that block the plan if missed:

- Trunk form and approvals filed by 2026-10-26 (W2).
- DSD connector decision 2026-10-21; until then AR-21 stands in.
- Nexus access to the Model Garden model before W3 tuning.
- A Genesys test queue with "Take Back and Transfer" enabled before W3.
- Test customer records in CRM for the four markets before W5.

Open items: `08-open-questions.md`.
