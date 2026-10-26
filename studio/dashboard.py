"""Loopback-only dashboard for the local issue-resolution prototype."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from .allocation import MAX_EVIDENCE_BYTES, AllocationError, TaskAllocationEngine
from .issues import IssueEngine, IssueError, IssueNotFound
from .models import ObserverAssessment, ObserverReport, parse_datetime
from .observer import ObserverError
from .pipeline import ProjectError, ProjectNotFound, ProjectPipeline
from .registry import RoleRegistry
from .worker_queue import WorkerJobQueue, WorkerQueueError
from .worker_contracts import WorkloadKind


_ISSUE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_MAX_REQUEST_BYTES = 8_192
_MAX_EVIDENCE_REQUEST_BYTES = MAX_EVIDENCE_BYTES * 4 + 4_096


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
    .evidence-content{width:100%;min-height:110px}
    .worker-grid{display:grid;grid-template-columns:minmax(250px,.7fr) minmax(340px,1.3fr);gap:12px}
    .worker-jobs{display:grid;gap:8px;max-height:55vh;overflow:auto}
    .worker-form{display:grid;gap:8px}.worker-form label{font-size:.82rem;color:#53637a}
    .worker-job{padding:11px;background:#f5f7fa;border-radius:8px;font-size:.84rem}
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
    <section class="panel" aria-label="CEO work allocation">
      <h2>CEO Work Allocation <span class="demo">Local task plans</span></h2>
      <p class="muted">Planning creates a draft only. A Director must explicitly approve a plan before tasks become ready. Evidence is stored locally as inert text and never executed.</p>
      <div class="project-grid">
        <section class="project-form">
          <label>Select Project<select id="allocation-project"><option value="">Select a project</option></select></label>
          <label>Template<select id="allocation-template">
            <option value="news_article">News Article</option>
            <option value="research_documentary">Research-Based Documentary</option>
            <option value="youtube_video">YouTube Video</option>
            <option value="social_media_campaign">Social Media Campaign</option>
          </select></label>
          <label>Director Instructions<textarea id="allocation-instructions" maxlength="2000" placeholder="Optional scope and constraints"></textarea></label>
          <label>Desired output format<select id="allocation-format">
            <option value="markdown">Markdown</option><option value="html">HTML</option>
            <option value="script">Script</option><option value="platform_package">Platform package</option>
          </select></label>
          <label>Language<input id="allocation-language" maxlength="35" value="en"></label>
          <label>Priority<select id="allocation-priority">
            <option>low</option><option selected>normal</option><option>high</option><option>urgent</option>
          </select></label>
          <label>Deadline<input id="allocation-deadline" type="datetime-local" required></label>
          <button id="generate-plan" type="button">Generate Task Plan</button>
          <span id="allocation-message" class="muted">Choose a queued or active Director-approved project.</span>
        </section>
        <section>
          <h2>Task Plan · Preview and Queue</h2>
          <div id="allocation-plan"><div class="empty">Select a project to view its persisted plan.</div></div>
        </section>
      </div>
    </section>
    <br>
    <section class="panel" aria-label="Persistent worker queue">
      <h2>Persistent Worker Queue <span class="demo">Manual local simulation</span></h2>
      <p class="notice">No Colab, Drive, networking, or workload execution is connected. Successful worker results remain unverified metadata only; they are never files or Studio evidence automatically.</p>
      <div id="worker-counts" class="project-counts"></div>
      <button id="recover-worker-jobs" type="button">Recover expired leases</button>
      <div class="worker-grid">
        <section>
          <h2>Register Simulated Worker</h2>
          <form id="worker-registration-form" class="worker-form">
            <label>Worker ID<input name="worker_id" maxlength="64" value="local-worker-01" required></label>
            <label>Workload<select name="workload"><option value="gpu_inference">GPU inference · T4 metadata</option><option value="cpu_ffmpeg">CPU FFmpeg · metadata only</option></select></label>
            <label>Availability<select name="state"><option value="available">Available</option><option value="gpu_unavailable">GPU unavailable</option><option value="disconnected">Disconnected</option><option value="registered">Registered, not available</option></select></label>
            <label>Declared VRAM (MiB)<input name="vram_mb" type="number" min="0" max="262144" value="16000" required></label>
            <label>Registration lifetime (hours)<input name="ttl_hours" type="number" min="1" max="12" value="4" required></label>
            <button type="submit">Register Manual Worker</button>
          </form>
          <div id="worker-list" class="history"></div>
        </section>
        <section>
          <h2>Jobs and Failures</h2>
          <div id="worker-job-list" class="worker-jobs"><div class="empty">Loading persisted worker jobs…</div></div>
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
let selectedPlan = null;
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(cls) node.className=cls; return node; }
function localDateTimeValue(value) { const date=new Date(value); return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16); }
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
  const allocationProject=$("allocation-project");
  const selectedAllocationProject=allocationProject.value || selectedProjectId || "";
  allocationProject.replaceChildren(new Option("Select a project",""));
  for(const project of data.projects)
    allocationProject.append(new Option(`${project.title} · ${project.project_id} · ${project.status}${project.is_demo?" · Demo":""}`,project.project_id));
  allocationProject.value=selectedAllocationProject;
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
    $("allocation-project").value=id;
    $("allocation-language").value=project.language;
    $("allocation-priority").value=project.priority;
    const deadline=new Date(Date.now()+7*24*60*60*1000);
    $("allocation-deadline").value=localDateTimeValue(deadline);
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
    await refreshTaskPlan(id);
  } catch(error) { $("project-detail").replaceChildren(el("div",error.message,"notice")); }
}
async function refreshTaskPlan(projectId=selectedProjectId) {
  if(!projectId) {
    selectedPlan=null;
    $("allocation-plan").replaceChildren(el("div","Select a project to view its persisted plan.","empty"));
    return;
  }
  const result=await api(`/api/task-plans?project_id=${encodeURIComponent(projectId)}`);
  selectedPlan=result.plan;
  if(selectedPlan) renderTaskPlan(selectedPlan);
  else $("allocation-plan").replaceChildren(el("div","No persisted task plan for this project.","empty"));
}
function renderTaskPlan(plan) {
  const panel=$("allocation-plan"); panel.replaceChildren();
  panel.append(el("h3",`${plan.template.replaceAll("_"," ")} · ${plan.status.toUpperCase()}`));
  panel.append(el("p",`${plan.project.title} · ${plan.project.project_id}${plan.is_demo?" · Synthetic demo record":""}`,"muted"));
  panel.append(el("p",`Format: ${plan.output_format} · Language: ${plan.language} · Priority: ${plan.priority} · Deadline: ${plan.deadline_at}`,"muted"));
  panel.append(el("p",`Progress: ${JSON.stringify(plan.counts)}`,"status"));
  if(plan.director_instructions) panel.append(section("Director Instructions",el("pre",plan.director_instructions)));
  if(plan.status==="draft") {
    const approve=el("button","Approve Plan"); approve.type="button";
    approve.addEventListener("click",()=>approveTaskPlan(plan.plan_id,approve));
    panel.append(approve,el("p","Draft only · no tasks are active or ready before Director approval.","notice"));
  } else if(plan.manifest.extensions.length===0) {
    const extend=el("button","Extend Plan by 7 Days"); extend.type="button";
    extend.addEventListener("click",()=>extendTaskPlan(plan.plan_id,extend));
    panel.append(extend);
  }
  const list=el("div",undefined,"history");
  for(const task of plan.tasks) {
    const item=el("div",undefined,"attempt");
    item.append(el("strong",`${task.title} · ${task.status}`));
    item.append(el("p",`${task.department_id} · ${task.role_id} · ${task.role_title} · Priority: ${task.priority} · Attempts: ${task.attempts}/${task.max_attempts}`));
    item.append(el("p",task.description));
    item.append(el("p",`Dependencies: ${task.dependencies.map(value=>`${value.task_id} (${value.status})`).join(", ")||"None"}`));
    item.append(el("p",`Evidence required: ${task.evidence_requirements.join(", ")}`));
    for(const artifact of task.evidence_artifacts) {
      item.append(el("p",`${artifact.evidence_type||"Unverified reference"} · ${artifact.verified?"Verified":"NOT VERIFIED"}${artifact.sha256?` · SHA-256 ${artifact.sha256}`:""}${artifact.error?` · ${artifact.error}`:""}`,artifact.verified?"muted":"notice"));
    }
    item.append(el("p",`Verification gates: ${task.verification_gates.join(" · ")}`));
    if(task.last_error_code) item.append(el("p",`Last failure: ${task.last_error_code}${task.retryable?" · retryable":""}`,"notice"));
    const assessmentState=task.assessment_state;
    item.append(el("p",`Submitted task assessments: ${assessmentState.assessment_count}/${assessmentState.required_assessments}${assessmentState.unresolved_disagreement?" · UNRESOLVED DISAGREEMENT":""}${assessmentState.has_block?" · BLOCKING ASSESSMENT":""}`,assessmentState.unresolved_disagreement||assessmentState.has_block?"notice":"muted"));
    item.append(el("p",assessmentState.source_notice,"notice"));
    for(const assessment of assessmentState.history) {
      item.append(el("p",`${assessment.is_current?"Current":"Historical"} · ${assessment.assessor_role_id} · ${assessment.assessor_role_title} · ${assessment.assessment.toUpperCase()} · ${assessment.finding} · Evidence: ${assessment.evidence_refs.join(", ")}`,assessment.assessment==="block"?"notice":"muted"));
    }
    for(const resolution of assessmentState.resolution_history) {
      item.append(el("p",`${resolution.is_current?"Current":"Historical"} · Director ${resolution.director_id} · ${resolution.decision.toUpperCase()} · ${resolution.rationale} · ${resolution.created_at}`,resolution.decision==="reject"||resolution.decision==="request_revision"?"notice":"muted"));
    }
    const verifiedTypes=new Set(task.evidence_artifacts.filter(artifact=>artifact.verified).map(artifact=>artifact.evidence_type));
    const missingEvidence=task.evidence_requirements.filter(type=>!verifiedTypes.has(type));
    if(missingEvidence.length&&["active","review"].includes(task.status)) {
      const evidenceType=el("select"); evidenceType.id=`evidence-type-${task.task_id}`;
      for(const type of missingEvidence) evidenceType.append(new Option(type,type));
      const content=el("textarea"); content.id=`evidence-content-${task.task_id}`;
      content.className="evidence-content"; content.maxLength=262144;
      content.placeholder="Enter actual evidence artifact text; saved locally as inert UTF-8 text (maximum 256 KiB).";
      const upload=el("button","Submit Evidence Artifact"); upload.type="button";
      upload.addEventListener("click",()=>uploadAllocationEvidence(task, evidenceType.value, content.value, upload));
      item.append(el("p","A type label alone is not evidence. File paths and URLs are not accepted.","notice"),evidenceType,content,upload);
    }
    if(task.status==="ready") {
      const start=el("button",`Start · ${task.role_id}`); start.type="button";
      start.addEventListener("click",()=>allocationTaskAction(task.task_id,"start",{actor_id:task.role_id},start));
      item.append(start);
    }
    if(task.status==="active") {
      if(task.evidence_verified&&plan.status==="approved") {
        const workload=el("select"); workload.id=`worker-workload-${task.task_id}`;
        workload.append(new Option("GPU inference · T4","gpu_inference"));
        workload.append(new Option("CPU FFmpeg","cpu_ffmpeg"));
        const queueJob=el("button","Queue Worker Job"); queueJob.type="button";
        queueJob.addEventListener("click",()=>enqueueWorkerJob(task,workload.value,queueJob));
        item.append(el("p","Worker jobs use only verified current task evidence as input. Queuing does not run the task.","muted"),workload,queueJob);
      }
      const fail=el("button","Report Failure"); fail.type="button";
      fail.addEventListener("click",()=>reportAllocationTaskFailure(task,fail));
      if(task.evidence_verified) {
        const review=el("button","Submit for Review"); review.type="button";
        review.addEventListener("click",()=>submitAllocationTaskReview(task,review));
        item.append(review);
      } else {
        item.append(el("p","Review is unavailable until every required artifact is verified.","notice"));
      }
      item.append(fail);
    }
    if(task.status==="review") {
      if(task.evidence_verified) {
        const complete=el("button","Director Accept & Complete"); complete.type="button";
        complete.addEventListener("click",()=>completeAllocationTask(task,complete));
        item.append(complete,el("p","Completion requires the approving Director to inspect the submitted evidence and accept the verification record.","notice"));
      } else {
        item.append(el("p","Completion blocked: provide genuine evidence, then submit the task for review again.","notice"));
      }
    }
    if(["review","blocked"].includes(task.status)&&task.evidence_verified&&!task.observer_review_pending&&assessmentState.assessor_roles.length) {
      const assessor=el("select");
      for(const role of assessmentState.assessor_roles) assessor.append(new Option(`${role.role_id} · ${role.title}`,role.role_id));
      const assessment=el("select");
      for(const value of ["clear","concern","block"]) assessment.append(new Option(value.toUpperCase(),value));
      const finding=el("textarea"); finding.maxLength=2000; finding.placeholder="Assessment finding (required)";
      const evidence=el("select"); evidence.multiple=true;
      for(const artifact of task.evidence_artifacts.filter(value=>value.verified))
        evidence.append(new Option(`${artifact.evidence_type} · ${artifact.artifact_id}`,artifact.artifact_id));
      const submit=el("button","Submit Assessment"); submit.type="button";
      submit.addEventListener("click",()=>submitTaskAssessment(task,assessor.value,assessment.value,[...evidence.selectedOptions].map(option=>option.value),finding.value,submit));
      item.append(el("p","Choose a different registered functional role. Role identity is locally asserted and unauthenticated; these assessment records are synthetic, not genuine independent verification.","notice"),assessor,assessment,finding,evidence,submit);
    }
    if(assessmentState.can_resolve) {
      const decision=el("select");
      if(!assessmentState.has_block) decision.append(new Option("Accept assessment outcome","accept"));
      decision.append(new Option("Request task revision","request_revision"),new Option("Reject; remain blocked","reject"));
      const rationale=el("textarea"); rationale.maxLength=2000; rationale.placeholder="Director rationale (required)";
      const resolve=el("button","Record Director Resolution"); resolve.type="button";
      resolve.addEventListener("click",()=>resolveTaskAssessmentDisagreement(task,decision.value,rationale.value,resolve));
      item.append(el("p","Director resolution queue · inspect all assessments and supporting artifact references. A BLOCK assessment cannot be accepted; request revision or reject instead.","notice"),decision,rationale,resolve);
    } else if(assessmentState.has_block) {
      item.append(el("p","Blocking assessments cannot be overridden. The approving Director may request revision; then submit new evidence and obtain new assessments.","notice"));
    }
    if(task.status==="blocked"&&task.observer_review_pending) {
      const directorReview=el("button","Director Review Observer Block"); directorReview.type="button";
      directorReview.addEventListener("click",()=>reviewObserverBlock(task,directorReview));
      item.append(directorReview,el("p","Director review reruns the Observer. Findings remain blocking until their cause is corrected; this action cannot override them.","notice"));
    }
    if(task.status==="blocked"&&task.retryable&&task.attempts<task.max_attempts) {
      const retry=el("button","Retry Task"); retry.type="button";
      retry.addEventListener("click",()=>allocationTaskAction(task.task_id,"retry",{actor_id:task.role_id},retry));
      item.append(retry);
    }
    list.append(item);
  }
  panel.append(section("Assigned Tasks and Dependencies",list));
  const observer=plan.observer;
  const observerContent=observer
    ? observer.findings.length
      ? el("pre",JSON.stringify(observer.findings,null,2),"notice")
      : el("p",`Clear · ${observer.observer_id} · ${observer.generated_at}`)
    : el("p","No persisted Observer run.");
  const observerSection=section("Observer Findings",observerContent);
  const refreshObserver=el("button","Run Observer Checks"); refreshObserver.type="button";
  refreshObserver.addEventListener("click",()=>allocationRequest(
    `/api/task-plans/${encodeURIComponent(plan.plan_id)}/observer`,{},refreshObserver
  ));
  observerSection.append(refreshObserver);
  panel.append(observerSection);
  const audit=el("pre",JSON.stringify(plan.audit_history.map(event=>({event_type:event.event_type,object_id:event.object_id,actor_id:event.actor_id,event:JSON.parse(event.event_json),created_at:event.created_at})),null,2));
  panel.append(section("Allocation Audit History",audit));
  refreshWorkerJobs().catch(error=>$("worker-job-list").replaceChildren(el("div",error.message,"notice")));
}
async function refreshWorkerJobs() {
  const snapshot=await api("/api/worker-jobs");
  const counts=$("worker-counts"); counts.replaceChildren();
  for(const state of ["queued","leased","running","succeeded","failed","interrupted","cancelled"]) {
    const tile=el("div",undefined,"project-count");
    tile.append(el("strong",snapshot.counts[state]||0),el("span",state.replaceAll("_"," ")));
    counts.append(tile);
  }
  const workers=$("worker-list"); workers.replaceChildren();
  if(!snapshot.workers.length) workers.append(el("p","No manual workers registered.","muted"));
  for(const worker of snapshot.workers) {
    const row=el("div",undefined,"attempt");
    row.append(el("strong",`${worker.worker_id} · ${worker.state}${worker.is_simulation?" · synthetic simulation":""}`));
    row.append(el("p",`${worker.workloads.join(", ")} · VRAM ${worker.vram_mb} MiB · expires ${worker.expires_at}`,"muted"));
    const state=el("select");
    for(const value of ["available","registered","gpu_unavailable","disconnected"])
      state.append(new Option(value.replaceAll("_"," "),value));
    state.value=worker.state;
    const update=el("button","Update availability"); update.type="button";
    update.addEventListener("click",()=>updateWorkerState(worker.worker_id,state.value,update));
    row.append(state,update); workers.append(row);
  }
  const list=$("worker-job-list"); list.replaceChildren();
  if(!snapshot.jobs.length) list.append(el("div","No persisted worker jobs.","empty"));
  for(const job of snapshot.jobs) {
    const card=el("div",undefined,"worker-job");
    card.append(el("strong",`${job.state.toUpperCase()} · ${job.workload} · ${job.job_id}`));
    card.append(el("p",`${job.project_title} · ${job.task_title} (${job.task_status})${job.project_is_demo?" · Demo":""}`,"muted"));
    card.append(el("p",`Attempt ${job.attempt_count}/${job.max_attempts} · ${job.resource_requirements.accelerator} · Deadline ${job.deadline_at}`));
    if(job.authorization) card.append(el("p",`Authorized by ${job.authorization.director_id} until ${job.authorization.expires_at}`,"muted"));
    if(job.lease) card.append(el("p",`Lease ${job.lease.lease_id} · ${job.lease.worker_id} · expires ${job.lease.expires_at}`,"muted"));
    if(job.last_error) card.append(el("p",`Failure: ${job.last_error}`,"notice"));
    if(job.outputs.length) card.append(el("p",`Output references: ${job.outputs.map(item=>`${item.artifact_id} (${item.verification_status})`).join(", ")}`,"notice"));
    if(job.state==="queued"&&!job.authorization) {
      const authorize=el("button","Director Authorize Execution"); authorize.type="button";
      authorize.addEventListener("click",()=>authorizeWorkerJob(job,authorize)); card.append(authorize);
    } else if(job.state==="queued"&&job.authorization) {
      const candidates=snapshot.workers.filter(worker=>worker.state==="available"&&worker.workloads.includes(job.workload));
      const worker=el("select");
      for(const candidate of candidates) worker.append(new Option(`${candidate.worker_id} · ${candidate.vram_mb} MiB VRAM`,candidate.worker_id));
      const renew=el("button","Renew Director Authorization"); renew.type="button";
      renew.addEventListener("click",()=>authorizeWorkerJob(job,renew));
      const lease=el("button","Lease · manual simulation"); lease.type="button";
      lease.disabled=!candidates.length;
      lease.addEventListener("click",()=>leaseWorkerJob(job,worker.value,lease));
      const cancel=el("button","Director Cancel"); cancel.type="button";
      cancel.addEventListener("click",()=>directorWorkerAction(job,"cancel",cancel));
      card.append(worker,renew,lease,cancel);
    } else if(job.state==="leased") {
      const start=el("button","Mark Running · simulation only"); start.type="button";
      start.addEventListener("click",()=>workerJobAction(job,"start",{worker_id:job.lease.worker_id},start));
      card.append(start);
    } else if(job.state==="running") {
      const heartbeat=el("button","Record Heartbeat"); heartbeat.type="button";
      heartbeat.addEventListener("click",()=>workerJobAction(job,"heartbeat",{worker_id:job.lease.worker_id,lease_seconds:300},heartbeat));
      const fail=el("button","Report Simulated Failure"); fail.type="button";
      fail.addEventListener("click",()=>reportWorkerFailure(job,fail));
      card.append(heartbeat,fail);
    } else if(job.state==="failed"&&job.attempt_count<job.max_attempts) {
      const retry=el("button","Director Retry"); retry.type="button";
      retry.addEventListener("click",()=>directorWorkerAction(job,"retry",retry));
      card.append(retry);
    } else if(["queued","interrupted"].includes(job.state)) {
      const cancel=el("button","Director Cancel"); cancel.type="button";
      cancel.addEventListener("click",()=>directorWorkerAction(job,"cancel",cancel));
      card.append(cancel);
    }
    list.append(card);
  }
  list.append(el("p",snapshot.output_notice,"notice"));
}
async function workerJobAction(job,action,payload,button) {
  await workerMutation(`/api/worker-jobs/${encodeURIComponent(job.job_id)}/${action}`,payload,button);
}
async function workerMutation(url,payload,button) {
  button.disabled=true;
  try {
    await api(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    await refreshWorkerJobs();
  } catch(error) { alert(`Worker queue action failed: ${error.message}`); button.disabled=false; }
}
async function enqueueWorkerJob(task,workload,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  await workerMutation(`/api/worker-jobs/queue/${encodeURIComponent(task.task_id)}`,{director_id,workload},button);
}
async function authorizeWorkerJob(job,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  await workerJobAction(job,"authorize",{director_id},button);
}
async function leaseWorkerJob(job,worker_id,button) {
  await workerJobAction(job,"lease",{worker_id,lease_seconds:300},button);
}
async function directorWorkerAction(job,action,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  await workerJobAction(job,action,{director_id},button);
}
async function reportWorkerFailure(job,button) {
  const error_code=window.prompt("Synthetic worker failure code (no sensitive details):");
  if(!error_code) return;
  await workerJobAction(job,"fail",{worker_id:job.lease.worker_id,error_code},button);
}
async function updateWorkerState(worker_id,state,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  await workerMutation(`/api/workers/${encodeURIComponent(worker_id)}/state`,{director_id,state},button);
}
async function registerManualWorker(event) {
  event.preventDefault();
  const director_id=await requestDirector(); if(!director_id) return;
  const form=new FormData($("worker-registration-form"));
  const ttl_hours=Number(form.get("ttl_hours"));
  const payload={
    director_id,worker_id:form.get("worker_id"),workloads:[form.get("workload")],
    state:form.get("state"),vram_mb:Number(form.get("vram_mb")),
    operations:[],language_tags:[],model_ids:[],ttl_seconds:ttl_hours*3600
  };
  try {
    await api("/api/workers",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    await refreshWorkerJobs();
  } catch(error) { alert(`Worker registration failed: ${error.message}`); }
}
async function generateTaskPlan() {
  const projectId=$("allocation-project").value;
  if(!projectId) { $("allocation-message").textContent="Select a project first."; return; }
  const director_id=await requestDirector(); if(!director_id) return;
  const deadline=new Date($("allocation-deadline").value);
  if(!Number.isFinite(deadline.getTime())) {
    $("allocation-message").textContent="Choose a valid deadline.";
    return;
  }
  const payload={
    project_id:projectId,director_id,template:$("allocation-template").value,
    director_instructions:$("allocation-instructions").value,
    output_format:$("allocation-format").value,language:$("allocation-language").value,
    priority:$("allocation-priority").value,deadline_at:deadline.toISOString()
  };
  const button=$("generate-plan"); button.disabled=true;
  try {
    const plan=await api("/api/task-plans",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    selectedProjectId=projectId; selectedPlan=plan; renderTaskPlan(plan);
    $("allocation-message").textContent=plan.status==="draft"
      ? `Draft plan ${plan.plan_id} saved. It is not approved or active.`
      : `Existing approved plan ${plan.plan_id} returned unchanged; no new plan or tasks were created.`;
  } catch(error) { $("allocation-message").textContent=`Plan not generated: ${error.message}`; }
  finally { button.disabled=false; }
}
async function approveTaskPlan(planId,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  await allocationRequest(`/api/task-plans/${encodeURIComponent(planId)}/approve`,{director_id},button);
}
async function extendTaskPlan(planId,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  await allocationRequest(`/api/task-plans/${encodeURIComponent(planId)}/extend`,{director_id},button);
}
async function allocationTaskAction(taskId,action,payload,button) {
  await allocationRequest(`/api/allocation-tasks/${encodeURIComponent(taskId)}/${action}`,payload,button);
}
async function allocationRequest(url,payload,button) {
  button.disabled=true;
  try {
    await api(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    await refreshTaskPlan();
  } catch(error) { alert(`Task plan action failed: ${error.message}`); button.disabled=false; }
}
async function uploadAllocationEvidence(task,evidence_type,content,button) {
  if(!content.trim()) { alert("Enter actual non-empty evidence content."); return; }
  await allocationTaskAction(task.task_id,"evidence",{actor_id:task.role_id,evidence_type,content},button);
}
async function submitAllocationTaskReview(task,button) {
  await allocationTaskAction(task.task_id,"review",{actor_id:task.role_id},button);
}
async function completeAllocationTask(task,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  if(!window.confirm("Confirm that you reviewed the persisted evidence and Observer record before accepting this task?")) return;
  await allocationTaskAction(task.task_id,"complete",{director_id},button);
}
async function reviewObserverBlock(task,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  await allocationTaskAction(task.task_id,"director-review",{director_id},button);
}
async function submitTaskAssessment(task,assessor_role_id,assessment,evidence_refs,finding,button) {
  await allocationTaskAction(task.task_id,"assess",{assessor_role_id,assessment,evidence_refs,finding},button);
}
async function resolveTaskAssessmentDisagreement(task,decision,rationale,button) {
  const director_id=await requestDirector(); if(!director_id) return;
  await allocationTaskAction(task.task_id,"resolve-disagreement",{director_id,decision,rationale},button);
}
async function reportAllocationTaskFailure(task,button) {
  const error_code=window.prompt("Failure code (uppercase identifier; no sensitive details):");
  if(!error_code) return;
  const retryable=window.confirm("Can this task be safely retried?");
  await allocationTaskAction(task.task_id,"fail",{actor_id:task.role_id,error_code,retryable},button);
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
$("worker-registration-form").addEventListener("submit",registerManualWorker);
$("recover-worker-jobs").addEventListener("click",async event=>{
  const button=event.currentTarget; button.disabled=true;
  try {
    await api("/api/worker-jobs/recover",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    await refreshWorkerJobs();
  } catch(error) { alert(`Worker recovery failed: ${error.message}`); }
  finally { button.disabled=false; }
});
$("allocation-project").addEventListener("change",event=>{
  if(event.target.value) showProject(event.target.value);
  else refreshTaskPlan(null);
});
$("generate-plan").addEventListener("click",()=>generateTaskPlan());
$("project-department").addEventListener("change",updateRoleOptions);
for(const id of ["project-status-filter","project-department-filter","project-type-filter"])
  $(id).addEventListener("change",()=>refreshProjects().catch(error=>$("project-list").replaceChildren(el("div",error.message,"notice"))));
refresh().catch(error=>$("inbox").replaceChildren(el("div",error.message,"notice")));
refreshProjects().catch(error=>$("project-list").replaceChildren(el("div",error.message,"notice")));
refreshWorkerJobs().catch(error=>$("worker-job-list").replaceChildren(el("div",error.message,"notice")));
</script>
</body>
</html>"""


