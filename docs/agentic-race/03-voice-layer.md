# Voice layer: what sits between the Genesys SIP trunk and n8n

Researched 2026-10-19 (web sources read that day; versions as published). n8n
has no telephony or realtime speech capability in 2.x (release notes through
2.44, 2026-10-13: the only audio features are batch transcription and TTS
nodes). The race therefore needs a voice gateway that terminates the trunk,
reads the UUI, runs speech in and out, sends the REFER, and calls n8n for
every decision. This document picks it.

## Decision

**Primary: LiveKit, self-hosted** (livekit-server v1.13.10, livekit-sip
v1.20.0, livekit-agents 1.8.8, all Apache-2.0, all released the week of
2026-10-14). **Fallback: AudioCodes VoiceAI Connect Enterprise** with n8n
implementing the AC Bot API in HTTP mode. Both are kept in dev until the W2
trunk test (section 4) passes on one of them.

Why LiveKit: it is the only open-source stack found that does all four
Genesys requirements itself and lets every speech and language model be
pointed at MB-controlled endpoints.

| Requirement | LiveKit | Evidence |
|---|---|---|
| Terminate the SIP trunk inside MB's cloud | livekit-sip, separate service, Docker with host networking, Redis | docs.livekit.io/transport/self-hosting/sip-server/ |
| Read the inbound UUI | `include_headers: SIP_ALL_HEADERS` or `headers_to_attributes: {"User-to-User": "uui"}` maps INVITE headers to participant attributes the agent reads | livekit_sip.proto; pkg/sip/types.go; community thread 1421 (attribute race fixed after server v1.9.0, use `GetRemoteHeaders` RPC) |
| REFER with our own Refer-To | `TransferSIPParticipant(transfer_to, headers)` sends an in-dialog REFER; `transfer_to` becomes the Refer-To URI verbatim | docs.livekit.io/sip/transfer-cold/; pkg/sip/protocol.go `NewReferRequest` |
| NOTIFY outcome, not just acceptance | the call blocks until the REFER NOTIFY: 200 = success, terminal non-2xx or terminated subscription = error | pkg/sip/protocol.go `handleReferNotify`, `waitReferResult` |
| Model Garden only | `openai.LLM/STT/TTS(base_url=...)` and `with_azure(...)`; Kokoro and faster-whisper behind OpenAI-compatible servers; Azure Speech `speech_host`; custom `stt_node/llm_node/tts_node` | livekit-plugins-openai; docs.livekit.io/agents/build/nodes/ |
| No recording | nothing is stored unless an egress is configured | default behaviour |
| TLS and SRTP | 5061, `SIP_MEDIA_ENCRYPT_REQUIRE`, AES_CM_128_HMAC_SHA1_80 | docs.livekit.io/telephony/features/secure-trunking/ |

Why AudioCodes as fallback: purpose-built Genesys-to-bot bridge, documented
Genesys BYOC notes, `sipHeadersToBot` for the inbound UUI, `transferSipHeaders`
landing as Refer-To URI parameters, `transferStatus` callbacks, deployable in
the customer's AWS account. Cost: AudioCodes manages the instance even inside
MB's cloud, commercial licence, STT and TTS limited to its vendor list (so the
Model Garden rule is only met for the LLM), professional-services lead time.

Rejected: Pipecat (no self-hosted SIP; would ride on LiveKit anyway),
jambonz (good REFER and NOTIFY semantics, but licence key, usage telemetry,
Helm only for paid support), Vocode (dormant since 2024-11), Vapi, Retell,
Bland, ElevenLabs Agents (SaaS; ElevenLabs has the best Genesys UUI support
but fails self-hosting and Model Garden), Deepgram self-hosted agent (needs a
SIP bridge anyway and GPU nodes), Asterisk and FreeSWITCH (REFER and header
handling partial, agent code is all yours), drachtio (toolkit, not a product),
OpenSIPS AI Voice Connector (85 stars, no releases; worth a look in W3 only
because it speaks MCP natively).

## Genesys facts that bind the design

From help.genesys.cloud, read 2026-10-19:

