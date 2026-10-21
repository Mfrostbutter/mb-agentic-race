#!/usr/bin/env python3
"""Node policy check: native n8n nodes first. Standard library only, offline.

  node_policy_check.py <workflow.json | dir> [...] [--policy <node-policy.json>] [--json]

Prints the findings, then RESULT: ok or RESULT: <n> finding(s). Exit 1 on an
error-level finding; tools/deploy_workflow.py runs it as a HARD gate.

Rules
  community-node        a node type n8n does not ship (not n8n-nodes-base.* or
                        @n8n/n8n-nodes-langchain.*) needs a recorded exception or
                        an entry in the engagement's deploy/node-policy.json
  http-native-available an HTTP Request (or HTTP Request Tool) node calls a host
                        that a native node covers; use that node or record why not
  code-http             a Code node makes an HTTP call; it cannot use a credential,
                        the HTTP Request node can
  exception-invalid     an exception without a reason and an approver is not one
  exception-stale       an exception names a node the workflow does not have (warn)

Exceptions live in the workflow JSON next to "project" and "folder":
  "nodePolicy": {"exceptions": [
    {"node": "PostToLegacyErp", "rule": "community-node",
     "reason": "no native node; package reviewed by customer security 2026-10-14",
     "approvedBy": "J. Doe (customer security)", "date": "2026-10-14"}]}

An engagement-wide allowlist (a package the customer cleared once) lives in
deploy/node-policy.json:
  {"allowedNodeTypes": {"n8n-nodes-foo.bar": {"reason": "...", "approvedBy": "...", "date": "..."}}}
Resolved from --policy, N8N_NODE_POLICY, ./deploy/node-policy.json, or a
deploy/node-policy.json up to three levels above the workflow file.

Runs under Python 3.9+ (no 3.10 syntax: the PreToolUse hook imports it).
"""
import json
import os
import re
import sys

NATIVE_PREFIXES = ("n8n-nodes-base.", "@n8n/n8n-nodes-langchain.")
HTTP_TYPES = ("n8n-nodes-base.httpRequest", "@n8n/n8n-nodes-langchain.toolHttpRequest")
CODE_TYPES = ("n8n-nodes-base.code", "@n8n/n8n-nodes-langchain.toolCode")
RULES = ("community-node", "http-native-available", "code-http")

