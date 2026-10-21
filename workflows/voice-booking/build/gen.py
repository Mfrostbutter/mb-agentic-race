#!/usr/bin/env python3
"""Generate the voice booking workflow set (AR-*) as n8n workflow JSON.

Run from anywhere: python3 workflows/voice-booking/build/gen.py
Reads the ids file (lab-ids.json, or AR_IDS_FILE): credential, Data Table and
workflow ids plus the per-instance settings build/setup.py writes. Workflow ids
are learned from previously deployed JSON files, so a second run after a deploy
resolves every sub-workflow reference.
"""
import json, os, sys, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(OUT))
IDS_PATH = os.environ.get("AR_IDS_FILE") or os.path.join(OUT, "lab-ids.json")
# The ids file is the per-instance configuration: credential and Data Table ids,
# names, placement, model and the instance's own URL. build/setup.py writes it.
IDS = json.load(open(IDS_PATH)) if os.path.exists(IDS_PATH) else {}
LAB = (IDS.get("selfUrl") or os.environ.get("N8N_URL") or "http://localhost:5678").rstrip("/")
MOCK = LAB + "/webhook/ar-mock"
CRED_PG = {"id": IDS.get("credPostgres", "PENDING"), "name": IDS.get("credPostgresName", "Chat memory Postgres")}
CRED_GW = {"id": IDS.get("credGatewayHeader", "PENDING"), "name": IDS.get("credGatewayHeaderName", "Gateway bearer")}
CRED_LM = {"id": IDS.get("credLmStudio", "PENDING"), "name": IDS.get("credLmStudioName", "Model endpoint")}
MODEL = IDS.get("model", "qwen/qwen3-coder-30b")
# Empty: the model node uses the URL stored on the credential.
MODEL_BASE_URL = IDS.get("modelBaseUrl", "http://host.docker.internal:1234/v1")
NAMES = {}  # key -> workflow name, filled as workflows are declared

# Placement on the instance: one project per customer, one folder per demo,
# a customer tag, a demo tag and a role tag. deploy.py applies it; the deploy
# gate refuses a workflow without it.
PROJECT = IDS.get("project", "Voice Booking")
FOLDER = IDS.get("folder", "Voice Booking Agent")
TAGS = ["voice-booking"]
ROLE_TAG = {"ar-01": "api", "ar-02": "api", "ar-03": "api", "ar-04": "api", "ar-05": "api",
            "ar-10": "sub-workflow", "ar-11": "sub-workflow", "ar-12": "sub-workflow", "ar-13": "sub-workflow",
            "ar-14": "sub-workflow", "ar-15": "sub-workflow", "ar-16": "sub-workflow", "ar-17": "sub-workflow",
            "ar-20": "lab-stand-in", "ar-21": "lab-stand-in", "ar-22": "lab-stand-in", "ar-23": "lab-stand-in",
            "ar-30": "ops", "ar-90": "ops"}

TABLE_COLS = {
    "ar_sessions": ["sessionId","correlationId","caseId","market","language","state","intent","customerRef","vehicleRef","greetingName","vehicleLabel","dealerId","dealerName","offeredSlots","slotId","bookingRef","confirmationCode","confirmationChannel","confirmationSent","handoffReason","handoffResult","handoffSummary","turns","startedAt","endedAt","endReason","lastLatencyMs"],
    "ar_events": ["at","correlationId","sessionId","caseId","workflow","event","state","tool","latencyMs","outcome","errorCode","detail"],
    "ar_markets": ["market","languages","defaultLanguage","bookable","disclosure_pl","disclosure_es","disclosure_en","disclosure_de","handoverLine","closingLine","confirmationChannels","returnNumber","slotWindowDays","maxSlotsOffered"],
    "ar_codes": ["kind","code","short","ccValue","description"],
    "ar_mock_bookings": ["bookingRef","confirmationCode","slotId","dealerId","vin","serviceType","mobility","startsAt","status","bookedAt"],
    "ar_mock_cases": ["caseId","correlationId","status","subject","classification","solution","delegatedReason","openedAt","lastUpdate"],
}
NUMERIC = {"turns","lastLatencyMs","latencyMs","slotWindowDays","maxSlotsOffered"}
BOOLEAN = {"confirmationSent","bookable"}


def coltype(c):
    return "number" if c in NUMERIC else "boolean" if c in BOOLEAN else "string"


def schema(cols, table=None):
    # Sub-workflow inputs and Data Table columns share one typing rule so a number declared on the trigger is sent as a number.
    return [{"id": c, "displayName": c, "required": False, "defaultMatch": False, "display": True,
             "type": coltype(c), "canBeUsedToMatch": True, "removed": False} for c in cols]


def learn_existing_ids():
    for f in os.listdir(OUT):
        if f.endswith(".json") and f.startswith("ar-"):
            try:
                w = json.load(open(os.path.join(OUT, f)))
            except Exception:
                continue
            key = f[:-5]
            if w.get("id") and not str(w["id"]).startswith("PENDING"):
                IDS["wf_" + key] = w["id"]


def wfref(key):
    return {"__rl": True, "value": IDS.get("wf_" + key, "PENDING_" + key), "mode": "list", "cachedResultName": NAMES.get(key, key)}


def snap16(v):
    return int(round(v / 16.0)) * 16


