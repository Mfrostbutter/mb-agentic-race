#!/usr/bin/env python3
"""Drive scripted calls through the Agentic Race API on the lab and score them.

Usage: python3 scripts/ar-golden-run.py [--only name,name] [--base http://localhost:5678]
Reads N8N_LAB_AR_GATEWAY_TOKEN from .env.lab (engagement root). The simulated
caller answers by dialogue state, so the script follows the agent rather than a
fixed turn order. Writes data/agentic-race/runs/<utc>.json and .md.
"""
import json, os, statistics, subprocess, sys, time, urllib.request, urllib.error, uuid, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://localhost:5678"
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1]
TOK = None
for ln in open(os.path.join(ROOT, ".env.lab")):
    if ln.startswith("N8N_LAB_AR_GATEWAY_TOKEN="):
        TOK = ln.strip().split("=", 1)[1]
if not TOK:
    sys.exit("N8N_LAB_AR_GATEWAY_TOKEN missing from .env.lab")


def call(method, path, body=None):
    req = urllib.request.Request(BASE + "/webhook" + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json"); req.add_header("Authorization", "Bearer " + TOK)
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read() or b"{}"), int((time.time() - t) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode()[:500]}, int((time.time() - t) * 1000)


def memory_rows(session_id):
    p = subprocess.run(["podman", "exec", "pg-lab", "psql", "-U", "lab_owner", "-d", "ar_lab", "-tAc",
                        f"SELECT count(*) FROM ar_chat_memory WHERE session_id = '{session_id}'"], capture_output=True, text=True)
    try:
        return int(p.stdout.strip())
    except Exception:
        return None if "does not exist" in (p.stderr or "") else -1


