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
    "ar_prompts": ["key","pl","es","de","en","note"],
    "ar_mock_bookings": ["bookingRef","confirmationCode","slotId","dealerId","vin","serviceType","mobility","startsAt","status","bookedAt"],
    "ar_mock_cases": ["caseId","correlationId","status","subject","classification","solution","delegatedReason","openedAt","lastUpdate"],
    "ar_mock_customers": ["vin","customerRef","greetingName","vehicleLabel","preferredDealerId","preferredDealerName","market","language"],
    "ar_mock_dealers": ["dealerId","name","city","market","bookable"],
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

    def set_json(self, name, expr, x, y, **extra):
        """Edit Fields in JSON mode: the expression's object becomes the whole item. Runs once per input item unless executeOnce=True."""
        return self.add(name, "n8n-nodes-base.set", 3.4, {"mode": "raw", "jsonOutput": expr, "options": {}}, x, y, **extra)

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

    def dt(self, name, table, op, x, y, conditions=None, values=None, limit=1, always=True, all=False):
        p = {"operation": op, "dataTableId": {"__rl": True, "mode": "id", "value": IDS["dt_" + table]}}
        if conditions is not None:
            p["matchType"] = "allConditions"
            p["filters"] = {"conditions": [{"keyName": k, "condition": "eq", "keyValue": v} for k, v in conditions]}
        if op == "get":
            p["returnAll"] = all
            if not all:
                p["limit"] = limit
        if values is not None:
            p["columns"] = {"mappingMode": "defineBelow", "value": values, "matchingColumns": [],
                            "schema": schema(TABLE_COLS[table], table), "attemptToConvertTypes": False, "convertFieldsToString": False}
        extra = {"alwaysOutputData": True} if always and op in ("get", "update", "upsert") else {}
        return self.add(name, "n8n-nodes-base.dataTable", 1.1, p, x, y, **extra)

    def aggregate(self, name, field, x, y):
        """All input items into one item holding a list under `field`."""
        return self.add(name, "n8n-nodes-base.aggregate", 1, {"aggregate": "aggregateAllItemData", "destinationFieldName": field, "options": {}}, x, y)

    def split_out(self, name, field, x, y):
        return self.add(name, "n8n-nodes-base.splitOut", 1, {"fieldToSplitOut": field, "options": {}}, x, y)

    def load_prompts(self, x, y):
        """ar_prompts rows aggregated into one item: $('Prompts').first().json.prompts. Place before the session is loaded."""
        g = self.dt("LoadPrompts", "ar_prompts", "get", x, y, all=True, always=False)
        a = self.aggregate("Prompts", "prompts", x + 208, y)
        self.link(g, a)
        return g, a

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


def prompt_text(key, lang_expr="$json.language"):
    """JS expression (no braces) picking an ar_prompts row's text in the session language, English fallback.
    Needs the Prompts aggregate node in the workflow (WF.load_prompts)."""
    row = f"($('Prompts').first().json.prompts.find(p => p.key === '{key}') || {{}})"
    return f"({row}[{lang_expr}] || {row}.en || '')"


def lang_pick(key, lang_expr="$json.language"):
    """Expression that speaks an ar_prompts row in the session language."""
    return f"={{{{ {prompt_text(key, lang_expr)} }}}}"


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
MOCK_CUSTOMERS = [
    {"vin": "TST2970461A123456", "customerRef": "C-PL-004711", "greetingName": "Pani Kowalska", "vehicleLabel": "GLC 300 4MATIC", "preferredDealerId": "PL-WAW-01", "preferredDealerName": "Dealer Warszawa Okęcie", "market": "PL", "language": "pl"},
    {"vin": "TST2130421A654321", "customerRef": "C-ES-002310", "greetingName": "Señor García", "vehicleLabel": "Clase E 220 d", "preferredDealerId": "ES-MAD-02", "preferredDealerName": "Dealer Madrid Castellana", "market": "ES", "language": "es"},
    {"vin": "TST1770871N998877", "customerRef": "C-DE-009902", "greetingName": "Herr Weber", "vehicleLabel": "A 200", "preferredDealerId": "DE-STR-01", "preferredDealerName": "Dealer Stuttgart Mitte", "market": "DE", "language": "de"},
    {"vin": "TST2476011J112233", "customerRef": "C-GB-001177", "greetingName": "Ms Patel", "vehicleLabel": "EQB 300", "preferredDealerId": "GB-LON-01", "preferredDealerName": "Dealer London West", "market": "GB", "language": "en"},
]
MOCK_DEALERS = [
    {"dealerId": "PL-WAW-01", "name": "Dealer Warszawa Okęcie", "city": "Warszawa", "market": "PL", "bookable": True},
    {"dealerId": "PL-WAW-02", "name": "Dealer Warszawa Wola", "city": "Warszawa", "market": "PL", "bookable": True},
    {"dealerId": "PL-KRK-01", "name": "Dealer Kraków", "city": "Kraków", "market": "PL", "bookable": True},
    {"dealerId": "PL-GDA-01", "name": "Dealer Gdańsk", "city": "Gdańsk", "market": "PL", "bookable": False},
    {"dealerId": "ES-MAD-02", "name": "Dealer Madrid Castellana", "city": "Madrid", "market": "ES", "bookable": True},
    {"dealerId": "ES-BCN-01", "name": "Dealer Barcelona", "city": "Barcelona", "market": "ES", "bookable": True},
    {"dealerId": "DE-STR-01", "name": "Dealer Stuttgart Mitte", "city": "Stuttgart", "market": "DE", "bookable": True},
    {"dealerId": "GB-LON-01", "name": "Dealer London West", "city": "London", "market": "GB", "bookable": True},
]

def build_ar20():
    w = WF("ar-20-mock-crm", "AR-MOCK-CRM customer master", "Lab stand-in for CRM / customer master. POST ar-mock/crm/customer-lookup {vin, msisdn} -> {found, customerRef, greetingName, vehicleLabel, preferredDealerId, ...}. Synthetic rows in ar_mock_customers.")
    w.sticky("## Mock CRM / customer master\nSynthetic customers in the ar_mock_customers table (seeded by AR-90), keyed by VIN. Unknown VIN returns found:false with HTTP 200. Real build: one HTTP Request to the customer API in place of the table read.", 0, -400, 620, 100)
    a = w.webhook("ReceiveLookup", "ar-mock/crm/customer-lookup", 0, 0, auth=False)
    g = w.dt("LookupCustomer", "ar_mock_customers", "get", 260, 0, conditions=[("vin", "={{ String($json.body.vin || '').toUpperCase().trim() }}")])
    s = w.set_json("ShapeFixture", "={{ $json.customerRef ? { found: true, vin: $json.vin, customerRef: $json.customerRef, greetingName: $json.greetingName, vehicleLabel: $json.vehicleLabel, preferredDealerId: $json.preferredDealerId, preferredDealerName: $json.preferredDealerName, market: $json.market, language: $json.language } : { found: false, vin: String($('ReceiveLookup').first().json.body.vin || '').toUpperCase().trim() } }}", 520, 0)
    c = w.respond("RespondCustomer", "={{ JSON.stringify($json) }}", 780, 0)
    w.chain(a, g, s, c)
    return w

DEALERBOOK_SLOTS_JS = """// Mock of DealerBook slot availability. Deterministic from dealerId and date so tests repeat. The dealer row comes from ar_mock_dealers (LoadDealer).
const b = $('ReceiveSlots').first().json.body || {};
const dealer = $json;
const dealerId = String(b.dealerId || '');
if (!dealer.dealerId || dealer.bookable !== true) return [{ json: { dealerId, dealerName: dealer.name || null, slots: [], reason: 'dealer_not_dealerbook_enabled' } }];
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
return [{ json: { dealerId, dealerName: dealer.name, slots: slots.slice(0, 6) } }];"""

