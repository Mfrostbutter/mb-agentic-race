# Open questions

As of 2026-10-19. Owner is who must answer; "us" is the n8n race team.

| # | Question | Why it matters | Owner | Needed by |
|---|---|---|---|---|
| 1 | Which STT, TTS and LLM models in the MB Model Garden are approved for voice, and how is Nexus called (base URL, auth, quotas)? Content of the 2026-10-15 session. | Gateway provider plumbing; criterion 3 latency; TCO | Daniel Eitler's team, Biniam | W2 |
| 2 | DSD connector decision: Sirius DSD booking API, direct DSD API, or MCP (meeting adjacent to the 2026-10-21 weekly). | AR-11 to AR-13 are built against a mock of the Sirius API shape | Race Control, Marta | 2026-10-21 |
| 3 | Salesforce case field list and endpoints for Sirius; GIS documentation. | AR-15 mapping; dealer lookup | Marta | W2 |
| 4 | Genesys trunk settings: header type, encoding, pd, Take Back and Transfer, region CIDRs, correlation ID = conversation ID? | UUI format in AR-14 and the gateway; form completion | Vivek, Dawid | W2 |
| 5 | Can MB's AWS account host LiveKit SIP with a public Elastic IP and UDP media range? Who owns the SIP FQDN and certificate? | The whole voice layer | MB network team, Biniam | W2 |
| 6 | Consent and AI disclosure wording per market (PL, ES) from MB Legal. | Placeholder texts in `ar_markets`; EU AI Act | MB Legal via Race Control | W3 |
| 7 | Final criteria catalogue, weights, KO criteria, the golden test set and the W6 change task definition. | Test plan and what we optimise | Race Control (evaluation lead) | W2 |
| 8 | Who in the n8n team books the W6 security review slot and owns the data classification, PII document and MASSP4AI filing (deadline 2026-10-26 for market approvals)? | A platform that misses one approval has no score | Biniam | 2026-10-23 |
| 9 | Which team identifiers (002, 623) are the n8n team on the race calls, and who is the tech lead? | Contacts in this folder | Frosty, Biniam | now |
| 10 | n8n Enterprise at MB: int and prod instances for the race, queue mode, log streaming target, OpenTelemetry on or off, Data Tables and Postgres availability for session and memory storage. | Sections 8 and 9 of the spec | MB platform team (Rainer) | W2 |
| 11 | Is the check-out call (journey step 6) scored in the race or optional? Does MB have an outbound calling path through Genesys or must the gateway originate? | Scope of AR-40 | Race Control | W3 |
| 12 | Vendor hours cap for the race: what is the n8n FDE allowance per week and how is it logged? | Frosty's involvement | Race Control, Malaika | W2 |
| 13 | Test access: can a Genesys test number be dialled by the team to trigger the IVR path, or do we self-test with our own SIP trunk until the dummy numbers exist? | W2 to W3 testing | Dawid | W2 |
| 14 | Does the Genesys agent desktop accept a data action to call the handoff lookup API, or should the context land in Salesforce only? | Section 6.4 | Vivek, Marta | W3 |