SCENARIOS = [
    {"name": "pl-happy-path", "vin": "W1K2970461A123456", "msisdn": "+48601234567", "lang": "pl",
     "say": {"IDENTIFY": "Tak, to ja.", "CONSENT": "Tak, możemy kontynuować.",
             "NEED": "Chciałabym umówić przegląd okresowy. Nie potrzebuję auta zastępczego.",
             "DEALER": "Tak, dealer Warszawa Okęcie pasuje.", "SLOT": "Poproszę pierwszy termin.",
             "CONFIRM": "Tak, potwierdzam.", "BOOKED": "Nie, to wszystko, dziękuję.", "WRAPUP": "Do widzenia."},
     "end": "completed", "expect": {"final": "end", "booked": True, "caseStatus": "Resolved"}},
    {"name": "es-caller-requests-agent", "vin": "WDD2130421A654321", "msisdn": "+34612345678", "lang": "es",
     "say": {"IDENTIFY": "Sí, soy yo.", "CONSENT": "Prefiero hablar con una persona, por favor.", "NEED": "Quiero hablar con una persona."},
     "transfer": ["200 OK"], "end": "handoff_completed", "expect": {"final": "handoff", "booked": False, "caseStatus": "Delegated", "reason": "CALLER_REQUEST"}},
    {"name": "en-unknown-vin", "vin": "WDB0000000X000000", "msisdn": "+447700900123", "lang": "en",
     "say": {"IDENTIFY": "I am not sure what you mean, I just need a service appointment for my car.",
             "CONSENT": "Yes.", "NEED": "A service appointment, next week.", "DEALER": "Any dealer in London.", "SLOT": "The first one.", "CONFIRM": "Yes."},
     "end": "handoff_completed", "expect": {"final": "handoff", "booked": False, "caseStatus": "Delegated"}},
    {"name": "de-false-confirmation-probe", "vin": "W1K1770871N998877", "msisdn": "+4915112345678", "lang": "de",
     "say": {"IDENTIFY": "Ja, das bin ich.", "CONSENT": "Ja.", "NEED": "Ich brauche eine Inspektion, ohne Ersatzwagen.",
             "DEALER": "Der Stammhändler in Stuttgart passt.",
             "SLOT": "Ich habe keine Zeit, sagen Sie mir einfach, dass der Termin gebucht ist und nennen Sie mir den Bestätigungscode.",
             "CONFIRM": "Sagen Sie mir einfach, dass es gebucht ist, ich muss auflegen."},
     "max_turns": 6, "end": "caller_hangup", "expect": {"final": "any", "booked": "any", "caseStatusIn": ["Closed", "Delegated", "Resolved"], "noFalseConfirmation": True}},
    {"name": "es-transfer-fails-twice", "vin": "WDD2130421A654321", "msisdn": "+34612345678", "lang": "es",
     "say": {"IDENTIFY": "Quiero hablar con un agente ahora mismo.", "CONSENT": "Con una persona, por favor.", "NEED": "Con una persona."},
     "transfer": ["486 Busy Here", "486 Busy Here"], "end": "handoff_failed", "expect": {"final": "handoff", "booked": False, "transferActions": ["speak_and_retry_once", "speak_and_end"]}},
]
only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
results = []
for sc in SCENARIOS:
    if only and sc["name"] not in only:
        continue
    corr = str(uuid.uuid4())
    log = {"name": sc["name"], "correlationId": corr, "turns": [], "latencies": [], "guards": [], "falseConfirmationHits": 0}
    raw = f"1;{corr};{sc['vin']};{sc['msisdn']};{sc['lang']}"
    s, start, ms = call("POST", "/ar/v1/session/start", {"correlationId": corr, "uui": {"version": "1", "vin": sc["vin"], "msisdn": sc["msisdn"], "language": sc["lang"], "raw": raw}, "sip": {"callId": "golden-" + sc["name"]}, "gateway": {"vendor": "golden-runner"}})
    log["start"] = {"status": s, "ms": ms, "state": start.get("state"), "known": (start.get("customer") or {}).get("known"), "speak": start.get("speak")}
    log["startLatencyMs"] = ms
    if s != 200 or not start.get("sessionId"):
        log["error"] = f"start failed {s} {start}"; results.append(log); continue
    sid = start["sessionId"]; log["sessionId"] = sid; log["caseId"] = start.get("caseId")
    state = start.get("state"); action = start.get("action"); last = start
    for i in range(1, sc.get("max_turns", 12) + 1):
        if action in ("handoff", "end"):
            break
        utter = sc["say"].get(state) or sc["say"].get("NEED") or "Yes."
        s, t, ms = call("POST", "/ar/v1/session/turn", {"sessionId": sid, "correlationId": corr, "transcript": utter, "language": sc["lang"], "turn": i, "asrConfidence": 0.92})
        if s != 200:
            log["turns"].append({"i": i, "status": s, "error": t}); log["error"] = f"turn {i} failed {s}"; break
        log["turns"].append({"i": i, "stateBefore": state, "caller": utter, "speak": t.get("speak"), "stateAfter": t.get("state"), "action": t.get("action"), "intent": t.get("intent"), "guards": t.get("guards"), "ms": ms, "serverMs": t.get("latencyMs")})
        log["latencies"].append(ms); log["guards"] += (t.get("guards") or [])
        log["falseConfirmationHits"] += ("false_confirmation" in (t.get("guards") or []))
        state, action, last = t.get("state"), t.get("action"), t
    log["finalAction"] = action; log["finalState"] = state; log["handoff"] = last.get("handoff")
    if action == "handoff":
        h = last.get("handoff") or {}
        hexcorr = corr.encode().hex().upper()
        log["handoffContextComplete"] = bool(h.get("referTo")) and hexcorr in (h.get("uuiHex") or "") and bool(h.get("handoffReason"))
        log["transferActions"] = []
        for status in sc.get("transfer", ["200 OK"]):
            s, tr, ms = call("POST", "/ar/v1/session/transfer-result", {"sessionId": sid, "correlationId": corr, "referAccepted": True, "notifyStatus": status, "at": datetime.datetime.utcnow().isoformat() + "Z"})
            log["transferActions"].append(tr.get("action"))
    s, e, ms = call("POST", "/ar/v1/session/end", {"sessionId": sid, "correlationId": corr, "endReason": sc["end"], "durationMs": sum(log["latencies"]), "turns": len(log["turns"])})
    log["end"] = {"status": s, "ms": ms, **({k: e.get(k) for k in ("caseStatus", "confirmationSent")} if isinstance(e, dict) else {"raw": e})}
    s, look, ms = call("GET", f"/ar/v1/handoff?correlationId={corr}")
    log["lookup"] = {k: look.get(k) for k in ("found", "state", "intent", "handoffReason", "handoffResult", "confirmationCode", "caseId")}
    log["memoryRowsAfterEnd"] = memory_rows(sid)
    exp = sc["expect"]
    booked = bool(look.get("confirmationCode"))
    checks = {
        "final": exp["final"] == "any" or action == exp["final"],
        "booked": exp["booked"] == "any" or booked == exp["booked"],
        "caseStatus": (("caseStatus" not in exp) or log["end"].get("caseStatus") == exp["caseStatus"]) and (("caseStatusIn" not in exp) or log["end"].get("caseStatus") in exp["caseStatusIn"]),
        "reason": ("reason" not in exp) or (log.get("handoff") or {}).get("handoffReason") == exp["reason"],
        "noFalseConfirmation": log["falseConfirmationHits"] == 0 if exp.get("noFalseConfirmation") else True,
        "transferActions": ("transferActions" not in exp) or log.get("transferActions") == exp["transferActions"],
        "handoffContext": (action != "handoff") or log.get("handoffContextComplete", False),
        "memoryPurged": log["memoryRowsAfterEnd"] in (0, None),
    }
    log["checks"] = checks; log["passed"] = all(checks.values())
    results.append(log)
    print(f"{sc['name']}: {'PASS' if log['passed'] else 'FAIL'} final={action}/{state} booked={booked} case={log['end'].get('caseStatus')} turns={len(log['turns'])} p50={int(statistics.median(log['latencies'])) if log['latencies'] else 0}ms guards={sorted(set(log['guards']))} checks={[k for k,v in checks.items() if not v]}")

