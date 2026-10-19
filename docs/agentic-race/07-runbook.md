# Agentic Race runbook

For whoever operates the n8n entry during the race and, after it, the MB
operations team. Lab values are stated as lab; MB values are filled at
promotion (`<MB>`).

## Health

- One check: `GET <base>/webhook/ar/v1/health` with the gateway bearer
  returns 200 and `{"ok": true, ...}`. The voice gateway probes it every
  30 s; three misses take the trunk out of rotation on the gateway side.
- Executions: n8n UI, Executions, filter tag `agentic-race`. API workflows
  keep success execution data off (`saveDataSuccessExecution: none`), so a
  successful call leaves rows in `ar_events`, not an execution. Failures
  keep their execution data.
- Per-call trace: Data Table `ar_events` filtered by `correlationId` (the
  Genesys conversation id). Every workflow logs start, tool calls, guards,
  handoff and end with latency.
- Metrics: `ar_events` latency column per event; the golden runner prints
  p50 and p95 per run. Lab: `data/agentic-race/runs/`.

## Workflows

| Workflow | Trigger | What it does | Owner |
|---|---|---|---|
| AR-01 Session start | `POST ar/v1/session/start` | Identify, case, session, greeting | n8n team |
| AR-02 Dialogue turn | `POST ar/v1/session/turn` | One turn of the dialogue, tools, guards | n8n team |
| AR-03 Session end | `POST ar/v1/session/end` | Case outcome, confirmation, memory purge | n8n team |
| AR-04 Transfer result | `POST ar/v1/session/transfer-result` | REFER result, retry once | n8n team |
| AR-05 Health and handoff lookup | `GET ar/v1/health`, `GET ar/v1/handoff` | Probe; agent desktop context | n8n team |
| AR-10..17 | Execute Workflow | Identify, tools, handoff, case, confirmation, log | n8n team |
| AR-20..23 | `ar-mock/*` | Lab stand-ins for CRM, DSD, Sirius, comms. Not deployed on MB | n8n team |
| AR-30 Error workflow | Error Trigger | Writes the failure to `ar_events` | n8n team |
| AR-90 Seed configuration | `POST ar/v1/admin/seed` | Markets and codes, run once | n8n team |

Full catalogue: `04-workflow-catalogue.md`.

## Most likely failures

1. **Gateway gets 401 or 403 from every endpoint.** Cause: bearer mismatch
   between the gateway config and the n8n credential `AR gateway bearer`.
   Fix: compare the secret names in Infisical (`N8N_LAB_AR_GATEWAY_TOKEN`
   on the lab, `<MB>` on MB), update the credential, no redeploy needed.
2. **Every turn ends in handoff with `PLATFORM_ERROR` or `agent_error`.**
   Cause: the model endpoint is down or the credential expired (LM Studio
   on the lab, Model Garden through Nexus on MB). Check `ar_events` for
   `errorCode agent_error`, then the model node's execution. Fix: restore
   the endpoint; calls already in flight were handed to an agent, nothing
   is lost.
3. **Caller is read the handover line but lands back in the queue, or
   the agent desktop shows no context.** Cause: Genesys did not accept
   the REFER (NOTIFY not 200), or the return UUI was dropped. Check
   `ar_sessions.handoffResult` for the session, and the referTo string in
   `ar_events` event `handoff_built`. The usual causes: "Take Back and
   Transfer" not enabled on the trunk, hex payload over 64 bytes
   (`truncated: true` on the event), wrong return number in
   `ar_markets.returnNumber`.
4. **Assistant repeats the same question.** Cause: the model ignored the
   state goal (seen with the lab model). The deterministic gates in AR-02
   (`gate_advance`, `caller_request_rule`) cover identity, consent, dealer
   and "give me a person"; anything else is a prompt change in
   `build/gen.py`, redeploy AR-02, rerun the golden set.
5. **Deploy says "references workflow ... which is not published".** Cause:
   a caller was published before its sub-workflow. Fix: rerun
   `build/deploy.py` without `--only`; it orders the two passes.
6. **`BookAppointment` keeps answering `not_confirmed`.** Working as
   designed: the slot was not read back in a CONFIRM turn before the yes.
   The model recovers by reading it back. If it persists, the session row's
   `slotId` is empty; check the `pending_slot_set` guard in `ar_events`.

## Credentials and secrets

| Credential | Type | Value source | Rotation owner |
|---|---|---|---|
| AR gateway bearer | httpHeaderAuth (Authorization: Bearer) | Lab: `.env.lab` `N8N_LAB_AR_GATEWAY_TOKEN`; MB: `<MB secret manager>` | n8n team (lab), MB platform team (MB) |
| Model endpoint | openAiApi (LM Studio on lab, Nexus gateway on MB) | Lab: none; MB: Nexus client credential `<MB>` | MB AI platform |
| Chat memory Postgres | postgres | Lab: pg-lab `ar_lab`; MB: `<MB>` | MB platform team |
| CRM, DSD, Sirius, comms | per system, one HTTP node each in AR-10, 11, 12, 13, 15, 16 | `<MB>` | MB system owners |
| n8n REST API key | header | Infisical `N8N_LOCAL_WORKFLOW_LAB_API_KEY` (lab) | n8n team |

Rules: values only through the n8n credential system, never a Set node or a
text field. API workflows keep success execution data off so bearer headers
do not land in the execution store. No call audio, no transcript retention
on the n8n side: `ar_chat_memory` is purged at session end, `ar_events`
stores structured fields only.

## Promotion

1. `./scripts/drift-check.sh lab`, then `./scripts/export-all.sh` and a
   commit, so the repo and the lab agree.
2. Gates: `scripts/validate.sh`, `scripts/lint-workflow.sh --allow-host
   localhost --allow-host host.docker.internal`, `layout_check.py`.
3. `scripts/promote.py --from lab --to <env>` re-mints ids, repoints
   loopback URLs, strips credential ids. Replace the mock HTTP targets in
   AR-10 to AR-16 with the MB endpoints (one node each).
4. Deploy with `build/deploy.py` against the target (two passes). Run AR-90
   once with the market rows for that environment, including
   `returnNumber`.
5. UI steps the API cannot do: assign AR-30 as the error workflow on every
   AR-* workflow (Workflow Settings), confirm `saveDataSuccessExecution:
   none` on AR-01 to AR-05, set the caller policy on AR-10 to AR-17 to
   "same workspace".
6. Smoke: health probe, then one scripted call per market with the golden
   runner pointed at the target (`N8N_LAB_BASE` and token in that
   environment's `.env`).
7. Only then point the voice gateway at the new base URL.

## Escalation

- n8n side: Frosty (build owner), then the n8n team lead for the race.
- Genesys and trunk: Vivek Saraswat (Genesys), Dawid Kacprzak (Orange).
- Race organisation: Daniele Zanchetta.
- Include: `correlationId`, `sessionId`, the `ar_events` rows for the
  session, execution id if a failure kept one, timestamp in UTC, what was
  expected.
