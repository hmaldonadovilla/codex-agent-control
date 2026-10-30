"use strict";
const $ = (q, root = document) => root.querySelector(q);
const $$ = (q, root = document) => [...root.querySelectorAll(q)];
const token = $('meta[name="control-token"]').content;
let state, currentView = "overview", toastTimer, conversationAgent;
const list = value => value.split(",").map(x => x.trim()).filter(Boolean);
const safe = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const time = value => new Date(value).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
function toast(message) { $("#toast").textContent = message; $("#toast").style.display = "block"; clearTimeout(toastTimer); toastTimer = setTimeout(() => $("#toast").style.display = "none", 7000); }
async function api(path, body, method = "POST") {
  const response = await fetch("/api" + path, {method: body === undefined ? "GET" : method, headers:{"X-Control-Token":token,"Content-Type":"application/json"}, body:body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json(); if (!response.ok) throw Error(typeof value.detail === "string" ? value.detail : "Request failed"); return value;
}
async function action(work) { try { await work(); await refresh(); } catch (error) { toast(error.message); } }
const titles = {overview:["A clear view of your agents.","Keep development moving. Keep the decisions yours."],agents:["The right agent for each job.","Configure responsibilities, conversations and collaboration."],reviews:["Every review, accounted for.","Follow a pull request from CI qualification to a reported assessment."],approvals:["The next move is yours.","Approve concrete actions, with an audit trail."],connections:["Connect your workflow.","Azure DevOps events in. Coordinated Codex work out."],activity:["A record of what happened.","Events, decisions and delivery outcomes in one place."]};
function view(name) { currentView = name; $$(".view").forEach(el => el.classList.toggle("hidden", el.id !== name)); $$('nav button').forEach(el => el.classList.toggle("selected",el.dataset.view === name)); $("#breadcrumb").textContent = name.toUpperCase(); $("#page-title").textContent = titles[name][0]; $("#page-subtitle").textContent = titles[name][1]; }
function empty(title, detail) { return `<div class="empty"><strong>${safe(title)}</strong>${safe(detail)}</div>`; }
function agentCards() {
  return state.agents.map(a => `<article class="agent-card"><div class="agent-top"><div class="avatar">${safe(a.name[0].toLowerCase())}</div><div><h3>${safe(a.name)}</h3><small>${safe(a.role)} · ${a.delivery === "owned" ? "Persistent Agent Desk conversation" : "Linked desktop chat"}</small></div><span class="pill ${a.enabled ? "good" : ""}">${a.enabled ? "Enabled" : "Paused"}</span></div><p>${safe(a.instructions.split("Agent Control reporting:")[0] || (a.review_on_ci ? "Reviews eligible external PRs after CI passes. Proposes changes for your approval." : "Develops within its assigned scope and coordinates integration changes with peers."))}</p><div class="agent-meta"><span>${a.review_on_ci ? "◈ CI review trigger" : "◇ Manual / peer messages"}</span><span>${a.workspaces.length} workspace${a.workspaces.length === 1 ? "" : "s"}</span><span>${a.peers.length} peer${a.peers.length === 1 ? "" : "s"}</span></div><div class="agent-bottom"><code title="${safe(a.thread_id)}">${a.delivery === "owned" ? "One conversation across all runs" : safe(a.thread_id || "Chat not linked")}</code><div>${a.delivery === "desktop" ? `<button class="text-button" data-bootstrap="${safe(a.id)}">Connect reporting</button>` : ""}<a class="text-button" href="codex://threads/${safe(a.thread_id)}">Open desktop chat</a><button class="text-button" data-test-agent="${safe(a.id)}">Test agent</button><button class="text-button" data-edit-agent="${safe(a.id)}">Configure →</button></div></div></article>`).join("") || empty("Build your agent team","Add a reviewer, a developer or a specialist. Every role is configurable.");
}
function activityRows(rows) { return rows.map(a => `<div class="activity-row"><span class="activity-icon">◇</span><div><strong>${safe(a.action.replaceAll("."," · "))}</strong><p>${safe(a.detail)}</p></div><time>${safe(time(a.at))}</time></div>`).join("") || empty("A quiet starting point","New events and decisions will appear here."); }
const statusNames = {pending:"Queued locally",dispatching:"Running",queued_in_codex:"Queued in Codex",reviewed:"Report received",awaiting_handoff:"Approved · manual handoff",blocked:"Needs attention",unknown:"Confirm delivery",superseded:"Revision changed"};
function render() {
  const approvals = state.proposals.filter(p => p.state === "pending").length;
  const blocked = state.jobs.filter(j => ["blocked","unknown"].includes(j.state)).length;
  $("#stat-agents").textContent = state.agents.length; $("#stat-jobs").textContent = state.jobs.filter(j => j.kind === "review").length;
  $("#stat-approvals").textContent = approvals; $("#stat-blocked").textContent = blocked;
  $("#nav-agent-count").textContent = state.agents.length; $("#nav-job-count").textContent = state.jobs.filter(j => j.state === "pending").length; $("#nav-approval-count").textContent = approvals;
  $("#monitor-label").textContent = state.settings.monitor_enabled ? "Automation running" : "Automation paused"; $("#monitor-label").className = "pill" + (state.settings.monitor_enabled ? " good" : "");
  $("#monitor-toggle").textContent = state.settings.monitor_enabled ? "Pause automation" : "Enable automation";
  $("#workspace-label").textContent = state.workspace_root;
  $("#notice").textContent = !state.ado_credential_configured ? "Start by configuring Azure DevOps and testing the Codex connection. Agents are paused until you enable them; no external actions are automatic." : blocked ? `${blocked} run${blocked === 1 ? " needs" : "s need"} attention. Review the queue before retrying; delivery is never assumed.` : "";
  $("#overview-agents").innerHTML = agentCards(); $("#all-agents").innerHTML = agentCards();
  $("#recent-activity").innerHTML = activityRows(state.activity.slice(0,5)); $("#activity-list").innerHTML = activityRows(state.activity);
  $("#jobs").innerHTML = state.jobs.map(j => `<article class="row-card"><div class="row-heading"><div><h3>${j.kind === "review" ? `${safe(j.value.repository_name)} · PR #${safe(j.value.pr)}` : safe(j.kind.replaceAll("_"," "))}</h3><p>${safe(state.agents.find(a => a.id === j.agent_id)?.name || j.agent_id)} · ${safe(time(j.created_at))}${j.value.head ? " · " + safe(j.value.head.slice(0,9)) : ""}</p></div><span class="pill ${["blocked","unknown"].includes(j.state) ? "warn" : j.state === "reviewed" ? "good" : ""}">${safe(statusNames[j.state] || j.state)}</span></div>${j.detail ? `<p class="muted">${safe(j.detail.slice(0,250))}</p>` : ""}<div class="row-actions"><button class="secondary" data-open-job="${j.id}">View run</button>${j.value.url ? `<a class="text-button" href="${safe(j.value.url)}" target="_blank" rel="noopener noreferrer">Open PR ↗</a>` : ""}${j.state === "blocked" ? `<button class="text-button" data-retry="${j.id}">Retry connection →</button>` : ""}</div></article>`).join("") || empty("No review runs yet","Use Check ADO now after connecting, or let authenticated CI events enter the queue.");
  $("#proposals").innerHTML = state.proposals.map(p => `<article class="row-card"><div class="row-heading"><div><h3>${safe(p.value.title)}</h3><p>${safe(p.value.kind.replaceAll("_"," "))} · ${safe(p.value.target_agent_id)}</p></div><span class="pill ${p.state === "pending" ? "warn" : ""}">${safe(p.state.replaceAll("_"," "))}</span></div><div class="proposal-body">${safe(p.value.body)}</div>${p.state === "pending" ? `<div class="row-actions"><button class="primary" data-approve="${p.id}">Approve & queue</button><button class="secondary" data-reject="${p.id}">Reject</button></div>` : ""}</article>`).join("") || empty("No pending decisions","Agents can submit proposals. You can also prepare an exact action from a review.");
  $$("[data-approve]").forEach(button => { const proposal = state.proposals.find(p => p.id === button.dataset.approve); button.textContent = (state.agents.find(a => a.id === proposal.value.target_agent_id)?.delivery === "owned" || !state.agents.find(a => a.id === proposal.value.target_agent_id)?.enabled) ? "Approve for handoff" : "Approve & queue"; });
  $$(".agent-select").forEach(select => { const value = select.value; select.innerHTML = state.agents.map(a => `<option value="${safe(a.id)}">${safe(a.name)}</option>`).join(""); if (state.agents.some(a => a.id === value)) select.value = value; });
  $("#proposal-job").innerHTML = '<option value="">No linked review</option>' + state.jobs.filter(j => j.kind === "review").map(j => `<option value="${j.id}">${safe(j.value.repository_name)} #${safe(j.value.pr)} · ${safe(j.value.head.slice(0,7))}</option>`).join("");
  $("#ado-status").textContent = state.ado_credential_configured ? "Credential saved" : "Not configured";
  $("#tunnel-status").textContent = state.tunnel?.configured ? "Azure Dev Tunnel configured" : "Tunnel not configured";
  $("#tunnel-url").textContent = state.tunnel?.configured ? state.tunnel.url + "/webhooks/ado" : "";
  const received = Object.values(state.event_counts || {}).reduce((sum, count) => sum + count, 0);
  $("#event-counts").textContent = `${received} event(s) received. ${state.event_counts?.pending || 0} awaiting validation. Chat delivery must be connected before automation can start.`;
  $("#subscription-status").textContent = state.ado_subscription?.registered ? "ADO subscription registered: successful builds in " + state.ado_subscription.project : "ADO event subscription not registered.";
  if (!$("dialog[open]")) {
    const f = $("#ado-form"); ["organization","project"].forEach(k => f.elements[k].value = state.settings.ado[k]);
    ["repositories","excluded_authors"].forEach(k => f.elements[k].value = state.settings.ado[k].join(", "));
    ["exclude_own","enabled"].forEach(k => f.elements[k].checked = state.settings.ado[k]);
    $("#codex-form").elements.command.value = state.settings.codex_command; $("#poll-form").elements.poll_seconds.value = state.settings.poll_seconds; $("#poll-form").elements.reconcile_periodically.checked = state.settings.reconcile_periodically; $("#poll-form").elements.review_grace_seconds.value = state.settings.review_grace_seconds;
  }
  $("#last-refreshed").textContent = "Updated " + time(new Date());
}
async function refresh() { state = await api("/state"); render(); }
function openAgent(id) {
  const f = $("#agent-form"); f.reset(); const a = state.agents.find(a => a.id === id);
  f.elements.id.readOnly = Boolean(a);
  if (a) {
    ["id","name","role","thread_id","instructions","delivery","model"].forEach(k => f.elements[k].value = a[k]);
    ["repositories","peers"].forEach(k => f.elements[k].value = a[k].join(", "));
    f.elements.workspaces.value = a.workspaces.map(w => w.repository + " | " + w.path).join("\n");
    ["enabled","review_on_ci"].forEach(k => f.elements[k].checked = a[k]);
  }
  $("#agent-dialog").showModal();
}
document.addEventListener("click", e => {
  const button = e.target.closest("button"); if (!button) return;
  if (button.dataset.view) view(button.dataset.view);
  if (button.dataset.close) $("#" + button.dataset.close).close();
  if (button.dataset.action === "add-agent") openAgent();
  if (button.dataset.editAgent) openAgent(button.dataset.editAgent);
  if (button.dataset.chatAgent) action(() => openConversation(button.dataset.chatAgent));
  if (button.dataset.testAgent) action(async () => { const result = await api(`/agents/${button.dataset.testAgent}/test`,{}); toast(result.detail); });
  if (button.dataset.bootstrap) action(async () => { const result=await api(`/agents/${button.dataset.bootstrap}/bootstrap`,{}); toast(`Private reporting credentials saved: ${result.path}`); });
  if (button.dataset.retry) action(async () => { await api(`/jobs/${button.dataset.retry}/retry`,{}); toast("Retry queued locally"); });
  if (button.dataset.approve) {
    const proposal = state.proposals.find(p => p.id === button.dataset.approve);
    const owned = (state.agents.find(a => a.id === proposal.value.target_agent_id)?.delivery === "owned" || !state.agents.find(a => a.id === proposal.value.target_agent_id)?.enabled);
    if (confirm(owned ? "Approve this exact action for manual handoff? The automatic reviewer remains read-only." : "Approve this exact action and queue it to the selected Codex chat?")) action(async () => { await api(`/proposals/${button.dataset.approve}/approve`,{}); toast("Approval recorded. Execution is still pending."); });
  }
  if (button.dataset.reject) action(() => api(`/proposals/${button.dataset.reject}/reject`,{}));
  if (button.dataset.openJob) openRun(button.dataset.openJob);
});
$("#agent-form").addEventListener("submit",e => { e.preventDefault(); action(async () => { const f = e.target.elements; const value = Object.fromEntries(["id","name","role","thread_id","instructions","delivery","model"].map(k => [k,f[k].value.trim()])); value.enabled=f.enabled.checked; value.review_on_ci=f.review_on_ci.checked; value.repositories=list(f.repositories.value); value.peers=list(f.peers.value); value.workspaces=f.workspaces.value.split("\n").filter(x => x.trim()).map(line => { const [repository,...parts]=line.split("|"); if (!parts.length) throw Error("Use repository | absolute path for each workspace"); return {repository:repository.trim(),path:parts.join("|").trim()}; }); await api(`/agents/${value.id}`,value,"PUT"); $("#agent-dialog").close(); toast("Agent saved"); }); });
$("#ado-form").addEventListener("submit",e => { e.preventDefault(); action(async () => { const f=e.target.elements; const settings={...state.settings.ado,organization:f.organization.value.trim(),project:f.project.value.trim(),repositories:list(f.repositories.value),excluded_authors:list(f.excluded_authors.value),exclude_own:f.exclude_own.checked,enabled:f.enabled.checked}; await api("/connections/ado",{settings,pat:f.pat.value || null}); f.pat.value=""; toast("ADO connection saved. Test it before enabling automation."); }); });
$("#codex-form").addEventListener("submit",e => { e.preventDefault(); action(async () => { await api("/settings",{...state.settings,codex_command:e.target.elements.command.value.trim()},"PUT"); toast("Codex executable saved"); }); });
$("#poll-form").addEventListener("submit",e => { e.preventDefault(); action(async () => { await api("/settings",{...state.settings,poll_seconds:Number(e.target.elements.poll_seconds.value),review_grace_seconds:Number(e.target.elements.review_grace_seconds.value),reconcile_periodically:e.target.elements.reconcile_periodically.checked},"PUT"); toast("Review timing saved"); }); });
$("#test-ado").onclick=() => action(async () => { const result=await api("/connections/ado/test",{}); $("#ado-result").textContent=`Connected to ${result.project}. Your author ID is ${result.identity}; it will be excluded from automatic reviews.`; toast("ADO identity verified"); });
$("#test-codex").onclick=() => action(async () => { const result=await api("/connections/codex/test",{}); $("#codex-result").textContent=result.detail; $("#codex-status").textContent=result.ready ? "Queue available" : "Needs connection"; $("#codex-status").className="pill " + (result.ready ? "good" : "warn"); });
$("#webhook-details").onclick=() => action(async () => { const result=await api("/webhook/setup"); $("#webhook-result").textContent=JSON.stringify(result,null,2); $("#webhook-result").classList.remove("hidden"); });
$("#connect-subscription").onclick=() => action(async () => { await api("/webhook/subscription",{}); toast("Successful ADO build events connected. Agent eligibility and CI are checked before each review."); });
$("#monitor-toggle").onclick=() => action(async () => { await api("/settings",{...state.settings,monitor_enabled:!state.settings.monitor_enabled},"PUT"); toast(state.settings.monitor_enabled ? "Automation paused" : "Automation enabled"); });
$("#reconcile").onclick=() => action(async () => { const result=await api("/reconcile",{}); toast(`${result.jobs.length} matching run(s). ${result.held.length} PR(s) held by author, scope or CI rules.`); });
$("#dispatch").onclick=() => action(async () => { const result=await api("/dispatch",{}); toast(result.processed ? "Dispatch processed. Check its delivery state." : "No queued runs"); });
$("#message-form").addEventListener("submit",e => { e.preventDefault(); action(async () => { await api("/messages",{target_agent_id:e.target.elements.target_agent_id.value,body:e.target.elements.body.value}); e.target.elements.body.value=""; toast("Message queued locally"); }); });
$("#worktree-form").addEventListener("submit",e => { e.preventDefault(); action(async () => { const result=await api("/worktrees",Object.fromEntries(new FormData(e.target))); $("#worktree-result").textContent=result.path+"\n\n"+result.detail; $("#worktree-result").classList.remove("hidden"); }); });
$("#new-proposal").onclick=() => { $("#proposal-form").reset(); $("#proposal-dialog").showModal(); };
$("#proposal-form").addEventListener("submit",e => { e.preventDefault(); action(async () => { const value=Object.fromEntries(new FormData(e.target)); value.job_id=value.job_id || null; await api("/proposals",value); $("#proposal-dialog").close(); toast("Approval request prepared"); }); });
function openRun(id) {
  const j=state.jobs.find(j => j.id === id); $("#run-title").textContent=j.kind === "review" ? `${j.value.repository_name} · PR #${j.value.pr}` : "Agent run";
  $("#run-content").innerHTML=`<p class="muted">Run ${safe(j.id)} · ${safe(statusNames[j.state] || j.state)}</p><pre>${safe(j.prompt)}</pre><p class="muted">${safe(j.detail)}</p><div class="button-row"><button class="secondary" id="copy-run">Copy handoff prompt</button>${j.state === "unknown" ? '<button class="secondary" id="resolve-delivered">I verified delivery</button><button class="secondary" id="resolve-not-delivered">I verified no delivery</button>' : ""}</div>${["queued_in_codex","reviewed","awaiting_handoff"].includes(j.state) ? '<form id="report-form"><label>Review summary / completion report<textarea name="summary" rows="5" required></textarea></label><button class="primary">Record report</button></form>' : ""}`;
  $("#copy-run").onclick=async () => { await navigator.clipboard.writeText(`Agent Control run ID: ${j.id}\n\n${j.prompt}`); toast("Handoff prompt copied"); };
  for (const [selector,outcome] of [["#resolve-delivered","delivered"],["#resolve-not-delivered","not_delivered"]]) if ($(selector)) $(selector).onclick=() => action(async () => { await api(`/jobs/${id}/resolve`,{outcome}); $("#run-dialog").close(); });
  if ($("#report-form")) $("#report-form").onsubmit=e => { e.preventDefault(); action(async () => { await api("/reports",{job_id:id,summary:e.target.elements.summary.value}); $("#run-dialog").close(); toast("Completion report recorded"); }); };
  $("#run-dialog").showModal();
}
async function openConversation(agentId) {
  conversationAgent = agentId;
  const result = await api(`/agents/${agentId}/conversation`);
  $("#conversation-title").textContent = result.agent;
  $("#conversation-info").textContent = result.delivery === "owned" ? (result.runtime_thread_id ? "Persistent conversation: " + result.runtime_thread_id : "The first message creates this agent's conversation; later runs reuse it.") : "Desktop chat linked. Messages need a working desktop queue connection; pending work remains visible here.";
  $("#conversation-messages").innerHTML = result.messages.map(m => {
    const job = state.jobs.find(j => j.id === m.job_id);
    const body = m.role === "user" && job ? (job.kind === "review" ? `CI passed. Review ${job.value.repository_name}, PR #${job.value.pr}, source ${job.value.head.slice(0,9)}.` : job.prompt.split("\n\n").slice(1).join("\n\n")) : m.body;
    return `<article class="row-card"><strong>${m.role === "user" ? "Incoming job / message" : "Agent"}</strong><pre class="proposal-body">${safe(body)}</pre></article>`;
  }).join("") || empty("No turns yet","One conversation will keep this agent's review and coordination history.");
  if (!$("#conversation-dialog").open) $("#conversation-dialog").showModal();
  const waiting = state.jobs.filter(j => j.agent_id === agentId && j.kind === "message" && ["pending","blocked","unknown"].includes(j.state));
  if (waiting.length) {
    if (!result.messages.length) $("#conversation-messages").innerHTML = "";
    $("#conversation-messages").insertAdjacentHTML("beforeend", waiting.map(j => `<article class="row-card"><strong>Message awaiting delivery</strong><p class="muted">${safe(j.detail || "Queued locally")}</p><pre class="proposal-body">${safe(j.prompt)}</pre></article>`).join(""));
  }
}
$("#refresh-conversation").onclick=() => action(() => openConversation(conversationAgent));
$("#conversation-form").onsubmit=e => { e.preventDefault(); action(async () => { await api("/messages",{target_agent_id:conversationAgent,body:e.target.elements.body.value}); e.target.elements.body.value=""; toast("Message queued to this agent's existing conversation"); }); };
refresh().catch(error => toast(error.message));
setInterval(() => { if (!$("dialog[open]") && !["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName)) refresh().catch(() => {}); },10000);