def build_ar21():
    w = WF("ar-21-mock-dealerbook", "AR-MOCK-DealerBook", "Lab stand-in for DealerBook (dealers, slots, book, bookings by VIN, cancel). Dealers in ar_mock_dealers, deterministic slots, bookings in ar_mock_bookings. Replace with the dealer booking API.")
    w.sticky("## Mock DealerBook\nFive endpoints under ar-mock/dealerbook/*. Dealers are rows in ar_mock_dealers (seeded by AR-90). Slots are deterministic per dealer and date. A slotId containing FULL returns 409. Bookings persist in ar_mock_bookings.", 0, -400, 760, 120)
    # dealers: every row, then the request's market, dealerId and city (accent-insensitive) narrow the list
    a = w.webhook("ReceiveDealers", "ar-mock/dealerbook/dealers", 0, 0, auth=False)
    ld = w.dt("LoadDealers", "ar_mock_dealers", "get", 260, 0, all=True, always=False)
    ag = w.aggregate("AllDealers", "dealers", 390, 0)
    B = "$('ReceiveDealers').first().json.body"
    norm = lambda x: f"String({x} || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')"
    f = w.set_fields("FilterDealers", [("dealers", f"={{{{ $json.dealers.filter(d => (!{B}.market || d.market === String({B}.market).toUpperCase()) && (!{B}.dealerId || d.dealerId === {B}.dealerId) && (!{B}.city || {norm('d.city')}.includes({norm(B + '.city')}))).map(d => ({{ dealerId: d.dealerId, name: d.name, city: d.city, market: d.market, bookable: d.bookable === true }})) }}}}", "array")], 520, 0)
    c = w.respond("RespondDealers", "={{ JSON.stringify($json) }}", 780, 0)
    w.chain(a, ld, ag, f, c)
    # slots
    a = w.webhook("ReceiveSlots", "ar-mock/dealerbook/slots", 0, 200, auth=False)
    dl = w.dt("LoadDealer", "ar_mock_dealers", "get", 260, 200, conditions=[("dealerId", "={{ String($json.body.dealerId || '') }}")])
    b = w.code("GenerateSlots", DEALERBOOK_SLOTS_JS, 390, 200)
    c = w.respond("RespondSlots", "={{ JSON.stringify($json) }}", 520, 200)
    w.chain(a, dl, b, c)
    # book: required fields, the FULL probe, then a deterministic confirmation code from the idempotency key
    a = w.webhook("ReceiveBook", "ar-mock/dealerbook/book", 0, 400, auth=False)
    inv = w.if_rules("BookInvalid", [w.cond("={{ String($json.body.idempotencyKey || '') }}", "empty"), w.cond("={{ String($json.body.slotId || '') }}", "empty"),
                                     w.cond("={{ String($json.body.dealerId || '') }}", "empty")], 260, 400, combinator="or")
    ri = w.set_fields("ReplyInvalid", [("conflict", False, "boolean"), ("invalid", True, "boolean"), ("message", "slotId, dealerId and idempotencyKey are required", "string")], 390, 560)
    full = w.if_rules("SlotFull", [w.cond("={{ String($json.body.slotId || '') }}", "contains", "FULL")], 390, 400)
    rc = w.set_fields("ReplyConflict", [("conflict", True, "boolean"), ("invalid", False, "boolean"), ("message", "slot no longer available", "string")], 520, 560)
    hk = w.add("HashKey", "n8n-nodes-base.crypto", 2, {"action": "hash", "type": "SHA256", "value": "={{ $json.body.idempotencyKey }}", "dataPropertyName": "hash", "encoding": "hex"}, 520, 400)
    b = w.set_fields("ComputeBooking", [
        ("conflict", False, "boolean"), ("invalid", False, "boolean"), ("bookingRef", "={{ $json.body.idempotencyKey }}", "string"),
        ("confirmationCode", "={{ 'BK-' + (parseInt($json.hash.slice(0, 8), 16) % 1000000).toString().padStart(6, '0') }}", "string"),
        ("slotId", "={{ $json.body.slotId }}", "string"), ("dealerId", "={{ $json.body.dealerId }}", "string"), ("vin", "={{ $json.body.vin || '' }}", "string"),
        ("serviceType", "={{ $json.body.serviceType || '' }}", "string"), ("mobility", "={{ $json.body.mobility || '' }}", "string"),
        ("startsAt", "={{ (m => m ? m[1].slice(0, 4) + '-' + m[1].slice(4, 6) + '-' + m[1].slice(6, 8) + 'T' + m[2].slice(0, 2) + ':' + m[2].slice(2, 4) + ':00' : '')(String($json.body.slotId).match(/-(\\d{8})-(\\d{4})$/)) }}", "string"),
        ("status", "booked", "string"), ("bookedAt", "={{ $now.toUTC().toISO() }}", "string"),
    ], 650, 400)
    i = w.if_node("IsConflict", "={{ $json.conflict === true || $json.invalid === true }}", 780, 400)
    r409 = w.respond("RespondConflict", "={{ JSON.stringify({ error: $json.invalid ? 'validation_error' : 'slot_taken', message: $json.message }) }}", 1040, 300, code="={{ $json.invalid ? 400 : 409 }}")
    up = w.dt("UpsertBooking", "ar_mock_bookings", "upsert", 1040, 500, conditions=[("bookingRef", "={{ $json.bookingRef }}")],
              values={c2: f"={{{{ $json.{c2} }}}}" for c2 in TABLE_COLS["ar_mock_bookings"]})
    rb = w.respond("RespondBooked", "={{ JSON.stringify($('ComputeBooking').first().json) }}", 1300, 500)
    w.chain(a, inv)
    w.link(inv, ri, 0); w.link(ri, i)
    w.link(inv, full, 1)
    w.link(full, rc, 0); w.link(rc, i)
    w.link(full, hk, 1); w.chain(hk, b, i)
    w.link(i, r409, 0)
    w.link(i, up, 1)
    w.link(up, rb)
    # bookings by vin
    a = w.webhook("ReceiveBookings", "ar-mock/dealerbook/bookings", 0, 700, auth=False)
    g = w.dt("FindBookingsByVin", "ar_mock_bookings", "get", 260, 700, conditions=[("vin", "={{ $json.body.vin }}")], limit=20)
    ab = w.aggregate("AllBookings", "rows", 390, 700)
    s = w.set_fields("ShapeBookings", [("bookings", "={{ $json.rows.filter(r => r.bookingRef) }}", "array")], 520, 700)
    c = w.respond("RespondBookings", "={{ JSON.stringify($json) }}", 780, 700)
    w.chain(a, g, ab, s, c)
    # cancel
    a = w.webhook("ReceiveCancel", "ar-mock/dealerbook/cancel", 0, 900, auth=False)
    u = w.dt("CancelBooking", "ar_mock_bookings", "update", 260, 900, conditions=[("bookingRef", "={{ $json.body.bookingRef }}")], values={"status": "cancelled"})
    c = w.respond("RespondCancelled", "={{ JSON.stringify({ ok: true, bookingRef: $('ReceiveCancel').first().json.body.bookingRef, status: 'cancelled' }) }}", 520, 900)
    w.chain(a, u, c)
    return w

def build_ar22():
    w = WF("ar-22-mock-case-system", "AR-MOCK-CASE Case system", "Lab stand-in for the case API. POST ar-mock/casesys/case {op: create|update|close, correlationId, caseId, ...} -> case. Persists in ar_mock_cases.")
    w.sticky("## Mock case system\nOne endpoint, op in the body. Case numbers are deterministic from the correlation ID (SHA-256), so a re-created case is the same case. Real build: the case system's REST with OAuth2 client credentials.", 0, -400, 700, 100)
    a = w.webhook("ReceiveCaseOp", "ar-mock/casesys/case", 0, 0, auth=False)
    h = w.add("HashCorrelation", "n8n-nodes-base.crypto", 2, {"action": "hash", "type": "SHA256", "value": "={{ String($json.body.correlationId || Date.now()) }}", "dataPropertyName": "hash", "encoding": "hex"}, 260, 0)
    OP = "($json.body.op || 'create')"
    b = w.set_fields("ComputeCase", [
        ("op", f"={{{{ {OP} }}}}", "string"),
        ("caseId", f"={{{{ ({OP} === 'create' || !$json.body.caseId) ? 'CS-' + (10000000 + (parseInt($json.hash.slice(0, 8), 16) % 90000000)) : $json.body.caseId }}}}", "string"),
        ("correlationId", "={{ $json.body.correlationId || '' }}", "string"),
        ("status", f"={{{{ $json.body.status || ({OP} === 'create' ? 'In Progress' : 'Closed') }}}}", "string"),
        ("subject", "={{ $json.body.subject || 'Appointment booking via voice assistant' }}", "string"),
        ("classification", "={{ $json.body.classification || 'Appointment Booking' }}", "string"),
        ("solution", "={{ $json.body.solution || '' }}", "string"), ("delegatedReason", "={{ $json.body.delegatedReason || '' }}", "string"),
        ("openedAt", f"={{{{ {OP} === 'create' ? $now.toUTC().toISO() : ($json.body.openedAt || '') }}}}", "string"),
        ("lastUpdate", "={{ $now.toUTC().toISO() }}", "string"),
    ], 390, 0)
    vals = {c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS["ar_mock_cases"]}
    vals["openedAt"] = "={{ $json.op === 'create' ? $json.openedAt : undefined }}"
    up = w.dt("UpsertCase", "ar_mock_cases", "upsert", 520, 0, conditions=[("caseId", "={{ $json.caseId }}")], values=vals)
    c = w.respond("RespondCase", "={{ JSON.stringify($('ComputeCase').first().json) }}", 780, 0)
    w.chain(a, h, b, up, c)
    return w

def build_ar23():
    w = WF("ar-23-mock-comms", "AR-MOCK-COMMS SMS and email", "Lab stand-in for the communication service. POST ar-mock/comms/send {channel, customerRef, language, text} -> {messageId}. Sends nothing.")
    w.sticky("## Mock communication service\nReturns a message id. The real service resolves the customer's number or address from the CRM reference, so n8n never stores it.", 0, -400, 700, 100)
    a = w.webhook("ReceiveSend", "ar-mock/comms/send", 0, 0, auth=False)
    hr = w.if_rules("HasRequired", [w.cond("={{ String($json.body.customerRef || '') }}", "notEmpty"), w.cond("={{ String($json.body.text || '') }}", "notEmpty")], 260, 0)
    acc = w.set_fields("AcceptMessage", [("accepted", True, "boolean"), ("messageId", "={{ 'msg-' + Date.now().toString(36) }}", "string"),
        ("channel", "={{ $json.body.channel || 'sms' }}", "string"), ("customerRef", "={{ $json.body.customerRef }}", "string"), ("length", "={{ String($json.body.text).length }}", "number")], 390, -80)
    rej = w.set_fields("RejectMessage", [("accepted", False, "boolean"), ("error", "customerRef and text are required", "string")], 390, 120)
    c = w.respond("RespondSend", "={{ JSON.stringify($json) }}", 520, 0, code="={{ $json.accepted ? 200 : 400 }}")
    w.chain(a, hr)
    w.link(hr, acc, 0); w.link(hr, rej, 1)
    w.link(acc, c); w.link(rej, c)
    return w

