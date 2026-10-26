"""Loopback-only dashboard for the local issue-resolution prototype."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from .issues import IssueEngine, IssueError, IssueNotFound
from .observer import ObserverError
from .pipeline import ProjectError, ProjectNotFound, ProjectPipeline
from .registry import RoleRegistry


_ISSUE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_MAX_REQUEST_BYTES = 8_192


class _DashboardHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        owned_resources: tuple[Any, ...] = (),
    ) -> None:
        self._owned_resources = owned_resources
        self._resources_closed = False
        super().__init__(address, handler)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            if not self._resources_closed:
                self._resources_closed = True
                for resource in self._owned_resources:
                    resource.close()


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Voocks Media — Project &amp; Issue Dashboard</title>
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
    .project-counts{display:grid;grid-template-columns:repeat(4,minmax(115px,1fr));gap:9px;margin:12px 0 18px}
    .project-count{background:#f5f7fa;border-radius:8px;padding:10px}.project-count strong{display:block;font-size:1.2rem}
    .project-grid{display:grid;grid-template-columns:minmax(240px,.8fr) minmax(320px,1.2fr);gap:12px}
    .project-form{display:grid;gap:8px}.project-form label{font-size:.82rem;color:#53637a}
    .project-form input,.project-form select{margin-top:3px}.project-list{display:grid;gap:7px;max-height:36vh;overflow:auto}
    .project-row{display:block;width:100%;text-align:left;background:#fff;color:#152238;border:1px solid #e0e6ef}
    .project-row.selected{border-color:#316bd6;background:#f3f7ff}.project-detail{margin-top:12px;padding:12px;background:#f8f9fc;border-radius:8px}
    .demo-tag{display:inline-block;padding:2px 6px;border-radius:999px;background:#fff0c2;color:#624b0a;font-size:.72rem;font-weight:700}
    .project-actions{display:flex;gap:7px;align-items:center;margin:8px 0}.project-actions select{max-width:180px}
    .project-links{font-size:.84rem}.project-links li{overflow-wrap:anywhere}
    @media(max-width:760px){.project-counts{grid-template-columns:repeat(2,1fr)}.project-grid{grid-template-columns:1fr}}
    @media(max-width:760px){.counts{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.inbox{max-height:36vh}}
  </style>
</head>
<body>
  <header class="top"><h1>Voocks Media · Project &amp; Issue Dashboard</h1>
    <span class="demo">Demo Mode · local synthetic repairs only</span>
    <p class="muted" style="color:#c5d1e2">Repairs run only after an explicit local Director action. No production access.</p>
  </header>
  <main>
    <section class="panel" aria-label="Project pipeline">
      <h2>Project Pipeline <span class="demo">Local records</span></h2>
      <div id="project-mode" class="muted">Loading project data…</div>
      <div id="project-counts" class="project-counts"></div>
      <div class="project-grid">
        <section>
          <h2>Create Project</h2>
          <form id="project-form" class="project-form">
            <label>Project ID (optional; generated if blank)<input name="project_id" maxlength="64"></label>
            <label>Title<input name="title" maxlength="200" required></label>
            <label>Project type<input name="project_type" maxlength="80" placeholder="article, video, campaign…" required></label>
            <label>Department<select name="department_id" id="project-department" required></select></label>
            <label>Digital employee role<select name="role_id" id="project-role" required></select></label>
            <label>Language tag<input name="language" value="en" maxlength="35" required></label>
            <label>Priority<select name="priority"><option>normal</option><option>low</option><option>high</option><option>urgent</option></select></label>
            <label><span><input name="is_demo" type="checkbox" checked> Synthetic demo record</span></label>
            <button type="submit">Create Project</button>
            <span id="project-form-message" class="muted">Director approval is required. New projects start in Draft.</span>
          </form>
        </section>
        <section>
          <h2>Projects</h2>
          <div class="filters">
            <select id="project-status-filter" aria-label="Filter projects by status"><option value="">All statuses</option></select>
            <select id="project-department-filter" aria-label="Filter projects by department"><option value="">All departments</option></select>
          </div>
          <input id="project-type-filter" placeholder="Filter by project type" aria-label="Filter by project type">
          <div id="project-list" class="project-list"><div class="empty">Loading projects…</div></div>
          <div id="project-detail" class="project-detail"><span class="muted">Select a project to inspect its status, links, and history.</span></div>
        </section>
      </div>
    </section>
    <br>
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
let selectedProjectId = null;
let projectOptions = null;
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(cls) node.className=cls; return node; }
async function api(url, options) { const response=await fetch(url,options); const body=await response.json(); if(!response.ok) throw new Error(body.error||`Request failed (${response.status})`); return body; }
const projectStatuses=["draft","queued","active","pending_review","blocked","ready","completed","cancelled"];
async function loadProjectOptions() {
  projectOptions=await api("/api/project-options");
  const department=$("project-department"); department.replaceChildren();
  for(const id of projectOptions.departments) department.append(new Option(id,id));
  const departmentFilter=$("project-department-filter");
  for(const id of projectOptions.departments) departmentFilter.append(new Option(id,id));
  const statusFilter=$("project-status-filter");
  for(const status of projectStatuses) statusFilter.append(new Option(status.replace("_"," "),status));
  updateRoleOptions();
}
function updateRoleOptions() {
  const department=$("project-department").value;
  const role=$("project-role"); role.replaceChildren();
  for(const item of projectOptions.roles.filter(value=>value.department_id===department))
    role.append(new Option(`${item.role_id} · ${item.title}`,item.role_id));
}
async function refreshProjects() {
  if(!projectOptions) await loadProjectOptions();
  const params=new URLSearchParams();
  if($("project-status-filter").value) params.set("status",$("project-status-filter").value);
  if($("project-department-filter").value) params.set("department_id",$("project-department-filter").value);
  if($("project-type-filter").value.trim()) params.set("project_type",$("project-type-filter").value.trim());
  const data=await api("/api/projects?"+params.toString());
  $("project-mode").textContent=data.mode;
  const counts=$("project-counts"); counts.replaceChildren();
  for(const status of projectStatuses) {
    const tile=el("div",undefined,"project-count");
    tile.append(el("strong",data.counts[status]),el("span",status.replace("_"," ")));
    counts.append(tile);
  }
  const list=$("project-list"); list.replaceChildren();
  if(!data.projects.length) list.append(el("div","No projects match these filters.","empty"));
  for(const project of data.projects) {
    const button=el("button",undefined,"project-row"+(selectedProjectId===project.project_id?" selected":""));
    button.type="button"; button.addEventListener("click",()=>showProject(project.project_id));
    button.append(el("strong",project.title),el("span",`${project.project_id} · ${project.department_id} · ${project.status}`,"muted"));
    if(project.is_demo) button.append(el("span","Synthetic demo","demo-tag"));
    list.append(button);
  }
  if(selectedProjectId) await showProject(selectedProjectId);
}
async function showProject(id) {
  try {
    const project=await api(`/api/projects/${encodeURIComponent(id)}`);
    selectedProjectId=id;
    const panel=$("project-detail"); panel.replaceChildren();
    panel.append(el("h3",project.title));
    panel.append(el("div",`${project.project_id} · ${project.project_type} · ${project.department_id} · ${project.role_id} · ${project.language} · ${project.priority}`,"muted"));
    panel.append(el("p",`Status: ${project.status}${project.is_demo?" · Synthetic demo record":""}`,"status"));
    if(project.allowed_transitions.length) {
      const status=el("select"); status.id="project-next-status";
      for(const value of project.allowed_transitions) status.append(new Option(value.replace("_"," "),value));
      const change=el("button","Change status"); change.type="button";
      change.addEventListener("click",()=>changeProjectStatus(id,status.value,change));
      const actions=el("div",undefined,"project-actions"); actions.append(status,change); panel.append(actions);
    }
    const links=el("ul",undefined,"project-links");
    for(const link of project.links) {
      const details=Object.keys(link.details).length?` · ${JSON.stringify(link.details)}`:"";
      links.append(el("li",`${link.link_type}: ${link.target_id}${details}`));
    }
    if(!project.links.length) links.append(el("li","No linked records."));
    panel.append(section("Linked tasks, issues, and observer findings",links));
    const linkType=el("select"); linkType.id="project-link-type";
    for(const kind of ["task","issue","observer_finding"]) linkType.append(new Option(kind.replace("_"," "),kind));
    linkType.addEventListener("change",()=>loadLinkOptions().catch(error=>alert(error.message)));
    const target=el("select"); target.id="project-link-target";
    const linkActions=el("div",undefined,"project-actions"); linkActions.append(linkType,target);
    const linkButton=el("button","Link record"); linkButton.type="button";
    linkButton.addEventListener("click",()=>linkProjectRecord(id,linkType.value,target.value,linkButton));
    linkActions.append(linkButton); panel.append(linkActions);
    const refreshFindings=el("button","Refresh observer findings (read-only)");
    refreshFindings.type="button";
    refreshFindings.addEventListener("click",()=>refreshObserverFindings(refreshFindings));
    panel.append(refreshFindings);
    const history=el("pre",JSON.stringify(project.audit_history.map(event=>({event_type:event.event_type,actor_id:event.actor_id,event:JSON.parse(event.event_json),created_at:event.created_at})),null,2));
    panel.append(section("Project change and audit history",history));
    await loadLinkOptions();
  } catch(error) { $("project-detail").replaceChildren(el("div",error.message,"notice")); }
}
async function loadLinkOptions() {
  const options=await api("/api/project-link-options");
  const selector=$("project-link-target"); selector.replaceChildren();
  const values=options[$("project-link-type").value]||[];
  for(const value of values) selector.append(new Option(value.label,value.id));
  if(!values.length) selector.append(new Option("No local records available",""));
}
async function requestDirector() {
  const id=window.prompt("Local prototype Director ID (simulated; not production authentication):");
  return id&&id.trim();
}
async function createProject(event) {
  event.preventDefault();
  const director_id=await requestDirector(); if(!director_id) return;
  const form=new FormData($("project-form"));
  const payload=Object.fromEntries(form.entries()); payload.director_id=director_id;
  if(!payload.project_id) delete payload.project_id;
  payload.is_demo=$("project-form").elements.is_demo.checked;
  $("project-form-message").textContent="Creating Director-authorized project…";
  try {
    const project=await api("/api/projects",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    $("project-form-message").textContent=`Created ${project.project_id} in Draft.`;
    $("project-form").reset(); $("project-form").elements.language.value="en"; $("project-form").elements.is_demo.checked=true;
    updateRoleOptions();
    await refreshProjects(); await showProject(project.project_id);
  } catch(error) { $("project-form-message").textContent=`Project not created: ${error.message}`; }
}
async function changeProjectStatus(id,status,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  button.disabled=true;
  try { await api(`/api/projects/${encodeURIComponent(id)}/status`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({director_id,status})}); await refreshProjects(); }
  catch(error) { alert(`Status not changed: ${error.message}`); button.disabled=false; }
}
async function linkProjectRecord(id,link_type,target_id,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  if(!target_id) { alert("No local record is available to link."); return; }
  button.disabled=true;
  try { await api(`/api/projects/${encodeURIComponent(id)}/links`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({director_id,link_type,target_id})}); await showProject(id); }
  catch(error) { alert(`Record not linked: ${error.message}`); button.disabled=false; }
}
async function refreshObserverFindings(button) {
  button.disabled=true;
  try {
    const result=await api("/api/project-observer-findings/refresh",{method:"POST"});
    await loadLinkOptions();
    alert(`Stored ${result.findings.length} finding(s) from the read-only observer run.`);
  } catch(error) { alert(`Observer findings not refreshed: ${error.message}`); }
  finally { button.disabled=false; }
}
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
$("project-form").addEventListener("submit",createProject);
$("project-department").addEventListener("change",updateRoleOptions);
for(const id of ["project-status-filter","project-department-filter","project-type-filter"])
  $(id).addEventListener("change",()=>refreshProjects().catch(error=>$("project-list").replaceChildren(el("div",error.message,"notice"))));
refresh().catch(error=>$("inbox").replaceChildren(el("div",error.message,"notice")));
refreshProjects().catch(error=>$("project-list").replaceChildren(el("div",error.message,"notice")));
</script>
</body>
</html>"""


