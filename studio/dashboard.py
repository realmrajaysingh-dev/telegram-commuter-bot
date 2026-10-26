"""Loopback-only dashboard for the local issue-resolution prototype."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from .issues import IssueEngine, IssueError, IssueNotFound


_ISSUE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_MAX_REQUEST_BYTES = 8_192


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Voocks Media — Issue Inbox</title>
  <style>
    :root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#152238;background:#f2f5fa}
    *{box-sizing:border-box}body{margin:0}.top{background:#14243b;color:#fff;padding:22px max(20px,calc((100% - 1200px)/2))}
    .top h1{margin:0 0 7px;font-size:1.5rem}.demo{display:inline-block;padding:5px 9px;border-radius:999px;background:#f5c451;color:#382800;font-size:.78rem;font-weight:800}
    main{max-width:1200px;margin:22px auto;padding:0 18px}.counts{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:12px;margin-bottom:22px}
    .tile,.panel{background:#fff;border:1px solid #dbe3ee;border-radius:12px;padding:16px;box-shadow:0 2px 7px #12213a0b}
    .tile strong{display:block;font-size:1.65rem}.tile span,.muted{color:#63728a;font-size:.88rem}
    .layout{display:grid;grid-template-columns:minmax(260px,.85fr) minmax(340px,1.5fr);gap:16px}
    .panel h2{font-size:1.05rem;margin:0 0 14px}.filters{display:flex;gap:8px;margin-bottom:12px}
    input,select,textarea,button{font:inherit;border:1px solid #cbd5e1;border-radius:8px;padding:9px;background:#fff}
    input,select{min-width:0;width:100%}.filters>*{flex:1}.inbox{display:grid;gap:8px;max-height:68vh;overflow:auto}
    .issue{border:1px solid #e0e6ef;border-radius:9px;padding:11px;text-align:left;cursor:pointer;color:inherit}
    .issue.selected{border-color:#316bd6;background:#f3f7ff}.issue strong{display:block}.pill{font-size:.72rem;font-weight:700;text-transform:uppercase;color:#42536c}
    .sev-critical,.sev-high{color:#a32222}.sev-medium{color:#985d00}.sev-low{color:#23603e}
    .detail{min-height:220px}.detail h3{margin:0 0 7px}.section{margin:14px 0}.section h3{font-size:.9rem;margin:0 0 6px}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;border-radius:8px;padding:10px;margin:0;font-size:.82rem}
    textarea{width:100%;min-height:68px;resize:vertical}.actions{display:flex;align-items:center;gap:10px;margin-top:9px}
    button{background:#245fc5;color:white;border:0;font-weight:700;cursor:pointer}button:disabled{opacity:.55;cursor:wait}
    .status{font-weight:700}.history{display:grid;gap:8px}.attempt{padding:10px;background:#f5f7fa;border-radius:8px;font-size:.84rem}
    .empty{padding:22px;text-align:center;color:#63728a}.notice{padding:10px;background:#fff6dd;border-radius:8px;color:#624b0a}
    @media(max-width:760px){.counts{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.inbox{max-height:36vh}}
  </style>
</head>
<body>
  <header class="top"><h1>Voocks Media · Issue Resolution</h1>
    <span class="demo">Demo Mode · local synthetic repairs only</span>
    <p class="muted" style="color:#c5d1e2">Repairs run only after an explicit local Director action. No production access.</p>
  </header>
  <main>
    <section class="counts" aria-label="Project pipeline counts">
      <div class="tile"><strong id="active">0</strong><span>Active tasks</span></div>
      <div class="tile"><strong id="pending">0</strong><span>Pending tasks</span></div>
      <div class="tile"><strong id="blocked">0</strong><span>Blocked tasks</span></div>
      <div class="tile"><strong id="ready">0</strong><span>Ready tasks</span></div>
    </section>
    <div class="layout">
      <section class="panel"><h2>Issue Inbox</h2>
        <div class="filters">
          <select id="severity" aria-label="Filter by severity"><option value="">All severity</option>
            <option>critical</option><option>high</option><option>medium</option><option>low</option></select>
          <input id="department" aria-label="Filter by department" placeholder="Department">
        </div>
        <div id="inbox" class="inbox"><div class="empty">Loading issues…</div></div>
      </section>
      <section class="panel detail" id="detail"><h2>Issue details</h2><div class="empty">Select an issue from the inbox.</div></section>
    </div>
  </main>
<script>
const $ = id => document.getElementById(id);
let selectedId = null;
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(cls) node.className=cls; return node; }
async function api(url, options) { const response=await fetch(url,options); const body=await response.json(); if(!response.ok) throw new Error(body.error||`Request failed (${response.status})`); return body; }
async function refresh() {
  const params=new URLSearchParams();
  if($("severity").value) params.set("severity",$("severity").value);
  if($("department").value.trim()) params.set("department",$("department").value.trim());
  const data=await api("/api/dashboard?"+params.toString());
  for(const key of ["active","pending","blocked","ready"]) $(key).textContent=data.pipeline[key];
  const inbox=$("inbox"); inbox.replaceChildren();
  if(!data.issues.length) inbox.append(el("div","No matching issues.","empty"));
  for(const issue of data.issues) {
    const button=el("button",undefined,"issue"+(issue.issue_id===selectedId?" selected":""));
    button.type="button"; button.addEventListener("click",()=>showIssue(issue.issue_id));
    button.append(el("strong",issue.description),el("span",`${issue.project} · ${issue.department} · ${issue.status}`,"pill"));
    const severity=el("span",` · ${issue.severity}`,`pill sev-${issue.severity}`); button.append(severity); inbox.append(button);
  }
  if(selectedId) await showIssue(selectedId);
}
function section(title, value) { const wrap=el("div",undefined,"section"); wrap.append(el("h3",title)); wrap.append(value); return wrap; }
async function showIssue(id) {
  try {
    const issue=await api(`/api/issues/${encodeURIComponent(id)}`); selectedId=id;
    const panel=$("detail"); panel.replaceChildren();
    panel.append(el("h2","Issue details"),el("h3",issue.description));
    panel.append(el("div",`${issue.project} · ${issue.department} · ${issue.category} · ${issue.severity} · ${issue.status}`,"muted"));
    panel.append(section("Suggested repair action",el("p",issue.suggested_repair_action)));
    panel.append(section("Evidence references",el("pre",JSON.stringify(issue.evidence_refs,null,2))));
    panel.append(section("Sanitized diagnostics",el("pre",issue.diagnostic_logs||"No diagnostic log supplied.")));
    const instruction=document.createElement("textarea"); instruction.id="director-instruction"; instruction.maxLength=1000;
    instruction.placeholder="Optional Director instruction (recorded, never executed as a command)";
    panel.append(section("Optional Director prompt input",instruction));
    const action=el("div",undefined,"actions");
    const fix=el("button","Fix Issue"); fix.type="button";
    const eligible=["pending","needs_approval","failed"].includes(issue.status);
    fix.disabled=!eligible; fix.addEventListener("click",()=>fixIssue(id,fix));
    action.append(fix,el("span",eligible?"Director action required":"No repair action available for this state","muted"));
    panel.append(action);
    const attempts=el("div",undefined,"history");
    for(const record of issue.repair_history) {
      const item=el("div",undefined,"attempt");
      item.append(el("strong",`Attempt ${record.attempt_no}: ${record.status}`));
      item.append(el("pre",JSON.stringify({repair_result:record.repair_result,observer_review:record.observer_review},null,2)));
      attempts.append(item);
    }
    panel.append(section("Repair history · tests · observer review",attempts));
    const audit=el("pre",JSON.stringify(issue.audit_history.map(event=>({event_type:event.event_type,actor_id:event.actor_id,created_at:event.created_at})),null,2));
    panel.append(section("Audit history",audit));
  } catch(error) { $("detail").replaceChildren(el("div",error.message,"notice")); }
}
async function fixIssue(id,button) {
  const directorId=window.prompt("Local prototype Director ID (simulated; not production authentication):");
  if(!directorId) return;
  button.disabled=true; const message=el("span","Authorizing and running deterministic repair…","status");
  button.after(message);
  try {
    const report=await api(`/api/issues/${encodeURIComponent(id)}/fix`,{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({director_id:directorId,instructions:$("director-instruction").value})});
    message.textContent=`Reported ${report.status}; independent observer ${report.observer_review.agrees?"agrees":"disagrees"}.`;
    await refresh();
  } catch(error) { message.textContent=`Repair not run: ${error.message}`; button.disabled=false; }
}
$("severity").addEventListener("change",()=>refresh().catch(error=>$("inbox").replaceChildren(el("div",error.message,"notice"))));
$("department").addEventListener("change",()=>refresh().catch(error=>$("inbox").replaceChildren(el("div",error.message,"notice"))));
refresh().catch(error=>$("inbox").replaceChildren(el("div",error.message,"notice")));
</script>
</body>
</html>"""


