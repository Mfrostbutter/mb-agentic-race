#!/usr/bin/env python3
"""Deploy the AR workflow set to the lab in dependency order, two passes.

Usage: infisical run --projectId <id> --env dev --silent -- python3 build/deploy.py [--only key,key]
Pass 1 creates by name and learns ids; gen.py is re-run so sub-workflow references
resolve; pass 2 updates in place. Workflows with a webhook or error trigger are
activated over the API; sub-workflows (Execute Workflow Trigger) are published
through the container CLI, which is the only path that works for them on 2.35.
"""
import json, os, subprocess, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.dirname(HERE)
BASE = os.environ.get("N8N_LAB_URL", "http://localhost:5678").rstrip("/")
KEY = os.environ.get("N8N_LOCAL_WORKFLOW_LAB_API_KEY") or sys.exit("N8N_LOCAL_WORKFLOW_LAB_API_KEY not set")
ORDER = ["ar-17-log-event","ar-20-mock-crm","ar-21-mock-dsd-book","ar-22-mock-sirius-case","ar-23-mock-comms",
         "ar-10-identify-customer","ar-11-find-dealers","ar-12-find-slots","ar-13-book-appointment","ar-14-build-handoff",
         "ar-15-sirius-case","ar-16-send-confirmation","ar-01-session-start","ar-02-dialogue-turn","ar-03-session-end",
         "ar-04-transfer-result","ar-05-health-and-handoff-lookup","ar-30-error-workflow","ar-90-seed-config"]
only = None
if "--only" in sys.argv:
    only = sys.argv[sys.argv.index("--only") + 1].split(",")


def call(method, path, body=None):
    req = urllib.request.Request(f"{BASE}/api/v1{path}", data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("X-N8N-API-KEY", KEY); req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def existing_by_name():
    out = {}
    cursor = None
    while True:
        s, r = call("GET", "/workflows?limit=250" + (f"&cursor={cursor}" if cursor else ""))
        for w in r.get("data", []):
            out[w["name"]] = w
        cursor = r.get("nextCursor")
        if not cursor:
            break
    return out


def is_api_activatable(wf):
    return any(n["type"] in ("n8n-nodes-base.webhook", "n8n-nodes-base.errorTrigger") for n in wf["nodes"])


def deploy_one(key, have):
    path = os.path.join(OUT, key + ".json")
    wf = json.load(open(path))
    payload = {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"], "settings": wf.get("settings", {"executionOrder": "v1"})}
    cur = have.get(wf["name"])
    if cur:
        wid = cur["id"]
        if cur.get("active"):
            call("POST", f"/workflows/{wid}/deactivate")
        s, r = call("PUT", f"/workflows/{wid}", payload)
        action = f"updated({s})"
    else:
        s, r = call("POST", "/workflows", payload)
        if s not in (200, 201):
            print(f"  {key}: create failed {s} {r}"); return None
        wid = r["id"]; action = "created"
    if is_api_activatable(wf):
        s, r = call("POST", f"/workflows/{wid}/activate")
        act = f"activated({s})" if s == 200 else f"activate-failed({s}) {str(r)[:160]}"
    else:
        p = subprocess.run(["podman", "exec", "n8n-lab", "n8n", "publish:workflow", f"--id={wid}"], capture_output=True, text=True)
        act = "published" if p.returncode == 0 else f"publish-failed: {(p.stderr or p.stdout)[-200:]}"
    if wf.get("id") != wid:
        wf = {"id": wid, **{k: v for k, v in wf.items() if k != "id"}}
        json.dump(wf, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False); open(path, "a").write("\n")
    print(f"  {key}: {action} {wid} {act}")
    return wid


def run_pass(n):
    print(f"pass {n}")
    subprocess.run([sys.executable, os.path.join(HERE, "gen.py")], check=True, stdout=subprocess.DEVNULL)
    have = existing_by_name()
    for key in ORDER:
        if only and key not in only:
            continue
        deploy_one(key, have)


run_pass(1)
if not only:
    run_pass(2)
subprocess.run([sys.executable, os.path.join(HERE, "gen.py")], check=True, stdout=subprocess.DEVNULL)
print("ids:", json.dumps({k: v for k, v in json.load(open(os.path.join(OUT, "lab-ids.json"))).items() if k.startswith("wf_")}, indent=1))