def create_dashboard_server(
    engine: IssueEngine,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    pipeline: ProjectPipeline | None = None,
    allocation: TaskAllocationEngine | None = None,
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
    else:
        role_registry = registry or project_pipeline.registry
    owns_allocation = allocation is None
    task_allocation = allocation
    try:
        if task_allocation is None:
            task_allocation = TaskAllocationEngine(
                engine.database, role_registry, engine.director_ids
            )
        worker_queue = WorkerJobQueue(task_allocation, engine.director_ids)
    except BaseException:
        if owns_pipeline:
            project_pipeline.close()
        if owns_allocation and task_allocation is not None:
            task_allocation.close()
        raise

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
            if parsed.path == "/api/task-plans":
                query = parse_qs(parsed.query)
                project_id = query.get("project_id", [None])[0]
                try:
                    if project_id is None:
                        raise AllocationError("project_id is required")
                    self._send_json(
                        200,
                        {"plan": task_allocation.get_plan_for_project(project_id)},
                    )
                except AllocationError as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/worker-jobs":
                try:
                    self._send_json(200, worker_queue.snapshot())
                except WorkerQueueError as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            worker_job_match = re.fullmatch(
                r"/api/worker-jobs/([A-Za-z0-9][A-Za-z0-9._-]{0,63})",
                parsed.path,
            )
            if worker_job_match:
                try:
                    self._send_json(200, worker_queue.get_job(worker_job_match.group(1)))
                except WorkerQueueError as exc:
                    status = 404 if "not found" in str(exc).lower() else 400
                    self._send_json(status, {"error": str(exc)})
                return
            plan_match = re.fullmatch(
                r"/api/task-plans/([A-Za-z0-9][A-Za-z0-9._-]{0,63})",
                parsed.path,
            )
            if plan_match:
                try:
                    self._send_json(200, task_allocation.get_plan(plan_match.group(1)))
                except AllocationError as exc:
                    status = 404 if "not found" in str(exc).lower() else 400
                    self._send_json(status, {"error": str(exc)})
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
            path = urlparse(self.path).path
            if (
                path == "/api/workers"
                or path == "/api/worker-jobs/recover"
                or re.fullmatch(
                    r"/api/workers/[A-Za-z0-9][A-Za-z0-9._-]{0,63}/state",
                    path,
                )
                or re.fullmatch(
                    r"/api/worker-jobs/queue/[A-Za-z0-9][A-Za-z0-9._-]{0,63}",
                    path,
                )
                or re.fullmatch(
                    r"/api/worker-jobs/[A-Za-z0-9][A-Za-z0-9._-]{0,63}/"
                    r"(authorize|lease|start|heartbeat|fail|result|retry|cancel)",
                    path,
                )
            ):
                self._handle_worker_action(path)
                return
            if path == "/api/task-plans" or re.fullmatch(
                r"/api/(?:task-plans/[A-Za-z0-9][A-Za-z0-9._-]{0,63}/(?:approve|extend|observer)|"
                r"allocation-tasks/[A-Za-z0-9][A-Za-z0-9._-]{0,63}/(?:evidence|start|review|complete|fail|retry|director-review|assess|resolve-disagreement))",
                path,
            ):
                self._handle_allocation_action(path)
                return
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

        def _handle_allocation_action(self, path: str) -> None:
            try:
                max_bytes = (
                    _MAX_EVIDENCE_REQUEST_BYTES
                    if path.endswith("/evidence")
                    else _MAX_REQUEST_BYTES
                )
                payload = self._read_payload(max_bytes=max_bytes)
                if path == "/api/task-plans":
                    allowed = {
                        "project_id", "director_id", "template", "director_instructions",
                        "output_format", "language", "priority", "deadline_at",
                    }
                    required = {"project_id", "director_id", "template"}
                    if set(payload) - allowed or required - set(payload):
                        raise AllocationError("Task plan fields are missing or unsupported")
                    plan = task_allocation.generate_task_plan(**payload)
                    self._send_json(201, plan)
                    return

                action = re.fullmatch(
                    r"/api/task-plans/([A-Za-z0-9][A-Za-z0-9._-]{0,63})/(approve|extend|observer)",
                    path,
                )
                if action:
                    plan_id, operation = action.groups()
                    if operation == "observer":
                        if payload:
                            raise AllocationError("Observer refresh accepts no request fields")
                        report = task_allocation.refresh_observer_findings(plan_id)
                        self._send_json(200, report)
                        return
                    if set(payload) != {"director_id"}:
                        raise AllocationError("Only Director ID is accepted for this plan action")
                    if operation == "approve":
                        result = task_allocation.approve_plan(
                            plan_id, director_id=payload["director_id"]
                        )
                    else:
                        result = task_allocation.extend_plan(
                            plan_id, director_id=payload["director_id"]
                        )
                    self._send_json(200, result)
                    return

                task_action = re.fullmatch(
                    r"/api/allocation-tasks/([A-Za-z0-9][A-Za-z0-9._-]{0,63})/(evidence|start|review|complete|fail|retry|director-review|assess|resolve-disagreement)",
                    path,
                )
                if not task_action:
                    self._send_json(404, {"error": "Not found"})
                    return
                task_id, operation = task_action.groups()
                if operation in {"start", "retry"}:
                    if set(payload) != {"actor_id"}:
                        raise AllocationError("Only the assigned task actor is accepted")
                    if operation == "start":
                        result = task_allocation.start_task(task_id, actor_id=payload["actor_id"])
                    else:
                        result = task_allocation.retry_task(task_id, actor_id=payload["actor_id"])
                elif operation == "complete":
                    if set(payload) != {"director_id"}:
                        raise AllocationError("Only the approving Director ID is accepted")
                    result = task_allocation.complete_task(
                        task_id, director_id=payload["director_id"]
                    )
                elif operation == "director-review":
                    if set(payload) != {"director_id"}:
                        raise AllocationError("Only the approving Director ID is accepted")
                    result = task_allocation.review_observer_block(
                        task_id, director_id=payload["director_id"]
                    )
                elif operation == "assess":
                    if set(payload) != {
                        "assessor_role_id", "assessment", "evidence_refs", "finding"
                    }:
                        raise AllocationError("Assessment role, type, evidence references, and finding are required")
                    try:
                        assessment_type = ObserverAssessment(payload["assessment"])
                        evidence_refs = tuple(payload["evidence_refs"])
                    except (TypeError, ValueError) as exc:
                        raise AllocationError("Assessment type or evidence references are invalid") from exc
                    result = task_allocation.submit_task_assessment(
                        task_id,
                        assessor_role_id=payload["assessor_role_id"],
                        report=ObserverReport(
                            observer_id=payload["assessor_role_id"],
                            assessment=assessment_type,
                            evidence_refs=evidence_refs,
                            finding=payload["finding"],
                        ),
                    )
                elif operation == "resolve-disagreement":
                    if set(payload) != {"director_id", "decision", "rationale"}:
                        raise AllocationError("Director ID, decision, and rationale are required")
                    result = task_allocation.resolve_task_assessment_disagreement(
                        task_id,
                        director_id=payload["director_id"],
                        decision=payload["decision"],
                        rationale=payload["rationale"],
                    )
                elif operation == "evidence":
                    if set(payload) != {"actor_id", "evidence_type", "content"}:
                        raise AllocationError("Task actor, evidence type, and artifact content are required")
                    result = task_allocation.submit_evidence(
                        task_id,
                        actor_id=payload["actor_id"],
                        evidence_type=payload["evidence_type"],
                        content=payload["content"],
                    )
                elif operation == "review":
                    if set(payload) != {"actor_id"}:
                        raise AllocationError("Only the assigned task actor is accepted")
                    result = task_allocation.submit_task_for_review(
                        task_id,
                        actor_id=payload["actor_id"],
                    )
                else:
                    if set(payload) != {"actor_id", "error_code", "retryable"}:
                        raise AllocationError("Task failure fields are missing or unsupported")
                    result = task_allocation.fail_task(
                        task_id,
                        actor_id=payload["actor_id"],
                        error_code=payload["error_code"],
                        retryable=payload["retryable"],
                    )
                self._send_json(200, result)
            except AllocationError as exc:
                status = 404 if "not found" in str(exc).lower() else 400
                self._send_json(status, {"error": str(exc)})
            except ProjectError as exc:
                self._send_json(400, {"error": str(exc)})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})

        def _handle_worker_action(self, path: str) -> None:
            try:
                payload = self._read_payload()
                if path == "/api/workers":
                    required = {
                        "director_id", "worker_id", "workloads", "state",
                        "vram_mb", "operations", "language_tags", "model_ids",
                        "ttl_seconds",
                    }
                    if set(payload) != required:
                        raise WorkerQueueError("Manual worker registration fields are invalid")
                    ttl = payload["ttl_seconds"]
                    if type(ttl) is not int or not 1 <= ttl <= 12 * 60 * 60:
                        raise WorkerQueueError("Worker registration lifetime must be at most 12 hours")
                    result = worker_queue.register_worker(
                        director_id=payload["director_id"],
                        worker_id=payload["worker_id"],
                        workloads=tuple(payload["workloads"]),
                        state=payload["state"],
                        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
                        vram_mb=payload["vram_mb"],
                        operations=tuple(payload["operations"]),
                        language_tags=tuple(payload["language_tags"]),
                        model_ids=tuple(payload["model_ids"]),
                    )
                elif path == "/api/worker-jobs/recover":
                    if payload:
                        raise WorkerQueueError("Worker recovery accepts no fields")
                    result = {"recovered": worker_queue.recover_expired_leases()}
                else:
                    state_match = re.fullmatch(
                        r"/api/workers/([A-Za-z0-9][A-Za-z0-9._-]{0,63})/state",
                        path,
                    )
                    queue_match = re.fullmatch(
                        r"/api/worker-jobs/queue/([A-Za-z0-9][A-Za-z0-9._-]{0,63})",
                        path,
                    )
                    action_match = re.fullmatch(
                        r"/api/worker-jobs/([A-Za-z0-9][A-Za-z0-9._-]{0,63})/"
                        r"(authorize|lease|start|heartbeat|fail|result|retry|cancel)",
                        path,
                    )
                    if state_match:
                        if set(payload) != {"director_id", "state"}:
                            raise WorkerQueueError("Director ID and worker state are required")
                        result = worker_queue.set_worker_state(
                            state_match.group(1),
                            director_id=payload["director_id"],
                            state=payload["state"],
                        )
                    elif queue_match:
                        if set(payload) != {"director_id", "workload"}:
                            raise WorkerQueueError("Director ID and workload are required")
                        result = worker_queue.enqueue_task(
                            queue_match.group(1),
                            director_id=payload["director_id"],
                            workload=payload["workload"],
                            idempotency_key="",
                        )
                    elif action_match:
                        job_id, operation = action_match.groups()
                        if operation in {"authorize", "retry", "cancel"}:
                            if set(payload) != {"director_id"}:
                                raise WorkerQueueError("Only Director ID is accepted")
                            if operation == "authorize":
                                result = worker_queue.authorize(
                                    job_id, director_id=payload["director_id"]
                                )
                            elif operation == "retry":
                                result = worker_queue.retry(
                                    job_id, director_id=payload["director_id"]
                                )
                            else:
                                result = worker_queue.cancel(
                                    job_id, director_id=payload["director_id"]
                                )
                        elif operation == "lease":
                            if set(payload) - {"worker_id", "lease_seconds"} or "worker_id" not in payload:
                                raise WorkerQueueError("Worker ID and optional lease duration are required")
                            result = worker_queue.lease(
                                job_id, worker_id=payload["worker_id"],
                                lease_seconds=payload.get("lease_seconds", 300),
                            )
                        elif operation == "start":
                            if set(payload) != {"worker_id"}:
                                raise WorkerQueueError("Only Worker ID is accepted")
                            result = worker_queue.start(
                                job_id, worker_id=payload["worker_id"]
                            )
                        elif operation == "heartbeat":
                            if set(payload) - {"worker_id", "lease_seconds"} or "worker_id" not in payload:
                                raise WorkerQueueError("Worker ID and optional lease duration are required")
                            result = worker_queue.heartbeat(
                                job_id, worker_id=payload["worker_id"],
                                lease_seconds=payload.get("lease_seconds", 300),
                            )
                        elif operation == "fail":
                            if set(payload) != {"worker_id", "error_code"}:
                                raise WorkerQueueError("Worker ID and failure code are required")
                            result = worker_queue.fail(
                                job_id, worker_id=payload["worker_id"],
                                error_code=payload["error_code"],
                            )
                        else:
                            required = {"worker_id", "lease_id", "attempt", "outputs"}
                            allowed = required | {"result_id", "completed_at"}
                            if set(payload) - allowed or required - set(payload):
                                raise WorkerQueueError("Worker result fields are missing or unsupported")
                            output_fields = {
                                "artifact_id", "backend", "key", "sha256",
                                "size_bytes", "media_type",
                            }
                            if not isinstance(payload["outputs"], list) or any(
                                not isinstance(output, dict)
                                or set(output) != output_fields
                                for output in payload["outputs"]
                            ):
                                raise WorkerQueueError("Every output reference must have the exact declared metadata fields")
                            if type(payload["attempt"]) is not int:
                                raise WorkerQueueError("Worker result attempt must be an integer")
                            completed_at = (
                                parse_datetime(payload["completed_at"])
                                if "completed_at" in payload
                                else None
                            )
                            result = worker_queue.submit_result(
                                job_id,
                                worker_id=payload["worker_id"],
                                lease_id=payload["lease_id"],
                                attempt=payload["attempt"],
                                outputs=tuple(payload["outputs"]),
                                result_id=payload.get("result_id"),
                                completed_at=completed_at,
                            )
                    else:
                        self._send_json(404, {"error": "Not found"})
                        return
                self._send_json(200 if path != "/api/workers" else 201, result)
            except WorkerQueueError as exc:
                self._send_json(400, {"error": str(exc)})
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})

        def _read_payload(self, *, max_bytes: int = _MAX_REQUEST_BYTES) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > max_bytes:
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
            owned_resources=tuple(
                resource
                for resource, owned in (
                    (project_pipeline, owns_pipeline),
                    (task_allocation, owns_allocation),
                    (worker_queue, True),
                )
                if owned
            ),
        )
    except BaseException:
        if owns_pipeline:
            project_pipeline.close()
        if owns_allocation:
            task_allocation.close()
        worker_queue.close()
        raise
