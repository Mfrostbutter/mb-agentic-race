# Source notes: what the race documents say

Distilled 2026-10-19 from the files in `00-sources-read.md`. Every fact carries
its source. Where the extraction was ambiguous the line says so.

## The race in one paragraph

Mercedes-Benz Sales & Customer Experience IT runs an "Agentic Race": four
teams build the same journey on four platforms (QUANT.AI/TAOS, Dify, n8n
Enterprise, SIRIUS internal) in eight weeks, scored on pre-prod with live data
against nine weighted criteria, with a live demo to a jury in week 4 and a
final jury vote on 2026-12-04. The journey: a customer in the car presses the
appointment-booking option in the existing Genesys Cloud IVR, is routed to the
team's AI voice agent over a dedicated SIP trunk, is identified by VIN and
MSISDN, picks a dealer and slot in DSD Book, the appointment is booked and
confirmed, a Sirius (Salesforce) case records it, and at every step the caller
can be handed back to a human agent in Genesys Cloud with full context.
(Kick-off deck pp. 2-3, 6; Confluence "Agentic Race" page.)

## Timeline (kick-off deck p. 6, p. 13)

| Week | From | Milestone |
|---|---|---|
| W1 | 2026-10-12 | Grid line-up and kick-off (14.10). Setup shared by 16.10. |
| W2-3 | 2026-10-19 | Data and system integration: access, Model Garden, CRM, vehicle data, DSD Book, Genesys. Checkpoint 1 (criterion 1). Hours logged weekly from 19.10. |
| W4 | 2026-11-02 | MVP end-to-end happy path, IVR entry to booked appointment, handover available. **Live demo to jury (02.11 to 06.11).** |
| W5 | 2026-11-09 | Agentic quality and process: edge cases, multilingual, handover from every step, as-is data contract, test runs. Checkpoint 2 (criteria 2 and 3). |
| W6 | 2026-11-16 | Hardening: security review, red teaming, load test, monitoring, **change task**, quality management. Production approval. |
| W7 | 2026-11-23 | Pre-prod test with live data, two pilot markets. All four platforms live. |
| W8 | 2026-11-30 | Scorecard, TCO, lessons learned, **pitch day, final jury voting 04.12**. |

Approvals: market approvals take 3 to 4 weeks, filing by 2026-10-26. Up to 28
approval items, four security reviews in W6. "A platform that misses one has
no score." (p. 13)

## Scoring (kick-off deck p. 8, "initial draft to be discussed")

| # | Criterion | Weight | Measured by |
|---|---|---|---|
| 1 | Data and system integration | 25% | Existing connectors and data points, no source-system changes, reusability. Hours per system, time to usable data, number of reusable connectors, error rate. |
| 2 | Process adherence and data contract | 10% | Step-sequence conformance, % schema-valid outputs, booking data accuracy, rule violations per 100 calls. |
| 3 | Agentic quality | 25% (inferred: the weights listed are 25, 10, 25, 10, 5, 5, 5, 5, 10 for 100; the deck says four core criteria carry 60%) | Task completion, intent and slot accuracy, groundedness and false-confirmation rate, latency. |
| 4 | Security, compliance, governance | 10% | GDPR, consent, roles, audit trail, MASSP4AI, EU AI Act, hosting. Essentials check plus open findings. |
| 5 | Operations and observability | 5% | Monitoring, tracing, versioning, staging, scaling. MTTR, load test, deployment time. |
| 6 | Human-in-the-loop and handover | 5% | Handover to Genesys from every step with context. Handover success per step, context completeness, agent CSAT. |
| 7 | Time-to-change and usability | 5% | Adding a step or market, business vs dev. Hours for the defined change task in W6. |
| 8 | Total cost of ownership | 5% | Licence, infra, model consumption, operations, skills. € per booked appointment, annual TCO. |
| 9 | Strategic fit, interoperability, transferability | 10% | Roadmap, lock-in, A2A/MCP, non-customer use cases, multi-market, China. Jury assessment. |

The weight column in the PDF extraction reads "25%, 10%, 10%, 10%, 5%, 5%,
5%, 5%" with one value lost to layout. Treat criterion 3 and 9 weights as
unconfirmed until the criteria catalogue is published. KO (knock-out)
criteria exist and are "shared" but not listed in the deck.

Scoring basis: about 200 golden test calls on pre-prod with live data
(p. 13). Platforms that pass the W6 gate go live in Poland and Spain, about
one week of real calls before the vote, reported next to the score.

## Rules that constrain the design

- Same use case, same data and existing connectors, same model source
  (approved models from the MB Model Garden / AI Governance), same time frame.
  (p. 2)
- Source systems stay unchanged. New process flows are welcome as long as the
  as-is data contract of every source system is respected. (p. 3)
- "The rule that everybody has to use the same and one only LLM" is no longer
  valid: different LLMs for different steps are allowed. (Genesys transcript,
  Biniam Ghebrai question, Daniele Zanchetta confirmed, 24:25)
- Document the model choice; same golden test set; model costs go into TCO.
  (p. 10, risk 4)