# Hosts a native node covers. (host regex, path regex or None, native node, label)
NATIVE_HOSTS = [
    (r"(^|\.)slack\.com$", None, "n8n-nodes-base.slack", "Slack"),
    (r"^api\.github\.com$", None, "n8n-nodes-base.github", "GitHub"),
    (r"^gitlab\.com$", r"^/api/", "n8n-nodes-base.gitlab", "GitLab"),
    (r"^api\.notion\.com$", None, "n8n-nodes-base.notion", "Notion"),
    (r"^sheets\.googleapis\.com$", None, "n8n-nodes-base.googleSheets", "Google Sheets"),
    (r"^(www\.)?googleapis\.com$", r"^/drive/", "n8n-nodes-base.googleDrive", "Google Drive"),
    (r"^(www\.)?googleapis\.com$", r"^/calendar/", "n8n-nodes-base.googleCalendar", "Google Calendar"),
    (r"^(www\.)?googleapis\.com$", r"^/gmail/", "n8n-nodes-base.gmail", "Gmail"),
    (r"^gmail\.googleapis\.com$", None, "n8n-nodes-base.gmail", "Gmail"),
    (r"^docs\.googleapis\.com$", None, "n8n-nodes-base.googleDocs", "Google Docs"),
    (r"^bigquery\.googleapis\.com$", None, "n8n-nodes-base.googleBigQuery", "Google BigQuery"),
    (r"^generativelanguage\.googleapis\.com$", None, "@n8n/n8n-nodes-langchain.lmChatGoogleGemini", "Google Gemini"),
    (r"^api\.openai\.com$", None, "@n8n/n8n-nodes-langchain.openAi", "OpenAI"),
    (r"^api\.anthropic\.com$", None, "@n8n/n8n-nodes-langchain.lmChatAnthropic", "Anthropic"),
    (r"^api\.mistral\.ai$", None, "@n8n/n8n-nodes-langchain.lmChatMistralCloud", "Mistral"),
    (r"^api\.groq\.com$", None, "@n8n/n8n-nodes-langchain.lmChatGroq", "Groq"),
    (r"^openrouter\.ai$", None, "@n8n/n8n-nodes-langchain.lmChatOpenRouter", "OpenRouter"),
    (r"^api\.cohere\.(ai|com)$", None, "@n8n/n8n-nodes-langchain.lmCohere", "Cohere"),
    (r"^api\.airtable\.com$", None, "n8n-nodes-base.airtable", "Airtable"),
    (r"^api\.hubapi\.com$", None, "n8n-nodes-base.hubspot", "HubSpot"),
    (r"(^|\.)salesforce\.com$", None, "n8n-nodes-base.salesforce", "Salesforce"),
    (r"^api\.trello\.com$", None, "n8n-nodes-base.trello", "Trello"),
    (r"^api\.telegram\.org$", None, "n8n-nodes-base.telegram", "Telegram"),
    (r"^discord(app)?\.com$", r"^/api/", "n8n-nodes-base.discord", "Discord"),
    (r"^api\.sendgrid\.com$", None, "n8n-nodes-base.sendGrid", "SendGrid"),
    (r"^api\.twilio\.com$", None, "n8n-nodes-base.twilio", "Twilio"),
    (r"^api\.stripe\.com$", None, "n8n-nodes-base.stripe", "Stripe"),
    (r"\.atlassian\.net$", r"^/rest/api/", "n8n-nodes-base.jira", "Jira"),
    (r"\.atlassian\.net$", r"^/wiki/", "n8n-nodes-base.confluence", "Confluence"),
    (r"^api\.linear\.app$", None, "n8n-nodes-base.linear", "Linear"),
    (r"^app\.asana\.com$", r"^/api/", "n8n-nodes-base.asana", "Asana"),
    (r"^api\.clickup\.com$", None, "n8n-nodes-base.clickUp", "ClickUp"),
    (r"^api\.monday\.com$", None, "n8n-nodes-base.mondayCom", "monday.com"),
    (r"^graph\.microsoft\.com$", None, "n8n-nodes-base.microsoft*", "Microsoft (Outlook, Teams, Excel, OneDrive, SharePoint, To Do)"),
    (r"\.zendesk\.com$", None, "n8n-nodes-base.zendesk", "Zendesk"),
    (r"\.freshdesk\.com$", None, "n8n-nodes-base.freshdesk", "Freshdesk"),
    (r"^api\.pipedrive\.com$", None, "n8n-nodes-base.pipedrive", "Pipedrive"),
    (r"\.api\.mailchimp\.com$", None, "n8n-nodes-base.mailchimp", "Mailchimp"),
    (r"^api\.intercom\.io$", None, "n8n-nodes-base.intercom", "Intercom"),
    (r"\.myshopify\.com$", None, "n8n-nodes-base.shopify", "Shopify"),
    (r"\.supabase\.co$", None, "n8n-nodes-base.supabase", "Supabase"),
    (r"^api\.todoist\.com$", None, "n8n-nodes-base.todoist", "Todoist"),
    (r"^api\.box\.com$", None, "n8n-nodes-base.box", "Box"),
    (r"^api\.dropboxapi\.com$", None, "n8n-nodes-base.dropbox", "Dropbox"),
    (r"\.amazonaws\.com$", None, "n8n-nodes-base.aws*", "AWS (S3, SES, SNS, SQS, Lambda, DynamoDB, Bedrock)"),
    (r"^api\.cloudflare\.com$", None, "n8n-nodes-base.cloudflare", "Cloudflare"),
    (r"^(app\.|eu\.)?posthog\.com$", None, "n8n-nodes-base.postHog", "PostHog"),
    (r"^api\.calendly\.com$", None, "n8n-nodes-base.calendlyTrigger", "Calendly"),
    (r"^api\.hunter\.io$", None, "n8n-nodes-base.hunter", "Hunter"),
    (r"^api\.openweathermap\.org$", None, "n8n-nodes-base.openWeatherMap", "OpenWeatherMap"),
]