- UUI follows RFC 7433. Trunk setting "UUI Passthrough" is off by default;
  header type is `x-UserToUser` (data only), `User-To-User` (first octet is
  the protocol discriminator and is stripped), or `User-To-User PD Attribute`
  (`pd=XX` parameter, nothing stripped). Encoding `hex` or `ascii`; without
  the parameter Genesys assumes hex. (uui-data-formats, external-trunk-settings)
- REFER format: `Refer-To: <sip:address@ip?User-to-User=XXhexdata%3Bencoding%3Dhex>`.
  A UUI header on the REFER itself is not read. Use `sip:` not `tel:` and keep
  the angle brackets (community threads 2020 to 2024).
- Inbound REFER from our side is only accepted when "Take Back and Transfer"
  is enabled on the trunk; one field report says "Release Link Transfer" must
  stay off on the inbound trunk for the UUI to survive. (external-trunk-settings)
- Size: Genesys allows 28 KB but recommends 128 characters or fewer for
  interoperability. Hex doubles the length, so **64 bytes of payload** in hex,
  128 in ASCII.
- UUI is not available on Genesys Cloud Voice numbers; it needs a BYOC trunk,
  which is what MB is building.
- Field reports on Genesys parsing the `%3Bencoding%3Dhex` tail inside
  Refer-To are split (2018 to 2024). The exact string must be tested on MB's
  trunk in W2 with both `pd` forms before anything else is built on it.

Consequence for the return UUI: the positional payload
`1;<correlationId>;<sessionId>;<I>;<RR>;<lang>` is 55 bytes with a UUID
correlation ID, which fits hex. The case ID (12 more bytes) only fits in
ASCII, so the Genesys agent desktop gets it from the handoff lookup API or
from Salesforce by correlation ID. AR-14 emits both encodings and refuses to
truncate the correlation ID.

## What the gateway must do (contract the agent code implements)

1. On INVITE: read `sip.h.user-to-user` (or the agreed header), decode,
   call `POST /ar/v1/session/start` with the raw and parsed UUI, speak the
   returned text.
2. Per final STT result: `POST /ar/v1/session/turn`, speak `speak`, act on
   `action`. Play a filler after 2.5 s without a response. Treat 12 s as a
   failure and REFER with `U;PE`.
3. On `handoff`: `TransferSIPParticipant(transfer_to=referTo)`; on success
   `POST session/transfer-result` with the NOTIFY status; on error do the
   same and follow the returned action (one retry, then speak and end).
4. On hangup or end: `POST session/end`.
5. Never write audio or transcript to disk. Log only correlationId,
   sessionId, event, UTC time, SIP status.

LiveKit specifics to verify in W2: the `?User-to-User=` parameter survives in
a `sip:` Refer-To (plausible from `NewReferRequest` taking a string; a LiveKit
field guide making the claim was 404 on 2026-10-19); the header-attribute
race (wait for `participant_attributes_changed` or call `GetRemoteHeaders`);
no official SIP Helm chart, so the Deployment uses hostNetwork and a narrowed
`rtp_port` range.

## What to ask MB's network and Genesys teams

- A public SIP endpoint in the MB AWS account (Elastic IP or L4 NLB) open to
  the Genesys BYOC region egress (Frankfurt DCVP egress 52.129.106.220/30,
  ingress 52.129.106.228 to .230; Ireland, London and Zurich equivalents on
  the same page) on 5061/TCP, plus the SRTP range from the Genesys media pool
  (not fixed IPs). Outbound to the Genesys ingress on the same ports.
- Trunk: BYOC Cloud, TLS, SRTP suite AES_CM_128_HMAC_SHA1_80, UUI
  Passthrough on with header type and encoding agreed, Take Back and
  Transfer on, Release Link Transfer decision, inbound identification by
  FQDN or TGRP (Dawid named both on 2026-10-19).
- A public-CA certificate for the SIP FQDN; NAT mapping if behind an NLB.
- Egress from the voice pods only to: the Nexus gateway, the STT and TTS
  endpoints, n8n, Redis.
- For the fallback: Mediant SBC placement and AudioCodes management access.

Sources: the agent's full report with every URL is kept in
`data/agentic-race/research/voice-layer-report-2026-10-19.md`.