class WF:
    def __init__(self, key, name, description):
        self.key, self.name, self.description = key, name, description
        self.nodes, self.conns = [], {}
        self.panels = []
        NAMES[key] = name

    def add(self, name, ntype, ver, params, x, y, **extra):
        node = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar/{self.key}/{name}")),
                "name": name, "type": ntype, "typeVersion": ver, "position": [x, y]}
        node.update(extra)
        self.nodes.append(node)
        return name

    def link(self, src, dst, out=0, ctype="main", dst_in=0):
        lst = self.conns.setdefault(src, {}).setdefault(ctype, [])
        while len(lst) <= out:
            lst.append([])
        lst[out].append({"node": dst, "type": ctype, "index": dst_in})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    # ---- node constructors -------------------------------------------------
    def sticky(self, content, x, y, w=560, h=120, color=7):
        """Overview text for the workflow. Rendered as the doc panel above the zone row by the canvas layout."""
        title, _, body = content.partition("\n")
        self.panels.append({"title": title.lstrip("# ").strip(), "body": body.strip(), "color": 6, "width": max(560, snap16(w))})
        return None

    def webhook(self, name, path, x, y, method="POST", auth=True):
        p = {"httpMethod": method, "path": path, "responseMode": "responseNode", "options": {}}
        extra = {"webhookId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar/{self.key}/{path}/{method}"))}
        if auth:
            p["authentication"] = "headerAuth"
            extra["credentials"] = {"httpHeaderAuth": CRED_GW}
        return self.add(name, "n8n-nodes-base.webhook", 2.1, p, x, y, **extra)

    def respond(self, name, body, x, y, code=200):
        return self.add(name, "n8n-nodes-base.respondToWebhook", 1.5,
                        {"respondWith": "json", "responseBody": body, "options": {"responseCode": code}}, x, y)

    def code(self, name, js, x, y, **extra):
        return self.add(name, "n8n-nodes-base.code", 2, {"jsCode": js}, x, y, **extra)

    def set_fields(self, name, fields, x, y, keep=False):
        """Edit Fields, manual mapping. fields: (name, value, type). keep=True passes the other input fields through."""
        assigns = [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar/{self.key}/{name}/{f}")), "name": f, "value": v, "type": t}
                   for f, v, t in fields]
        p = {"assignments": {"assignments": assigns}, "options": {}}
        if keep:
            p["includeOtherFields"] = True
        return self.add(name, "n8n-nodes-base.set", 3.4, p, x, y)

    def set_json(self, name, expr, x, y):
        """Edit Fields in JSON mode: the expression's object becomes the whole item."""
        return self.add(name, "n8n-nodes-base.set", 3.4, {"mode": "raw", "jsonOutput": expr, "options": {}}, x, y)

    # ---- conditions ----------------------------------------------------------
    SINGLE_OPS = {"true", "false", "empty", "notEmpty", "exists", "notExists"}

    @classmethod
    def cond(cls, left, op, right="", kind="string"):
        """One filter condition. kind string|boolean|number; op per n8n's filter operators (equals, notEquals,
        regex, notRegex, empty, notEmpty, true, false, gt, lt, ...). For regex: lower-case the left side and pass a bare pattern."""
        operator = {"type": kind, "operation": op}
        if op in cls.SINGLE_OPS:
            operator["singleValue"] = True
        return {"leftValue": left, "rightValue": right, "operator": operator}

    def _rule(self, name, conds, combinator="and", version=2):
        out = []
        for i, c in enumerate(conds):
            out.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar/{self.key}/{name}/c{i}")), **c})
        return {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": version},
                "conditions": out, "combinator": combinator}

    def if_node(self, name, left, x, y, kind="boolean"):
        """Single-condition If: boolean true, or string not empty."""
        c = self.cond(left, "true", kind="boolean") if kind == "boolean" else self.cond(left, "notEmpty")
        return self.if_rules(name, [c], x, y)

    def if_rules(self, name, conds, x, y, combinator="and"):
        """If with several conditions (see cond). Output 0 true, 1 false."""
        return self.add(name, "n8n-nodes-base.if", 2.2, {"conditions": self._rule(name, conds, combinator), "options": {}}, x, y)

    def switch(self, name, rules, x, y, fallback=None):
        """Switch in rules mode. rules: list of (outputName, conds, combinator); first match wins, in order.
        fallback names the extra output for items no rule matched (index len(rules))."""
        vals = [{"conditions": self._rule(f"{name}/{o}", c, comb, version=3), "renameOutput": True, "outputKey": o} for o, c, comb in rules]
        opts = {"fallbackOutput": "extra", "renameFallbackOutput": fallback} if fallback else {}
        return self.add(name, "n8n-nodes-base.switch", 3.4, {"rules": {"values": vals}, "looseTypeValidation": True, "options": opts}, x, y)

    def guard_chain(self, srcs, guards):
        """Named rule chain: for each (ifName, conds, combinator, setName, fields) an If and an Edit Fields that applies the rule.
        Both outcomes continue to the next rule. srcs: [(node, output)] feeding the first If. Returns the sources for the next node."""
        for ifn, conds, comb, setn, fields in guards:
            i = self.if_rules(ifn, conds, 0, 0, comb)
            for s, out in srcs:
                self.link(s, i, out)
            st = self.set_fields(setn, fields, 0, 0, keep=True)
            self.link(i, st, 0)
            srcs = [(st, 0), (i, 1)]
        return srcs

    def return_error(self, name, x, y):
        """Shapes a failed HTTP call into the sub-workflow's error contract. No throw: the caller branches on ok."""
        conflict = "JSON.stringify($json.error || {}).includes('409')"
        return self.set_fields(name, [
            ("ok", False, "boolean"),
            ("error", f"={{{{ {conflict} ? 'slot_taken' : 'backend_unavailable' }}}}", "string"),
            ("message", f"={{{{ {conflict} ? 'That slot was taken in the meantime. Call FindSlots again and offer new options.' : 'The backend system is unavailable. Apologise once and hand the caller to an agent (BACKEND_UNAVAILABLE).' }}}}", "string"),
            ("detail", "={{ String(($json.error || {}).message || '').slice(0, 200) }}", "string"),
        ], x, y)

    def dt(self, name, table, op, x, y, conditions=None, values=None, limit=1, always=True):
        p = {"operation": op, "dataTableId": {"__rl": True, "mode": "id", "value": IDS["dt_" + table]}}
        if conditions is not None:
            p["matchType"] = "allConditions"
            p["filters"] = {"conditions": [{"keyName": k, "condition": "eq", "keyValue": v} for k, v in conditions]}
        if op == "get":
            p["returnAll"] = False
            p["limit"] = limit
        if values is not None:
            p["columns"] = {"mappingMode": "defineBelow", "value": values, "matchingColumns": [],
                            "schema": schema(TABLE_COLS[table], table), "attemptToConvertTypes": False, "convertFieldsToString": False}
        extra = {"alwaysOutputData": True} if always and op in ("get", "update", "upsert") else {}
        return self.add(name, "n8n-nodes-base.dataTable", 1.1, p, x, y, **extra)

    def sub_trigger(self, fields, x=0, y=0, name="ToolInput"):
        return self.add(name, "n8n-nodes-base.executeWorkflowTrigger", 1.2,
                        {"inputSource": "workflowInputs", "workflowInputs": {"values": [{"name": n, "type": t} for n, t in fields]}}, x, y)

    def exec_wf(self, name, key, inputs, x, y, wait=True):
        p = {"workflowId": wfref(key),
             "workflowInputs": {"mappingMode": "defineBelow", "value": inputs, "matchingColumns": [],
                                "schema": schema(list(inputs.keys())), "attemptToConvertTypes": False, "convertFieldsToString": True},
             "mode": "once", "options": {"waitForSubWorkflow": wait}}
        return self.add(name, "n8n-nodes-base.executeWorkflow", 1.3, p, x, y)

    def tool_wf(self, name, key, description, inputs, x, y):
        p = {"description": description, "workflowId": wfref(key),
             "workflowInputs": {"mappingMode": "defineBelow", "value": inputs, "matchingColumns": [],
                                "schema": schema(list(inputs.keys())), "attemptToConvertTypes": False, "convertFieldsToString": True}}
        return self.add(name, "@n8n/n8n-nodes-langchain.toolWorkflow", 2.2, p, x, y)

    def http_post(self, name, url, json_expr, x, y, fatal=False):
        p = {"method": "POST", "url": url, "sendBody": True, "specifyBody": "json", "jsonBody": json_expr,
             "options": {"timeout": 15000}}
        extra = {"retryOnFail": True, "maxTries": 2, "waitBetweenTries": 1000}
        if not fatal:
            extra["onError"] = "continueErrorOutput"
        return self.add(name, "n8n-nodes-base.httpRequest", 4.2, p, x, y, **extra)

    def model(self, name, x, y, temperature=0):
        return self.add(name, "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3,
                        {"model": {"__rl": True, "value": MODEL, "mode": "id"},
                         "options": {**({"baseURL": MODEL_BASE_URL} if MODEL_BASE_URL else {}), "temperature": temperature},
                         "responsesApiEnabled": False}, x, y, credentials={"openAiApi": CRED_LM})

    def layout(self):
        """Positions from the zone spec: 208 px pitch, branch rows 272 px lower, <= 8 columns per board row.
        Zones never split across board rows; a zone wider than the remaining columns starts a new row."""
        spec = ZONES[self.key]
        by_name = {n["name"]: n for n in self.nodes}
        seen = set()
        col, board_row = 0, 0
        for z in spec:
            width = max(len(r) for r in z["rows"])
            if col and col + width > MAX_COLS:
                board_row += 1; col = 0
            for ri, row in enumerate(z["rows"]):
                for ci, name in enumerate(row):
                    if name is None:
                        continue
                    if name not in by_name:
                        raise SystemExit(f"{self.key}: zone '{z['title']}' names unknown node {name}")
                    if name in seen:
                        raise SystemExit(f"{self.key}: node {name} is in two zones")
                    seen.add(name)
                    by_name[name]["position"] = [(col + ci) * PITCH, board_row * BOARD_ROW_PITCH + ri * ROW_PITCH]
            col += width
        orphans = sorted(set(by_name) - seen)
        if orphans:
            raise SystemExit(f"{self.key}: nodes outside every zone: {orphans}")
        return {"gutter": 16,
                "zones": [{"title": z["title"], "body": z["body"], "color": z["color"],
                           "nodes": [n for r in z["rows"] for n in r if n]} for z in spec],
                "panels": self.panels}

    def dump(self):
        spec = self.layout()
        wf = {"name": self.name, "nodes": self.nodes, "connections": self.conns,
              "settings": {"executionOrder": "v1"}, "meta": {"description": self.description},
              "tags": TAGS + [ROLE_TAG[self.key[:5]]], "project": PROJECT, "folder": FOLDER}
        wid = IDS.get("wf_" + self.key)
        if wid:
            wf = {"id": wid, **wf}
        path = os.path.join(OUT, self.key + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(wf, f, indent=2, ensure_ascii=False)
            f.write("\n")
        zpath = os.path.join(OUT, self.key + ".zones.json")
        with open(zpath, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
            f.write("\n")
        canvas_layout(path, zpath)
        return path


PITCH, ROW_PITCH, BOARD_ROW_PITCH, MAX_COLS = 208, 272, 1600, 8


def canvas_layout(path, zpath):
    """Zone stickies and the layout gate from the canvas layout scripts (scripts/canvas next to the repository, or N8N_CANVAS_SCRIPTS)."""
    import subprocess
    cands = [os.environ.get("N8N_CANVAS_SCRIPTS", ""),
             os.path.join(ROOT, "scripts", "canvas")]
    d = next((c for c in cands if c and os.path.exists(os.path.join(c, "zone_layout.py"))), None)
    if not d:
        raise SystemExit("n8n-canvas-docs scripts not found (set N8N_CANVAS_SCRIPTS)")
    subprocess.run([sys.executable, os.path.join(d, "zone_layout.py"), path, zpath, "-o", path], check=True, stdout=subprocess.DEVNULL)
    r = subprocess.run([sys.executable, os.path.join(d, "layout_check.py"), path], capture_output=True, text=True)
    if "RESULT: ok" not in r.stdout:
        raise SystemExit(f"canvas gate failed for {os.path.basename(path)}:\n{r.stdout[-1500:]}{r.stderr[-500:]}")


STATES = ["IDENTIFY", "CONSENT", "NEED", "DEALER", "SLOT", "CONFIRM", "BOOKED", "WRAPUP", "HANDOFF"]
INTENTS = ["BOOK", "RESCHEDULE", "CANCEL", "OTHER", "UNKNOWN"]
REASONS = ["CALLER_REQUEST", "SCENARIO_REQUIRES_AGENT", "NOT_UNDERSTOOD", "BACKEND_UNAVAILABLE", "IDENTIFICATION_FAILED", "NO_SLOT", "POLICY", "PLATFORM_ERROR"]
STATES_JS, INTENTS_JS, REASONS_JS = (json.dumps(x) for x in (STATES, INTENTS, REASONS))


def lang_pick(texts, lang_expr="$json.language"):
    """Expression that picks a text by language, English fallback. Moves to ar_markets columns in step 2."""
    return f"={{{{ ({json.dumps(texts, ensure_ascii=False)})[{lang_expr}] || {json.dumps(texts['en'], ensure_ascii=False)} }}}}"


def with_guard(code, when=None):
    """Expression appending a guard code to $json.guards, optionally only when a JS condition holds."""
    if when:
        return f"={{{{ ({when}) ? $json.guards.concat(['{code}']) : $json.guards }}}}"
    return f"={{{{ $json.guards.concat(['{code}']) }}}}"


# =============================================================================
# AR-17 log event
# =============================================================================
def build_ar17():
    w = WF("ar-17-log-event", "AR-17 Log event", "Append one row to ar_events. Inputs: correlationId, sessionId, caseId, workflow, event, state, tool, latencyMs, outcome, errorCode, detail. Returns {ok}. No PII.")
    w.sticky("## AR-17 Log event\nOne append per event. Columns carry ids, states and codes only, never caller text.", 0, -400, 620, 100)
    t = w.sub_trigger([("correlationId","string"),("sessionId","string"),("caseId","string"),("workflow","string"),("event","string"),("state","string"),("tool","string"),("latencyMs","number"),("outcome","string"),("errorCode","string"),("detail","string")])
    vals = {c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_events"] if c != "at"}
    vals["at"] = "={{ $now.toUTC().toISO() }}"
    ins = w.dt("InsertEvent", "ar_events", "insert", 260, 0, values=vals, always=False)
    ret = w.set_fields("ReturnOk", [("ok", True, "boolean")], 520, 0)
    w.chain(t, ins, ret)
    return w

# =============================================================================
# Mocks
# =============================================================================
CRM_FIXTURE_JS = """// Mock of CRM / customer master. Real build: HTTP Request to the customer API with a technical-user credential.
const b = $json.body || {};
const vin = String(b.vin || '').toUpperCase().trim();
const customers = {
  'TST2970461A123456': { customerRef: 'C-PL-004711', greetingName: 'Pani Kowalska', vehicleLabel: 'GLC 300 4MATIC', preferredDealerId: 'PL-WAW-01', preferredDealerName: 'Dealer Warszawa Okęcie', market: 'PL', language: 'pl' },
  'TST2130421A654321': { customerRef: 'C-ES-002310', greetingName: 'Señor García', vehicleLabel: 'Clase E 220 d', preferredDealerId: 'ES-MAD-02', preferredDealerName: 'Dealer Madrid Castellana', market: 'ES', language: 'es' },
  'TST1770871N998877': { customerRef: 'C-DE-009902', greetingName: 'Herr Weber', vehicleLabel: 'A 200', preferredDealerId: 'DE-STR-01', preferredDealerName: 'Dealer Stuttgart Mitte', market: 'DE', language: 'de' },
  'TST2476011J112233': { customerRef: 'C-GB-001177', greetingName: 'Ms Patel', vehicleLabel: 'EQB 300', preferredDealerId: 'GB-LON-01', preferredDealerName: 'Dealer London West', market: 'GB', language: 'en' }
};
const c = customers[vin];
return [{ json: c ? { found: true, vin, ...c } : { found: false, vin } }];"""

def build_ar20():
    w = WF("ar-20-mock-crm", "AR-MOCK-CRM customer master", "Lab stand-in for CRM / customer master. POST ar-mock/crm/customer-lookup {vin, msisdn} -> {found, customerRef, greetingName, vehicleLabel, preferredDealerId, ...}. Synthetic data only.")
    w.sticky("## Mock CRM / customer master\nFour synthetic customers keyed by VIN. Unknown VIN returns found:false with HTTP 200.", 0, -400, 620, 100)
    a = w.webhook("ReceiveLookup", "ar-mock/crm/customer-lookup", 0, 0, auth=False)
    b = w.code("LookupFixture", CRM_FIXTURE_JS, 260, 0)
    c = w.respond("RespondCustomer", "={{ JSON.stringify($json) }}", 520, 0)
    w.chain(a, b, c)
    return w

DEALERBOOK_DEALERS_JS = """// Mock of dealer search. Real build: dealer booking API or the dealer directory.
const b = $json.body || {};
const all = [
  { dealerId: 'PL-WAW-01', name: 'Dealer Warszawa Okęcie', city: 'Warszawa', market: 'PL', bookable: true },
  { dealerId: 'PL-WAW-02', name: 'Dealer Warszawa Wola', city: 'Warszawa', market: 'PL', bookable: true },
  { dealerId: 'PL-KRK-01', name: 'Dealer Kraków', city: 'Kraków', market: 'PL', bookable: true },
  { dealerId: 'PL-GDA-01', name: 'Dealer Gdańsk', city: 'Gdańsk', market: 'PL', bookable: false },
  { dealerId: 'ES-MAD-02', name: 'Dealer Madrid Castellana', city: 'Madrid', market: 'ES', bookable: true },
  { dealerId: 'ES-BCN-01', name: 'Dealer Barcelona', city: 'Barcelona', market: 'ES', bookable: true },
  { dealerId: 'DE-STR-01', name: 'Dealer Stuttgart Mitte', city: 'Stuttgart', market: 'DE', bookable: true },
  { dealerId: 'GB-LON-01', name: 'Dealer London West', city: 'London', market: 'GB', bookable: true }
];
const market = String(b.market || '').toUpperCase();
const city = String(b.city || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
let hits = all.filter(d => !market || d.market === market);
if (b.dealerId) hits = hits.filter(d => d.dealerId === b.dealerId);
if (city) hits = hits.filter(d => d.city.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').includes(city));
return [{ json: { dealers: hits } }];"""

DEALERBOOK_SLOTS_JS = """// Mock of DealerBook slot availability. Deterministic from dealerId and date so tests repeat.
const b = $json.body || {};
const dealerId = String(b.dealerId || '');
const names = { 'PL-WAW-01': 'Dealer Warszawa Okęcie', 'PL-WAW-02': 'Dealer Warszawa Wola', 'PL-KRK-01': 'Dealer Kraków', 'ES-MAD-02': 'Dealer Madrid Castellana', 'ES-BCN-01': 'Dealer Barcelona', 'DE-STR-01': 'Dealer Stuttgart Mitte', 'GB-LON-01': 'Dealer London West' };
if (!names[dealerId]) return [{ json: { dealerId, dealerName: null, slots: [], reason: 'dealer_not_dealerbook_enabled' } }];
let from = new Date(b.fromDate || Date.now());
if (isNaN(from.getTime())) from = new Date();
from.setUTCHours(0, 0, 0, 0);
const seed = [...dealerId].reduce((a, ch) => a + ch.charCodeAt(0), 0);
const times = ['08:00', '10:30', '14:00'];
const slots = [];
for (let d = 1; d <= 10 && slots.length < 6; d++) {
  const day = new Date(from.getTime() + d * 86400000);
  if (day.getUTCDay() === 0) continue;
  times.forEach((t, i) => { if ((seed + d + i) % 3 !== 0) slots.push({ slotId: `${dealerId}-${day.toISOString().slice(0,10).replace(/-/g,'')}-${t.replace(':','')}`, startsAt: `${day.toISOString().slice(0,10)}T${t}:00`, dealerId }); });
}
return [{ json: { dealerId, dealerName: names[dealerId], slots: slots.slice(0, 6) } }];"""

DEALERBOOK_BOOK_JS = """// Mock of DealerBook booking. Idempotent on idempotencyKey; a slotId containing FULL simulates a slot taken in the meantime.
const b = $json.body || {};
const key = String(b.idempotencyKey || '');
const slotId = String(b.slotId || '');
if (!key || !slotId || !b.dealerId) return [{ json: { conflict: false, invalid: true, message: 'slotId, dealerId and idempotencyKey are required' } }];
if (/FULL/.test(slotId)) return [{ json: { conflict: true, message: 'slot no longer available' } }];
let h = 0; for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
const code = 'BK-' + (h % 1000000).toString().padStart(6, '0');
const m = slotId.match(/-(\\d{8})-(\\d{4})$/);
const startsAt = m ? `${m[1].slice(0,4)}-${m[1].slice(4,6)}-${m[1].slice(6,8)}T${m[2].slice(0,2)}:${m[2].slice(2,4)}:00` : '';
return [{ json: { conflict: false, invalid: false, bookingRef: key, confirmationCode: code, slotId, dealerId: b.dealerId, vin: b.vin || '', serviceType: b.serviceType || '', mobility: b.mobility || '', startsAt, status: 'booked', bookedAt: new Date().toISOString() } }];"""

def build_ar21():
    w = WF("ar-21-mock-dealerbook", "AR-MOCK-DealerBook", "Lab stand-in for DealerBook (dealers, slots, book, bookings by VIN, cancel). Deterministic synthetic data. Replace with the dealer booking API.")
    w.sticky("## Mock DealerBook\nFive endpoints under ar-mock/dealerbook/*. Slots are deterministic per dealer and date. A slotId containing FULL returns 409. Bookings persist in ar_mock_bookings.", 0, -400, 760, 120)
    # dealers
    a = w.webhook("ReceiveDealers", "ar-mock/dealerbook/dealers", 0, 0, auth=False)
    b = w.code("FilterDealers", DEALERBOOK_DEALERS_JS, 260, 0)
    c = w.respond("RespondDealers", "={{ JSON.stringify($json) }}", 520, 0)
    w.chain(a, b, c)
    # slots
    a = w.webhook("ReceiveSlots", "ar-mock/dealerbook/slots", 0, 200, auth=False)
    b = w.code("GenerateSlots", DEALERBOOK_SLOTS_JS, 260, 200)
    c = w.respond("RespondSlots", "={{ JSON.stringify($json) }}", 520, 200)
    w.chain(a, b, c)
    # book
    a = w.webhook("ReceiveBook", "ar-mock/dealerbook/book", 0, 400, auth=False)
    b = w.code("ComputeBooking", DEALERBOOK_BOOK_JS, 260, 400)
    i = w.if_node("IsConflict", "={{ $json.conflict === true || $json.invalid === true }}", 520, 400)
    r409 = w.respond("RespondConflict", "={{ JSON.stringify({ error: $json.invalid ? 'validation_error' : 'slot_taken', message: $json.message }) }}", 780, 300, code="={{ $json.invalid ? 400 : 409 }}")
    up = w.dt("UpsertBooking", "ar_mock_bookings", "upsert", 780, 500, conditions=[("bookingRef", "={{ $json.bookingRef }}")],
              values={c2: f"={{{{ $json.{c2} }}}}" for c2 in TABLE_COLS["ar_mock_bookings"]})
    rb = w.respond("RespondBooked", "={{ JSON.stringify($('ComputeBooking').first().json) }}", 1040, 500)
    w.chain(a, b, i)
    w.link(i, r409, 0)
    w.link(i, up, 1)
    w.link(up, rb)
    # bookings by vin
    a = w.webhook("ReceiveBookings", "ar-mock/dealerbook/bookings", 0, 700, auth=False)
    g = w.dt("FindBookingsByVin", "ar_mock_bookings", "get", 260, 700, conditions=[("vin", "={{ $json.body.vin }}")], limit=20)
    s = w.code("ShapeBookings", "const rows = $input.all().map(i => i.json).filter(r => r.bookingRef);\nreturn [{ json: { bookings: rows } }];", 520, 700)
    c = w.respond("RespondBookings", "={{ JSON.stringify($json) }}", 780, 700)
    w.chain(a, g, s, c)
    # cancel
    a = w.webhook("ReceiveCancel", "ar-mock/dealerbook/cancel", 0, 900, auth=False)
    u = w.dt("CancelBooking", "ar_mock_bookings", "update", 260, 900, conditions=[("bookingRef", "={{ $json.body.bookingRef }}")], values={"status": "cancelled"})
    c = w.respond("RespondCancelled", "={{ JSON.stringify({ ok: true, bookingRef: $('ReceiveCancel').first().json.body.bookingRef, status: 'cancelled' }) }}", 520, 900)
    w.chain(a, u, c)
    return w

CASE_JS = """// Mock of the case system API. Real build: the case system's REST with OAuth2 client credentials.
const b = $json.body || {};
const op = b.op || 'create';
let caseId = b.caseId || '';
if (op === 'create' || !caseId) {
  let h = 0; for (const ch of String(b.correlationId || Date.now())) h = (h * 33 + ch.charCodeAt(0)) >>> 0;
  caseId = 'CS-' + (10000000 + (h % 90000000)).toString();
}
const now = new Date().toISOString();
return [{ json: { caseId, correlationId: b.correlationId || '', status: b.status || (op === 'create' ? 'In Progress' : 'Closed'),
  subject: b.subject || 'Appointment booking via voice assistant', classification: b.classification || 'Appointment Booking',
  solution: b.solution || '', delegatedReason: b.delegatedReason || '', openedAt: op === 'create' ? now : (b.openedAt || ''), lastUpdate: now, op } }];"""

def build_ar22():
    w = WF("ar-22-mock-case-system", "AR-MOCK-CASE Case system", "Lab stand-in for the case API. POST ar-mock/casesys/case {op: create|update|close, correlationId, caseId, ...} -> case. Persists in ar_mock_cases.")
    w.sticky("## Mock case system\nOne endpoint, op in the body. Case numbers are deterministic from the correlation ID, so a re-created case is the same case.", 0, -400, 700, 100)
    a = w.webhook("ReceiveCaseOp", "ar-mock/casesys/case", 0, 0, auth=False)
    b = w.code("ComputeCase", CASE_JS, 260, 0)
    vals = {c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_mock_cases"]}
    vals["openedAt"] = "={{ $json.op === 'create' ? $json.openedAt : undefined }}"
    up = w.dt("UpsertCase", "ar_mock_cases", "upsert", 520, 0, conditions=[("caseId", "={{ $json.caseId }}")], values=vals)
    c = w.respond("RespondCase", "={{ JSON.stringify($('ComputeCase').first().json) }}", 780, 0)
    w.chain(a, b, up, c)
    return w

def build_ar23():
    w = WF("ar-23-mock-comms", "AR-MOCK-COMMS SMS and email", "Lab stand-in for the communication service. POST ar-mock/comms/send {channel, customerRef, language, text} -> {messageId}. Sends nothing.")
    w.sticky("## Mock communication service\nReturns a message id. The real service resolves the customer's number or address from the CRM reference, so n8n never stores it.", 0, -400, 700, 100)
    a = w.webhook("ReceiveSend", "ar-mock/comms/send", 0, 0, auth=False)
    b = w.code("ComputeMessage", "const b = $json.body || {};\nif (!b.customerRef || !b.text) return [{ json: { accepted: false, error: 'customerRef and text are required' } }];\nreturn [{ json: { accepted: true, messageId: 'msg-' + Date.now().toString(36), channel: b.channel || 'sms', customerRef: b.customerRef, length: String(b.text).length } }];", 260, 0)
    c = w.respond("RespondSend", "={{ JSON.stringify($json) }}", 520, 0, code="={{ $json.accepted ? 200 : 400 }}")
    w.chain(a, b, c)
    return w

# =============================================================================
# Tools and sub-workflows
# =============================================================================
def build_ar10():
    w = WF("ar-10-identify-customer", "AR-10 Identify customer by VIN and MSISDN", "Looks the caller up in CRM / customer master from the UUI identifiers. Inputs: vin, msisdn, correlationId. Returns {ok, known, customerRef, greetingName, vehicleLabel, preferredDealerId, preferredDealerName, market, language}. Called by AR-01, never by the model.")
    w.sticky("## AR-10 Identify customer\nIdentity is plumbed from the UUI, never from speech. The model never sees the VIN or the phone number.", 0, -400, 700, 100)
    t = w.sub_trigger([("vin","string"),("msisdn","string"),("correlationId","string")])
    h = w.http_post("CallCrm", MOCK + "/crm/customer-lookup", "={{ JSON.stringify({ vin: $json.vin, msisdn: $json.msisdn, correlationId: $json.correlationId }) }}", 260, 0)
    s = w.code("ShapeCustomer", "const r = $json;\nreturn [{ json: { ok: true, known: !!r.found, customerRef: r.customerRef || '', vehicleRef: r.vin || '', greetingName: r.greetingName || '', vehicleLabel: r.vehicleLabel || '', preferredDealerId: r.preferredDealerId || '', preferredDealerName: r.preferredDealerName || '', market: r.market || '', language: r.language || '' } }];", 520, -80)
    e = w.return_error("ReturnCrmError", 520, 120)
    w.chain(t, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

def build_ar11():
    w = WF("ar-11-find-dealers", "AR-11 Tool: Find dealers", "Agent tool. Lists dealers for the session's market, optionally filtered by city. Inputs: sessionId (plumbed), city. Returns {ok, dealers:[{dealerId, name, city, bookable}]}.")
    w.sticky("## AR-11 FindDealers\nMarket comes from the session row, city from the model. Non-bookable dealers are returned flagged so the agent routes them to a handoff.", 0, -400, 700, 100)
    t = w.sub_trigger([("sessionId","string"),("city","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    h = w.http_post("CallDealerBookDealers", MOCK + "/dealerbook/dealers", "={{ JSON.stringify({ market: $json.market, city: $('ToolInput').first().json.city || '' }) }}", 520, 0)
    s = w.code("ShapeDealers", "const r = $json;\nconst dealers = (r.dealers || []).slice(0, 5);\nreturn [{ json: { ok: true, count: dealers.length, dealers, note: dealers.length ? 'Offer at most two by name. Dealers with bookable false cannot be booked here.' : 'No dealer matched. Ask for another city or offer the preferred dealer.' } }];", 780, -80)
    e = w.return_error("ReturnDealersError", 780, 120)
    w.chain(t, g, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

SHAPE_SLOTS_JS = """// Keeps at most three slots, labels them for speech, and prepares the session update.
const r = $json;
const inp = $('ToolInput').first().json;
const s = $('LoadSession').first().json;
const max = 3;
const days = { pl: ['niedziela','poniedziałek','wtorek','środa','czwartek','piątek','sobota'], es: ['domingo','lunes','martes','miércoles','jueves','viernes','sábado'], de: ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag'], en: ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'] };
const dn = days[s.language] || days.en;
const slots = (r.slots || []).slice(0, max).map(o => { const d = new Date(o.startsAt + 'Z'); return { slotId: o.slotId, startsAt: o.startsAt, dealerId: o.dealerId, dealerName: r.dealerName, label: `${dn[d.getUTCDay()]} ${o.startsAt.slice(8,10)}.${o.startsAt.slice(5,7)}. ${o.startsAt.slice(11,16)}` }; });
const result = slots.length
  ? { ok: true, dealerId: r.dealerId, dealerName: r.dealerName, slots: slots.map(o => ({ slotId: o.slotId, when: o.label })), note: 'Offer these by day and time, never by slotId. When the caller picks one, use its slotId in BookAppointment.' }
  : { ok: true, dealerId: r.dealerId, dealerName: r.dealerName, slots: [], note: r.reason === 'dealer_not_dealerbook_enabled' ? 'This dealer cannot be booked online. Hand off with SCENARIO_REQUIRES_AGENT.' : 'No slots in this window. Try once more with a later fromDate, then hand off with NO_SLOT.' };
return [{ json: { result, offeredSlots: JSON.stringify(slots), dealerId: r.dealerId || inp.dealerId || s.dealerId, dealerName: r.dealerName || s.dealerName, sessionId: s.sessionId } }];"""

def build_ar12():
    w = WF("ar-12-find-slots", "AR-12 Tool: Find appointment slots", "Agent tool. Returns up to three bookable DealerBook slots for a dealer from a date. Inputs: sessionId (plumbed), dealerId, fromDate (YYYY-MM-DD), serviceType. Side effect: stores the offered slots on the session so BookAppointment can validate the choice.")
    w.sticky("## AR-12 FindSlots\nThe offered slots are written to the session row. BookAppointment refuses any slotId that was not offered in this call: slots are facts, not model output.", 0, -400, 760, 100)
    t = w.sub_trigger([("sessionId","string"),("dealerId","string"),("fromDate","string"),("serviceType","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    h = w.http_post("CallDealerBookSlots", MOCK + "/dealerbook/slots", "={{ JSON.stringify({ dealerId: $('ToolInput').first().json.dealerId || $json.dealerId, fromDate: $('ToolInput').first().json.fromDate || '', serviceType: $('ToolInput').first().json.serviceType || '', market: $json.market }) }}", 520, 0)
    s = w.code("ShapeSlots", SHAPE_SLOTS_JS, 780, -80)
    u = w.dt("StoreOffered", "ar_sessions", "update", 1040, -80, conditions=[("sessionId", "={{ $json.sessionId }}")],
             values={"offeredSlots": "={{ $json.offeredSlots }}", "dealerId": "={{ $json.dealerId }}", "dealerName": "={{ $json.dealerName }}"})
    rt = w.code("ReturnSlots", "return [{ json: $('ShapeSlots').first().json.result }];", 1300, -80)
    e = w.return_error("ReturnSlotsError", 780, 120)
    w.chain(t, g, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    w.chain(s, u, rt)
    return w

def build_ar13():
    w = WF("ar-13-book-appointment", "AR-13 Tool: Book appointment", "Agent tool. Books one of the slots offered in this call in DealerBook and stores the confirmation on the session. Inputs: sessionId (plumbed), slotId, serviceType, mobility, notes. Returns {ok, confirmationCode, bookingRef, when, dealerName} or {ok:false, error}. Idempotent per session and slot.")
    w.sticky("## AR-13 BookAppointment\nBookingGate is the only place a booking can start: the slot must be one offered in this call, read back in CONFIRM, and confirmed in a later turn. Books with an idempotency key, writes bookingRef and confirmationCode to the session, sets state BOOKED. The spoken confirmation is built from this result, never from the model.", 0, -400, 820, 110)
    t = w.sub_trigger([("sessionId","string"),("slotId","string"),("serviceType","string"),("mobility","string"),("notes","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    wanted = "String($('ToolInput').first().json.slotId || '').trim()"
    offered = "JSON.parse($json.offeredSlots || '[]')"
    gate = w.switch("BookingGate", [
        ("AlreadyBooked", [w.cond("={{ $json.bookingRef || '' }}", "notEmpty")], "and"),
        ("SlotNotOffered", [w.cond(f"={{{{ {offered}.some(o => o.slotId === {wanted}) }}}}", "false", kind="boolean")], "and"),
        ("NotConfirmed", [w.cond("={{ $json.state || '' }}", "notEquals", "CONFIRM"),
                          w.cond("={{ $json.slotId || '' }}", "notEquals", f"={{{{ {wanted} }}}}")], "or"),
    ], 520, 0, fallback="Proceed")
    r0 = w.set_fields("ReplyAlreadyBooked", [
        ("ok", True, "boolean"), ("already", True, "boolean"),
        ("confirmationCode", "={{ $json.confirmationCode || '' }}", "string"), ("bookingRef", "={{ $json.bookingRef }}", "string"),
        ("message", "An appointment is already booked in this call. Read the existing confirmation code back; do not book again.", "string")], 780, 140)
    r1 = w.set_fields("ReplySlotNotOffered", [
        ("ok", False, "boolean"), ("error", "slot_not_offered", "string"),
        ("message", "slotId is not one of the slots offered in this call. Call FindSlots and use one of its slotIds.", "string"),
        ("offeredSlotIds", f"={{{{ {offered}.map(o => o.slotId) }}}}", "array")], 780, 280)
    r2 = w.set_fields("ReplyNotConfirmed", [
        ("ok", False, "boolean"), ("error", "not_confirmed", "string"),
        ("message", "The caller has not confirmed this slot yet. Read back dealer, day and time with this slotId in the slotId field (state CONFIRM), and call BookAppointment only in the next turn after the caller says yes.", "string"),
        ("pendingSlotId", "={{ $json.slotId || '' }}", "string")], 780, 420)
    req = w.set_fields("BookingRequest", [
        ("slot", f"={{{{ {offered}.find(o => o.slotId === {wanted}) }}}}", "object"),
        ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId }}", "string"),
        ("vehicleRef", "={{ $json.vehicleRef || '' }}", "string"),
        ("serviceType", "={{ $('ToolInput').first().json.serviceType || '' }}", "string"),
        ("mobility", "={{ $('ToolInput').first().json.mobility || '' }}", "string"),
        ("notes", "={{ String($('ToolInput').first().json.notes || '').slice(0, 200) }}", "string"),
        ("idempotencyKey", f"={{{{ $json.sessionId + ':' + {wanted} }}}}", "string")], 780, 0)
    h = w.http_post("CallDealerBookBook", MOCK + "/dealerbook/book", "={{ JSON.stringify({ slotId: $json.slot.slotId, dealerId: $json.slot.dealerId, vin: $json.vehicleRef, serviceType: $json.serviceType, mobility: $json.mobility, notes: $json.notes, idempotencyKey: $json.idempotencyKey, correlationId: $json.correlationId }) }}", 1040, -100)
    u = w.dt("StoreBooking", "ar_sessions", "update", 1300, -180, conditions=[("sessionId", "={{ $('BookingRequest').first().json.sessionId }}")],
             values={"bookingRef": "={{ $json.bookingRef }}", "confirmationCode": "={{ $json.confirmationCode }}", "slotId": "={{ $json.slotId }}", "state": "BOOKED", "intent": "BOOK", "confirmationChannel": "sms"})
    rb = w.set_fields("ReturnBooking", [
        ("ok", True, "boolean"),
        ("confirmationCode", "={{ $('CallDealerBookBook').first().json.confirmationCode }}", "string"),
        ("bookingRef", "={{ $('CallDealerBookBook').first().json.bookingRef }}", "string"),
        ("when", "={{ $('BookingRequest').first().json.slot.label }}", "string"),
        ("dealerName", "={{ $('BookingRequest').first().json.slot.dealerName || '' }}", "string"),
        ("serviceType", "={{ $('BookingRequest').first().json.serviceType }}", "string"),
        ("mobility", "={{ $('BookingRequest').first().json.mobility }}", "string"),
        ("note", "Booked. Read the confirmation code back to the caller slowly and tell them a confirmation message will follow.", "string")], 1560, -180)
    e = w.return_error("ReturnBookError", 1300, 0)
    w.chain(t, g, gate)
    w.link(gate, r0, 0); w.link(gate, r1, 1); w.link(gate, r2, 2); w.link(gate, req, 3)
    w.link(req, h)
    w.link(h, u, 0)
    w.link(h, e, 1)
    w.link(u, rb)
    return w

BUILD_UUI_JS = """// Builds the contact-center return context. Positional payload, version first, correlation ID never truncated.
const inp = $('ToolInput').first().json;
const s = $('LoadSession').first().json;
const m = $json;
const I = ({ BOOK: 'B', RESCHEDULE: 'R', CANCEL: 'C', OTHER: 'O', UNKNOWN: 'U' })[inp.intent] || 'U';
const RR = ({ CALLER_REQUEST: 'CR', SCENARIO_REQUIRES_AGENT: 'SA', NOT_UNDERSTOOD: 'NU', BACKEND_UNAVAILABLE: 'BU', IDENTIFICATION_FAILED: 'IF', NO_SLOT: 'NS', POLICY: 'PO', PLATFORM_ERROR: 'PE' })[inp.handoffReason] || 'PE';
const lang = s.language || m.defaultLanguage || 'en';
const base = ['1', s.correlationId || '', s.sessionId || '', I, RR, lang];
const withCase = s.caseId ? base.concat([s.caseId]) : base;
const MAX_ASCII = 128, MAX_HEX_BYTES = 64;
const truncated = [];
let ascii = withCase.join(';');
if (ascii.length > MAX_ASCII) { ascii = base.join(';'); truncated.push('ascii:caseId'); }
let hexSrc = withCase.join(';');
if (Buffer.byteLength(hexSrc, 'utf8') > MAX_HEX_BYTES) { hexSrc = base.join(';'); truncated.push('hex:caseId'); }
if (Buffer.byteLength(hexSrc, 'utf8') > MAX_HEX_BYTES) truncated.push('hex:over_budget_correlationId_kept');
const hex = Buffer.from(hexSrc, 'utf8').toString('hex').toUpperCase();
const pd = '00';
const addr = String(m.returnNumber || 'sip:111102@cc.invalid').replace(/^sip:/, '');
const referTo = `<sip:${addr}?User-to-User=${hex}%3Bpd%3D${pd}%3Bencoding%3Dhex>`;
const referToAscii = `<sip:${addr}?User-to-User=${encodeURIComponent(ascii)}%3Bencoding%3Dascii>`;
return [{ json: { ok: true, sessionId: s.sessionId, correlationId: s.correlationId, caseId: s.caseId || '', intent: inp.intent || 'UNKNOWN', handoffReason: inp.handoffReason || 'PLATFORM_ERROR', uuiPayload: hexSrc, uuiAscii: ascii, uuiHex: hex, pd, referTo, referToAscii, truncated, summary: String(inp.summary || '').slice(0, 500) } }];"""

def build_ar14():
    w = WF("ar-14-build-handoff", "AR-14 Build handoff context", "Builds the SIP REFER target with the return UUI (version, correlationId, sessionId, intent, handoffReason, language, caseId when it fits) and marks the session HANDOFF. Inputs: sessionId, intent, handoffReason, summary. Returns {ok, referTo, uuiHex, uuiAscii, uuiPayload, truncated}. Deterministic; called by AR-01 and AR-02, never by the model.")
    w.sticky("## AR-14 Build handoff\nThe one place that knows the contact-center UUI format. Hex budget 64 bytes, ASCII 128. The correlation ID is never truncated; the case ID is dropped first and logged.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId","string"),("intent","string"),("handoffReason","string"),("summary","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    m = w.dt("LoadMarket", "ar_markets", "get", 520, 0, conditions=[("market", "={{ $json.market }}")])
    b = w.code("BuildUui", BUILD_UUI_JS, 780, 0)
    u = w.dt("StoreHandoff", "ar_sessions", "update", 1040, 0, conditions=[("sessionId", "={{ $json.sessionId }}")],
             values={"state": "HANDOFF", "handoffReason": "={{ $json.handoffReason }}", "handoffSummary": "={{ $json.summary }}", "intent": "={{ $json.intent }}"})
    r = w.code("ReturnHandoff", "return [{ json: $('BuildUui').first().json }];", 1300, 0)
    w.chain(t, g, m, b, u, r)
    return w

def build_ar15():
    w = WF("ar-15-case-system", "AR-15 Case create, update or close", "Writes the case. Inputs: op (create|update|close), correlationId, caseId, subject, classification, status, solution, delegatedReason. Returns {ok, caseId, status} or {ok:false, error}.")
    w.sticky("## AR-15 Case\nOne case per call, created at session start, updated at the end. A standard REST API behind the mock.", 0, -400, 700, 100)
    t = w.sub_trigger([("op","string"),("correlationId","string"),("caseId","string"),("subject","string"),("classification","string"),("status","string"),("solution","string"),("delegatedReason","string")])
    h = w.http_post("CallCaseSystem", MOCK + "/casesys/case", "={{ JSON.stringify($json) }}", 260, 0)
    s = w.code("ShapeCase", "const r = $json; return [{ json: { ok: true, caseId: r.caseId, status: r.status } }];", 520, -80)
    e = w.return_error("ReturnCaseError", 520, 120)
    w.chain(t, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

def build_ar16():
    w = WF("ar-16-send-confirmation", "AR-16 Send booking confirmation", "Sends the SMS or email confirmation through the communication service. Inputs: channel, customerRef, language, text, correlationId. Returns {ok, messageId} or {ok:false, error}.")
    w.sticky("## AR-16 Send confirmation\nThe service resolves the destination from the CRM reference. n8n holds no phone number or e-mail address.", 0, -400, 700, 100)
    t = w.sub_trigger([("channel","string"),("customerRef","string"),("language","string"),("text","string"),("correlationId","string")])
    h = w.http_post("CallComms", MOCK + "/comms/send", "={{ JSON.stringify($json) }}", 260, 0)
    s = w.code("ShapeComms", "const r = $json; return [{ json: { ok: !!r.accepted, messageId: r.messageId || '', channel: r.channel || '' } }];", 520, -80)
    e = w.return_error("ReturnCommsError", 520, 120)
    w.chain(t, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

# =============================================================================
# AR-01 session start
# =============================================================================
PARSE_START_JS = """// Validates the gateway's session/start call and parses the inbound UUI. The raw string wins over pre-parsed fields.
const b = $json.body || {};
const u = b.uui || {};
const raw = String(u.raw || '');
const parts = raw ? raw.split(';') : [];
const version = String(parts[0] || u.version || '').trim();
const correlationId = String(parts[1] || b.correlationId || '').trim();
const vin = String(parts[2] || u.vin || '').trim().toUpperCase();
const msisdn = String(parts[3] || u.msisdn || '').trim();
const language = String(parts[4] || u.language || '').trim().toLowerCase().slice(0, 2);
const errors = [];
if (!correlationId) errors.push('correlationId missing');
if (version !== '1') errors.push('unsupported uui version: ' + (version || 'none'));
const byPrefix = [['+48', 'PL'], ['+34', 'ES'], ['+49', 'DE'], ['+44', 'GB']];
let market = String(b.market || '').toUpperCase();
if (!market) { const hit = byPrefix.find(([p]) => msisdn.startsWith(p)); market = hit ? hit[1] : ''; }
if (!market) market = ({ pl: 'PL', es: 'ES', de: 'DE', en: 'GB' })[language] || 'PL';
return [{ json: { valid: errors.length === 0, statusCode: errors.length ? 400 : 200, error: errors.length ? 'validation_error' : null, message: errors.join('; ') || null,
  correlationId, version, vin, msisdn, language, market, uuiRaw: raw, sipCallId: (b.sip || {}).callId || '', gatewayVendor: (b.gateway || {}).vendor || '', startedAt: Date.now() } }];"""

COMPOSE_SESSION_JS = """// Creates the session row content, the greeting and the first instruction for the gateway.
const p = $('ParseStart').first().json;
const m = $('LoadMarket').first().json;
const c = $('IdentifyCustomer').first().json;
const k = $('CreateCase').first().json;
const alphabet = 'ABCDEFGHJKMNPQRSTVWXYZ23456789';
let sessionId = ''; for (let i = 0; i < 8; i++) sessionId += alphabet[Math.floor(Math.random() * alphabet.length)];
const langs = String(m.languages || '').split(',').map(x => x.trim());
const language = p.language && langs.includes(p.language) ? p.language : (m.defaultLanguage || 'en');
const disclosure = m['disclosure_' + language] || m.disclosure_en || '';
const known = !!(c.ok && c.known);
const name = known ? c.greetingName : '';
const veh = known ? c.vehicleLabel : '';
const greetings = {
  pl: `Dzień dobry${name ? ', ' + name : ''}. W czym mogę pomóc w związku z wizytą serwisową${veh ? ' dla Państwa ' + veh : ''}?`,
  es: `Buenos días${name ? ', ' + name : ''}. ¿En qué puedo ayudarle con su cita de taller${veh ? ' para su ' + veh : ''}?`,
  de: `Guten Tag${name ? ', ' + name : ''}. Wie kann ich Ihnen bei Ihrem Werkstatttermin${veh ? ' für Ihren ' + veh : ''} helfen?`,
  en: `Hello${name ? ', ' + name : ''}. How can I help with your service appointment${veh ? ' for your ' + veh : ''}?`
};
const backendDown = c.ok === false || k.ok === false;
return [{ json: { sessionId, correlationId: p.correlationId, caseId: k.caseId || '', market: m.market || p.market, language,
  state: backendDown ? 'HANDOFF' : 'IDENTIFY', intent: 'UNKNOWN', customerRef: known ? c.customerRef : '', vehicleRef: p.vin,
  greetingName: name, vehicleLabel: veh, dealerId: known ? (c.preferredDealerId || '') : '', dealerName: known ? (c.preferredDealerName || '') : '',
  offeredSlots: '[]', turns: 0, startedAt: new Date(p.startedAt).toISOString(), known, backendDown,
  handoffReason: backendDown ? 'BACKEND_UNAVAILABLE' : '',
  speak: backendDown ? (m.handoverLine || '') : (disclosure + ' ' + (greetings[language] || greetings.en)).trim() } }];"""

SHAPE_START_JS = """// Response contract for session/start (section 4.1 of the spec).
const s = $('ComposeSession').first().json;
let handoff = null;
if (s.backendDown) { try { const h = $('BuildStartHandoff').first().json; handoff = { intent: 'UNKNOWN', handoffReason: 'BACKEND_UNAVAILABLE', caseId: s.caseId, uuiPayload: h.uuiPayload, uuiHex: h.uuiHex, uuiAscii: h.uuiAscii, referTo: h.referTo }; } catch (e) { handoff = { intent: 'UNKNOWN', handoffReason: 'BACKEND_UNAVAILABLE', caseId: s.caseId, error: 'handoff_build_failed' }; } }
return [{ json: { sessionId: s.sessionId, correlationId: s.correlationId, caseId: s.caseId, market: s.market, language: s.language, state: s.state,
  customer: { known: s.known, greetingName: s.greetingName, vehicle: s.vehicleLabel }, speak: s.speak, action: s.backendDown ? 'handoff' : 'continue', handoff, listen: !s.backendDown, latencyMs: Date.now() - new Date(s.startedAt).getTime() } }];"""

def build_ar01():
    w = WF("ar-01-session-start", "AR-01 API: Session start", "POST /webhook/ar/v1/session/start. Parses the contact-center UUI, identifies the customer, opens the case, creates the session and returns the AI disclosure plus greeting. Idempotent on correlationId.")
    w.sticky("## AR-01 Session start\nGateway calls this once the INVITE is answered. Header auth. Re-delivery of a known correlationId returns the existing session without speaking again.", 0, -400, 820, 110)
    a = w.webhook("ReceiveStart", "ar/v1/session/start", 0, 0)
    p = w.code("ParseStart", PARSE_START_JS, 260, 0)
    iv = w.if_node("IsValid", "={{ $json.valid }}", 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: $json.error, message: $json.message }) }}", 780, 200, code="={{ $json.statusCode }}")
    ex = w.dt("CheckExisting", "ar_sessions", "get", 780, -100, conditions=[("correlationId", "={{ $json.correlationId }}")])
    ie = w.if_node("AlreadyStarted", "={{ $json.sessionId }}", 1040, -100, kind="string")
    ce = w.code("ShapeExisting", "const s = $json;\nreturn [{ json: { sessionId: s.sessionId, correlationId: s.correlationId, caseId: s.caseId, market: s.market, language: s.language, state: s.state, customer: { known: !!s.customerRef, greetingName: s.greetingName, vehicle: s.vehicleLabel }, speak: '', action: 'continue', handoff: null, listen: true, duplicate: true } }];", 1300, -260)
    rdup = w.respond("RespondExisting", "={{ JSON.stringify($json) }}", 1560, -260)
    lm = w.dt("LoadMarket", "ar_markets", "get", 1300, -40, conditions=[("market", "={{ $('ParseStart').first().json.market }}")])
    ic = w.exec_wf("IdentifyCustomer", "ar-10-identify-customer", {"vin": "={{ $('ParseStart').first().json.vin }}", "msisdn": "={{ $('ParseStart').first().json.msisdn }}", "correlationId": "={{ $('ParseStart').first().json.correlationId }}"}, 1560, -40)
    cc = w.exec_wf("CreateCase", "ar-15-case-system", {"op": "create", "correlationId": "={{ $('ParseStart').first().json.correlationId }}", "caseId": "", "subject": "Appointment booking via voice assistant", "classification": "Appointment Booking", "status": "In Progress", "solution": "", "delegatedReason": ""}, 1820, -40)
    cs = w.code("ComposeSession", COMPOSE_SESSION_JS, 2080, -40)
    ins = w.dt("InsertSession", "ar_sessions", "insert", 2340, -40, always=False,
               values={c: f"={{{{ $json.{c} }}}}" for c in ["sessionId","correlationId","caseId","market","language","state","intent","customerRef","vehicleRef","greetingName","vehicleLabel","dealerId","dealerName","offeredSlots","turns","startedAt","handoffReason"]})
    lg = w.exec_wf("LogStart", "ar-17-log-event", {"correlationId": "={{ $('ComposeSession').first().json.correlationId }}", "sessionId": "={{ $('ComposeSession').first().json.sessionId }}", "caseId": "={{ $('ComposeSession').first().json.caseId }}", "workflow": "AR-01", "event": "session_start", "state": "={{ $('ComposeSession').first().json.state }}", "tool": "", "latencyMs": "={{ Date.now() - new Date($('ComposeSession').first().json.startedAt).getTime() }}", "outcome": "={{ $('ComposeSession').first().json.backendDown ? 'handoff' : 'continue' }}", "errorCode": "={{ $('ComposeSession').first().json.backendDown ? 'backend_unavailable' : '' }}", "detail": "={{ 'known=' + $('ComposeSession').first().json.known + ' market=' + $('ComposeSession').first().json.market + ' lang=' + $('ComposeSession').first().json.language }}"}, 2600, -40)
    ib = w.if_node("BackendDown", "={{ $('ComposeSession').first().json.backendDown }}", 2860, -40)
    bh = w.exec_wf("BuildStartHandoff", "ar-14-build-handoff", {"sessionId": "={{ $('ComposeSession').first().json.sessionId }}", "intent": "UNKNOWN", "handoffReason": "BACKEND_UNAVAILABLE", "summary": "Backend unavailable at session start"}, 3120, -140)
    sh = w.code("ShapeStart", SHAPE_START_JS, 3380, -40)
    rs = w.respond("RespondStart", "={{ JSON.stringify($json) }}", 3640, -40)
    w.chain(a, p, iv)
    w.link(iv, ex, 0); w.link(iv, r400, 1)
    w.link(ex, ie)
    w.link(ie, ce, 0); w.link(ce, rdup)
    w.link(ie, lm, 1)
    w.chain(lm, ic, cc, cs, ins, lg, ib)
    w.link(ib, bh, 0); w.link(bh, sh)
    w.link(ib, sh, 1)
    w.link(sh, rs)
    return w

# =============================================================================
# AR-02 dialogue turn
# =============================================================================
VALIDATE_TURN_JS = """const b = $json.body || {};
const errors = [];
if (!b.sessionId) errors.push('sessionId missing');
if (typeof b.transcript !== 'string') errors.push('transcript missing');
return [{ json: { valid: !errors.length, statusCode: errors.length ? 400 : 200, error: errors.length ? 'validation_error' : null, message: errors.join('; ') || null,
  sessionId: String(b.sessionId || ''), correlationId: String(b.correlationId || ''), transcript: String(b.transcript || '').trim(), language: String(b.language || '').slice(0, 2),
  asrConfidence: typeof b.asrConfidence === 'number' ? b.asrConfidence : null, turn: b.turn || null, startedAt: Date.now() } }];"""

PREPARE_TURN_JS = r"""// Assembles the facts and the goal for this turn. The system prompt is rebuilt every turn from the session row, so state lives in n8n, not in the model.
const req = $('ValidateTurn').first().json;
const s = $('LoadSession').first().json;
const m = $('LoadMarket').first().json;
const lang = s.language || m.defaultLanguage || 'en';
const langName = ({ pl: 'Polish', es: 'Spanish', de: 'German', en: 'English' })[lang] || 'English';
let offered = []; try { offered = JSON.parse(s.offeredSlots || '[]'); } catch (e) { offered = []; }
const slotLines = offered.length ? offered.map(o => `- slotId ${o.slotId}: ${o.label} at ${o.dealerName || s.dealerName}`).join('\n') : '(none offered yet)';
const lowConf = req.asrConfidence !== null && req.asrConfidence < 0.5;
const goals = {
  IDENTIFY: 'Confirm you are speaking with the customer named in the facts about the vehicle named in the facts, in one short question. Never read out a VIN or a phone number. If the caller says it is not them, or no customer is known, set action handoff with handoffReason IDENTIFICATION_FAILED. When confirmed, move to CONSENT.',
  CONSENT: 'The AI disclosure was already spoken. Ask once whether the caller is happy to continue with the assistant. Yes: move to NEED. No: set action handoff with handoffReason POLICY.',
  NEED: 'Find out what the caller needs: a new appointment (intent BOOK), moving an existing one (RESCHEDULE), cancelling (CANCEL) or something else (OTHER, then action handoff with SCENARIO_REQUIRES_AGENT). For BOOK, collect the service type (inspection, regular service, tyre change, repair, warning light, other) and whether replacement mobility is needed, then move to DEALER.',
  DEALER: 'Propose the preferred dealer from the facts; it can be booked here. If the caller wants a different one, ask for the city and call FindDealers. Only a dealer that FindDealers returned with bookable false cannot be booked here: then set action handoff with SCENARIO_REQUIRES_AGENT. When a dealer is agreed, move to SLOT and call FindSlots.',
  SLOT: 'Call FindSlots for the agreed dealer and the earliest date the caller wants, then offer at most three options by day and time, never by slotId. If none fits, call FindSlots once more with a later fromDate; if still none, set action handoff with NO_SLOT. When the caller picks one, move to CONFIRM.',
  CONFIRM: 'Read back dealer, day, time, service type and mobility, put the slotId of that option in the slotId field, and ask for an explicit yes. Booking is only possible in the turn after the read-back, once the caller has said yes: then call BookAppointment with the same slotId. If BookAppointment answers not_confirmed, read the slot back again and ask for a yes. Never say the appointment is booked before BookAppointment returns a confirmation code. After a confirmation code, move to BOOKED and read the code back.',
  BOOKED: 'Tell the caller the confirmation code slowly, that a confirmation message will follow, ask if there is anything else. If nothing else, say goodbye with action end and state WRAPUP.',
  WRAPUP: 'Close politely with action end.',
  HANDOFF: 'Say a colleague will take over and set action handoff.'
};
const rowState = s.state || 'IDENTIFY';
// Deterministic yes/no gates for IDENTIFY and CONSENT, so a small model cannot loop on a confirmed question.
const utter = String(req.transcript || '').trim().toLowerCase();
const saidYes = /^(tak|zgadza|owszem|dok[łl]adnie|to ja|oczywi[śs]cie|jasne|s[íi]|claro|correcto|as[íi] es|soy yo|exacto|vale|de acuerdo|por supuesto|ja|genau|richtig|korrekt|das bin ich|stimmt|nat[üu]rlich|gerne|in ordnung|yes|yeah|yep|correct|that'?s me|that is me|sure|of course|go ahead|ok(ay)?)(?=[\s,.!?;:]|$)/.test(utter);
const saidNo = /^(nie|no|nein|not me|wrong)(?=[\s,.!?;:]|$)/.test(utter);
let gateAdvanced = false; let gateDecline = '';
let state = rowState;
if (rowState === 'IDENTIFY' && saidYes) { state = 'CONSENT'; gateAdvanced = true; }
if (rowState === 'CONSENT' && saidYes) { state = 'NEED'; gateAdvanced = true; }
if (rowState === 'DEALER' && saidYes && s.dealerId) { state = 'SLOT'; gateAdvanced = true; }
if (rowState === 'IDENTIFY' && (saidNo || !s.customerRef)) gateDecline = 'IDENTIFICATION_FAILED';
if (rowState === 'CONSENT' && saidNo) gateDecline = 'POLICY';
const facts = [
  gateAdvanced ? `The caller has just ${rowState === 'IDENTIFY' ? 'confirmed their identity' : rowState === 'CONSENT' ? 'agreed to continue with the assistant' : `agreed to the dealer ${s.dealerName} (dealerId ${s.dealerId}); call FindSlots for it now`}. Do not ask that again; follow the current goal.` : '',
  `Session state: ${state}.`,
  `Customer known: ${s.customerRef ? 'yes' : 'no'}.`,
  s.greetingName ? `Name to address the customer: ${s.greetingName}.` : '',
  s.vehicleLabel ? `Vehicle: ${s.vehicleLabel}.` : '',
  s.dealerName ? `Preferred dealer: ${s.dealerName} (dealerId ${s.dealerId}).` : 'Preferred dealer: none on file.',
  `Market: ${s.market}. Online booking enabled in this market: ${String(m.bookable) === 'false' ? 'no' : 'yes'}.`,
  s.bookingRef ? `Booking already made in this call: confirmation code ${s.confirmationCode}.` : 'No booking made yet in this call.',
  `Offered slots in this call:\n${slotLines}`,
  `Today is ${new Date().toISOString().slice(0, 10)} (UTC).`
].filter(Boolean).join('\n');
const systemPrompt = `You are the workshop service appointment assistant on a live phone call. Speak ${langName} only. Keep every reply under 45 words, one question at a time, no lists, no markdown; say numbers, dates and times the way a person would on the phone.

Current goal (state ${state}): ${goals[state] || goals.NEED}

Facts you may rely on:
${facts}

Rules:
- Never invent a dealer, a time slot, a confirmation code or any customer data. Dealers come from FindDealers, slots from FindSlots, confirmation codes from BookAppointment.
- Never say an appointment is booked, confirmed or reserved unless BookAppointment returned a confirmation code in this call.
- If the caller asks for a person at any point, set action handoff with handoffReason CALLER_REQUEST.
- If you could not understand the caller twice in a row, set action handoff with handoffReason NOT_UNDERSTOOD.
- If a tool reports it is unavailable, apologise once and set action handoff with handoffReason BACKEND_UNAVAILABLE.
- Move the state forward only when its goal is met. Allowed states: IDENTIFY, CONSENT, NEED, DEALER, SLOT, CONFIRM, BOOKED, WRAPUP, HANDOFF.
- Return only json matching the schema. The speak field is read aloud to the caller.${lowConf ? '\n- The speech recogniser reported low confidence for this utterance; if it is unclear, ask the caller to repeat.' : ''}`;
return [{ json: { sessionId: s.sessionId, correlationId: s.correlationId, caseId: s.caseId, market: s.market, language: lang, state, chatInput: req.transcript || '(silence)', systemPrompt, startedAt: req.startedAt, turn: (Number(s.turns) || 0) + 1, lowConf, handoverLine: m.handoverLine || '', offeredSlotIds: offered.map(o => o.slotId), pendingSlotId: s.slotId || '', gateAdvanced, gateDecline } }];"""

# Phrase patterns for the guard rules. Matched against the lower-cased text with the If node's "matches regex" operator.
ASKS_FOR_HUMAN = r"(\b(agent|human|person|operator|someone real|real person)\b|konsultant|cz[łl]owiek|pracownik|doradc|persona|humano|agente|operador|mitarbeiter|mensch|berater|echte person)"
DECLINES_HUMAN = r"\b(no|nie|nein|not|kein)\b[^.]{0,12}(agent|human|person|konsultant|persona|mitarbeiter)"
CLAIMS_BOOKING = r"((is|has been|was|are|'s) (now |all )?(booked|confirmed|reserved|scheduled)|confirmation code|zosta[łl][ao]? (zarezerwowan|potwierdzon|umówion)|kod potwierdzenia|(ha sido|está|queda) (reservad|confirmad)|código de confirmación|(ist|wurde) (gebucht|bestätigt|reserviert)|bestätigungscode)"

NOT_BOOKED_LINE = {"pl": "Jeszcze nie zarezerwowałem terminu. Czy mam go teraz zarezerwować?", "es": "Todavía no he reservado la cita. ¿Quiere que la reserve ahora?", "de": "Ich habe den Termin noch nicht gebucht. Soll ich ihn jetzt buchen?", "en": "I have not booked the appointment yet. Shall I book it now?"}
REPEAT_LINE = {"pl": "Przepraszam, czy mogą Państwo powtórzyć?", "es": "Disculpe, ¿puede repetirlo?", "de": "Entschuldigung, können Sie das wiederholen?", "en": "Sorry, could you repeat that?"}
TECH_ERROR_LINE = {"pl": "Przepraszam, mam problem techniczny. Połączę Państwa z konsultantem.", "es": "Disculpe, tengo un problema técnico. Le paso con un agente.", "de": "Entschuldigung, ich habe ein technisches Problem. Ich verbinde Sie mit einem Mitarbeiter.", "en": "Sorry, I have a technical problem. I will connect you to a colleague."}

# Fields every guard-chain result carries into the response stage.
GUARD_RESULT_FIELDS = ["sessionId", "correlationId", "caseId", "language", "speak", "action", "state", "intent", "handoffReason", "summary",
                       "guards", "needsHandoff", "turn", "startedAt", "bookingRef", "confirmationCode", "pendingSlotId", "errorCode"]


def add_guards(w, src):
    """The deterministic guards on the model's structured output, as named If + Edit Fields pairs. Runs after the session
    was re-read, so a booking made by a tool in this turn is visible. src: the ReloadSession node. Returns the result node."""
    P = "$('PrepareTurn').first().json"
    A = "$('DialogueAgent').first().json.output"
    ro = w.set_fields("ReadOutput", [
        ("out", f"={{{{ typeof {A} === 'string' ? {{ speak: {A} }} : ({A} || {{}}) }}}}", "object"),
        ("sessionId", f"={{{{ {P}.sessionId }}}}", "string"), ("correlationId", f"={{{{ {P}.correlationId }}}}", "string"),
        ("caseId", f"={{{{ {P}.caseId || '' }}}}", "string"), ("language", f"={{{{ {P}.language }}}}", "string"),
        ("turn", f"={{{{ {P}.turn }}}}", "number"), ("startedAt", f"={{{{ {P}.startedAt }}}}", "number"),
        ("prepState", f"={{{{ {P}.state }}}}", "string"), ("gateDecline", f"={{{{ {P}.gateDecline || '' }}}}", "string"),
        ("gateAdvanced", f"={{{{ {P}.gateAdvanced === true }}}}", "boolean"), ("handoverLine", f"={{{{ {P}.handoverLine || '' }}}}", "string"),
        ("chatInput", f"={{{{ String({P}.chatInput || '').toLowerCase() }}}}", "string"),
        ("offeredSlotIds", f"={{{{ {P}.offeredSlotIds || [] }}}}", "array"), ("prevPendingSlotId", f"={{{{ {P}.pendingSlotId || '' }}}}", "string"),
        ("booked", "={{ !!$json.bookingRef }}", "boolean"), ("bookingRef", "={{ $json.bookingRef || '' }}", "string"),
        ("confirmationCode", "={{ $json.confirmationCode || '' }}", "string"),
    ], 0, 0)
    cl = w.set_fields("ClampOutput", [
        ("speak", "={{ String($json.out.speak || '').trim() }}", "string"),
        ("action", "={{ ['continue','handoff','end'].includes($json.out.action) ? $json.out.action : 'continue' }}", "string"),
        ("state", f"={{{{ {STATES_JS}.includes($json.out.state) ? $json.out.state : $json.prepState }}}}", "string"),
        ("intent", f"={{{{ {INTENTS_JS}.includes($json.out.intent) ? $json.out.intent : 'UNKNOWN' }}}}", "string"),
        ("handoffReason", f"={{{{ {REASONS_JS}.includes($json.out.handoffReason) ? $json.out.handoffReason : '' }}}}", "string"),
        ("summary", "={{ String($json.out.summary || '').slice(0, 500) }}", "string"),
        ("slotId", "={{ $json.out.slotId ? String($json.out.slotId) : '' }}", "string"),
        ("guards", "={{ [] }}", "array"),
    ], 0, 0, keep=True)
    w.chain(src, ro, cl)
    booked_false = w.cond("={{ $json.booked }}", "false", kind="boolean")
    srcs = w.guard_chain([(cl, 0)], [
        ("GateDecline", [w.cond("={{ $json.gateDecline }}", "notEmpty"), booked_false, w.cond("={{ $json.action }}", "notEquals", "handoff")], "and",
         "OnGateDecline", [("action", "handoff", "string"), ("handoffReason", "={{ $json.gateDecline }}", "string"), ("guards", with_guard("gate_decline"), "array")]),
        ("GateAdvance", [w.cond("={{ $json.gateAdvanced }}", "true", kind="boolean"), w.cond("={{ $json.action }}", "equals", "continue"),
                         w.cond(f"={{{{ {STATES_JS}.indexOf($json.state) < {STATES_JS}.indexOf($json.prepState) }}}}", "true", kind="boolean")], "and",
         "OnGateAdvance", [("state", "={{ $json.prepState }}", "string"), ("guards", with_guard("gate_advance"), "array")]),
        ("ScenarioDowngrade", [w.cond("={{ $json.gateAdvanced }}", "true", kind="boolean"), w.cond("={{ $json.action }}", "equals", "handoff"),
                               w.cond("={{ $json.handoffReason }}", "equals", "SCENARIO_REQUIRES_AGENT")], "and",
         "OnScenarioDowngrade", [("action", "continue", "string"), ("state", "={{ $json.prepState }}", "string"), ("handoffReason", "", "string"),
                                 ("speak", "", "string"), ("guards", with_guard("scenario_downgrade"), "array")]),
        ("CallerAsksForHuman", [w.cond("={{ $json.chatInput }}", "regex", ASKS_FOR_HUMAN), w.cond("={{ $json.chatInput }}", "notRegex", DECLINES_HUMAN),
                                w.cond("={{ $json.action }}", "notEquals", "handoff"), booked_false], "and",
         "OnCallerRequest", [("action", "handoff", "string"), ("handoffReason", "CALLER_REQUEST", "string"), ("guards", with_guard("caller_request_rule"), "array")]),
    ])
    slot_set = "$json.state === 'CONFIRM' && $json.slotId && $json.offeredSlotIds.includes($json.slotId)"
    ps = w.set_fields("PendingSlot", [
        ("pendingSlotId", f"={{{{ ({slot_set}) ? $json.slotId : (($json.state !== 'CONFIRM' && !$json.booked) ? '' : $json.prevPendingSlotId) }}}}", "string"),
        ("guards", with_guard("pending_slot_set", slot_set), "array"),
    ], 0, 0, keep=True)
    for s, out in srcs:
        w.link(s, ps, out)
    srcs = w.guard_chain([(ps, 0)], [
        ("FalseConfirmation", [w.cond("={{ $json.speak.toLowerCase() }}", "regex", CLAIMS_BOOKING), booked_false], "and",
         "OnFalseConfirmation", [("speak", lang_pick(NOT_BOOKED_LINE), "string"),
                                 ("state", "={{ ['BOOKED','WRAPUP'].includes($json.state) ? 'CONFIRM' : $json.state }}", "string"),
                                 ("action", "={{ $json.action === 'end' ? 'continue' : $json.action }}", "string"),
                                 ("guards", with_guard("false_confirmation"), "array")]),
        ("ForceBooked", [w.cond("={{ $json.booked }}", "true", kind="boolean"),
                         w.cond("={{ ['BOOKED','WRAPUP','HANDOFF'].includes($json.state) }}", "false", kind="boolean")], "and",
         "OnForceBooked", [("state", "BOOKED", "string"), ("guards", with_guard("state_forced_booked"), "array")]),
        ("EmptyReply", [w.cond("={{ $json.speak }}", "empty")], "and",
         "OnEmptyReply", [("speak", lang_pick(REPEAT_LINE), "string"), ("guards", with_guard("empty_reply"), "array")]),
        ("ReplyTooLong", [w.cond("={{ $json.speak.length }}", "gt", 400, kind="number")], "and",
         "OnReplyTooLong", [("speak", "={{ $json.speak.slice(0, 397) + '...' }}", "string"), ("guards", with_guard("reply_truncated"), "array")]),
        ("HandoffNormalise", [w.cond("={{ $json.action }}", "equals", "handoff")], "and",
         "OnHandoff", [("state", "HANDOFF", "string"), ("handoffReason", "={{ $json.handoffReason || 'CALLER_REQUEST' }}", "string"),
                       ("speak", "={{ $json.handoverLine || $json.speak }}", "string"),
                       ("guards", "={{ $json.guards.concat($json.handoffReason ? [] : ['reason_defaulted']).concat($json.handoverLine ? ['handover_line'] : []) }}", "array")]),
        ("ForceHandoff", [w.cond("={{ $json.state }}", "equals", "HANDOFF"), w.cond("={{ $json.action }}", "notEquals", "handoff")], "and",
         "OnForceHandoff", [("action", "handoff", "string"), ("handoffReason", "={{ $json.handoffReason || 'SCENARIO_REQUIRES_AGENT' }}", "string"),
                            ("speak", "={{ $json.handoverLine || $json.speak }}", "string"), ("guards", with_guard("action_forced_handoff"), "array")]),
        ("EndBlocked", [w.cond("={{ $json.action }}", "equals", "end"), w.cond("={{ ['BOOKED','WRAPUP'].includes($json.state) }}", "false", kind="boolean")], "and",
         "OnEndBlocked", [("action", "continue", "string"), ("guards", with_guard("end_blocked"), "array")]),
    ])
    gr = w.set_fields("GuardResult", [
        (f, {"state": "={{ $json.action === 'end' ? 'WRAPUP' : $json.state }}",
             "needsHandoff": "={{ $json.action === 'handoff' }}",
             "errorCode": "={{ $json.guards.join(',') }}"}.get(f, f"={{{{ $json.{f} }}}}"),
         {"guards": "array", "needsHandoff": "boolean", "turn": "number", "startedAt": "number"}.get(f, "string"))
        for f in GUARD_RESULT_FIELDS], 0, 0)
    for s, out in srcs:
        w.link(s, gr, out)
    return gr


def agent_error_node(w):
    """The agent threw (model down, parser gave up). Deterministic apology and a PLATFORM_ERROR handoff; no model involved."""
    P = "$('PrepareTurn').first().json"
    fixed = {"speak": lang_pick(TECH_ERROR_LINE, f"{P}.language"), "action": "handoff", "state": "HANDOFF", "intent": "UNKNOWN", "handoffReason": "PLATFORM_ERROR",
             "summary": "={{ 'Dialogue agent failed: ' + String(($json.error || {}).message || ($json.error || {}).description || 'unknown').slice(0, 200) }}",
             "guards": "={{ ['agent_error'] }}", "needsHandoff": True, "bookingRef": "", "confirmationCode": "", "pendingSlotId": "", "errorCode": "agent_error",
             "caseId": f"={{{{ {P}.caseId || '' }}}}"}
    return w.set_fields("HandleAgentError", [
        (f, fixed.get(f, f"={{{{ {P}.{f} }}}}"), {"guards": "array", "needsHandoff": "boolean", "turn": "number", "startedAt": "number"}.get(f, "string"))
        for f in GUARD_RESULT_FIELDS], 0, 0)


def attach_handoff_expr():
    """JSON-mode expression: the guard result that went through NeedsHandoff plus the handoff object built from BuildHandoff's output."""
    G = "$('NeedsHandoff').first().json"
    return (f"={{{{ Object.assign({{}}, {G}, {{ handoff: $json.ok "
            f"? {{ intent: {G}.intent, handoffReason: {G}.handoffReason, caseId: {G}.caseId, uuiPayload: $json.uuiPayload, uuiHex: $json.uuiHex, uuiAscii: $json.uuiAscii, referTo: $json.referTo, truncated: $json.truncated, summary: {G}.summary }} "
            f": {{ intent: {G}.intent, handoffReason: {G}.handoffReason, caseId: {G}.caseId, error: $json.error || 'handoff_build_failed' }} }}) }}}}")


OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "required": ["speak", "action", "state", "intent"],
    "properties": {
        "speak": {"type": "string", "description": "What is read aloud to the caller, in the session language, under 45 words"},
        "action": {"type": "string", "enum": ["continue", "handoff", "end"]},
        "state": {"type": "string", "enum": ["IDENTIFY","CONSENT","NEED","DEALER","SLOT","CONFIRM","BOOKED","WRAPUP","HANDOFF"]},
        "intent": {"type": "string", "enum": ["BOOK","RESCHEDULE","CANCEL","OTHER","UNKNOWN"]},
        "handoffReason": {"type": ["string", "null"], "enum": ["CALLER_REQUEST","SCENARIO_REQUIRES_AGENT","NOT_UNDERSTOOD","BACKEND_UNAVAILABLE","IDENTIFICATION_FAILED","NO_SLOT","POLICY","PLATFORM_ERROR", None]},
        "slotId": {"type": ["string", "null"], "description": "In state CONFIRM: the slotId of the option being read back to the caller. Otherwise null."},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string", "description": "One sentence for a human agent: what the caller wants and where the conversation is"}
    }
}, indent=2)

def build_ar02():
    w = WF("ar-02-dialogue-turn", "AR-02 API: Dialogue turn", "POST /webhook/ar/v1/session/turn. One call per caller utterance. Loads the session, runs the dialogue agent with its three tools, applies deterministic guards, builds the handoff context when needed, updates the session and logs. Returns speak, action, state and handoff.")
    w.sticky("## AR-02 Dialogue turn\nThe brain. State lives in ar_sessions; the system prompt is rebuilt every turn. Guards after the model: no booking claim without a confirmation code, no slot that was not offered, no end before WRAPUP.", 0, -448, 896, 160)
    w.sticky("### Tools\nFindDealers, FindSlots, BookAppointment. sessionId is plumbed, never a model parameter. Handoff is not a tool: it is a deterministic consequence of action=handoff.", 1296, 560, 704, 160, color=4)
    a = w.webhook("ReceiveTurn", "ar/v1/session/turn", 0, 0)
    v = w.code("ValidateTurn", VALIDATE_TURN_JS, 260, 0)
    iv = w.if_node("IsValid", "={{ $json.valid }}", 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: $json.error, message: $json.message }) }}", 780, 200, code="={{ $json.statusCode }}")
    ls = w.dt("LoadSession", "ar_sessions", "get", 780, -100, conditions=[("sessionId", "={{ $json.sessionId }}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 1040, -100, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 1300, 60, code=404)
    lm = w.dt("LoadMarket", "ar_markets", "get", 1300, -180, conditions=[("market", "={{ $json.market }}")])
    pt = w.code("PrepareTurn", PREPARE_TURN_JS, 1560, -180)
    ag = w.add("DialogueAgent", "@n8n/n8n-nodes-langchain.agent", 3.1,
               {"promptType": "define", "text": "={{ $json.chatInput }}", "hasOutputParser": True,
                "options": {"systemMessage": "={{ $json.systemPrompt }}", "maxIterations": 8}}, 1820, -180, onError="continueErrorOutput")
    mdl = w.model("DialogueModel", 1700, 60)
    mem = w.add("TurnMemory", "@n8n/n8n-nodes-langchain.memoryPostgresChat", 1.4,
                {"sessionIdType": "customKey", "sessionKey": "={{ $('PrepareTurn').first().json.sessionId }}", "tableName": "ar_chat_memory", "contextWindowLength": 30},
                1860, 60, credentials={"postgres": CRED_PG})
    prs = w.add("TurnOutputShape", "@n8n/n8n-nodes-langchain.outputParserStructured", 1.3,
                {"schemaType": "manual", "inputSchema": OUTPUT_SCHEMA, "autoFix": True}, 2020, 60)
    fix = w.model("ParserFixerModel", 2020, 240)
    sid = "={{ $('PrepareTurn').first().json.sessionId }}"
    t1 = w.tool_wf("FindDealers", "ar-11-find-dealers",
                   "Lists dealers the caller can choose for the appointment. Use it when the caller wants a dealer other than the preferred one, or no preferred dealer is on file. Pass the city the caller names; leave city empty to list the market's dealers. Returns dealerId, name, city and whether online booking (bookable) is possible there.",
                   {"sessionId": sid, "city": "={{ $fromAI('city', 'City the caller named for the dealer, as spoken, or empty for all dealers in the market', 'string') }}"}, 1300, 380)
    t2 = w.tool_wf("FindSlots", "ar-12-find-slots",
                   "Returns up to three free appointment slots at one dealer from a given date. Call it once a dealer is agreed, and again with a later date if the caller rejects all options. Offer the returned slots by day and time; keep their slotId for BookAppointment.",
                   {"sessionId": sid, "dealerId": "={{ $fromAI('dealerId', 'dealerId of the agreed dealer, exactly as given in the facts or by FindDealers, for example PL-WAW-01', 'string') }}",
                    "fromDate": "={{ $fromAI('fromDate', 'Earliest acceptable date as YYYY-MM-DD, or empty for the earliest available', 'string') }}",
                    "serviceType": "={{ $fromAI('serviceType', 'Service type the caller needs: inspection, service, tyre change, repair, warning light or other', 'string') }}"}, 1560, 380)
    t3 = w.tool_wf("BookAppointment", "ar-13-book-appointment",
                   "Books one of the slots offered by FindSlots in this call. Call it only after the caller explicitly confirmed dealer, day and time. Returns the confirmation code to read back, or ok false with the reason (slot not offered, slot taken, backend unavailable).",
                   {"sessionId": sid, "slotId": "={{ $fromAI('slotId', 'slotId of the option the caller chose, exactly as returned by FindSlots', 'string') }}",
                    "serviceType": "={{ $fromAI('serviceType', 'Service type agreed with the caller', 'string') }}",
                    "mobility": "={{ $fromAI('mobility', 'Replacement mobility the caller asked for: none, courtesy car, pick-up, or shuttle', 'string') }}",
                    "notes": "={{ $fromAI('notes', 'Anything the dealer should know, in one sentence, or empty', 'string') }}"}, 1820, 380)
    for t in (t1, t2, t3):
        w.link(t, ag, ctype="ai_tool")
    w.link(mdl, ag, ctype="ai_languageModel")
    w.link(mem, ag, ctype="ai_memory")
    w.link(prs, ag, ctype="ai_outputParser")
    w.link(fix, prs, ctype="ai_languageModel")
    rl = w.dt("ReloadSession", "ar_sessions", "get", 2340, -260, conditions=[("sessionId", "={{ $('PrepareTurn').first().json.sessionId }}")])
    gd = add_guards(w, rl)
    he = agent_error_node(w)
    ih = w.if_node("NeedsHandoff", "={{ $json.needsHandoff }}", 2860, -260)
    bh = w.exec_wf("BuildHandoff", "ar-14-build-handoff", {"sessionId": "={{ $json.sessionId }}", "intent": "={{ $json.intent }}", "handoffReason": "={{ $json.handoffReason }}", "summary": "={{ $json.summary }}"}, 3120, -140)
    ah = w.set_json("AttachHandoff", attach_handoff_expr(), 3250, -140)
    cr = w.set_fields("ComposeResponse", [
        ("response", "={{ { sessionId: $json.sessionId, correlationId: $json.correlationId, speak: $json.speak, action: $json.action, state: $json.state, intent: $json.intent, handoff: $json.handoff || null, endReason: $json.action === 'end' ? 'completed' : null, latencyMs: Date.now() - Number($json.startedAt), turn: $json.turn, guards: $json.guards } }}", "object"),
        ("latencyMs", "={{ Date.now() - Number($json.startedAt) }}", "number"),
    ], 3380, -260, keep=True)
    us = w.dt("UpdateSession", "ar_sessions", "update", 3640, -260, conditions=[("sessionId", "={{ $json.sessionId }}")],
              values={"state": "={{ $json.state }}", "intent": "={{ $json.intent }}", "turns": "={{ $json.turn }}", "lastLatencyMs": "={{ $json.latencyMs }}", "slotId": "={{ $json.bookingRef ? undefined : ($json.pendingSlotId || '') }}"})
    lg = w.exec_wf("LogTurn", "ar-17-log-event", {"correlationId": "={{ $('ComposeResponse').first().json.correlationId }}", "sessionId": "={{ $('ComposeResponse').first().json.sessionId }}", "caseId": "={{ $('ComposeResponse').first().json.caseId }}", "workflow": "AR-02", "event": "turn", "state": "={{ $('ComposeResponse').first().json.state }}", "tool": "", "latencyMs": "={{ $('ComposeResponse').first().json.latencyMs }}", "outcome": "={{ $('ComposeResponse').first().json.action }}", "errorCode": "={{ $('ComposeResponse').first().json.errorCode || '' }}", "detail": "={{ 'turn=' + $('ComposeResponse').first().json.turn + ' intent=' + $('ComposeResponse').first().json.intent + ($('ComposeResponse').first().json.handoffReason ? ' reason=' + $('ComposeResponse').first().json.handoffReason : '') }}"}, 3900, -260)
    rs = w.respond("RespondTurn", "={{ JSON.stringify($('ComposeResponse').first().json.response) }}", 4160, -260)
    w.chain(a, v, iv)
    w.link(iv, ls, 0); w.link(iv, r400, 1)
    w.link(ls, ifound)
    w.link(ifound, lm, 0); w.link(ifound, r404, 1)
    w.chain(lm, pt, ag)
    w.link(ag, rl, 0); w.link(ag, he, 1)
    w.link(gd, ih); w.link(he, ih)
    w.link(ih, bh, 0); w.link(ih, cr, 1)
    w.chain(bh, ah, cr)
    w.chain(cr, us, lg, rs)
    return w

# =============================================================================
# AR-03 session end
# =============================================================================
DECIDE_OUTCOME_JS = """// Decides the case outcome and whether a confirmation is still owed.
const req = $('ValidateEnd').first().json;
const s = $json;
const booked = !!s.bookingRef;
let caseStatus, solution = '', delegatedReason = '';
if (booked) { caseStatus = 'Resolved'; solution = `Appointment booked via voice assistant. Confirmation code ${s.confirmationCode}. Dealer ${s.dealerName || s.dealerId}. Slot ${s.slotId}.`; }
else if (s.state === 'HANDOFF' || String(req.endReason).startsWith('handoff')) { caseStatus = 'Delegated'; delegatedReason = `Handed to agent: ${s.handoffReason || 'unknown'}. ${s.handoffSummary || ''}`.trim(); }
else { caseStatus = 'Closed'; solution = `Call ended without booking (${req.endReason}) in state ${s.state}.`; }
const needsConfirmation = booked && String(s.confirmationSent) !== 'true';
const texts = { pl: `Serwis: wizyta serwisowa potwierdzona. Kod ${s.confirmationCode}, ${s.dealerName}, termin ${s.slotId}.`, es: `Taller: cita de taller confirmada. Código ${s.confirmationCode}, ${s.dealerName}, cita ${s.slotId}.`, de: `Werkstatt: Werkstatttermin bestätigt. Code ${s.confirmationCode}, ${s.dealerName}, Termin ${s.slotId}.`, en: `Workshop: service appointment confirmed. Code ${s.confirmationCode}, ${s.dealerName}, slot ${s.slotId}.` };
return [{ json: { sessionId: s.sessionId, correlationId: s.correlationId, caseId: s.caseId, caseStatus, solution, delegatedReason, needsConfirmation, booked, language: s.language, customerRef: s.customerRef, confirmationChannel: s.confirmationChannel || 'sms', confirmationText: texts[s.language] || texts.en, endReason: req.endReason, state: s.state, durationMs: req.durationMs } }];"""

def build_ar03():
    w = WF("ar-03-session-end", "AR-03 API: Session end", "POST /webhook/ar/v1/session/end. Closes or delegates the case, sends the confirmation if owed, purges the chat memory rows for the session, finalises the session row and logs. Idempotent.")
    w.sticky("## AR-03 Session end\nCase outcome from the session state. Chat memory for the session is deleted here: no transcript survives the call.", 0, -400, 760, 100)
    a = w.webhook("ReceiveEnd", "ar/v1/session/end", 0, 0)
    v = w.code("ValidateEnd", "const b = $json.body || {};\nconst ok = !!b.sessionId;\nreturn [{ json: { valid: ok, statusCode: ok ? 200 : 400, error: ok ? null : 'validation_error', message: ok ? null : 'sessionId missing', sessionId: String(b.sessionId || ''), endReason: String(b.endReason || 'completed'), durationMs: Number(b.durationMs) || 0, startedAt: Date.now() } }];", 260, 0)
    iv = w.if_node("IsValid", "={{ $json.valid }}", 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: $json.error, message: $json.message }) }}", 780, 200, code="={{ $json.statusCode }}")
    ls = w.dt("LoadSession", "ar_sessions", "get", 780, -100, conditions=[("sessionId", "={{ $json.sessionId }}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 1040, -100, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 1300, 60, code=404)
    d = w.code("DecideOutcome", DECIDE_OUTCOME_JS, 1300, -180)
    cc = w.exec_wf("CloseCase", "ar-15-case-system", {"op": "close", "correlationId": "={{ $json.correlationId }}", "caseId": "={{ $json.caseId }}", "subject": "", "classification": "", "status": "={{ $json.caseStatus }}", "solution": "={{ $json.solution }}", "delegatedReason": "={{ $json.delegatedReason }}"}, 1560, -180)
    ic = w.if_node("NeedsConfirmation", "={{ $('DecideOutcome').first().json.needsConfirmation }}", 1820, -180)
    sc = w.exec_wf("SendConfirmation", "ar-16-send-confirmation", {"channel": "={{ $('DecideOutcome').first().json.confirmationChannel }}", "customerRef": "={{ $('DecideOutcome').first().json.customerRef }}", "language": "={{ $('DecideOutcome').first().json.language }}", "text": "={{ $('DecideOutcome').first().json.confirmationText }}", "correlationId": "={{ $('DecideOutcome').first().json.correlationId }}"}, 2080, -280)
    pm = w.add("PurgeMemory", "n8n-nodes-base.postgres", 2.7,
               {"operation": "executeQuery", "query": "DELETE FROM ar_chat_memory WHERE session_id = $1", "options": {"queryReplacement": "={{ $('DecideOutcome').first().json.sessionId }}"}},
               2340, -180, credentials={"postgres": CRED_PG}, onError="continueRegularOutput", alwaysOutputData=True)
    fs = w.dt("FinalizeSession", "ar_sessions", "update", 2600, -180, conditions=[("sessionId", "={{ $('DecideOutcome').first().json.sessionId }}")],
              values={"endedAt": "={{ $now.toUTC().toISO() }}", "endReason": "={{ $('DecideOutcome').first().json.endReason }}", "confirmationSent": "={{ $('DecideOutcome').first().json.needsConfirmation ? ($('SendConfirmation').first().json.ok === true) : ($('LoadSession').first().json.confirmationSent === true) }}", "greetingName": "", "offeredSlots": "[]"})
    lg = w.exec_wf("LogEnd", "ar-17-log-event", {"correlationId": "={{ $('DecideOutcome').first().json.correlationId }}", "sessionId": "={{ $('DecideOutcome').first().json.sessionId }}", "caseId": "={{ $('DecideOutcome').first().json.caseId }}", "workflow": "AR-03", "event": "session_end", "state": "={{ $('DecideOutcome').first().json.state }}", "tool": "", "latencyMs": "={{ Date.now() - $('ValidateEnd').first().json.startedAt }}", "outcome": "={{ $('DecideOutcome').first().json.caseStatus }}", "errorCode": "={{ $('CloseCase').first().json.ok === false ? 'case_update_failed' : '' }}", "detail": "={{ 'endReason=' + $('DecideOutcome').first().json.endReason + ' booked=' + $('DecideOutcome').first().json.booked + ' memoryPurged=' + ($('PurgeMemory').first().json.error ? 'no' : 'yes') }}"}, 2860, -180)
    rs = w.respond("RespondEnd", "={{ JSON.stringify({ ok: true, sessionId: $('DecideOutcome').first().json.sessionId, caseId: $('DecideOutcome').first().json.caseId, caseStatus: $('DecideOutcome').first().json.caseStatus, confirmationSent: $('DecideOutcome').first().json.needsConfirmation }) }}", 3120, -180)
    w.chain(a, v, iv)
    w.link(iv, ls, 0); w.link(iv, r400, 1)
    w.link(ls, ifound)
    w.link(ifound, d, 0); w.link(ifound, r404, 1)
    w.chain(d, cc, ic)
    w.link(ic, sc, 0); w.link(sc, pm)
    w.link(ic, pm, 1)
    w.chain(pm, fs, lg, rs)
    return w

# =============================================================================
# AR-04 transfer result
# =============================================================================
TRANSFER_JS = """// One retry, never a loop. contact-center requirement.
const b = $('ReceiveTransferResult').first().json.body || {};
const s = $json;
const ok = b.referAccepted !== false && /^2\\d\\d/.test(String(b.notifyStatus || ''));
const prior = String(s.handoffResult || '');
const lines = { pl: ['Chwileczkę, próbuję ponownie połączyć z konsultantem.', 'Nie udało mi się połączyć. Konsultant oddzwoni do Państwa. Do widzenia.'], es: ['Un momento, vuelvo a intentar pasarle con un agente.', 'No he podido transferirle. Un agente le devolverá la llamada. Adiós.'], de: ['Einen Moment, ich versuche die Weiterleitung noch einmal.', 'Die Weiterleitung ist fehlgeschlagen. Ein Mitarbeiter ruft Sie zurück. Auf Wiederhören.'], en: ['One moment, I am trying the transfer again.', 'I could not transfer you. A colleague will call you back. Goodbye.'] };
const L = lines[s.language] || lines.en;
let action, speak = '';
if (ok) action = 'release';
else if (!prior.startsWith('failed')) { action = 'speak_and_retry_once'; speak = L[0]; }
else { action = 'speak_and_end'; speak = L[1]; }
const handoffResult = ok ? 'completed:' + (b.notifyStatus || '') : (prior.startsWith('failed') ? 'failed_final:' : 'failed:') + (b.notifyStatus || 'unknown');
return [{ json: { sessionId: s.sessionId, correlationId: s.correlationId, caseId: s.caseId, action, speak, handoffResult, notifyStatus: String(b.notifyStatus || ''), state: s.state } }];"""

def build_ar04():
    w = WF("ar-04-transfer-result", "AR-04 API: Transfer result", "POST /webhook/ar/v1/session/transfer-result. Records the REFER NOTIFY outcome and tells the gateway what to do: release, speak and retry once, or speak and end.")
    w.sticky("## AR-04 Transfer result\nREFER acceptance is not completion. The outcome is stored on the session and the retry policy is one attempt.", 0, -400, 700, 100)
    a = w.webhook("ReceiveTransferResult", "ar/v1/session/transfer-result", 0, 0)
    ls = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.body.sessionId }}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 520, 0, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 780, 160, code=404)
    d = w.code("DecideTransferAction", TRANSFER_JS, 780, -80)
    u = w.dt("UpdateTransfer", "ar_sessions", "update", 1040, -80, conditions=[("sessionId", "={{ $json.sessionId }}")], values={"handoffResult": "={{ $json.handoffResult }}"})
    lg = w.exec_wf("LogTransfer", "ar-17-log-event", {"correlationId": "={{ $('DecideTransferAction').first().json.correlationId }}", "sessionId": "={{ $('DecideTransferAction').first().json.sessionId }}", "caseId": "={{ $('DecideTransferAction').first().json.caseId }}", "workflow": "AR-04", "event": "transfer_result", "state": "={{ $('DecideTransferAction').first().json.state }}", "tool": "", "latencyMs": 0, "outcome": "={{ $('DecideTransferAction').first().json.action }}", "errorCode": "={{ $('DecideTransferAction').first().json.action === 'release' ? '' : 'refer_failed' }}", "detail": "={{ 'notify=' + $('DecideTransferAction').first().json.notifyStatus }}"}, 1300, -80)
    rs = w.respond("RespondTransfer", "={{ JSON.stringify({ action: $('DecideTransferAction').first().json.action, speak: $('DecideTransferAction').first().json.speak }) }}", 1560, -80)
    w.chain(a, ls, ifound)
    w.link(ifound, d, 0); w.link(ifound, r404, 1)
    w.chain(d, u, lg, rs)
    return w

# =============================================================================
# AR-05 health and handoff lookup
# =============================================================================
def build_ar05():
    w = WF("ar-05-health-and-handoff-lookup", "AR-05 API: Health and handoff context lookup", "GET /webhook/ar/v1/health for probes. GET /webhook/ar/v1/handoff?correlationId=... returns the handoff context (case, intent, reason, summary) for the agent desktop. No transcript.")
    w.sticky("## AR-05 Health and handoff lookup\nHealth is unauthenticated and cheap. The lookup is header-authenticated and returns only what the agent desktop needs.", 0, -400, 700, 100)
    h = w.webhook("HealthCheck", "ar/v1/health", 0, 0, method="GET", auth=False)
    rh = w.respond("RespondHealth", "={{ JSON.stringify({ ok: true, service: 'voice-booking', version: '0.1', at: $now.toUTC().toISO() }) }}", 260, 0)
    w.chain(h, rh)
    l = w.webhook("LookupHandoff", "ar/v1/handoff", 0, 200, method="GET")
    g = w.dt("LoadByCorrelation", "ar_sessions", "get", 260, 200, conditions=[("correlationId", "={{ $json.query.correlationId }}")])
    s = w.code("ShapeHandoffContext", "const s = $json;\nif (!s.sessionId) return [{ json: { found: false, statusCode: 404 } }];\nreturn [{ json: { found: true, statusCode: 200, correlationId: s.correlationId, sessionId: s.sessionId, caseId: s.caseId, state: s.state, intent: s.intent, handoffReason: s.handoffReason || null, handoffResult: s.handoffResult || null, summary: s.handoffSummary || '', dealer: s.dealerName || null, slotId: s.slotId || null, confirmationCode: s.confirmationCode || null, language: s.language, market: s.market, startedAt: s.startedAt, endedAt: s.endedAt || null } }];", 520, 200)
    r = w.respond("RespondHandoffContext", "={{ JSON.stringify($json) }}", 780, 200, code="={{ $json.statusCode }}")
    w.chain(l, g, s, r)
    return w

# =============================================================================
# AR-30 error workflow
# =============================================================================
def build_ar30():
    w = WF("ar-30-error-workflow", "AR-30 Error workflow", "Catch-all for any AR-* workflow failure that escapes per-node handling. Writes an error event with workflow, node and message. Assign it as the Error Workflow in each AR-* workflow's settings (UI).")
    w.sticky("## AR-30 Error workflow\nAssign in Workflow Settings > Error Workflow on every AR-* workflow. Writes to ar_events; wire a Teams or e-mail notification here on the production instance (different channel from the monitored one).", 0, -400, 760, 110)
    t = w.add("OnWorkflowError", "n8n-nodes-base.errorTrigger", 1, {}, 0, 0)
    s = w.code("ShapeError", "const e = $json;\nconst msg = String((e.execution && e.execution.error && e.execution.error.message) || '').slice(0, 300);\nreturn [{ json: { at: new Date().toISOString(), correlationId: '', sessionId: '', caseId: '', workflow: (e.workflow && e.workflow.name) || '', event: 'error', state: '', tool: (e.execution && e.execution.lastNodeExecuted) || '', latencyMs: 0, outcome: 'failed', errorCode: 'workflow_error', detail: msg + ' execution=' + ((e.execution && e.execution.id) || '') } }];", 260, 0)
    ins = w.dt("InsertErrorEvent", "ar_events", "insert", 520, 0, always=False, values={c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_events"]})
    w.chain(t, s, ins)
    return w

# =============================================================================
# AR-90 seed configuration
# =============================================================================
MARKET_ROWS = [
    {"market": "PL", "languages": "pl,en", "defaultLanguage": "pl", "bookable": True,
     "disclosure_pl": "Dzień dobry, tu asystent cyfrowy serwisu. Ta rozmowa jest prowadzona przez sztuczną inteligencję. W każdej chwili mogą Państwo poprosić o rozmowę z konsultantem.",
     "disclosure_es": "", "disclosure_en": "Hello, this is the workshop's digital assistant. This conversation is handled by an artificial intelligence. You can ask for a human colleague at any time.", "disclosure_de": "",
     "handoverLine": "Łączę Państwa z konsultantem, proszę chwilę poczekać.", "closingLine": "Dziękuję za rozmowę. Do widzenia.",
     "confirmationChannels": "sms,email", "returnNumber": "sip:111102@cc.example", "slotWindowDays": 14, "maxSlotsOffered": 3},
    {"market": "ES", "languages": "es,en", "defaultLanguage": "es", "bookable": True,
     "disclosure_pl": "", "disclosure_es": "Hola, le atiende el asistente digital del taller. Esta conversación la gestiona una inteligencia artificial. Puede pedir hablar con una persona en cualquier momento.",
     "disclosure_en": "Hello, this is the workshop's digital assistant. This conversation is handled by an artificial intelligence. You can ask for a human colleague at any time.", "disclosure_de": "",
     "handoverLine": "Le paso con un agente, un momento por favor.", "closingLine": "Gracias por su llamada. Adiós.",
     "confirmationChannels": "sms,email", "returnNumber": "sip:111102@cc.example", "slotWindowDays": 14, "maxSlotsOffered": 3},
    {"market": "DE", "languages": "de,en", "defaultLanguage": "de", "bookable": True,
     "disclosure_pl": "", "disclosure_es": "", "disclosure_en": "Hello, this is the workshop's digital assistant. This conversation is handled by an artificial intelligence. You can ask for a human colleague at any time.",
     "disclosure_de": "Guten Tag, hier ist der digitale Assistent der Werkstatt. Dieses Gespräch wird von einer künstlichen Intelligenz geführt. Sie können jederzeit einen Mitarbeiter verlangen.",
     "handoverLine": "Ich verbinde Sie mit einem Mitarbeiter, einen Moment bitte.", "closingLine": "Vielen Dank für Ihren Anruf. Auf Wiederhören.",
     "confirmationChannels": "sms,email", "returnNumber": "sip:111102@cc.example", "slotWindowDays": 14, "maxSlotsOffered": 3},
    {"market": "GB", "languages": "en", "defaultLanguage": "en", "bookable": True,
     "disclosure_pl": "", "disclosure_es": "", "disclosure_en": "Hello, this is the workshop's digital assistant. This conversation is handled by an artificial intelligence. You can ask for a human colleague at any time.", "disclosure_de": "",
     "handoverLine": "I am connecting you to a colleague, one moment please.", "closingLine": "Thank you for calling. Goodbye.",
     "confirmationChannels": "sms,email", "returnNumber": "sip:111102@cc.example", "slotWindowDays": 14, "maxSlotsOffered": 3},
]
CODE_ROWS = [
    ("intent", "BOOK", "B", "APPT_BOOK", "New service appointment"), ("intent", "RESCHEDULE", "R", "APPT_RESCHEDULE", "Move an existing appointment"),
    ("intent", "CANCEL", "C", "APPT_CANCEL", "Cancel an existing appointment"), ("intent", "OTHER", "O", "OTHER", "Outside appointment booking"), ("intent", "UNKNOWN", "U", "unknown", "Intent not identified"),
    ("reason", "CALLER_REQUEST", "CR", "CUSTOMER_REQUEST", "Caller asked for a person"), ("reason", "SCENARIO_REQUIRES_AGENT", "SA", "PROCESS", "Non-bookable dealer, callback or out-of-scope request"),
    ("reason", "NOT_UNDERSTOOD", "NU", "LOW_CONFIDENCE", "Two consecutive turns not understood"), ("reason", "BACKEND_UNAVAILABLE", "BU", "SYSTEM_ERROR", "CRM, DealerBook or case system unavailable"),
    ("reason", "IDENTIFICATION_FAILED", "IF", "IDENTIFICATION", "Customer or vehicle could not be confirmed"), ("reason", "NO_SLOT", "NS", "NO_AVAILABILITY", "No acceptable slot found"),
    ("reason", "POLICY", "PO", "CONSENT", "Caller declined the assistant or a policy trigger fired"), ("reason", "PLATFORM_ERROR", "PE", "SYSTEM_ERROR", "Dialogue platform failure"),
]

def build_ar90():
    w = WF("ar-90-seed-config", "AR-90 Seed configuration tables", "POST /webhook/ar/v1/admin/seed. Upserts ar_markets (PL, ES, DE, GB) and ar_codes (intent and handoff reason codes). Run once per environment; safe to re-run.")
    w.sticky("## AR-90 Seed config\nMarkets and codes are data, not prompt text: a new market is one row here. Disclosure texts are placeholders until Legal supplies the market wording.", 0, -400, 760, 100)
    a = w.webhook("ReceiveSeed", "ar/v1/admin/seed", 0, 0)
    mr = w.code("MarketRows", "const rows = " + json.dumps(MARKET_ROWS, ensure_ascii=False) + ";\nreturn rows.map(r => ({ json: r }));", 260, 0)
    um = w.dt("UpsertMarket", "ar_markets", "upsert", 520, 0, conditions=[("market", "={{ $json.market }}")], values={c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_markets"]})
    cr = w.code("CodeRows", "const rows = " + json.dumps([dict(zip(["kind","code","short","ccValue","description"], r)) for r in CODE_ROWS], ensure_ascii=False) + ";\nreturn rows.map(r => ({ json: r }));", 780, 0)
    uc = w.dt("UpsertCode", "ar_codes", "upsert", 1040, 0, conditions=[("kind", "={{ $json.kind }}"), ("code", "={{ $json.code }}")], values={c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_codes"]})
    rs = w.respond("RespondSeeded", "={{ JSON.stringify({ ok: true, markets: " + str(len(MARKET_ROWS)) + ", codes: " + str(len(CODE_ROWS)) + " }) }}", 1300, 0)
    w.chain(a, mr, um, cr, uc, rs)
    return w


BUILDERS = [build_ar17, build_ar20, build_ar21, build_ar22, build_ar23, build_ar10, build_ar11, build_ar12, build_ar13, build_ar14, build_ar15, build_ar16,
            build_ar01, build_ar02, build_ar03, build_ar04, build_ar05, build_ar30, build_ar90]

# =============================================================================
# Canvas zones (n8n-canvas-docs): one entry per stage, rows of node names, None = empty column.
# Colours: 4 green human touchpoints, 3 red state and critical calls, 5 blue logic, 6 purple setup, 7 gray docs.
# =============================================================================
ZONES = {
    "ar-01-session-start": [
        {"title": "Receive", "color": 4, "body": "The gateway posts the SIP UUI once the INVITE is answered. Header auth; a malformed body answers 400 and the call is released.",
         "rows": [["ReceiveStart", "ParseStart", "IsValid"], [None, None, "RespondInvalid"]]},
        {"title": "Idempotency", "color": 5, "body": "A re-delivered correlationId returns the existing session without speaking again.",
         "rows": [["CheckExisting", "AlreadyStarted"], ["ShapeExisting", "RespondExisting"]]},
        {"title": "Identify and open the case", "color": 3, "body": "Market row, CRM lookup by VIN, case. The model never sees the VIN or the number.",
         "rows": [["LoadMarket", "IdentifyCustomer", "CreateCase"]]},
        {"title": "Session", "color": 3, "body": "One row in ar_sessions per call; state starts at IDENTIFY. Every start is logged to ar_events.",
         "rows": [["ComposeSession", "InsertSession", "LogStart"]]},
        {"title": "Greet or hand over", "color": 4, "body": "Backend down at start: the handover payload is built here and the greeting becomes the handover line.",
         "rows": [["BackendDown", "ShapeStart", "RespondStart"], [None, "BuildStartHandoff"]]},
    ],
    "ar-02-dialogue-turn": [
        {"title": "Receive", "color": 4, "body": "One turn per call: sessionId, transcript, ASR confidence.",
         "rows": [["ReceiveTurn", "ValidateTurn", "IsValid"], [None, None, "RespondInvalid"]]},
        {"title": "Load state", "color": 3, "body": "The session row and the market row are the only state the model sees.",
         "rows": [["LoadSession", "SessionFound", "LoadMarket"], [None, "RespondNotFound"]]},
        {"title": "Reason", "color": 5, "body": "PrepareTurn rebuilds the system prompt from facts and applies the yes/no gates. The agent has three tools, Postgres memory and a structured parser with auto-fix.",
         "rows": [["PrepareTurn", "DialogueAgent"], ["DialogueModel", "TurnMemory", "TurnOutputShape"], ["FindDealers", "FindSlots", "BookAppointment", "ParserFixerModel"]]},
        {"title": "Guards 1: gates", "color": 5, "body": "The model's output is clamped to the allowed values, then each rule is an If (the condition) and an Edit Fields (what it changes). Gates: a deterministic yes or no in IDENTIFY and CONSENT wins over the model.",
         "rows": [["ReloadSession", "ReadOutput", "ClampOutput", "GateDecline", "GateAdvance", "ScenarioDowngrade"], [None, None, None, "OnGateDecline", "OnGateAdvance", "OnScenarioDowngrade"]]},
        {"title": "Guards 2: caller and booking", "color": 5, "body": "Caller asks for a person: handoff. A booking claim without a confirmation code is replaced by a question. A booking made by a tool forces BOOKED.",
         "rows": [["CallerAsksForHuman", "PendingSlot", "FalseConfirmation", "ForceBooked"], ["OnCallerRequest", None, "OnFalseConfirmation", "OnForceBooked"]]},
        {"title": "Guards 3: reply and end", "color": 5, "body": "Empty or over-long replies are fixed. A handoff always speaks the market's handover line. No end before BOOKED or WRAPUP.",
         "rows": [["EmptyReply", "ReplyTooLong", "HandoffNormalise", "ForceHandoff", "EndBlocked", "GuardResult"], ["OnEmptyReply", "OnReplyTooLong", "OnHandoff", "OnForceHandoff", "OnEndBlocked"]]},
        {"title": "Handoff", "color": 3, "body": "A model failure answers with a fixed apology and PLATFORM_ERROR. Any handoff builds the Refer-To through AR-14.",
         "rows": [["NeedsHandoff", "BuildHandoff", "AttachHandoff"], ["HandleAgentError"]]},
        {"title": "Respond", "color": 4, "body": "Session update, event row, the reply the gateway speaks.",
         "rows": [["ComposeResponse", "UpdateSession", "LogTurn", "RespondTurn"]]},
    ],
    "ar-03-session-end": [
        {"title": "Receive", "color": 4, "body": "The gateway posts the end reason when the call drops or the transfer completes.",
         "rows": [["ReceiveEnd", "ValidateEnd", "IsValid"], [None, None, "RespondInvalid"]]},
        {"title": "Load state", "color": 3, "body": "The session row decides the outcome.",
         "rows": [["LoadSession", "SessionFound"], [None, "RespondNotFound"]]},
        {"title": "Outcome", "color": 3, "body": "Resolved, Delegated or Closed on the case; a confirmation goes out when a booking exists.",
         "rows": [["DecideOutcome", "CloseCase", "NeedsConfirmation"], [None, None, "SendConfirmation"]]},
        {"title": "Close", "color": 4, "body": "Chat memory purged, session finalised, end logged. Nothing of the call is retained beyond ar_events.",
         "rows": [["PurgeMemory", "FinalizeSession", "LogEnd", "RespondEnd"]]},
    ],
    "ar-04-transfer-result": [
        {"title": "Receive", "color": 4, "body": "NOTIFY result of the REFER, posted by the gateway.",
         "rows": [["ReceiveTransferResult", "LoadSession", "SessionFound"], [None, None, "RespondNotFound"]]},
        {"title": "Decide", "color": 5, "body": "First failure: speak and retry once. Second failure: speak and end. Success: release.",
         "rows": [["DecideTransferAction", "UpdateTransfer", "LogTransfer", "RespondTransfer"]]},
    ],
    "ar-05-health-and-handoff-lookup": [
        {"title": "Health", "color": 4, "body": "Probe for the gateway and the load balancer.",
         "rows": [["HealthCheck", "RespondHealth"]]},
        {"title": "Handoff lookup", "color": 4, "body": "Agent desktop context by correlationId: intent, reason, summary, case.",
         "rows": [["LookupHandoff", "LoadByCorrelation", "ShapeHandoffContext", "RespondHandoffContext"]]},
    ],
    "ar-10-identify-customer": [
        {"title": "Identify", "color": 3, "body": "CRM lookup by VIN and number. Returns the greeting name and the vehicle label, never the identifiers.",
         "rows": [["ToolInput", "CallCrm", "ShapeCustomer"], [None, None, "ReturnCrmError"]]},
    ],
    "ar-11-find-dealers": [
        {"title": "Find dealers", "color": 5, "body": "DealerBook dealer search by city; bookable tells the agent whether the dealer can be booked here.",
         "rows": [["ToolInput", "LoadSession", "CallDealerBookDealers", "ShapeDealers"], [None, None, None, "ReturnDealersError"]]},
    ],
    "ar-12-find-slots": [
        {"title": "Find slots", "color": 5, "body": "DealerBook availability for the agreed dealer from the requested date.",
         "rows": [["ToolInput", "LoadSession", "CallDealerBookSlots", "ShapeSlots"], [None, None, None, "ReturnSlotsError"]]},
        {"title": "Remember what was offered", "color": 3, "body": "Offered slots are stored on the session so a booking can only name one of them.",
         "rows": [["StoreOffered", "ReturnSlots"]]},
    ],
    "ar-13-book-appointment": [
        {"title": "Booking gate", "color": 5, "body": "Rules in order: a booking already exists; the slot was not offered in this call; the caller has not confirmed it (state CONFIRM with this slotId read back). Only Proceed books.",
         "rows": [["ToolInput", "LoadSession", "BookingGate", "BookingRequest"], [None, None, None, "ReplyAlreadyBooked"], [None, None, None, "ReplySlotNotOffered"], [None, None, None, "ReplyNotConfirmed"]]},
        {"title": "Book", "color": 3, "body": "Idempotency key session:slot. The confirmation code comes from DealerBook, never from the model.",
         "rows": [["CallDealerBookBook", "StoreBooking", "ReturnBooking"], ["ReturnBookError"]]},
    ],
    "ar-14-build-handoff": [
        {"title": "Context", "color": 5, "body": "Session and market rows: the return number and the language come from the market.",
         "rows": [["ToolInput", "LoadSession", "LoadMarket"]]},
        {"title": "Return UUI", "color": 3, "body": "Hex payload within 64 bytes, Refer-To string, stored on the session for the lookup endpoint.",
         "rows": [["BuildUui", "StoreHandoff", "ReturnHandoff"]]},
    ],
    "ar-15-case-system": [
        {"title": "case", "color": 3, "body": "Create at start, close at end. Standard CRM case REST behind the mock.",
         "rows": [["ToolInput", "CallCaseSystem", "ShapeCase"], [None, None, "ReturnCaseError"]]},
    ],
    "ar-16-send-confirmation": [
        {"title": "Send confirmation", "color": 4, "body": "SMS or e-mail through the communication service; the destination is resolved from the CRM reference.",
         "rows": [["ToolInput", "CallComms", "ShapeComms"], [None, None, "ReturnCommsError"]]},
    ],
    "ar-17-log-event": [
        {"title": "Log event", "color": 5, "body": "One structured row per step in ar_events. No audio, no transcript.",
         "rows": [["ToolInput", "InsertEvent", "ReturnOk"]]},
    ],
    "ar-20-mock-crm": [
        {"title": "Mock CRM", "color": 7, "body": "Four synthetic customers keyed by VIN. Lab only.",
         "rows": [["ReceiveLookup", "LookupFixture", "RespondCustomer"]]},
    ],
    "ar-21-mock-dealerbook": [
        {"title": "Dealers", "color": 7, "body": "Fixture dealers per market.",
         "rows": [["ReceiveDealers", "FilterDealers", "RespondDealers"]]},
        {"title": "Slots", "color": 7, "body": "Deterministic from dealerId and date so tests repeat.",
         "rows": [["ReceiveSlots", "GenerateSlots", "RespondSlots"]]},
        {"title": "Book", "color": 7, "body": "A slotId containing FULL answers 409. Bookings persist in ar_mock_bookings.",
         "rows": [["ReceiveBook", "ComputeBooking", "IsConflict", "UpsertBooking", "RespondBooked"], [None, None, "RespondConflict"]]},
        {"title": "Cancel", "color": 7, "body": "Marks the booking cancelled.",
         "rows": [["ReceiveCancel", "CancelBooking", "RespondCancelled"]]},
        {"title": "Bookings by VIN", "color": 7, "body": "Lists bookings for reschedule and cancel flows.",
         "rows": [["ReceiveBookings", "FindBookingsByVin", "ShapeBookings", "RespondBookings"]]},
    ],
    "ar-22-mock-case-system": [
        {"title": "Mock case system", "color": 7, "body": "Case numbers deterministic per correlationId; rows in ar_mock_cases.",
         "rows": [["ReceiveCaseOp", "ComputeCase", "UpsertCase", "RespondCase"]]},
    ],
    "ar-23-mock-comms": [
        {"title": "Mock comms", "color": 7, "body": "Returns a message id, sends nothing.",
         "rows": [["ReceiveSend", "ComputeMessage", "RespondSend"]]},
    ],
    "ar-30-error-workflow": [
        {"title": "Error trigger", "color": 3, "body": "Every AR-* failure lands in ar_events with workflow, node and message. Assign in Workflow Settings.",
         "rows": [["OnWorkflowError", "ShapeError", "InsertErrorEvent"]]},
    ],
    "ar-90-seed-config": [
        {"title": "Seed", "color": 6, "body": "Upserts ar_markets and ar_codes. Run once per environment; safe to re-run.",
         "rows": [["ReceiveSeed", "MarketRows", "UpsertMarket", "CodeRows", "UpsertCode", "RespondSeeded"]]},
    ],
}


if __name__ == "__main__":
    learn_existing_ids()
    # names must be known before references are rendered: declare all, then dump all
    built = [b() for b in BUILDERS]
    # second pass: re-render references now that every name is registered
    built = [b() for b in BUILDERS]
    for w in built:
        print(os.path.basename(w.dump()), "nodes:", len(w.nodes), "id:", IDS.get("wf_" + w.key, "PENDING"))
    json.dump(IDS, open(IDS_PATH, "w"), indent=2)