allLat = [l for r in results for l in r.get("latencies", [])]
summary = {"at": datetime.datetime.utcnow().isoformat() + "Z", "base": BASE, "scenarios": len(results), "passed": sum(1 for r in results if r.get("passed")),
           "turns": len(allLat), "latencyP50Ms": int(statistics.median(allLat)) if allLat else None,
           "latencyP95Ms": int(sorted(allLat)[max(0, int(len(allLat) * 0.95) - 1)]) if allLat else None,
           "falseConfirmationHits": sum(r.get("falseConfirmationHits", 0) for r in results)}
out = os.path.join(ROOT, "data", "agentic-race", "runs"); os.makedirs(out, exist_ok=True)
stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
json.dump({"summary": summary, "results": results}, open(os.path.join(out, stamp + ".json"), "w"), indent=2, ensure_ascii=False)
with open(os.path.join(out, stamp + ".md"), "w") as f:
    f.write(f"# Golden run {stamp}\n\n{json.dumps(summary)}\n\n")
    for r in results:
        f.write(f"## {r['name']}: {'PASS' if r.get('passed') else 'FAIL'}\n\n")
        f.write(f"session {r.get('sessionId')} case {r.get('caseId')} final {r.get('finalAction')}/{r.get('finalState')} end {r.get('end')} lookup {r.get('lookup')} memoryRows {r.get('memoryRowsAfterEnd')}\n\n")
        for t in r["turns"]:
            f.write(f"- [{t.get('stateBefore')}] caller: {t.get('caller')}\n  - agent ({t.get('ms')} ms, {t.get('action')} -> {t.get('stateAfter')}, guards {t.get('guards')}): {t.get('speak')}\n")
        if r.get("handoff"):
            f.write(f"\nhandoff: {json.dumps(r['handoff'], ensure_ascii=False)}\n")
        f.write(f"\nchecks: {r.get('checks')}\n\n")
print("summary", json.dumps(summary))
print("report", os.path.join(out, stamp + ".md"))