- Vendor support hours are capped and logged. (risk 8) Hours logged weekly
  from 19.10. (p. 4)
- No call recording and no transcript retention on the AI platform. Genesys
  keeps the recording. The platform keeps the correlation ID for tracing.
  "Any conversation or call or transcripts has to be purged and observably
  maintained within the Sirius platform." (transcript 21:48 and 22:13)
- Consent and AI disclosure per market via MB Legal. A false confirmation
  "puts a real customer at the dealer without a booking." (p. 13)
- Handover to Genesys at every step, with full context (customer request,
  low confidence, error, policy trigger). (p. 3)

## Journey steps (kick-off deck p. 3)

1. Call entry and voice dialogue: voice/intake agent, full AI voice capability
   via Model Garden. IVR/Genesys Cloud telephony.
2. Identification: customer, vehicle/VIN, consent. CRM / Customer 360, vehicle
   data.
3. Find dealer and slot: dealer/slot finder agent. DSD Book.
4. Book appointment: booking, replacement mobility, notes. DSD Book.
5. Confirmation: SMS/email, CRM update. Communication services, CRM.
6. Optional: check-out call. Automated AI voice bot call to close the loop,
   voice of the customer.

Orchestration layer: orchestrator, guardrails, logging; approved models via
the MB Model Garden; MB AI Ecosystem, Agent Garden.

## The as-is process (Signavio "Appointment Booking_Communication Model")

Flow labels only survive extraction; the order below is reconstructed from
them and from the Confluence page.

- Initiator: IVR selection, in-car call, in-app call. Lands at MB CCC first
  level / front desk agent.
- Agent checks account information, classifies the case (appointment
  booking, topic, reason), validates the request, checks the knowledge
  database for the market, checks the preferred retailer and whether the
  market is DSD-enabled ("supported country list").
- Branches: cancel or reschedule (only the timeslot can be changed; otherwise
  cancel and rebook); new booking.
- Preferred retailer supported by Sirius? If not DSD-enabled: retailer
  callback (dispatch case to retailer, case left open in "Delegated", await
  retailer response, retailer must confirm the booked day and time, mileage
  and unit, type of service, customer preference), or assisted self-service
  (send link via SMS or email, customer books on the MB website).
- DSD-enabled markets: agent books on behalf in DSDbook (Online Appointment
  Booking 6.11.0): select booking tab, confirm correct customer and vehicle
  details, preferred dealer correct and populated (GIS launched in the CS/CMS
  tab to select the dealer), select services and mobility options, select
  timeslot, finalize booking, booking document and confirmation code shown,
  email confirmation sent from the OAB tool. Confirmation code goes into the
  case solution field, mandatory fields completed, case closed "Resolved".
- Timeslot not available: change timeslot or restart, choose another
  retailer, or offer retailer callback.
- Risk noted in the diagram: "the assigned dealer in the customer account is
  NOT always the preferred retailer". Retailer callback only when the
  customer specifically requests it. Case entitlements as for other CS
  requests, with reminders and escalations.
- Markets named: US and Canada, Malta, Japan, Thailand have "always retailer
  booking" or callback variants. Pilot markets for the race are Poland and
  Spain (Italy ruled out, works council; Spain flagged as not recommended on
  AI-regulation grounds but still listed as pilot, kick-off risk 6).

## Genesys Cloud integration contract (Confluence "SIP integration requirements" and transcript)

Genesys Audio Connector is ruled out "due to some limitations in our
environment" (Vivek Saraswat, 2:57). The only integration is a dedicated SIP
trunk per provider; one pattern for every team.

Inbound (Genesys to AI platform): INVITE over the trunk, context in SIP UUI
set by the Architect flow with "Set UUI Data". Fields: `version` (mandatory),
`correlationId` (mandatory, stays unchanged for the life of the interaction,
expected to be the Genesys conversation ID), `vin` (when included), `msisdn`
(when included, "use the supplied MSISDN as the car number; the SIP caller
number must not be assumed to contain the same value"), `language` (if
included). Field names, separators, max lengths and missing-value handling
are agreed before implementation. Genesys recommends 128 characters or fewer
for the encoded payload; larger context goes through a separate API exchange
keyed by correlation or session ID.

Outbound (handoff): SIP REFER over the established dialog to the assigned
Genesys return number, with the return UUI in the Refer-To URI (a plain UUI
header on the REFER "does not meet the documented Genesys format"). Fields:
`version`, `correlationId` (exact value received), `sessionId` (the AI
session), `intent` (agreed code; `unknown` when not identified, never
invented), `handoffReason` (agreed code), `language` (if required). The
platform must handle NOTIFY transfer results: REFER acceptance is not
completion. Avoid transfer loops. Case ID created by the bot must reach
Genesys so the agent sees the same case rather than a new one (Marta, 13:17;
Dawid: "correlation ID that can be our case ID", 14:12; Vivek expects the
Genesys conversation ID, 14:36. Unresolved: whether the case ID travels as
correlationId or as a separate field. Design for a separate `caseId` field
and keep correlationId as received.)

