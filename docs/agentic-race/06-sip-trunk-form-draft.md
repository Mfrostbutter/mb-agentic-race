# Genesys "AI provider SIP trunk information form": draft answers

Draft 2026-10-19 for the n8n team to complete and return to Vivek Saraswat
(Genesys) and Dawid Kacprzak (Orange). The trunk is configured only after
this form is complete, so it is on the W2 critical path. Values marked
`<MB>` come from the MB network team once the LiveKit SIP endpoint exists
(`03-voice-layer.md`). Nothing secret goes in this document; credentials go
through the agreed secure channel.

## Provider information

| Field | Answer |
|---|---|
| Company name | Mercedes-Benz, n8n race team (MBRDI), platform vendor n8n GmbH |
| AI platform or service name | n8n Agentic Appointment Booking (LiveKit SIP gateway + n8n Enterprise) |
| Environment | test (race pre-prod); production follows the W6 gate |
| Technical contact | `<MB>` tech lead name and e-mail |
| SIP/network support contact | `<MB>` network team on-call |
| Support hours and escalation | `<MB>` |
| Preferred testing dates and timezone | W2 to W3 (2026-10-26 to 2026-11-06), CET, mornings |

## SIP trunk configuration

| Area | Information required | Answer |
|---|---|---|
| SIP endpoint | Primary SIP server or SBC FQDN | `<MB>` e.g. `sip-ar.<mb-domain>` (LiveKit SIP behind an Elastic IP or L4 NLB) |
| SIP endpoint | Primary public signaling IP | `<MB>` Elastic IP |
| SIP endpoint | Backup FQDN and IP | `<MB>` second AZ, or "Not used" for the race |
| SIP endpoint | Destination SIP port | 5061 (TLS); 5060 only if TLS is not possible on the Genesys side |
| SIP endpoint | Transports | TLS preferred; TCP, UDP supported |
| SIP endpoint | DNS requirements | A record only; no SRV required |
| Network access | Source IPs that may send SIP to Genesys | `<MB>` the same Elastic IP(s) |
| Network access | Public media IPs | `<MB>` same host(s); LiveKit uses `use_external_ip` / `media_nat_1_to_1_ip` |
| Network access | RTP/SRTP UDP port range | 10000 to 20000 (LiveKit default; can be narrowed on request) |
| Network access | Firewall, NAT, allow-listing | Inbound allow-list: Genesys BYOC region egress CIDR (Frankfurt DCVP 52.129.106.220/30 or the region MB uses) plus the Genesys media pool; outbound to Genesys ingress 52.129.106.228 to .230 (region specific) |
| Call addressing | Destination SIP URI format | `sip:<assigned-user-part>@sip-ar.<mb-domain>` |
| Call addressing | Dialed number / user part selecting the AI application | the dummy internal number Genesys assigns (e.g. 111101); LiveKit dispatch rule keys on it |
| Call addressing | Required domain in Request-URI and To | `sip-ar.<mb-domain>` |
| Call addressing | SIP proxy, if different | Not used |
| Call addressing | From / caller ID / P-Asserted-Identity | Any; the platform does not use the SIP caller number as the car number, it uses the MSISDN from UUI (per the Genesys requirement) |
| Call addressing | Additional mandatory headers | `User-To-User` with the agreed header type and encoding; no others |
| Authentication | Required for calls from Genesys? | IP allow-list only; SIP digest optional if Genesys prefers it (LiveKit supports inbound auth per trunk) |
| Authentication | Digest realm and username | `<MB>` if enabled; password via the secure channel |
| Authentication | Registration required? | No |
| TLS | Hostname, port, versions | `sip-ar.<mb-domain>`:5061, TLS 1.2 and 1.3, public CA certificate |
| Media | Codecs in preference order | G.711 A-law (PCMA), G.711 µ-law (PCMU), Opus; G.722 if the Genesys trunk offers it |
| Media | Packetization | 20 ms |
| Media | Encryption | SRTP preferred (AES_CM_128_HMAC_SHA1_80); RTP accepted on the test trunk |
| Media | DTMF | RFC 2833 / RFC 4733; not used by the dialogue |
| Capacity | Max concurrent calls | 20 for the race test trunk; 50 target for production |
| Capacity | Max new calls per second | 2 |
| Capacity | Call setup timeout, max duration | 10 s setup; max duration 20 minutes, then the platform hands off |
| Capacity | Response when capacity is reached | SIP 503 Service Unavailable |
| Resilience | Primary and backup use | Active-standby if a backup is provided; otherwise Genesys fallback flow on 503 or timeout |
| Resilience | Maintenance notification | `<MB>` channel, 48 hours notice, maintenance windows outside market hours |

## SIP REFER and UUI

| Information required | Answer |
|---|---|
| REFER over the established dialog | Yes (LiveKit `TransferSIPParticipant`, in-dialog REFER) |
| REFER acceptance, NOTIFY, failed transfers | The platform waits for the NOTIFY result; 2xx completes; a terminal non-2xx or a terminated subscription is a failure. One retry after 3 s, then the caller is told a colleague will call back and the call ends; the case is set to Delegated with the reason. No transfer loops. |
| UUI in the Refer-To URI | Yes: `<sip:<return-address>?User-to-User=<hex>%3Bpd%3D00%3Bencoding%3Dhex>` (or the ASCII form if agreed) |
| Supported UUI header types | `User-To-User` with PD attribute (preferred), `User-To-User` with PD prefix, `x-User-to-User` |
| Supported encodings | hex and ASCII |
| Protocol discriminator | `pd=00` proposed; any two-digit value Genesys prefers |
| Max UUI payload | 128 characters after encoding; the platform builds payloads of at most 64 bytes before hex encoding |
| Reads VIN and MSISDN from inbound UUI | Yes. Proposed inbound format: `1;<correlationId>;<vin>;<msisdn>;<language>`, positional, empty fields allowed |
| Returns the original correlation ID unchanged | Yes; it is also the key of every log line |
| AI session ID format | 8 characters, upper-case letters and digits (no 0, 1, I, O), e.g. `S7K2M9Q4` |
| Intent and handoff-reason codes | Intent B/R/C/O/U (book, reschedule, cancel, other, unknown); reason CR, SA, NU, BU, IF, NS, PO, PE; full table in `02-technical-spec.md` 5.2 and the `ar_codes` table. Genesys values in the `genesysValue` column are a proposal. |
| Sanitized inbound INVITE example | to be captured on the first test call (sngrep on the gateway) |
| Sanitized REFER example | `REFER sip:... SIP/2.0` with `Refer-To: <sip:111102@<genesys-host>?User-to-User=313B37663063...%3Bpd%3D00%3Bencoding%3Dhex>`; full trace from the first test call |

## Decisions we need from the Genesys team

1. Header type and encoding on the trunk, and whether the return UUI may be
   ASCII (lets the case ID travel in the UUI).
2. Confirmation that "Take Back and Transfer" (inbound REFER) is enabled on
   our trunk and whether "Release Link Transfer" is off.
3. The Genesys region and therefore the signalling and media CIDRs to allow.
4. Whether the correlation ID is the Genesys conversation ID (36 characters)
   so we can fix the payload budget.
5. Agreement that the case ID reaches the agent desktop through the lookup
   API or Salesforce when it does not fit the UUI.