# =============================================================================
# Tools and sub-workflows
# =============================================================================
def build_ar10():
    w = WF("ar-10-identify-customer", "AR-10 Identify customer by VIN and MSISDN", "Looks the caller up in CRM / customer master from the UUI identifiers. Inputs: vin, msisdn, correlationId. Returns {ok, known, customerRef, greetingName, vehicleLabel, preferredDealerId, preferredDealerName, market, language}. Called by AR-01, never by the model.")
    w.sticky("## AR-10 Identify customer\nIdentity is plumbed from the UUI, never from speech. The model never sees the VIN or the phone number.", 0, -400, 700, 100)
    t = w.sub_trigger([("vin","string"),("msisdn","string"),("correlationId","string")])
    h = w.http_post("CallCrm", MOCK + "/crm/customer-lookup", "={{ JSON.stringify({ vin: $json.vin, msisdn: $json.msisdn, correlationId: $json.correlationId }) }}", 260, 0)
    s = w.set_fields("ShapeCustomer", [("ok", True, "boolean"), ("known", "={{ !!$json.found }}", "boolean"), ("customerRef", "={{ $json.customerRef || '' }}", "string"),
        ("vehicleRef", "={{ $json.vin || '' }}", "string"), ("greetingName", "={{ $json.greetingName || '' }}", "string"), ("vehicleLabel", "={{ $json.vehicleLabel || '' }}", "string"),
        ("preferredDealerId", "={{ $json.preferredDealerId || '' }}", "string"), ("preferredDealerName", "={{ $json.preferredDealerName || '' }}", "string"),
        ("market", "={{ $json.market || '' }}", "string"), ("language", "={{ $json.language || '' }}", "string")], 520, -80)
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
    s = w.set_fields("ShapeDealers", [("ok", True, "boolean"), ("count", "={{ ($json.dealers || []).slice(0, 5).length }}", "number"),
        ("dealers", "={{ ($json.dealers || []).slice(0, 5) }}", "array"),
        ("note", "={{ ($json.dealers || []).length ? 'Offer at most two by name. Dealers with bookable false cannot be booked here.' : 'No dealer matched. Ask for another city or offer the preferred dealer.' }}", "string")], 780, -80)
    e = w.return_error("ReturnDealersError", 780, 120)
    w.chain(t, g, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

def build_ar12():
    w = WF("ar-12-find-slots", "AR-12 Tool: Find appointment slots", "Agent tool. Returns up to maxSlotsOffered (market row, default three) bookable DealerBook slots for a dealer from a date. Inputs: sessionId (plumbed), dealerId, fromDate (YYYY-MM-DD), serviceType. Side effect: stores the offered slots on the session so BookAppointment can validate the choice.")
    w.sticky("## AR-12 FindSlots\nThe offered slots are written to the session row. BookAppointment refuses any slotId that was not offered in this call: slots are facts, not model output. Labels are spoken-form weekday, date and time in the session language.", 0, -400, 760, 100)
    t = w.sub_trigger([("sessionId","string"),("dealerId","string"),("fromDate","string"),("serviceType","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    m = w.dt("LoadMarket", "ar_markets", "get", 390, 0, conditions=[("market", "={{ $json.market }}")])
    S, I = "$('LoadSession').first().json", "$('ToolInput').first().json"
    h = w.http_post("CallDealerBookSlots", MOCK + "/dealerbook/slots", f"={{{{ JSON.stringify({{ dealerId: {I}.dealerId || {S}.dealerId, fromDate: {I}.fromDate || '', serviceType: {I}.serviceType || '', market: {S}.market }}) }}}}", 520, 0)
    # Keeps at most maxSlotsOffered slots and labels them for speech (Luxon, session locale: "wtorek 22.10. 08:00").
    s = w.set_fields("ShapeSlots", [
        ("slots", f"={{{{ ($json.slots || []).slice(0, Number($('LoadMarket').first().json.maxSlotsOffered) || 3).map(o => ({{ slotId: o.slotId, startsAt: o.startsAt, dealerId: o.dealerId, dealerName: $json.dealerName, label: DateTime.fromISO(o.startsAt, {{ zone: 'utc' }}).setLocale({S}.language || 'en').toFormat('cccc dd.MM. HH:mm') }})) }}}}", "array"),
        ("dealerId", f"={{{{ $json.dealerId || {I}.dealerId || {S}.dealerId || '' }}}}", "string"),
        ("dealerName", f"={{{{ $json.dealerName || {S}.dealerName || '' }}}}", "string"),
        ("reason", "={{ $json.reason || '' }}", "string"), ("sessionId", f"={{{{ {S}.sessionId }}}}", "string"),
    ], 780, -80)
    o = w.set_fields("OfferSlots", [
        ("offeredSlots", "={{ JSON.stringify($json.slots) }}", "string"),
        ("result", "={{ $json.slots.length ? { ok: true, dealerId: $json.dealerId, dealerName: $json.dealerName, slots: $json.slots.map(o => ({ slotId: o.slotId, when: o.label })), note: 'Offer these by day and time, never by slotId. When the caller picks one, use its slotId in BookAppointment.' } : { ok: true, dealerId: $json.dealerId, dealerName: $json.dealerName, slots: [], note: $json.reason === 'dealer_not_dealerbook_enabled' ? 'This dealer cannot be booked online. Hand off with SCENARIO_REQUIRES_AGENT.' : 'No slots in this window. Try once more with a later fromDate, then hand off with NO_SLOT.' } }}", "object"),
    ], 910, -80, keep=True)
    u = w.dt("StoreOffered", "ar_sessions", "update", 1040, -80, conditions=[("sessionId", "={{ $json.sessionId }}")],
             values={"offeredSlots": "={{ $json.offeredSlots }}", "dealerId": "={{ $json.dealerId }}", "dealerName": "={{ $json.dealerName }}"})
    rt = w.set_json("ReturnSlots", "={{ $('OfferSlots').first().json.result }}", 1300, -80)
    e = w.return_error("ReturnSlotsError", 780, 120)
    w.chain(t, g, m, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    w.chain(s, o, u, rt)
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

ENCODE_UUI_JS = """// Hex-encodes the return UUI and builds the SIP REFER targets. What goes in and what is dropped is decided upstream (AssemblePayload, FitsBudget).
const p = $json;
const hex = Buffer.from(p.payload, 'utf8').toString('hex').toUpperCase();
const addr = String(p.returnNumber || 'sip:111102@cc.invalid').replace(/^sip:/, '');
return [{ json: { ok: true, sessionId: p.sessionId, correlationId: p.correlationId, caseId: p.caseId, intent: p.intent, handoffReason: p.handoffReason, uuiPayload: p.payload, uuiAscii: p.payload, uuiHex: hex, pd: '00',
  referTo: `<sip:${addr}?User-to-User=${hex}%3Bpd%3D00%3Bencoding%3Dhex>`, referToAscii: `<sip:${addr}?User-to-User=${encodeURIComponent(p.payload)}%3Bencoding%3Dascii>`, truncated: p.truncated || [], summary: p.summary } }];"""

def utf8_bytes(expr):
    """JS expression: UTF-8 byte length of a string expression (one byte per percent-escape)."""
    return f"encodeURIComponent({expr}).replace(/%[0-9A-F]{{2}}/g, 'x').length"

def build_ar14():
    w = WF("ar-14-build-handoff", "AR-14 Build handoff context", "Builds the SIP REFER target with the return UUI (version, correlationId, sessionId, intent, handoffReason, language, caseId when it fits) and marks the session HANDOFF. Inputs: sessionId, intent, handoffReason, summary. Returns {ok, referTo, uuiHex, uuiAscii, uuiPayload, truncated}. Deterministic; called by AR-01 and AR-02, never by the model.")
    w.sticky("## AR-14 Build handoff\nThe one place that knows the contact-center UUI format. Short codes come from ar_codes. FitsBudget: 64 bytes hex-encoded; the case ID is dropped first and logged, the correlation ID is never truncated. EncodeUui only encodes.", 0, -400, 760, 110)
    t = w.sub_trigger([("sessionId","string"),("intent","string"),("handoffReason","string"),("summary","string")])
    g = w.dt("LoadSession", "ar_sessions", "get", 260, 0, conditions=[("sessionId", "={{ $json.sessionId }}")])
    m = w.dt("LoadMarket", "ar_markets", "get", 520, 0, conditions=[("market", "={{ $json.market }}")])
    I, S, M = "$('ToolInput').first().json", "$('LoadSession').first().json", "$('LoadMarket').first().json"
    ic = w.dt("LoadIntentCode", "ar_codes", "get", 650, 0, conditions=[("kind", "intent"), ("code", f"={{{{ {I}.intent || 'UNKNOWN' }}}}")])
    rc = w.dt("LoadReasonCode", "ar_codes", "get", 780, 0, conditions=[("kind", "reason"), ("code", f"={{{{ {I}.handoffReason || 'PLATFORM_ERROR' }}}}")])
    # Positional payload: version;correlationId;sessionId;intent;reason;language, plus caseId when it fits.
    base = f"['1', {S}.correlationId || '', {S}.sessionId || '', $('LoadIntentCode').first().json.short || 'U', $json.short || 'PE', {S}.language || {M}.defaultLanguage || 'en'].join(';')"
    ap = w.set_fields("AssemblePayload", [
        ("sessionId", f"={{{{ {S}.sessionId }}}}", "string"), ("correlationId", f"={{{{ {S}.correlationId || '' }}}}", "string"), ("caseId", f"={{{{ {S}.caseId || '' }}}}", "string"),
        ("intent", f"={{{{ {I}.intent || 'UNKNOWN' }}}}", "string"), ("handoffReason", f"={{{{ {I}.handoffReason || 'PLATFORM_ERROR' }}}}", "string"),
        ("summary", f"={{{{ String({I}.summary || '').slice(0, 500) }}}}", "string"), ("returnNumber", f"={{{{ {M}.returnNumber || '' }}}}", "string"),
        ("base", f"={{{{ {base} }}}}", "string"),
        ("payload", f"={{{{ {S}.caseId ? {base} + ';' + {S}.caseId : {base} }}}}", "string"),
        ("truncated", "={{ [] }}", "array"),
    ], 910, 0)
    fb = w.if_rules("FitsBudget", [w.cond(f"={{{{ {utf8_bytes('$json.payload')} }}}}", "lte", 64, kind="number")], 1040, 0)
    dc = w.set_fields("DropCaseId", [
        ("payload", "={{ $json.base }}", "string"),
        ("truncated", f"={{{{ ['caseId'].concat({utf8_bytes('$json.base')} > 64 ? ['over_budget_correlationId_kept'] : []) }}}}", "array"),
    ], 1040, 200, keep=True)
    b = w.code("EncodeUui", ENCODE_UUI_JS, 1170, 0)
    u = w.dt("StoreHandoff", "ar_sessions", "update", 1300, 0, conditions=[("sessionId", "={{ $json.sessionId }}")],
             values={"state": "HANDOFF", "handoffReason": "={{ $json.handoffReason }}", "handoffSummary": "={{ $json.summary }}", "intent": "={{ $json.intent }}"})
    r = w.set_json("ReturnHandoff", "={{ $('EncodeUui').first().json }}", 1430, 0)
    w.chain(t, g, m, ic, rc, ap, fb)
    w.link(fb, b, 0); w.link(fb, dc, 1); w.link(dc, b)
    w.chain(b, u, r)
    return w

def build_ar15():
    w = WF("ar-15-case-system", "AR-15 Case create, update or close", "Writes the case. Inputs: op (create|update|close), correlationId, caseId, subject, classification, status, solution, delegatedReason. Returns {ok, caseId, status} or {ok:false, error}.")
    w.sticky("## AR-15 Case\nOne case per call, created at session start, updated at the end. A standard REST API behind the mock.", 0, -400, 700, 100)
    t = w.sub_trigger([("op","string"),("correlationId","string"),("caseId","string"),("subject","string"),("classification","string"),("status","string"),("solution","string"),("delegatedReason","string")])
    h = w.http_post("CallCaseSystem", MOCK + "/casesys/case", "={{ JSON.stringify($json) }}", 260, 0)
    s = w.set_fields("ShapeCase", [("ok", True, "boolean"), ("caseId", "={{ $json.caseId || '' }}", "string"), ("status", "={{ $json.status || '' }}", "string")], 520, -80)
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
    s = w.set_fields("ShapeComms", [("ok", "={{ !!$json.accepted }}", "boolean"), ("messageId", "={{ $json.messageId || '' }}", "string"), ("channel", "={{ $json.channel || '' }}", "string")], 520, -80)
    e = w.return_error("ReturnCommsError", 520, 120)
    w.chain(t, h)
    w.link(h, s, 0)
    w.link(h, e, 1)
    return w

# =============================================================================
# AR-01 session start
# =============================================================================
def build_ar01():
    w = WF("ar-01-session-start", "AR-01 API: Session start", "POST /webhook/ar/v1/session/start. Parses the contact-center UUI, identifies the customer, opens the case, creates the session and returns the AI disclosure plus greeting. Idempotent on correlationId.")
    w.sticky("## AR-01 Session start\nGateway calls this once the INVITE is answered. Header auth. Re-delivery of a known correlationId returns the existing session without speaking again.", 0, -400, 820, 110)
    a = w.webhook("ReceiveStart", "ar/v1/session/start", 0, 0)
    # The raw UUI string wins over pre-parsed fields: version;correlationId;vin;msisdn;language
    U = "($json.body.uui || {})"; parts = "String(($json.body.uui || {}).raw || '').split(';')"
    p = w.set_fields("ParseStart", [
        ("uuiRaw", f"={{{{ String({U}.raw || '') }}}}", "string"),
        ("version", f"={{{{ String({parts}[0] || {U}.version || '').trim() }}}}", "string"),
        ("correlationId", f"={{{{ String({parts}[1] || $json.body.correlationId || '').trim() }}}}", "string"),
        ("vin", f"={{{{ String({parts}[2] || {U}.vin || '').trim().toUpperCase() }}}}", "string"),
        ("msisdn", f"={{{{ String({parts}[3] || {U}.msisdn || '').trim() }}}}", "string"),
        ("language", f"={{{{ String({parts}[4] || {U}.language || '').trim().toLowerCase().slice(0, 2) }}}}", "string"),
        ("sipCallId", "={{ String(($json.body.sip || {}).callId || '') }}", "string"),
        ("gatewayVendor", "={{ String(($json.body.gateway || {}).vendor || '') }}", "string"),
        ("startedAt", "={{ Date.now() }}", "number"),
    ], 260, 0)
    # Market: the gateway's value, else the phone prefix, else the language, else PL.
    rm = w.set_fields("ResolveMarket", [
        ("market", "={{ String($('ReceiveStart').first().json.body.market || '').toUpperCase() || ({ '+48': 'PL', '+34': 'ES', '+49': 'DE', '+44': 'GB' })[$json.msisdn.slice(0, 3)] || ({ pl: 'PL', es: 'ES', de: 'DE', en: 'GB' })[$json.language] || 'PL' }}", "string"),
        ("message", "={{ [!$json.correlationId ? 'correlationId missing' : '', $json.version !== '1' ? 'unsupported uui version: ' + ($json.version || 'none') : ''].filter(Boolean).join('; ') }}", "string"),
    ], 390, 0, keep=True)
    iv = w.if_rules("IsValid", [w.cond("={{ $json.correlationId }}", "notEmpty"), w.cond("={{ $json.version }}", "equals", "1")], 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: 'validation_error', message: $json.message }) }}", 780, 200, code=400)
    lp, pr = w.load_prompts(780, -100)
    ex = w.dt("CheckExisting", "ar_sessions", "get", 780, -100, conditions=[("correlationId", "={{ $('ResolveMarket').first().json.correlationId }}")])
    ie = w.if_node("AlreadyStarted", "={{ $json.sessionId }}", 1040, -100, kind="string")
    ce = w.set_json("ShapeExisting", "={{ { sessionId: $json.sessionId, correlationId: $json.correlationId, caseId: $json.caseId, market: $json.market, language: $json.language, state: $json.state, customer: { known: !!$json.customerRef, greetingName: $json.greetingName, vehicle: $json.vehicleLabel }, speak: '', action: 'continue', handoff: null, listen: true, duplicate: true } }}", 1300, -260)
    rdup = w.respond("RespondExisting", "={{ JSON.stringify($json) }}", 1560, -260)
    lm = w.dt("LoadMarket", "ar_markets", "get", 1300, -40, conditions=[("market", "={{ $('ResolveMarket').first().json.market }}")])
    ic = w.exec_wf("IdentifyCustomer", "ar-10-identify-customer", {"vin": "={{ $('ParseStart').first().json.vin }}", "msisdn": "={{ $('ParseStart').first().json.msisdn }}", "correlationId": "={{ $('ParseStart').first().json.correlationId }}"}, 1560, -40)
    cc = w.exec_wf("CreateCase", "ar-15-case-system", {"op": "create", "correlationId": "={{ $('ParseStart').first().json.correlationId }}", "caseId": "", "subject": "Appointment booking via voice assistant", "classification": "Appointment Booking", "status": "In Progress", "solution": "", "delegatedReason": ""}, 1820, -40)
    gid = w.add("GenerateSessionId", "n8n-nodes-base.crypto", 2, {"action": "generate", "encodingType": "hex", "stringLength": 8, "dataPropertyName": "sessionId"}, 1950, -40)
    P, RM, M, C, K = "$('ParseStart').first().json", "$('ResolveMarket').first().json", "$('LoadMarket').first().json", "$('IdentifyCustomer').first().json", "$('CreateCase').first().json"
    known = f"({C}.ok && {C}.known)"
    cs = w.set_fields("ComposeSession", [
        ("sessionId", "={{ String($json.sessionId).toUpperCase() }}", "string"), ("correlationId", f"={{{{ {P}.correlationId }}}}", "string"),
        ("caseId", f"={{{{ {K}.caseId || '' }}}}", "string"), ("market", f"={{{{ {M}.market || {RM}.market }}}}", "string"),
        ("language", f"={{{{ {P}.language && String({M}.languages || '').split(',').map(x => x.trim()).includes({P}.language) ? {P}.language : ({M}.defaultLanguage || 'en') }}}}", "string"),
        ("backendDown", f"={{{{ {C}.ok === false || {K}.ok === false }}}}", "boolean"),
        ("state", f"={{{{ ({C}.ok === false || {K}.ok === false) ? 'HANDOFF' : 'IDENTIFY' }}}}", "string"), ("intent", "UNKNOWN", "string"),
        ("handoffReason", f"={{{{ ({C}.ok === false || {K}.ok === false) ? 'BACKEND_UNAVAILABLE' : '' }}}}", "string"),
        ("known", f"={{{{ !!{known} }}}}", "boolean"),
        ("customerRef", f"={{{{ {known} ? ({C}.customerRef || '') : '' }}}}", "string"), ("vehicleRef", f"={{{{ {P}.vin }}}}", "string"),
        ("greetingName", f"={{{{ {known} ? ({C}.greetingName || '') : '' }}}}", "string"), ("vehicleLabel", f"={{{{ {known} ? ({C}.vehicleLabel || '') : '' }}}}", "string"),
        ("dealerId", f"={{{{ {known} ? ({C}.preferredDealerId || '') : '' }}}}", "string"), ("dealerName", f"={{{{ {known} ? ({C}.preferredDealerName || '') : '' }}}}", "string"),
        ("offeredSlots", "[]", "string"), ("turns", 0, "number"), ("startedAt", f"={{{{ new Date({P}.startedAt).toISOString() }}}}", "string"),
    ], 2080, -40)
    # Disclosure (market row) plus the greeting (ar_prompts) in the session language; the handover line when a backend is down.
    greeting = (f"{prompt_text('greeting')}.replace('{{name}}', $json.greetingName ? ', ' + $json.greetingName : '')"
                f".replace('{{vehicle}}', $json.vehicleLabel ? {prompt_text('vehiclePhrase')}.replace('{{vehicle}}', $json.vehicleLabel) : '')")
    cg = w.set_fields("ComposeGreeting", [
        ("speak", f"={{{{ $json.backendDown ? ({M}.handoverLine || '') : (({M}['disclosure_' + $json.language] || {M}.disclosure_en || '') + ' ' + {greeting}).trim() }}}}", "string"),
    ], 2210, -40, keep=True)
    ins = w.dt("InsertSession", "ar_sessions", "insert", 2340, -40, always=False,
               values={c: f"={{{{ $json.{c} }}}}" for c in ["sessionId","correlationId","caseId","market","language","state","intent","customerRef","vehicleRef","greetingName","vehicleLabel","dealerId","dealerName","offeredSlots","turns","startedAt","handoffReason"]})
    lg = w.exec_wf("LogStart", "ar-17-log-event", {"correlationId": "={{ $('ComposeSession').first().json.correlationId }}", "sessionId": "={{ $('ComposeSession').first().json.sessionId }}", "caseId": "={{ $('ComposeSession').first().json.caseId }}", "workflow": "AR-01", "event": "session_start", "state": "={{ $('ComposeSession').first().json.state }}", "tool": "", "latencyMs": "={{ Date.now() - new Date($('ComposeSession').first().json.startedAt).getTime() }}", "outcome": "={{ $('ComposeSession').first().json.backendDown ? 'handoff' : 'continue' }}", "errorCode": "={{ $('ComposeSession').first().json.backendDown ? 'backend_unavailable' : '' }}", "detail": "={{ 'known=' + $('ComposeSession').first().json.known + ' market=' + $('ComposeSession').first().json.market + ' lang=' + $('ComposeSession').first().json.language }}"}, 2600, -40)
    ib = w.if_node("BackendDown", "={{ $('ComposeSession').first().json.backendDown }}", 2860, -40)
    bh = w.exec_wf("BuildStartHandoff", "ar-14-build-handoff", {"sessionId": "={{ $('ComposeSession').first().json.sessionId }}", "intent": "UNKNOWN", "handoffReason": "BACKEND_UNAVAILABLE", "summary": "Backend unavailable at session start"}, 3120, -140)
    # Response contract for session/start (section 4.1 of the spec). On the handoff path $json is BuildStartHandoff's output.
    sh = w.set_json("ShapeStart", "={{ (s => ({ sessionId: s.sessionId, correlationId: s.correlationId, caseId: s.caseId, market: s.market, language: s.language, state: s.state, customer: { known: s.known, greetingName: s.greetingName, vehicle: s.vehicleLabel }, speak: s.speak, action: s.backendDown ? 'handoff' : 'continue', handoff: s.backendDown ? { intent: 'UNKNOWN', handoffReason: 'BACKEND_UNAVAILABLE', caseId: s.caseId, uuiPayload: $json.uuiPayload, uuiHex: $json.uuiHex, uuiAscii: $json.uuiAscii, referTo: $json.referTo, error: $json.ok === false ? ($json.error || 'handoff_build_failed') : undefined } : null, listen: !s.backendDown, latencyMs: Date.now() - new Date(s.startedAt).getTime() }))($('ComposeGreeting').first().json) }}", 3380, -40)
    rs = w.respond("RespondStart", "={{ JSON.stringify($json) }}", 3640, -40)
    w.chain(a, p, rm, iv)
    w.link(iv, lp, 0); w.link(iv, r400, 1)
    w.chain(pr, ex, ie)
    w.link(ie, ce, 0); w.link(ce, rdup)
    w.link(ie, lm, 1)
    w.chain(lm, ic, cc, gid, cs, cg, ins, lg, ib)
    w.link(ib, bh, 0); w.link(bh, sh)
    w.link(ib, sh, 1)
    w.link(sh, rs)
    return w

# =============================================================================
# AR-02 dialogue turn
# =============================================================================
def validate_turn_node(w):
    """Normalises the gateway's turn body and flags what is missing. IsValid branches on `valid`."""
    B = "($json.body || {})"
    ok = f"(!!{B}.sessionId && typeof {B}.transcript === 'string')"
    return w.set_fields("ValidateTurn", [
        ("valid", f"={{{{ {ok} }}}}", "boolean"), ("statusCode", f"={{{{ {ok} ? 200 : 400 }}}}", "number"),
        ("error", f"={{{{ {ok} ? '' : 'validation_error' }}}}", "string"),
        ("message", f"={{{{ [!{B}.sessionId ? 'sessionId missing' : '', typeof {B}.transcript !== 'string' ? 'transcript missing' : ''].filter(Boolean).join('; ') }}}}", "string"),
        ("sessionId", f"={{{{ String({B}.sessionId || '') }}}}", "string"), ("correlationId", f"={{{{ String({B}.correlationId || '') }}}}", "string"),
        ("transcript", f"={{{{ String({B}.transcript || '').trim() }}}}", "string"), ("language", f"={{{{ String({B}.language || '').slice(0, 2) }}}}", "string"),
        ("asrConfidence", f"={{{{ typeof {B}.asrConfidence === 'number' ? {B}.asrConfidence : 1 }}}}", "number"),
        ("turn", f"={{{{ Number({B}.turn) || 0 }}}}", "number"), ("startedAt", "={{ Date.now() }}", "number"),
    ], 260, 0)


def validate_end_node(w):
    B = "($json.body || {})"
    return w.set_fields("ValidateEnd", [
        ("valid", f"={{{{ !!{B}.sessionId }}}}", "boolean"), ("statusCode", f"={{{{ {B}.sessionId ? 200 : 400 }}}}", "number"),
        ("error", f"={{{{ {B}.sessionId ? '' : 'validation_error' }}}}", "string"), ("message", f"={{{{ {B}.sessionId ? '' : 'sessionId missing' }}}}", "string"),
        ("sessionId", f"={{{{ String({B}.sessionId || '') }}}}", "string"), ("endReason", f"={{{{ String({B}.endReason || 'completed') }}}}", "string"),
        ("durationMs", f"={{{{ Number({B}.durationMs) || 0 }}}}", "number"), ("startedAt", "={{ Date.now() }}", "number"),
    ], 260, 0)

def add_prepare_turn(w, src):
    """Facts for this turn, the deterministic yes/no gates (YesNoGate: a small model cannot loop on a confirmed question),
    then the system prompt from ar_prompts rows. Rebuilt every turn from the session row, so state lives in n8n, not in the model.
    src: the LoadMarket node ($json is the market row). Returns the PrepareTurn node."""
    S = "$('LoadSession').first().json"; R = "$('ValidateTurn').first().json"
    sess = lambda f, default="''": f"={{{{ {S}.{f} || {default} }}}}"
    tf = w.set_fields("TurnFacts", [
        ("sessionId", f"={{{{ {S}.sessionId }}}}", "string"), ("correlationId", sess("correlationId"), "string"), ("caseId", sess("caseId"), "string"),
        ("market", sess("market"), "string"), ("language", f"={{{{ {S}.language || $json.defaultLanguage || 'en' }}}}", "string"),
        ("rowState", sess("state", "'IDENTIFY'"), "string"), ("state", sess("state", "'IDENTIFY'"), "string"),
        ("gateAdvanced", False, "boolean"), ("gateDecline", "", "string"),
        ("utter", f"={{{{ String({R}.transcript || '').trim().toLowerCase() }}}}", "string"),
        ("chatInput", f"={{{{ {R}.transcript || '(silence)' }}}}", "string"),
        ("customerRef", sess("customerRef"), "string"), ("greetingName", sess("greetingName"), "string"), ("vehicleLabel", sess("vehicleLabel"), "string"),
        ("dealerId", sess("dealerId"), "string"), ("dealerName", sess("dealerName"), "string"),
        ("bookingRef", sess("bookingRef"), "string"), ("confirmationCode", sess("confirmationCode"), "string"), ("pendingSlotId", sess("slotId"), "string"),
        ("offered", f"={{{{ JSON.parse({S}.offeredSlots || '[]') }}}}", "array"),
        ("marketBookable", "={{ String($json.bookable) !== 'false' }}", "boolean"), ("handoverLine", "={{ $json.handoverLine || '' }}", "string"),
        ("lowConf", f"={{{{ {R}.asrConfidence !== null && {R}.asrConfidence < 0.5 }}}}", "boolean"),
        ("turn", f"={{{{ (Number({S}.turns) || 0) + 1 }}}}", "number"), ("startedAt", f"={{{{ {R}.startedAt }}}}", "number"),
    ], 0, 0)
    st = lambda v: w.cond("={{ $json.rowState }}", "equals", v)
    yes, no = w.cond("={{ $json.utter }}", "regex", SAID_YES), w.cond("={{ $json.utter }}", "regex", SAID_NO)
    gate = w.switch("YesNoGate", [
        ("IdentityDenied", [st("IDENTIFY"), no], "and"),
        ("CustomerUnknown", [st("IDENTIFY"), w.cond("={{ $json.customerRef }}", "empty")], "and"),
        ("IdentityConfirmed", [st("IDENTIFY"), yes], "and"),
        ("ConsentDeclined", [st("CONSENT"), no], "and"),
        ("ConsentGiven", [st("CONSENT"), yes], "and"),
        ("DealerAgreed", [st("DEALER"), yes, w.cond("={{ $json.dealerId }}", "notEmpty")], "and"),
    ], 0, 0, fallback="NoGate")
    on = {
        "OnIdentityFailed": [("gateDecline", "IDENTIFICATION_FAILED", "string")],
        "OnIdentityConfirmed": [("state", "CONSENT", "string"), ("gateAdvanced", True, "boolean")],
        "OnConsentDeclined": [("gateDecline", "POLICY", "string")],
        "OnConsentGiven": [("state", "NEED", "string"), ("gateAdvanced", True, "boolean")],
        "OnDealerAgreed": [("state", "SLOT", "string"), ("gateAdvanced", True, "boolean")],
    }
    for name, fields in on.items():
        w.set_fields(name, fields, 0, 0, keep=True)
    w.chain(src, tf, gate)
    routes =["OnIdentityFailed", "OnIdentityFailed", "OnIdentityConfirmed", "OnConsentDeclined", "OnConsentGiven", "OnDealerAgreed"]
    af = w.set_fields("AssembleFacts", [
        ("languageName", lang_pick("languageName"), "string"),
        ("persona", f"={{{{ {prompt_text('persona', chr(39) + 'en' + chr(39))}.replace('{{language}}', {prompt_text('languageName')}) }}}}", "string"),
        ("goalText", "={{ (($('Prompts').first().json.prompts.find(p => p.key === 'goal_' + $json.state) || $('Prompts').first().json.prompts.find(p => p.key === 'goal_NEED') || {}).en || '') }}", "string"),
        ("rulesText", f"={{{{ {prompt_text('rules', chr(39) + 'en' + chr(39))} }}}}", "string"),
        ("gateLine", "={{ $json.gateAdvanced ? 'The caller has just ' + ($json.rowState === 'IDENTIFY' ? 'confirmed their identity' : $json.rowState === 'CONSENT' ? 'agreed to continue with the assistant' : 'agreed to the dealer ' + $json.dealerName + ' (dealerId ' + $json.dealerId + '); call FindSlots for it now') + '. Do not ask that again; follow the current goal.' : '' }}", "string"),
        ("slotLines", "={{ $json.offered.length ? $json.offered.map(o => '- slotId ' + o.slotId + ': ' + o.label + ' at ' + (o.dealerName || $json.dealerName)).join('\\n') : '(none offered yet)' }}", "string"),
    ], 0, 0, keep=True)
    for out, target in enumerate(routes):
        w.link(gate, target, out)
    w.link(gate, af, len(routes))
    for name in on:
        w.link(name, af)
    cf = w.set_fields("ComposeFacts", [
        ("factsText", "={{ [ $json.gateLine, 'Session state: ' + $json.state + '.', 'Customer known: ' + ($json.customerRef ? 'yes' : 'no') + '.', "
                      "$json.greetingName ? 'Name to address the customer: ' + $json.greetingName + '.' : '', $json.vehicleLabel ? 'Vehicle: ' + $json.vehicleLabel + '.' : '', "
                      "$json.dealerName ? 'Preferred dealer: ' + $json.dealerName + ' (dealerId ' + $json.dealerId + ').' : 'Preferred dealer: none on file.', "
                      "'Market: ' + $json.market + '. Online booking enabled in this market: ' + ($json.marketBookable ? 'yes' : 'no') + '.', "
                      "$json.bookingRef ? 'Booking already made in this call: confirmation code ' + $json.confirmationCode + '.' : 'No booking made yet in this call.', "
                      "'Offered slots in this call:\\n' + $json.slotLines, 'Today is ' + new Date().toISOString().slice(0, 10) + ' (UTC).' ].filter(Boolean).join('\\n') }}", "string"),
    ], 0, 0, keep=True)
    pt = w.set_fields("PrepareTurn", [
        ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId }}", "string"), ("caseId", "={{ $json.caseId }}", "string"),
        ("market", "={{ $json.market }}", "string"), ("language", "={{ $json.language }}", "string"), ("state", "={{ $json.state }}", "string"),
        ("chatInput", "={{ $json.chatInput }}", "string"),
        ("systemPrompt", "={{ $json.persona + '\\n\\nCurrent goal (state ' + $json.state + '): ' + $json.goalText + '\\n\\nFacts you may rely on:\\n' + $json.factsText + '\\n\\nRules:\\n' + $json.rulesText + ($json.lowConf ? '\\n- The speech recogniser reported low confidence for this utterance; if it is unclear, ask the caller to repeat.' : '') }}", "string"),
        ("startedAt", "={{ $json.startedAt }}", "number"), ("turn", "={{ $json.turn }}", "number"), ("lowConf", "={{ $json.lowConf }}", "boolean"),
        ("handoverLine", "={{ $json.handoverLine }}", "string"), ("offeredSlotIds", "={{ $json.offered.map(o => o.slotId) }}", "array"),
        ("pendingSlotId", "={{ $json.pendingSlotId }}", "string"), ("gateAdvanced", "={{ $json.gateAdvanced }}", "boolean"), ("gateDecline", "={{ $json.gateDecline }}", "string"),
    ], 0, 0)
    w.chain(af, cf, pt)
    return pt