def create_dashboard_server(
    engine: IssueEngine,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    pipeline: ProjectPipeline | None = None,
    registry: RoleRegistry | None = None,
) -> ThreadingHTTPServer:
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError as exc:
        raise ValueError("Dashboard host must be a loopback IP address") from exc
    if not loopback:
        raise ValueError("Dashboard host must be a loopback IP address")
    owns_pipeline = pipeline is None
    project_pipeline = pipeline
    if project_pipeline is None:
        role_registry = registry or RoleRegistry.from_markdown(
            Path(__file__).resolve().parents[1] / "docs" / "AGENT_REGISTRY.md"
        )
        project_pipeline = ProjectPipeline(
            engine.database, engine.director_ids, role_registry
        )

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
            if parsed.path == "/api/projects":
                query = parse_qs(parsed.query)
                try:
                    self._send_json(
                        200,
                        project_pipeline.snapshot(
                            status=query.get("status", [None])[0],
                            department_id=query.get("department_id", [None])[0],
                            project_type=query.get("project_type", [None])[0],
                        ),
                    )
                except ProjectError as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/project-options":
                snapshot = project_pipeline.snapshot()
                self._send_json(
                    200,
                    {
                        "departments": snapshot["departments"],
                        "roles": snapshot["roles"],
                    },
                )
                return
            if parsed.path == "/api/project-link-options":
                self._send_json(200, project_pipeline.link_options())
                return
            project_match = re.fullmatch(r"/api/projects/([A-Za-z0-9][A-Za-z0-9._-]{0,63})", parsed.path)
            if project_match:
                try:
                    self._send_json(200, project_pipeline.project_detail(project_match.group(1)))
                except ProjectNotFound as exc:
                    self._send_json(404, {"error": str(exc)})
                except ProjectError as exc:
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
            if urlparse(self.path).path == "/api/projects":
                self._handle_project_create()
                return
            if urlparse(self.path).path == "/api/project-observer-findings/refresh":
                try:
                    findings = project_pipeline.refresh_observer_findings()
                    self._send_json(200, {"findings": findings})
                except ObserverError as exc:
                    self._send_json(409, {"error": str(exc)})
                return
            project_action = re.fullmatch(
                r"/api/projects/([A-Za-z0-9][A-Za-z0-9._-]{0,63})/(status|links)",
                urlparse(self.path).path,
            )
            if project_action:
                self._handle_project_action(project_action.group(1), project_action.group(2))
                return
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

        def _read_payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > _MAX_REQUEST_BYTES:
                raise ProjectError("Request body size is invalid")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ProjectError("Request body must be a JSON object")
            return payload

        def _handle_project_create(self) -> None:
            try:
                payload = self._read_payload()
                allowed = {
                    "director_id", "project_id", "title", "project_type",
                    "department_id", "language", "priority", "role_id", "is_demo",
                }
                if set(payload) - allowed or not {
                    "director_id", "title", "project_type", "department_id",
                    "language", "priority", "role_id",
                } <= set(payload):
                    raise ProjectError("Project fields are missing or unsupported")
                project = project_pipeline.create_project(**payload)
                self._send_json(201, project)
            except ProjectError as exc:
                self._send_json(400, {"error": str(exc)})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})

        def _handle_project_action(self, project_id: str, action: str) -> None:
            try:
                payload = self._read_payload()
                if action == "status":
                    if set(payload) != {"director_id", "status"}:
                        raise ProjectError("Only Director ID and target status are accepted")
                    project = project_pipeline.transition_status(
                        project_id,
                        payload["status"],
                        director_id=payload["director_id"],
                    )
                else:
                    if set(payload) != {"director_id", "link_type", "target_id"}:
                        raise ProjectError("Only Director ID and local link fields are accepted")
                    project = project_pipeline.link_record(
                        project_id,
                        payload["link_type"],
                        payload["target_id"],
                        director_id=payload["director_id"],
                    )
                self._send_json(200, project)
            except ProjectNotFound as exc:
                self._send_json(404, {"error": str(exc)})
            except ProjectError as exc:
                self._send_json(400, {"error": str(exc)})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            return

    try:
        return _DashboardHTTPServer(
            (host, port),
            DashboardHandler,
            owned_resources=(project_pipeline,) if owns_pipeline else (),
        )
    except BaseException:
        if owns_pipeline:
            project_pipeline.close()
        raise