# Community packages seen in the field and the native node that replaces them.
COMMUNITY_HINTS = [
    (r"^n8n-nodes-mcp\.", "@n8n/n8n-nodes-langchain.mcpClientTool (native MCP Client Tool)"),
    (r"^n8n-nodes-(pdf|pdfco|pdf-extract)\.", "n8n-nodes-base.extractFromFile (Extract from File, PDF operation)"),
    (r"^n8n-nodes-document-generator\.", "n8n-nodes-base.html or an Edit Fields expression"),
    (r"^n8n-nodes-text-manipulation\.", "an expression or n8n-nodes-base.set (Edit Fields)"),
    (r"^n8n-nodes-globals\.", "n8n Variables ($vars) or a Data Table"),
    (r"^n8n-nodes-(scrapeninja|firecrawl|scraper)\.", "n8n-nodes-base.httpRequest with a predefined credential, then n8n-nodes-base.html"),
]

CODE_HTTP = re.compile(r"(this\.helpers\.httpRequest|\$helpers\.httpRequest|\bfetch\s*\(|\baxios\b|\burllib\.(request|urlopen)|\brequests\.(get|post|put|patch|delete|request)\s*\()")
URL_RE = re.compile(r"https?://([^\s/:\"'`<>{}$]+)(?::\d+)?(/[^\s\"'`<>{}?#]*)?", re.I)


def is_native(node_type):
    return str(node_type).startswith(NATIVE_PREFIXES)


def walk_strings(v, out):
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, list):
        for x in v:
            walk_strings(x, out)
    elif isinstance(v, dict):
        for x in v.values():
            walk_strings(x, out)
    return out


def native_for_url(url):
    for m in URL_RE.finditer(url):
        host, path = m.group(1).lower(), (m.group(2) or "/")
        for host_rx, path_rx, ntype, label in NATIVE_HOSTS:
            if re.search(host_rx, host) and (path_rx is None or re.match(path_rx, path)):
                return host, ntype, label
    return None


def community_hint(node_type):
    for rx, hint in COMMUNITY_HINTS:
        if re.match(rx, node_type):
            return hint
    return None


def find_policy(explicit, wf_path):
    if explicit:
        return explicit
    env = os.environ.get("N8N_NODE_POLICY")
    if env:
        return env
    here = os.path.join(os.getcwd(), "deploy", "node-policy.json")
    if os.path.isfile(here):
        return here
    d = os.path.dirname(os.path.abspath(wf_path))
    for _ in range(3):
        p = os.path.join(d, "deploy", "node-policy.json")
        if os.path.isfile(p):
            return p
        d = os.path.dirname(d)
    return None


