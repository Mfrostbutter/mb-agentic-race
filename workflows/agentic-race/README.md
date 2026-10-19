# Agentic Race workflows (AR-*)

Reference build for the Mercedes-Benz Agentic Race, n8n entry. Lab only,
synthetic data, mocks for every MB system. Spec and catalogue in
`../../docs/agentic-race/`. Verified at runtime on the lab 2026-10-19; results
in `../../data/agentic-race/runs/` (gitignored) and summarised in
`../../docs/agentic-race/05-test-plan.md`.

## Files

`ar-NN-*.json` are the workflows. `build/gen.py` generates them; `build/deploy.py`
deploys them to the lab in dependency order in two passes (the second pass
resolves sub-workflow ids). `lab-ids.json` holds credential, Data Table and
workflow ids for the lab. `../../scripts/ar-golden-run.py` drives scripted
calls through the API and scores them.

Edit the generator, not the JSON. Then:

```bash
cd workflows/agentic-race
infisical run --projectId 7fe3abe3-07d8-48cf-af75-14c038f94174 --env dev --silent -- python3 build/deploy.py
# or a subset after a small change
infisical run ... -- python3 build/deploy.py --only ar-02-dialogue-turn,ar-13-book-appointment
cd ../.. && python3 scripts/ar-golden-run.py
```

Gates before any commit: `scripts/validate.sh`, `scripts/lint-workflow.sh
--allow-host localhost --allow-host host.docker.internal` (lab loopback URLs
are rewritten at promotion), and
`~/.claude/skills/n8n-canvas-docs/scripts/layout_check.py`. All nineteen pass
all three as of 2026-10-19.

## Lab wiring

- n8n 2.35.5, container `n8n-lab`, `http://localhost:5678`, REST API key in
  Infisical (`N8N_LOCAL_WORKFLOW_LAB_API_KEY`, project n8n-FDE, env dev).
- Gateway bearer: credential `AR gateway bearer (lab)`; the token is in the
  engagement's gitignored `.env.lab` as `N8N_LAB_AR_GATEWAY_TOKEN`.
- Model: LM Studio on the host, `qwen/qwen3-coder-30b`, credential `LM Studio
  (lab)`, `responsesApiEnabled: false`. Stand-in for the Model Garden model;
  Polish grammar from this model is rough and is not a design property.
- Chat memory: Postgres `ar_lab` on `pg-lab`, table `ar_chat_memory`,
  credential `AR lab Postgres (ar_lab)`.
- Data Tables created over the API; ids in `lab-ids.json`. `createdAt` is a
  reserved column name, hence `bookedAt` and `openedAt` on the mocks.
- Sub-workflows (Execute Workflow Trigger) cannot be activated over the public
  API on 2.35; `deploy.py` publishes them with `podman exec n8n-lab n8n
  publish:workflow --id=...`. Webhook workflows activate over the API and
  need no restart.
- A workflow that references an unpublished sub-workflow cannot be published
  (400 "references workflow ... which is not published"), which is why the
  deploy is two-pass and ordered.

## Smoke test by hand

```bash
TOK=$(awk -F= '/^N8N_LAB_AR_GATEWAY_TOKEN=/{print $2}' .env.lab)
C=$(uuidgen | tr A-Z a-z)
curl -s -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d "{\"correlationId\":\"$C\",\"uui\":{\"version\":\"1\",\"raw\":\"1;$C;W1K2970461A123456;+48601234567;pl\"}}" \
  http://localhost:5678/webhook/ar/v1/session/start
```

Then `session/turn` with the returned `sessionId` and a transcript, and
`session/end`. Synthetic VINs: `W1K2970461A123456` (PL), `WDD2130421A654321`
(ES), `W1K1770871N998877` (DE), `W1N2476011J112233` (GB); any other VIN is an
unknown customer.

## Promotion to the MB instance

`../../scripts/promote.py` pattern from the MB-DEMO family applies: ids are
re-minted, loopback URLs repointed, model nodes swapped to the Nexus gateway
credential, credential ids stripped for a human to fill. Before promotion the
mocks are replaced by the real system calls inside AR-10 to AR-16 (one HTTP
node each) and `ar_markets.returnNumber` gets the Genesys return addresses.