Numbers: each team gets two internal dummy numbers on the trunk, one that
Genesys dials to reach the bot (example 111101) and one the bot REFERs to for
the agent handoff. No PSTN cost. (Dawid, 22:51)

Failure handling to agree: platform cannot accept a call, setup timeout, no
audio, backend failure mid-conversation, Genesys rejects REFER, target
unreachable, caller disconnects. Genesys handles fallbacks where it controls
the step; the platform handles failures while it owns the session or the
REFER. Troubleshooting records must match correlation ID, AI session ID,
event timestamp (UTC) and transfer result; SIP traces on request.

Joint acceptance tests: call connection, two-way audio, car context received,
correlation retained, REFER reaches the return flow, return context correct,
unknown intent routes as agreed, failed transfer behaviour, caller disconnect
releases resources, capacity at agreed concurrency and setup rate.

Prerequisite: the "AI provider SIP trunk information form" must be filled
before the Genesys team configures the trunk and assigns the numbers. Vivek:
"prerequisite is, you need to fill us that questionnaire" (26:21).
Documentation was sent after the call; numbers follow configuration. Teams
can test independently by procuring their own SIP trunk and number and
dialling it with the same UUI pattern (Vivek, 20:26). No DTMF or IVR testing
is needed or offered: the IVR already has the appointment option and routes
by number.

Authentication on the trunk: the form asks whether digest auth or
registration is required; Genesys allowlists the domain first.

## Other system interfaces (transcript)

- Salesforce (Sirius case creation): standard Salesforce REST API, "nothing
  special in Sirius", different endpoints. No dedicated session; field list
  and endpoint documentation to be shared in the race folder. (Marta, 27:03)
- GIS (dealer selection) and the DSD Book API "that we have in Sirius":
  documentation to be shared. (Marta, 27:48)
- DSD connector decision pending: MCP, direct DSD API, or the encapsulated
  Sirius API. Race Control's stated preference is the Sirius DSD booking API
  for business coverage, observability, traceability, monitoring. Decision
  meeting adjacent to the Wednesday weekly (2026-10-21). (Daniele, 28:20)
- Nexus and MB Model Garden: a meeting on 2026-10-15 covered it; no transcript
  in the folder. From the lighthouse prep (2026-10-08): on the MB platform AI
  is only reachable through a configured gateway credential (Nexus,
  Anthropic).

## People named

Race Control and MB: Daniele Zanchetta (race lead, 122), Christian
Brueggemann (Eraneos, race channel and folder owner), Marta Dias de Assuncao
Domingues (Ebicus, CAC/Sirius), Vivek Saraswat (Genesys service architect,
Maastricht), Dawid Kacprzak (Orange, Genesys telephony), Lukas, JO Ruysen.
Jury: Alexandra Strassburger (sponsor, CIO Sales and CX), Axel Harries
(sponsor, GCSP), Daniel Eitler (AI platform owner), Matthias Graf (security
and data protection), Katja Bott (head of Customer Care Center), market reps
for Poland and Spain (tbd). SteCo adds Susann Moeschlin, Eva Wiese, Thorsten
Lamaak.

Teams (deck p. 4, column alignment lost in extraction, treat as
provisional): QUANT.AI with Kevin Cavlek as tech lead and Artisans Cloud
external; Dify with Snehaa Raveendhran (MB.io); n8n team with Biniam Ghebrai
(team lead) and the MBRDI team, "n8n Enterprise hosted at MB", prior work
"none known yet", commitment pending on names, FTE and start date; SIRIUS
internal (CAC AI foundation, SaaS) admitted as the fourth team 2026-10-12.
On the Genesys call, Biniam Ghebrai (002) asked the LLM and Sirius questions,
Preeth Nair (623) and Gaurav Makhecha (623, Artisans) asked about test access
and documentation, PS3252 (002-Extern) asked about simulating the first leg.
Which of 623 and 002 is the n8n team is not stated. Confirm with Frosty.

n8n hosting: "n8n Enterprise hosted at MB"; "MB production hosting needed for
every platform, in place today for n8n only" (p. 13). The platform team's
constraints known from the lighthouse work apply: no native AI nodes without a
gateway credential, chat trigger auth not enabled, PDF via a Gotenberg custom
node, network shares unreachable.

## Staffing recommendation (deck pp. 11-12)

Per platform team 5 FTE: tech lead / agent architect 1.0, agent/prompt
engineer 2.0, integration engineer 1.0, conversation designer (voice) 0.5,
platform expert (vendor) 0.5 with hours logged. The vendor slot is where n8n
FDE time sits, and it is capped.

## What is still missing from the folder

- Nexus / Model Garden session content (2026-10-15): which voice-capable
  models (STT, TTS, realtime) are approved, how the gateway is called, quotas.
- System Readiness / DSD-book session content (2026-10-16): the DSD Book API
  or MCP surface, environments, test data.
- Salesforce field list and endpoints for the Sirius case; GIS documentation.
- Criteria catalogue with final weights and KO criteria; the signed race
  rules; the "model and voice rule in writing".
- Golden test set and the W6 change task definition.
- The filled Genesys form values for the n8n team (endpoint, IPs, codecs).