def load_policy(path):
    if not path or not os.path.isfile(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return {}, "policy file %s is not valid JSON (%s)" % (path, e)
    allowed = data.get("allowedNodeTypes") or {}
    if isinstance(allowed, list):
        allowed = {t: {} for t in allowed}
    return allowed, None


def check(wf, allowed_types):
    """Returns (errors, warnings, summary). Each finding: (rule, node name, message)."""
    errors, warnings = [], []
    nodes = [n for n in (wf.get("nodes") or []) if isinstance(n, dict)]
    names = {n.get("name", "") for n in nodes}
    exceptions = ((wf.get("nodePolicy") or {}).get("exceptions") or [])
    excepted = {}  # node name -> set of rules ("*" = any)
    for ex in exceptions:
        if not isinstance(ex, dict):
            errors.append(("exception-invalid", "?", "exception entries are objects with node, reason, approvedBy"))
            continue
        node = str(ex.get("node") or "")
        reason = str(ex.get("reason") or "").strip()
        approver = str(ex.get("approvedBy") or "").strip()
        rule = str(ex.get("rule") or "*")
        if not node or len(reason) < 10 or not approver:
            errors.append(("exception-invalid", node or "?",
                           "an exception needs node, a reason (one sentence) and approvedBy; it is the record a reviewer reads"))
            continue
        if rule != "*" and rule not in RULES:
            errors.append(("exception-invalid", node, "unknown rule '%s' (one of %s)" % (rule, ", ".join(RULES))))
            continue
        if node not in names:
            warnings.append(("exception-stale", node, "exception names a node this workflow does not have; remove it"))
            continue
        excepted.setdefault(node, set()).add(rule)

    def covered(name, rule):
        rules = excepted.get(name, set())
        return "*" in rules or rule in rules

    summary = {"nodes": 0, "native": 0, "community": 0, "code": 0, "httpRequest": 0, "excepted": 0}
    for n in nodes:
        t, name = str(n.get("type") or ""), str(n.get("name") or "?")
        if t.endswith(".stickyNote"):
            continue
        summary["nodes"] += 1
        params = n.get("parameters") or {}
        if not is_native(t):
            summary["community"] += 1
            if covered(name, "community-node"):
                summary["excepted"] += 1
            elif t in allowed_types:
                summary["excepted"] += 1
            else:
                hint = community_hint(t)
                errors.append(("community-node", name,
                               "'%s' is not a node n8n ships%s. Search the native catalog first (search_nodes). "
                               "If nothing native covers it, record an exception in nodePolicy.exceptions "
                               "(reason, approvedBy) or add the package to deploy/node-policy.json after the "
                               "customer's security review." % (t, ("; native alternative: " + hint) if hint else "")))
            continue
        summary["native"] += 1
        if t in HTTP_TYPES:
            summary["httpRequest"] += 1
            hit = native_for_url(" ".join(walk_strings(params, [])))
            if hit and not covered(name, "http-native-available"):
                host, ntype, label = hit
                errors.append(("http-native-available", name,
                               "HTTP Request to %s: the %s node (%s) covers this service. Use it, or record an "
                               "exception naming the operation the native node lacks." % (host, label, ntype)))
            elif hit:
                summary["excepted"] += 1
        elif t in CODE_TYPES:
            summary["code"] += 1
            code = str(params.get("jsCode") or params.get("pythonCode") or params.get("code") or "")
            if CODE_HTTP.search(code) and not covered(name, "code-http"):
                errors.append(("code-http", name,
                               "Code node makes an HTTP call. A Code node cannot use a credential, so the key is "
                               "either hardcoded or passed as data. Use the HTTP Request node with a credential, "
                               "or record an exception saying why not."))
            elif CODE_HTTP.search(code):
                summary["excepted"] += 1
    return errors, warnings, summary


def run_file(path, policy_path):
    try:
        with open(path, encoding="utf-8") as fh:
            wf = json.load(fh)
    except (OSError, ValueError) as e:
        return {"file": path, "errors": [("parse", "?", "not valid JSON (%s)" % e)], "warnings": [], "summary": {}}
    allowed, perr = load_policy(find_policy(policy_path, path))
    errors, warnings, summary = check(wf, allowed)
    if perr:
        errors.insert(0, ("policy", "?", perr))
    return {"file": path, "name": wf.get("name"), "errors": errors, "warnings": warnings, "summary": summary}


def main(argv):
    args = argv[1:]
    policy, as_json, files = None, False, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--policy" and i + 1 < len(args):
            policy = args[i + 1]
            i += 2
            continue
        if a == "--json":
            as_json = True
        elif a.startswith("--"):
            sys.stderr.write("unknown flag %s\n" % a)
            return 2
        elif os.path.isdir(a):
            files += [os.path.join(a, f) for f in sorted(os.listdir(a)) if f.endswith(".json") and not f.endswith(".zones.json")]
        elif os.path.isfile(a):
            files.append(a)
        else:
            sys.stderr.write("no such file: %s\n" % a)
            return 2
        i += 1
    if not files:
        sys.stderr.write(__doc__.split("\n\n")[0] + "\n")
        return 2
    reports = [run_file(f, policy) for f in files]
    total = sum(len(r["errors"]) for r in reports)
    if as_json:
        print(json.dumps({"files": len(files), "errors": total, "reports": [
            dict(r, errors=[dict(zip(("rule", "node", "message"), e)) for e in r["errors"]],
                 warnings=[dict(zip(("rule", "node", "message"), w)) for w in r["warnings"]]) for r in reports]}, indent=2))
        return 1 if total else 0
    for r in reports:
        s = r["summary"]
        head = r.get("name") or r["file"]
        if s:
            print("%s: %d node(s), %d native, %d community, %d code, %d http, %d excepted"
                  % (head, s["nodes"], s["native"], s["community"], s["code"], s["httpRequest"], s["excepted"]))
        for rule, node, msg in r["errors"]:
            print("  ERROR %-22s %s: %s" % (rule, node, msg))
        for rule, node, msg in r["warnings"]:
            print("  WARN  %-22s %s: %s" % (rule, node, msg))
    print("RESULT: ok" if not total else "RESULT: %d finding(s)" % total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