# Phrase patterns for the guard rules. Matched against the lower-cased text with the If node's "matches regex" operator.
ASKS_FOR_HUMAN = r"(\b(agent|human|person|operator|someone real|real person)\b|konsultant|cz[łl]owiek|pracownik|doradc|persona|humano|agente|operador|mitarbeiter|mensch|berater|echte person)"
DECLINES_HUMAN = r"\b(no|nie|nein|not|kein)\b[^.]{0,12}(agent|human|person|konsultant|persona|mitarbeiter)"
CLAIMS_BOOKING = r"((is|has been|was|are|'s) (now |all )?(booked|confirmed|reserved|scheduled)|confirmation code|zosta[łl][ao]? (zarezerwowan|potwierdzon|umówion)|kod potwierdzenia|(ha sido|está|queda) (reservad|confirmad)|código de confirmación|(ist|wurde) (gebucht|bestätigt|reserviert)|bestätigungscode)"

SAID_YES = r"^(tak|zgadza|owszem|dok[łl]adnie|to ja|oczywi[śs]cie|jasne|s[íi]|claro|correcto|as[íi] es|soy yo|exacto|vale|de acuerdo|por supuesto|ja|genau|richtig|korrekt|das bin ich|stimmt|nat[üu]rlich|gerne|in ordnung|yes|yeah|yep|correct|that'?s me|that is me|sure|of course|go ahead|ok(ay)?)(?=[\s,.!?;:]|$)"
SAID_NO = r"^(nie|no|nein|not me|wrong)(?=[\s,.!?;:]|$)"

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
         "OnFalseConfirmation", [("speak", lang_pick("notBooked"), "string"),
                                 ("state", "={{ ['BOOKED','WRAPUP'].includes($json.state) ? 'CONFIRM' : $json.state }}", "string"),
                                 ("action", "={{ $json.action === 'end' ? 'continue' : $json.action }}", "string"),
                                 ("guards", with_guard("false_confirmation"), "array")]),
        ("ForceBooked", [w.cond("={{ $json.booked }}", "true", kind="boolean"),
                         w.cond("={{ ['BOOKED','WRAPUP','HANDOFF'].includes($json.state) }}", "false", kind="boolean")], "and",
         "OnForceBooked", [("state", "BOOKED", "string"), ("guards", with_guard("state_forced_booked"), "array")]),
        ("EmptyReply", [w.cond("={{ $json.speak }}", "empty")], "and",
         "OnEmptyReply", [("speak", lang_pick("repeat"), "string"), ("guards", with_guard("empty_reply"), "array")]),
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
    fixed = {"speak": lang_pick("techError", f"{P}.language"), "action": "handoff", "state": "HANDOFF", "intent": "UNKNOWN", "handoffReason": "PLATFORM_ERROR",
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
    v = validate_turn_node(w)
    iv = w.if_node("IsValid", "={{ $json.valid }}", 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: $json.error, message: $json.message }) }}", 780, 200, code="={{ $json.statusCode }}")
    lp, pr = w.load_prompts(780, -100)
    ls = w.dt("LoadSession", "ar_sessions", "get", 1200, -100, conditions=[("sessionId", "={{ $('ValidateTurn').first().json.sessionId }}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 1040, -100, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 1300, 60, code=404)
    lm = w.dt("LoadMarket", "ar_markets", "get", 1300, -180, conditions=[("market", "={{ $json.market }}")])
    pt = add_prepare_turn(w, lm)
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
    w.link(iv, lp, 0); w.link(iv, r400, 1)
    w.chain(pr, ls, ifound)
    w.link(ifound, lm, 0); w.link(ifound, r404, 1)
    w.link(pt, ag)
    w.link(ag, rl, 0); w.link(ag, he, 1)
    w.link(gd, ih); w.link(he, ih)
    w.link(ih, bh, 0); w.link(ih, cr, 1)
    w.chain(bh, ah, cr)
    w.chain(cr, us, lg, rs)
    return w

# =============================================================================
# AR-03 session end
# =============================================================================
def build_ar03():
    w = WF("ar-03-session-end", "AR-03 API: Session end", "POST /webhook/ar/v1/session/end. Closes or delegates the case, sends the confirmation if owed, purges the chat memory rows for the session, finalises the session row and logs. Idempotent.")
    w.sticky("## AR-03 Session end\nCase outcome from the session state. Chat memory for the session is deleted here: no transcript survives the call.", 0, -400, 760, 100)
    a = w.webhook("ReceiveEnd", "ar/v1/session/end", 0, 0)
    v = validate_end_node(w)
    iv = w.if_node("IsValid", "={{ $json.valid }}", 520, 0)
    r400 = w.respond("RespondInvalid", "={{ JSON.stringify({ error: $json.error, message: $json.message }) }}", 780, 200, code="={{ $json.statusCode }}")
    lp, pr = w.load_prompts(780, -100)
    R = "$('ValidateEnd').first().json"
    ls = w.dt("LoadSession", "ar_sessions", "get", 1200, -100, conditions=[("sessionId", f"={{{{ {R}.sessionId }}}}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 1040, -100, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 1300, 60, code=404)
    # Case outcome, in order: a booking exists; the session ended in HANDOFF or the gateway reports a handoff end; otherwise closed.
    co = w.switch("CaseOutcome", [
        ("Booked", [w.cond("={{ $json.bookingRef || '' }}", "notEmpty")], "and"),
        ("HandedOff", [w.cond("={{ $json.state || '' }}", "equals", "HANDOFF")], "and"),
        ("HandoffEnd", [w.cond(f"={{{{ String({R}.endReason || '') }}}}", "startsWith", "handoff")], "and"),
    ], 1300, -180, fallback="Closed")
    ob = w.set_fields("OnBooked", [("caseStatus", "Resolved", "string"),
        ("solution", "={{ 'Appointment booked via voice assistant. Confirmation code ' + $json.confirmationCode + '. Dealer ' + ($json.dealerName || $json.dealerId) + '. Slot ' + $json.slotId + '.' }}", "string"),
        ("delegatedReason", "", "string")], 1300, 0, keep=True)
    oh = w.set_fields("OnHandedOff", [("caseStatus", "Delegated", "string"), ("solution", "", "string"),
        ("delegatedReason", "={{ ('Handed to agent: ' + ($json.handoffReason || 'unknown') + '. ' + ($json.handoffSummary || '')).trim() }}", "string")], 1300, 140, keep=True)
    oc = w.set_fields("OnClosed", [("caseStatus", "Closed", "string"),
        ("solution", f"={{{{ 'Call ended without booking (' + {R}.endReason + ') in state ' + ($json.state || '') + '.' }}}}", "string"),
        ("delegatedReason", "", "string")], 1300, 280, keep=True)
    d = w.set_fields("DecideOutcome", [
        ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId || '' }}", "string"), ("caseId", "={{ $json.caseId || '' }}", "string"),
        ("caseStatus", "={{ $json.caseStatus }}", "string"), ("solution", "={{ $json.solution }}", "string"), ("delegatedReason", "={{ $json.delegatedReason }}", "string"),
        ("booked", "={{ !!$json.bookingRef }}", "boolean"),
        ("needsConfirmation", "={{ !!$json.bookingRef && String($json.confirmationSent) !== 'true' }}", "boolean"),
        ("language", "={{ $json.language || 'en' }}", "string"), ("customerRef", "={{ $json.customerRef || '' }}", "string"),
        ("confirmationChannel", "={{ $json.confirmationChannel || 'sms' }}", "string"),
        ("confirmationText", f"={{{{ {prompt_text('confirmation')}.replace('{{code}}', $json.confirmationCode || '').replace('{{dealer}}', $json.dealerName || '').replace('{{slot}}', $json.slotId || '') }}}}", "string"),
        ("endReason", f"={{{{ {R}.endReason }}}}", "string"), ("state", "={{ $json.state || '' }}", "string"), ("durationMs", f"={{{{ {R}.durationMs }}}}", "number"),
    ], 1500, -180)
    w.link(co, ob, 0); w.link(co, oh, 1); w.link(co, oh, 2); w.link(co, oc, 3)
    for n in (ob, oh, oc):
        w.link(n, d)
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
    w.link(iv, lp, 0); w.link(iv, r400, 1)
    w.chain(pr, ls, ifound)
    w.link(ifound, co, 0); w.link(ifound, r404, 1)
    w.chain(d, cc, ic)
    w.link(ic, sc, 0); w.link(sc, pm)
    w.link(ic, pm, 1)
    w.chain(pm, fs, lg, rs)
    return w

# =============================================================================
# AR-04 transfer result
# =============================================================================
def build_ar04():
    w = WF("ar-04-transfer-result", "AR-04 API: Transfer result", "POST /webhook/ar/v1/session/transfer-result. Records the REFER NOTIFY outcome and tells the gateway what to do: release, speak and retry once, or speak and end.")
    w.sticky("## AR-04 Transfer result\nREFER acceptance is not completion. The outcome is stored on the session and the retry policy is one attempt: TransferOutcome routes to release, retry once, or end.", 0, -400, 700, 100)
    a = w.webhook("ReceiveTransferResult", "ar/v1/session/transfer-result", 0, 0)
    lp, pr = w.load_prompts(260, 0)
    B = "$('ReceiveTransferResult').first().json.body"
    ls = w.dt("LoadSession", "ar_sessions", "get", 680, 0, conditions=[("sessionId", f"={{{{ {B}.sessionId }}}}")])
    ifound = w.if_node("SessionFound", "={{ $json.sessionId }}", 520, 0, kind="string")
    r404 = w.respond("RespondNotFound", "={{ JSON.stringify({ error: 'not_found', message: 'unknown sessionId' }) }}", 780, 160, code=404)
    # One retry, never a loop (contact-center requirement): NOTIFY 2xx releases; a first failure retries once; a second failure ends the call.
    to = w.switch("TransferOutcome", [
        ("Released", [w.cond(f"={{{{ {B}.referAccepted !== false }}}}", "true", kind="boolean"),
                      w.cond(f"={{{{ String({B}.notifyStatus || '') }}}}", "regex", r"^2\d\d")], "and"),
        ("RetryOnce", [w.cond("={{ $json.handoffResult || '' }}", "notStartsWith", "failed")], "and"),
    ], 780, -80, fallback="Final")
    status = f"({B}.notifyStatus || 'unknown')"
    orl = w.set_fields("OnReleased", [("action", "release", "string"), ("speak", "", "string"),
                                       ("handoffResult", f"={{{{ 'completed:' + ({B}.notifyStatus || '') }}}}", "string")], 780, 60, keep=True)
    ort = w.set_fields("OnRetryOnce", [("action", "speak_and_retry_once", "string"), ("speak", lang_pick("retryTransfer"), "string"),
                                        ("handoffResult", f"={{{{ 'failed:' + {status} }}}}", "string")], 780, 200, keep=True)
    ofn = w.set_fields("OnFinal", [("action", "speak_and_end", "string"), ("speak", lang_pick("transferFailed"), "string"),
                                    ("handoffResult", f"={{{{ 'failed_final:' + {status} }}}}", "string")], 780, 340, keep=True)
    d = w.set_fields("DecideTransferAction", [
        ("sessionId", "={{ $json.sessionId }}", "string"), ("correlationId", "={{ $json.correlationId || '' }}", "string"), ("caseId", "={{ $json.caseId || '' }}", "string"),
        ("action", "={{ $json.action }}", "string"), ("speak", "={{ $json.speak }}", "string"), ("handoffResult", "={{ $json.handoffResult }}", "string"),
        ("notifyStatus", f"={{{{ String({B}.notifyStatus || '') }}}}", "string"), ("state", "={{ $json.state || '' }}", "string"),
    ], 900, -80)
    w.link(to, orl, 0); w.link(to, ort, 1); w.link(to, ofn, 2)
    for n in (orl, ort, ofn):
        w.link(n, d)
    u = w.dt("UpdateTransfer", "ar_sessions", "update", 1040, -80, conditions=[("sessionId", "={{ $json.sessionId }}")], values={"handoffResult": "={{ $json.handoffResult }}"})
    lg = w.exec_wf("LogTransfer", "ar-17-log-event", {"correlationId": "={{ $('DecideTransferAction').first().json.correlationId }}", "sessionId": "={{ $('DecideTransferAction').first().json.sessionId }}", "caseId": "={{ $('DecideTransferAction').first().json.caseId }}", "workflow": "AR-04", "event": "transfer_result", "state": "={{ $('DecideTransferAction').first().json.state }}", "tool": "", "latencyMs": 0, "outcome": "={{ $('DecideTransferAction').first().json.action }}", "errorCode": "={{ $('DecideTransferAction').first().json.action === 'release' ? '' : 'refer_failed' }}", "detail": "={{ 'notify=' + $('DecideTransferAction').first().json.notifyStatus }}"}, 1300, -80)
    rs = w.respond("RespondTransfer", "={{ JSON.stringify({ action: $('DecideTransferAction').first().json.action, speak: $('DecideTransferAction').first().json.speak }) }}", 1560, -80)
    w.chain(a, lp)
    w.chain(pr, ls, ifound)
    w.link(ifound, to, 0); w.link(ifound, r404, 1)
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
    s = w.set_json("ShapeHandoffContext", "={{ $json.sessionId ? { found: true, statusCode: 200, correlationId: $json.correlationId, sessionId: $json.sessionId, caseId: $json.caseId, state: $json.state, intent: $json.intent, handoffReason: $json.handoffReason || null, handoffResult: $json.handoffResult || null, summary: $json.handoffSummary || '', dealer: $json.dealerName || null, slotId: $json.slotId || null, confirmationCode: $json.confirmationCode || null, language: $json.language, market: $json.market, startedAt: $json.startedAt, endedAt: $json.endedAt || null } : { found: false, statusCode: 404 } }}", 520, 200)
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
    E = "($json.execution || {})"
    s = w.set_fields("ShapeError", [
        ("at", "={{ $now.toUTC().toISO() }}", "string"), ("correlationId", "", "string"), ("sessionId", "", "string"), ("caseId", "", "string"),
        ("workflow", "={{ ($json.workflow || {}).name || '' }}", "string"), ("event", "error", "string"), ("state", "", "string"),
        ("tool", f"={{{{ {E}.lastNodeExecuted || '' }}}}", "string"), ("latencyMs", 0, "number"), ("outcome", "failed", "string"), ("errorCode", "workflow_error", "string"),
        ("detail", f"={{{{ String(({E}.error || {{}}).message || '').slice(0, 300) + ' execution=' + ({E}.id || '') }}}}", "string"),
    ], 260, 0)
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


def _prompt(key, en, pl="", es="", de="", note=""):
    return {"key": key, "pl": pl, "es": es, "de": de, "en": en, "note": note}

GOALS = {
    "IDENTIFY": "Confirm you are speaking with the customer named in the facts about the vehicle named in the facts, in one short question. Never read out a VIN or a phone number. If the caller says it is not them, or no customer is known, set action handoff with handoffReason IDENTIFICATION_FAILED. When confirmed, move to CONSENT.",
    "CONSENT": "The AI disclosure was already spoken. Ask once whether the caller is happy to continue with the assistant. Yes: move to NEED. No: set action handoff with handoffReason POLICY.",
    "NEED": "Find out what the caller needs: a new appointment (intent BOOK), moving an existing one (RESCHEDULE), cancelling (CANCEL) or something else (OTHER, then action handoff with SCENARIO_REQUIRES_AGENT). For BOOK, collect the service type (inspection, regular service, tyre change, repair, warning light, other) and whether replacement mobility is needed, then move to DEALER.",
    "DEALER": "Propose the preferred dealer from the facts; it can be booked here. If the caller wants a different one, ask for the city and call FindDealers. Only a dealer that FindDealers returned with bookable false cannot be booked here: then set action handoff with SCENARIO_REQUIRES_AGENT. When a dealer is agreed, move to SLOT and call FindSlots.",
    "SLOT": "Call FindSlots for the agreed dealer and the earliest date the caller wants, then offer at most three options by day and time, never by slotId. If none fits, call FindSlots once more with a later fromDate; if still none, set action handoff with NO_SLOT. When the caller picks one, move to CONFIRM.",
    "CONFIRM": "Read back dealer, day, time, service type and mobility, put the slotId of that option in the slotId field, and ask for an explicit yes. Booking is only possible in the turn after the read-back, once the caller has said yes: then call BookAppointment with the same slotId. If BookAppointment answers not_confirmed, read the slot back again and ask for a yes. Never say the appointment is booked before BookAppointment returns a confirmation code. After a confirmation code, move to BOOKED and read the code back.",
    "BOOKED": "Tell the caller the confirmation code slowly, that a confirmation message will follow, ask if there is anything else. If nothing else, say goodbye with action end and state WRAPUP.",
    "WRAPUP": "Close politely with action end.",
    "HANDOFF": "Say a colleague will take over and set action handoff.",
}
RULES_TEXT = "\n".join([
    "- Never invent a dealer, a time slot, a confirmation code or any customer data. Dealers come from FindDealers, slots from FindSlots, confirmation codes from BookAppointment.",
    "- Never say an appointment is booked, confirmed or reserved unless BookAppointment returned a confirmation code in this call.",
    "- If the caller asks for a person at any point, set action handoff with handoffReason CALLER_REQUEST.",
    "- If you could not understand the caller twice in a row, set action handoff with handoffReason NOT_UNDERSTOOD.",
    "- If a tool reports it is unavailable, apologise once and set action handoff with handoffReason BACKEND_UNAVAILABLE.",
    "- Move the state forward only when its goal is met. Allowed states: IDENTIFY, CONSENT, NEED, DEALER, SLOT, CONFIRM, BOOKED, WRAPUP, HANDOFF.",
    "- Return only json matching the schema. The speak field is read aloud to the caller.",
])
# Everything the assistant says or is told, one row per key. Language columns pl, es, de, en; English is the fallback.
PROMPT_ROWS = [
    _prompt("persona", "You are the workshop service appointment assistant on a live phone call. Speak {language} only. Keep every reply under 45 words, one question at a time, no lists, no markdown; say numbers, dates and times the way a person would on the phone.", note="First paragraph of the system prompt. {language} is the session language name."),
    _prompt("rules", RULES_TEXT, note="Rules block of the system prompt, one bullet per line."),
    _prompt("languageName", "English", "Polish", "Spanish", "German", note="Language name spoken into the persona line."),
] + [_prompt("goal_" + k, v, note="Goal for state " + k) for k, v in GOALS.items()] + [
    _prompt("greeting", "Hello{name}. How can I help with your service appointment{vehicle}?",
            "Dzień dobry{name}. W czym mogę pomóc w związku z wizytą serwisową{vehicle}?", "Buenos días{name}. ¿En qué puedo ayudarle con su cita de taller{vehicle}?",
            "Guten Tag{name}. Wie kann ich Ihnen bei Ihrem Werkstatttermin{vehicle} helfen?", note="Spoken after the disclosure. {name} becomes ', <name>' and {vehicle} the vehiclePhrase when known."),
    _prompt("vehiclePhrase", " for your {vehicle}", " dla Państwa {vehicle}", " para su {vehicle}", " für Ihren {vehicle}", note="Inserted into the greeting when the vehicle is known."),
    _prompt("notBooked", "I have not booked the appointment yet. Shall I book it now?", "Jeszcze nie zarezerwowałem terminu. Czy mam go teraz zarezerwować?",
            "Todavía no he reservado la cita. ¿Quiere que la reserve ahora?", "Ich habe den Termin noch nicht gebucht. Soll ich ihn jetzt buchen?", note="Replaces a booking claim made without a confirmation code (guard false_confirmation)."),
    _prompt("repeat", "Sorry, could you repeat that?", "Przepraszam, czy mogą Państwo powtórzyć?", "Disculpe, ¿puede repetirlo?", "Entschuldigung, können Sie das wiederholen?", note="Spoken when the model returned an empty reply."),
    _prompt("techError", "Sorry, I have a technical problem. I will connect you to a colleague.", "Przepraszam, mam problem techniczny. Połączę Państwa z konsultantem.",
            "Disculpe, tengo un problema técnico. Le paso con un agente.", "Entschuldigung, ich habe ein technisches Problem. Ich verbinde Sie mit einem Mitarbeiter.", note="Spoken when the dialogue agent fails (PLATFORM_ERROR handoff)."),
    _prompt("confirmation", "Workshop: service appointment confirmed. Code {code}, {dealer}, slot {slot}.", "Serwis: wizyta serwisowa potwierdzona. Kod {code}, {dealer}, termin {slot}.",
            "Taller: cita de taller confirmada. Código {code}, {dealer}, cita {slot}.", "Werkstatt: Werkstatttermin bestätigt. Code {code}, {dealer}, Termin {slot}.", note="SMS or e-mail text sent at session end when a booking exists."),
    _prompt("retryTransfer", "One moment, I am trying the transfer again.", "Chwileczkę, próbuję ponownie połączyć z konsultantem.", "Un momento, vuelvo a intentar pasarle con un agente.", "Einen Moment, ich versuche die Weiterleitung noch einmal.", note="First REFER failure."),
    _prompt("transferFailed", "I could not transfer you. A colleague will call you back. Goodbye.", "Nie udało mi się połączyć. Konsultant oddzwoni do Państwa. Do widzenia.",
            "No he podido transferirle. Un agente le devolverá la llamada. Adiós.", "Die Weiterleitung ist fehlgeschlagen. Ein Mitarbeiter ruft Sie zurück. Auf Wiederhören.", note="Second REFER failure: the call ends."),
]


def build_ar90():
    w = WF("ar-90-seed-config", "AR-90 Seed configuration tables", "POST /webhook/ar/v1/admin/seed. Upserts ar_markets (PL, ES, DE, GB), ar_codes (intent and handoff reason codes) and ar_prompts (goals, rules and every spoken line, per language). Run once per environment; safe to re-run.")
    w.sticky("## AR-90 Seed config\nMarkets, codes and prompts are data, not code: a new market is one row, a wording change is one cell. Disclosure texts are placeholders until Legal supplies the market wording.", 0, -400, 760, 100)
    a = w.webhook("ReceiveSeed", "ar/v1/admin/seed", 0, 0)
    code_rows = [dict(zip(["kind","code","short","ccValue","description"], r)) for r in CODE_ROWS]
    seeds = [("Market", "ar_markets", MARKET_ROWS, [("market", "={{ $json.market }}")]),
             ("Code", "ar_codes", code_rows, [("kind", "={{ $json.kind }}"), ("code", "={{ $json.code }}")]),
             ("Prompt", "ar_prompts", PROMPT_ROWS, [("key", "={{ $json.key }}")]),
             ("MockCustomer", "ar_mock_customers", MOCK_CUSTOMERS, [("vin", "={{ $json.vin }}")]),
             ("MockDealer", "ar_mock_dealers", MOCK_DEALERS, [("dealerId", "={{ $json.dealerId }}")])]
    prev = a
    for i, (label, table, rows, match) in enumerate(seeds):
        # executeOnce: the previous upsert hands over one item per row; without it each table's rows would be emitted once per incoming item.
        r = w.set_json(f"{label}Rows", json.dumps({"rows": rows}, ensure_ascii=False, indent=2), 260 + i * 780, 0, executeOnce=True)
        s = w.split_out(f"Split{label}s", "rows", 520 + i * 780, 0)
        u = w.dt(f"Upsert{label}", table, "upsert", 780 + i * 780, 0, conditions=match, values={c: f"={{{{ $json.{c} }}}}" for c in TABLE_COLS[table]})
        w.chain(prev, r, s, u)
        prev = u
    rs = w.respond("RespondSeeded", "={{ JSON.stringify({ ok: true, markets: " + str(len(MARKET_ROWS)) + ", codes: " + str(len(CODE_ROWS)) + ", prompts: " + str(len(PROMPT_ROWS)) + ", mockCustomers: " + str(len(MOCK_CUSTOMERS)) + ", mockDealers: " + str(len(MOCK_DEALERS)) + " }) }}", 4160, 0)
    w.link(prev, rs)
    return w


BUILDERS = [build_ar17, build_ar20, build_ar21, build_ar22, build_ar23, build_ar10, build_ar11, build_ar12, build_ar13, build_ar14, build_ar15, build_ar16,
            build_ar01, build_ar02, build_ar03, build_ar04, build_ar05, build_ar30, build_ar90]

# =============================================================================
# Canvas zones (n8n-canvas-docs): one entry per stage, rows of node names, None = empty column.
# Colours: 4 green human touchpoints, 3 red state and critical calls, 5 blue logic, 6 purple setup, 7 gray docs.
# =============================================================================
ZONES = {
    "ar-01-session-start": [
        {"title": "Receive", "color": 4, "body": "The gateway posts the SIP UUI once the INVITE is answered. Header auth. ParseStart splits the raw UUI; ResolveMarket: gateway value, phone prefix, language, PL. A missing correlationId or a UUI version other than 1 answers 400 and the call is released.",
         "rows": [["ReceiveStart", "ParseStart", "ResolveMarket", "IsValid"], [None, None, None, "RespondInvalid"]]},
        {"title": "Idempotency", "color": 5, "body": "Prompts are loaded once. A re-delivered correlationId returns the existing session without speaking again.",
         "rows": [["LoadPrompts", "Prompts", "CheckExisting", "AlreadyStarted"], [None, None, "ShapeExisting", "RespondExisting"]]},
        {"title": "Identify and open the case", "color": 3, "body": "Market row, CRM lookup by VIN, case. The model never sees the VIN or the number.",
         "rows": [["LoadMarket", "IdentifyCustomer", "CreateCase"]]},
        {"title": "Session", "color": 3, "body": "Random session id, one row in ar_sessions per call, state starts at IDENTIFY. The greeting is the market's disclosure plus the ar_prompts greeting in the session language. Every start is logged to ar_events.",
         "rows": [["GenerateSessionId", "ComposeSession", "ComposeGreeting", "InsertSession", "LogStart"]]},
        {"title": "Greet or hand over", "color": 4, "body": "Backend down at start: the handover payload is built here and the greeting becomes the handover line.",
         "rows": [["BackendDown", "ShapeStart", "RespondStart"], [None, "BuildStartHandoff"]]},
    ],
    "ar-02-dialogue-turn": [
        {"title": "Receive", "color": 4, "body": "One turn per call: sessionId, transcript, ASR confidence.",
         "rows": [["ReceiveTurn", "ValidateTurn", "IsValid"], [None, None, "RespondInvalid"]]},
        {"title": "Load state", "color": 3, "body": "Prompts (goals, rules, spoken lines), the session row and the market row are the only state the model sees.",
         "rows": [["LoadPrompts", "Prompts", "LoadSession", "SessionFound", "LoadMarket"], [None, None, None, "RespondNotFound"]]},
        {"title": "Gates and prompt", "color": 5, "body": "YesNoGate: a deterministic yes or no in IDENTIFY, CONSENT and DEALER moves the state before the model runs, so a small model cannot loop on a confirmed question. The system prompt is rebuilt every turn from ar_prompts and the facts.",
         "rows": [["TurnFacts", "YesNoGate", "AssembleFacts", "ComposeFacts", "PrepareTurn"], [None, "OnIdentityFailed"], [None, "OnIdentityConfirmed"], [None, "OnConsentDeclined"], [None, "OnConsentGiven"], [None, "OnDealerAgreed"]]},
        {"title": "Reason", "color": 5, "body": "The agent has three tools, Postgres memory and a structured parser with auto-fix.",
         "rows": [["DialogueAgent"], ["DialogueModel", "TurnMemory", "TurnOutputShape"], ["FindDealers", "FindSlots", "BookAppointment", "ParserFixerModel"]]},
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
        {"title": "Load state", "color": 3, "body": "The confirmation text comes from ar_prompts; the session row decides the outcome.",
         "rows": [["LoadPrompts", "Prompts", "LoadSession", "SessionFound"], [None, None, None, "RespondNotFound"]]},
        {"title": "Outcome", "color": 3, "body": "CaseOutcome in order: a booking exists (Resolved); ended in HANDOFF or a handoff end reason (Delegated); otherwise Closed. A confirmation goes out when a booking exists.",
         "rows": [["CaseOutcome", "DecideOutcome", "CloseCase", "NeedsConfirmation"], ["OnBooked", None, None, "SendConfirmation"], ["OnHandedOff"], ["OnClosed"]]},
        {"title": "Close", "color": 4, "body": "Chat memory purged, session finalised, end logged. Nothing of the call is retained beyond ar_events.",
         "rows": [["PurgeMemory", "FinalizeSession", "LogEnd", "RespondEnd"]]},
    ],
    "ar-04-transfer-result": [
        {"title": "Receive", "color": 4, "body": "NOTIFY result of the REFER, posted by the gateway.",
         "rows": [["ReceiveTransferResult", "LoadPrompts", "Prompts", "LoadSession", "SessionFound"], [None, None, None, None, "RespondNotFound"]]},
        {"title": "Decide", "color": 5, "body": "TransferOutcome: NOTIFY 2xx releases; a first failure speaks and retries once; a second failure speaks and ends. One retry, never a loop.",
         "rows": [["TransferOutcome", "DecideTransferAction", "UpdateTransfer", "LogTransfer", "RespondTransfer"], ["OnReleased"], ["OnRetryOnce"], ["OnFinal"]]},
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
        {"title": "Find slots", "color": 5, "body": "DealerBook availability for the agreed dealer from the requested date; at most maxSlotsOffered (market row), labelled in the session language.",
         "rows": [["ToolInput", "LoadSession", "LoadMarket", "CallDealerBookSlots", "ShapeSlots"], [None, None, None, None, "ReturnSlotsError"]]},
        {"title": "Remember what was offered", "color": 3, "body": "Offered slots are stored on the session so a booking can only name one of them; the tool returns them by day and time, never by slotId.",
         "rows": [["OfferSlots", "StoreOffered", "ReturnSlots"]]},
    ],
    "ar-13-book-appointment": [
        {"title": "Booking gate", "color": 5, "body": "Rules in order: a booking already exists; the slot was not offered in this call; the caller has not confirmed it (state CONFIRM with this slotId read back). Only Proceed books.",
         "rows": [["ToolInput", "LoadSession", "BookingGate", "BookingRequest"], [None, None, None, "ReplyAlreadyBooked"], [None, None, None, "ReplySlotNotOffered"], [None, None, None, "ReplyNotConfirmed"]]},
        {"title": "Book", "color": 3, "body": "Idempotency key session:slot. The confirmation code comes from DealerBook, never from the model.",
         "rows": [["CallDealerBookBook", "StoreBooking", "ReturnBooking"], ["ReturnBookError"]]},
    ],
    "ar-14-build-handoff": [
        {"title": "Context", "color": 5, "body": "Session and market rows (return number, default language) and the short codes for the intent and the handoff reason from ar_codes.",
         "rows": [["ToolInput", "LoadSession", "LoadMarket", "LoadIntentCode", "LoadReasonCode"]]},
        {"title": "Return UUI", "color": 3, "body": "AssemblePayload builds version;correlationId;sessionId;intent;reason;language plus caseId. FitsBudget checks 64 bytes; DropCaseId removes the case id first and logs it. EncodeUui hex-encodes and builds the Refer-To, stored on the session for the lookup endpoint.",
         "rows": [["AssemblePayload", "FitsBudget", "EncodeUui", "StoreHandoff", "ReturnHandoff"], [None, "DropCaseId"]]},
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
        {"title": "Mock CRM", "color": 7, "body": "Synthetic customers in ar_mock_customers, keyed by VIN. Lab only; the real build swaps the table read for one HTTP Request.",
         "rows": [["ReceiveLookup", "LookupCustomer", "ShapeFixture", "RespondCustomer"]]},
    ],
    "ar-21-mock-dealerbook": [
        {"title": "Dealers", "color": 7, "body": "Rows from ar_mock_dealers, narrowed by market, dealerId and city (accent-insensitive).",
         "rows": [["ReceiveDealers", "LoadDealers", "AllDealers", "FilterDealers", "RespondDealers"]]},
        {"title": "Slots", "color": 7, "body": "Dealer row from ar_mock_dealers; slots deterministic from dealerId and date so tests repeat (the one Code node kept in the mocks).",
         "rows": [["ReceiveSlots", "LoadDealer", "GenerateSlots", "RespondSlots"]]},
        {"title": "Book", "color": 7, "body": "Required fields, then the FULL probe (409), then a confirmation code hashed from the idempotency key. Bookings persist in ar_mock_bookings.",
         "rows": [["ReceiveBook", "BookInvalid", "SlotFull", "HashKey", "ComputeBooking", "IsConflict", "UpsertBooking", "RespondBooked"], [None, "ReplyInvalid", "ReplyConflict", None, None, "RespondConflict"]]},
        {"title": "Cancel", "color": 7, "body": "Marks the booking cancelled.",
         "rows": [["ReceiveCancel", "CancelBooking", "RespondCancelled"]]},
        {"title": "Bookings by VIN", "color": 7, "body": "Lists bookings for reschedule and cancel flows.",
         "rows": [["ReceiveBookings", "FindBookingsByVin", "AllBookings", "ShapeBookings", "RespondBookings"]]},
    ],
    "ar-22-mock-case-system": [
        {"title": "Mock case system", "color": 7, "body": "Case number from a hash of the correlationId; rows in ar_mock_cases.",
         "rows": [["ReceiveCaseOp", "HashCorrelation", "ComputeCase", "UpsertCase", "RespondCase"]]},
    ],
    "ar-23-mock-comms": [
        {"title": "Mock comms", "color": 7, "body": "customerRef and text required; returns a message id, sends nothing.",
         "rows": [["ReceiveSend", "HasRequired", "AcceptMessage", "RespondSend"], [None, None, "RejectMessage"]]},
    ],
    "ar-30-error-workflow": [
        {"title": "Error trigger", "color": 3, "body": "Every AR-* failure lands in ar_events with workflow, node and message. Assign in Workflow Settings.",
         "rows": [["OnWorkflowError", "ShapeError", "InsertErrorEvent"]]},
    ],
    "ar-90-seed-config": [
        {"title": "Seed", "color": 6, "body": "Rows live in the Edit Fields nodes as JSON; Split Out makes one item per row; upserts are keyed on the natural key. Run once per environment; safe to re-run.",
         "rows": [["ReceiveSeed", "MarketRows", "SplitMarkets", "UpsertMarket"], [None, "CodeRows", "SplitCodes", "UpsertCode"], [None, "PromptRows", "SplitPrompts", "UpsertPrompt"],
                  [None, "MockCustomerRows", "SplitMockCustomers", "UpsertMockCustomer"], [None, "MockDealerRows", "SplitMockDealers", "UpsertMockDealer", "RespondSeeded"]]},
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