def create_dashboard_server(
    engine: IssueEngine, *, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError as exc:
        raise ValueError("Dashboard host must be a loopback IP address") from exc
    if not loopback:
        raise ValueError("Dashboard host must be a loopback IP address")

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "VoocksLocalDashboard/1.0"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, value: dict[str, Any]) -> None:
            self._send(
                status,
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(200, _DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/dashboard":
                query = parse_qs(parsed.query)
                severity = query.get("severity", [None])[0]
                department = query.get("department", [None])[0]
                try:
                    self._send_json(
                        200, engine.dashboard_snapshot(severity=severity, department=department)
                    )
                except IssueError as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            match = re.fullmatch(r"/api/issues/([a-f0-9]{32})", parsed.path)
            if match:
                try:
                    self._send_json(200, engine.issue_detail(match.group(1)))
                except IssueNotFound as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            self._send_json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            match = re.fullmatch(r"/api/issues/([a-f0-9]{32})/fix", urlparse(self.path).path)
            if not match:
                self._send_json(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > _MAX_REQUEST_BYTES:
                    raise IssueError("Request body size is invalid")
                payload = json.loads(self.rfile.read(length))
                if (
                    not isinstance(payload, dict)
                    or set(payload) - {"director_id", "instructions"}
                    or "director_id" not in payload
                ):
                    raise IssueError("Only Director ID and optional instructions are accepted")
                instructions = payload.get("instructions", "")
                if not isinstance(payload["director_id"], str) or not isinstance(instructions, str):
                    raise IssueError("Director ID and instructions must be text")
                report = engine.fix_issue(
                    match.group(1),
                    director_id=payload["director_id"],
                    instructions=instructions,
                )
                self._send_json(200, report.to_dict())
            except IssueNotFound as exc:
                self._send_json(404, {"error": str(exc)})
            except (IssueError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), DashboardHandler)
