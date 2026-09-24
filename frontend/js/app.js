const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  token: localStorage.getItem("learning-agent-token") || "dev-token",
  view: "chat",
  archiveSection: "overview",
  threads: [],
  currentThreadId: null,
  messages: [],
  memory: null,
  files: [],
  selectedImages: [],
  streaming: false,
  pendingVision: null,
};

const factLabels = {
  goal: "学习目标",
  constraint: "学习约束",
  learning_signal: "薄弱点",
  preference: "学习偏好",
};
const factIcons = { goal: "◎", constraint: "◷", learning_signal: "△", preference: "✦" };
const masteryLabels = { unknown: "尚未评估", weak: "需要加强", developing: "正在形成", stable: "较稳定" };
const learningLabels = { unseen: "尚未开始", exposed: "接触过", practicing: "练习中", reviewing: "复习中" };

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function formatText(value = "") {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}

function apiHeaders(extra = {}) {
  return { Authorization: `Bearer ${state.token}`, ...extra };
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${state.token}`);
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const response = await fetch(path, { ...options, body, headers });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      message = payload.detail || payload.message || payload.title || message;
    } catch { /* response may not be JSON */ }
    throw new Error(message);
  }
  return response;
}

async function jsonApi(path, options = {}) {
  const response = await api(path, options);
  return response.json();
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("#toast-stack").append(node);
  window.setTimeout(() => node.remove(), 3600);
}

function setBusy(button, busy, busyText = "处理中…") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

function currentThread() {
  return state.threads.find((item) => item.id === state.currentThreadId) || null;
}

function renderSessions() {
  const container = $("#session-list");
  if (!state.threads.length) {
    container.innerHTML = '<div class="empty-small">还没有会话，点击右上角新建一个。</div>';
    return;
  }
  container.innerHTML = state.threads.map((thread) => `
    <div class="session-item ${thread.id === state.currentThreadId ? "is-active" : ""}" data-thread-id="${escapeHtml(thread.id)}">
      <span class="session-name" title="${escapeHtml(thread.name)}">${escapeHtml(thread.name)}</span>
      <button class="session-action" data-session-action="rename" data-thread-id="${escapeHtml(thread.id)}" title="重命名" aria-label="重命名">✎</button>
    </div>`).join("");
}

function updateThreadLabels() {
  const thread = currentThread();
  const name = thread ? thread.name : "未选择会话";
  $("#current-session-pill").textContent = name;
  $("#breadcrumb-current").textContent = thread ? name : "新问题";
}

async function loadThreads(selectFirst = true) {
  try {
    const data = await jsonApi("/v1/threads");
    state.threads = data.items || [];
    if (state.currentThreadId && !state.threads.some((item) => item.id === state.currentThreadId)) state.currentThreadId = null;
    if (!state.currentThreadId && selectFirst && state.threads[0]) state.currentThreadId = state.threads[0].id;
    renderSessions();
    updateThreadLabels();
    if (state.currentThreadId) await loadThread(state.currentThreadId);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadThread(threadId) {
  if (!threadId) return;
  try {
    const data = await jsonApi(`/v1/threads/${encodeURIComponent(threadId)}`);
    state.currentThreadId = data.id;
    state.messages = data.messages || [];
    renderSessions();
    updateThreadLabels();
    renderMessages();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function createSession() {
  const number = state.threads.length + 1;
  const data = await jsonApi("/v1/threads", { method: "POST", body: { name: `学习会话 ${number}` } });
  state.currentThreadId = data.id;
  state.messages = [];
  await loadThreads(false);
  renderMessages();
  toast("已创建新会话", "success");
  return data.id;
}

function openRename(threadId) {
  const thread = state.threads.find((item) => item.id === threadId);
  if (!thread) return;
  const modal = $("#rename-modal");
  modal.dataset.threadId = threadId;
  $("#rename-input").value = thread.name;
  modal.showModal();
  window.setTimeout(() => $("#rename-input").select(), 40);
}

async function renameSession() {
  const modal = $("#rename-modal");
  const threadId = modal.dataset.threadId;
  const name = $("#rename-input").value.trim();
  if (!name) return toast("会话名称不能为空", "error");
  const button = $("#rename-submit");
  setBusy(button, true, "保存中…");
  try {
    const data = await jsonApi(`/v1/threads/${encodeURIComponent(threadId)}`, { method: "PATCH", body: { name } });
    const thread = state.threads.find((item) => item.id === threadId);
    if (thread) thread.name = data.name;
    renderSessions();
    updateThreadLabels();
    modal.close();
    toast("会话名称已更新", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function deleteCurrentSession() {
  const thread = currentThread();
  if (!thread || !window.confirm(`确定删除“${thread.name}”吗？该会话的短期状态和关联学习来源都会被清理。`)) return;
  try {
    await api(`/v1/threads/${encodeURIComponent(thread.id)}`, { method: "DELETE", headers: { "Idempotency-Key": crypto.randomUUID() } });
    state.currentThreadId = null;
    state.messages = [];
    await loadThreads();
    renderMessages();
    toast("会话已删除", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderMessages() {
  const container = $("#messages");
  if (!state.messages.length) {
    container.innerHTML = welcomeMarkup();
    return;
  }
  container.innerHTML = state.messages.map((message) => {
    const role = message.role === "assistant" ? "assistant" : "user";
    return `<div class="message-row ${role}">
      ${role === "assistant" ? '<div class="message-avatar">✦</div>' : ""}
      <div><div class="message-bubble">${formatText(message.content || "")}</div><div class="message-meta">${role === "assistant" ? "拾光助手" : "你"}</div></div>
      ${role === "user" ? '<div class="message-avatar">学</div>' : ""}
    </div>`;
  }).join("");
  container.scrollTop = container.scrollHeight;
}

function welcomeMarkup() {
  return `<div class="welcome-card" id="welcome-card">
    <div class="welcome-orb">✦</div><h2>从一个问题开始</h2>
    <p>可以问我解释概念、梳理资料、制定学习计划，或者帮你复盘刚刚做错的题。</p>
    <div class="suggestion-grid">
      <button class="suggestion-card" data-prompt="请帮我制定今天的学习计划"><span class="suggestion-icon warm">☀</span><span><b>制定学习计划</b><small>安排今天的学习节奏</small></span></button>
      <button class="suggestion-card" data-prompt="用一个例子解释什么是特征值"><span class="suggestion-icon blue">◇</span><span><b>解释一个概念</b><small>从直觉和例子开始理解</small></span></button>
      <button class="suggestion-card" data-prompt="帮我复盘最近容易出错的知识点"><span class="suggestion-icon green">✓</span><span><b>复盘薄弱点</b><small>找到下一步最值得练习的内容</small></span></button>
    </div>
  </div>`;
}

function addTyping(label = "正在思考…") {
  const container = $("#messages");
  const node = document.createElement("div");
  node.className = "message-row assistant";
  node.id = "typing-row";
  node.innerHTML = `<div class="message-avatar">✦</div><div><div class="message-bubble"><span class="typing"><i></i><i></i><i></i></span><span id="typing-label"> ${escapeHtml(label)}</span></div><div class="message-meta">拾光助手</div></div>`;
  container.append(node);
  container.scrollTop = container.scrollHeight;
}

function updateTyping(label) {
  const node = $("#typing-label");
  if (node) node.textContent = ` ${label}`;
}

function removeTyping() { $("#typing-row")?.remove(); }

async function consumeSse(response, onEvent) {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (block) => {
    const lines = block.split(/\r?\n/);
    let event = "message";
    let raw = "";
    for (const line of lines) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) raw += line.slice(5).trim();
    }
    if (!raw) return;
    let data = raw;
    try { data = JSON.parse(raw); } catch { /* plain SSE data */ }
    onEvent(event, data);
  };
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      dispatch(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
    }
    if (done) break;
  }
  if (buffer.trim()) dispatch(buffer);
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.streaming) return;
  const input = $("#message-input");
  const message = input.value.trim();
  if (!message && !state.selectedImages.length) return;
  state.streaming = true;
  $("#send-button").disabled = true;
  try {
    if (!state.currentThreadId) await createSession();
    state.messages.push({ role: "user", content: message || "请解答这道图片题。" });
    renderMessages();
    addTyping();
    input.value = "";
    autoSizeTextarea(input);
    const form = new FormData();
    form.append("payload", JSON.stringify({ thread_id: state.currentThreadId, message: message || "请解答这道图片题。" }));
    state.selectedImages.forEach((image) => form.append("images", image));
    const response = await api("/v1/chat/stream", { method: "POST", body: form, headers: { "Idempotency-Key": crypto.randomUUID() } });
    await consumeSse(response, (eventName, data) => {
      if (eventName === "turn.started" && data.thread_id) state.currentThreadId = data.thread_id;
      if (eventName === "node.progress") updateTyping(data.label || "正在处理…");
      if (eventName === "answer.completed") {
        removeTyping();
        state.messages.push({ role: "assistant", content: data.answer || "" });
        renderMessages();
      }
      if (eventName === "confirmation.required") openVisionConfirmation(data);
      if (eventName === "error") toast(data.message || "回答失败", "error");
    });
    removeTyping();
    await loadThreads(false);
    clearSelectedImages();
    if (state.view === "archive") await loadMemory(true);
  } catch (error) {
    removeTyping();
    toast(error.message, "error");
  } finally {
    state.streaming = false;
    $("#send-button").disabled = false;
  }
}

function autoSizeTextarea(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 150)}px`;
}

function clearSelectedImages() {
  state.selectedImages = [];
  $("#image-input").value = "";
  $("#file-chips").innerHTML = "";
}

function renderSelectedImages() {
  $("#file-chips").innerHTML = state.selectedImages.map((file) => `<span class="file-chip">⌁ ${escapeHtml(file.name)}</span>`).join("");
}

function openVisionConfirmation(data) {
  state.pendingVision = { ...data, key: crypto.randomUUID() };
  $("#vision-text").value = data.recognized_text || "";
  $("#vision-modal").showModal();
}

async function submitVision(action) {
  if (!state.pendingVision) return;
  const data = state.pendingVision;
  const body = { action, recognized_text: action === "confirm" ? $("#vision-text").value : null, formulas: data.formulas || [] };
  try {
    const response = await api(`/v1/turns/${encodeURIComponent(data.turn_id)}/vision-confirmation`, { method: "POST", body, headers: { "Idempotency-Key": data.key } });
    $("#vision-modal").close();
    await consumeSse(response, (eventName, payload) => {
      if (eventName === "node.progress") { addTyping(payload.label || "正在处理…"); }
      if (eventName === "answer.completed") { removeTyping(); state.messages.push({ role: "assistant", content: payload.answer || "" }); renderMessages(); }
    });
    state.pendingVision = null;
  } catch (error) { toast(error.message, "error"); }
}

function switchView(view) {
  state.view = view;
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("is-visible", item.id === `view-${view}`));
  $("#archive-nav").hidden = view !== "archive";
  const labels = { chat: "对话学习", library: "资料库", archive: "学习档案" };
  $("#breadcrumb-root").textContent = labels[view];
  if (view === "library") loadFiles();
  if (view === "archive") loadMemory();
  if (window.innerWidth < 780) $("#sidebar").classList.remove("is-open");
}

function switchArchiveSection(section) {
  state.archiveSection = section;
  $$(".archive-nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.archiveSection === section));
  $$(".archive-section").forEach((item) => item.classList.toggle("is-visible", item.dataset.archiveContent === section));
  const titles = { overview: "总览", profile: "个人偏好", knowledge: "知识点", review: "复习计划" };
  const descriptions = { overview: "把零散的学习痕迹整理成下一步可执行的方向。", profile: "记录你的目标、偏好和学习限制，让回答越来越贴合你。", knowledge: "用状态和进度看见每个知识点正在发生的变化。", review: "按优先级安排今天要巩固的内容，保持稳定的学习节奏。" };
  $("#archive-title").textContent = `学习档案 · ${titles[section]}`;
  $("#archive-subtitle").textContent = descriptions[section];
  if (state.memory) renderArchive();
}

async function loadMemory(force = false) {
  if (state.memory && !force) { renderArchive(); updateChatContext(); return; }
  try {
    state.memory = await jsonApi("/v1/memory");
    renderArchive();
    updateChatContext();
  } catch (error) { toast(error.message, "error"); }
}

function factText(fact) {
  const value = fact.value || "";
  const key = fact.fact_key || "";
  if (key === "study_goal") return `学习目标：${value}`;
  if (key === "daily_minutes") return `每天大约安排 ${value} 分钟学习`;
  if (key === "weak_topic") return `正在加强：${value}`;
  if (key === "teaching_preference") return `回答偏好：${value}`;
  return `${factLabels[fact.kind] || "学习档案"}：${value}`;
}

function factName(fact) { return factLabels[fact.kind] || fact.fact_key || "学习档案"; }
function masteryPercent(item) { return { stable: 92, developing: 66, weak: 34, unknown: 15 }[item.mastery_status] || 15; }
function masteryLabel(item) { return masteryLabels[item.mastery_status] || "尚未评估"; }

function renderArchive() {
  renderOverview();
  renderProfile();
  renderKnowledge();
  renderReview();
  switchArchiveSection(state.archiveSection);
}

function renderOverview() {
  const memory = state.memory || { facts: [], candidates: [], knowledge: [], review: { items: [], backlog: [], budget_minutes: 0, used_minutes: 0 } };
  const facts = memory.facts || [];
  const knowledge = memory.knowledge || [];
  const review = memory.review || {};
  const items = review.items || [];
  const avg = knowledge.length ? Math.round(knowledge.reduce((sum, item) => sum + masteryPercent(item), 0) / knowledge.length) : 0;
  const weak = knowledge.filter((item) => item.mastery_status === "weak");
  const focus = weak.length ? `建议优先复习“${weak[0].name}”，它目前被标记为需要加强。` : items.length ? `今天有 ${items.length} 个知识点到期，安排一小段专注时间就能保持进度。` : "先完成一次学习或添加一个知识点，这里会逐渐形成你的学习地图。";
  $("#overview-content").innerHTML = `
    <div class="stats-grid">
      ${statCard("✦", "长期记忆", facts.length, "条已确认档案")}
      ${statCard("◇", "知识点", knowledge.length, `${weak.length} 个需要加强`)}
      ${statCard("◷", "今日复习", items.length, `${review.used_minutes || 0} / ${review.budget_minutes || 0} 分钟`)}
      ${statCard("↗", "整体进度", knowledge.length ? `${avg}%` : "—", knowledge.length ? "来自知识点掌握度" : "等待第一条记录")}
    </div>
    <div class="archive-grid">
      <div>
        <div class="archive-panel">
          <div class="archive-panel-heading"><div><h2>最近记录的学习偏好</h2><p>这些信息会帮助助手调整讲解方式。</p></div><button class="text-link" data-archive-link="profile">查看全部 →</button></div>
          <div class="fact-list">${facts.length ? facts.slice(0, 5).map((fact) => `<div class="fact-line"><span class="fact-icon">${factIcons[fact.kind] || "✦"}</span><div><b>${escapeHtml(factName(fact))}</b><span>${escapeHtml(factText(fact).replace(`${factName(fact)}：`, ""))}</span></div><small>${Math.round((fact.confidence || 0) * 100)}% 可信</small></div>`).join("") : '<div class="empty-panel">还没有长期档案。和助手聊几次，你的目标和偏好会逐渐被整理出来。</div>'}</div>
        </div>
        <div class="archive-panel">
          <div class="archive-panel-heading"><div><h2>下一步建议</h2><p>根据当前档案自动整理出的学习提醒。</p></div></div>
          <div class="insight-line" style="color:var(--ink);"><span>✦</span><div>${escapeHtml(focus)}</div></div>
        </div>
      </div>
      <div class="insight-card"><div class="eyebrow" style="color:#b5d9a5">TODAY'S DIRECTION</div><h2>今天，保持一点点前进。</h2><p>学习不需要一次完成所有事情。先选择一个最值得投入的知识点，完成一轮专注练习。</p><div class="insight-list"><div class="insight-line"><span>↗</span><div>${items.length ? `有 ${items.length} 项复习任务等你处理` : "今天暂时没有到期复习"}</div></div><div class="insight-line"><span>◌</span><div>${facts.length ? `助手已经记住 ${facts.length} 条你的学习信息` : "完成一次对话后会生成你的第一条学习信息"}</div></div></div></div>
    </div>`;
}

function statCard(icon, label, value, note) { return `<div class="archive-stat"><span class="archive-stat-icon">${icon}</span><span class="archive-stat-label">${label}</span><strong class="archive-stat-value">${escapeHtml(value)}</strong><div class="archive-stat-note">${escapeHtml(note)}</div></div>`; }

function renderProfile() {
  const memory = state.memory || { facts: [], candidates: [] };
  const facts = memory.facts || [];
  const candidates = memory.candidates || [];
  $("#profile-content").innerHTML = `<div class="profile-layout"><div class="surface table-surface"><div class="surface-heading"><div><h2>已确认的个人档案</h2><p>展示为可读的学习信息，而不是原始字段。</p></div><span class="status-badge">${facts.length} 条记录</span></div><div class="table-scroll"><table class="data-table"><thead><tr><th>档案分类</th><th>内容</th><th>可信度</th><th>操作</th></tr></thead><tbody>${facts.length ? facts.map((fact) => `<tr><td><span class="table-title">${escapeHtml(factName(fact))}</span><div class="table-subtitle">${escapeHtml(fact.fact_key || "")}</div></td><td><span class="table-title">${escapeHtml(factText(fact))}</span><div class="table-subtitle">来源于一次学习对话</div></td><td><span class="status-badge">${Math.round((fact.confidence || 0) * 100)}%</span></td><td><button class="small-button danger" data-fact-delete="${escapeHtml(fact.fact_id)}">删除</button></td></tr>`).join("") : '<tr><td colspan="4"><div class="empty-panel">还没有已确认的个人档案。</div></td></tr>'}</tbody></table></div></div><div class="archive-panel"><div class="archive-panel-heading"><div><h2>待确认信息</h2><p>助手不确定的内容会先放在这里。</p></div></div><div class="candidate-list">${candidates.length ? candidates.map(candidateCard).join("") : '<div class="empty-panel">暂无待确认内容</div>'}</div></div></div>`;
}

function candidateCard(candidate) { return `<div class="candidate-card"><div class="candidate-head"><b>${escapeHtml(factLabels[candidate.kind] || candidate.key || "待确认信息")}</b><span class="status-badge warning">待确认</span></div><div class="candidate-value">${escapeHtml(candidate.value || "")}</div><div class="candidate-evidence">原话依据：“${escapeHtml(candidate.evidence_quote || "未记录") }”</div><div class="candidate-actions"><button class="small-button" data-candidate-action="reject" data-candidate-id="${escapeHtml(candidate.candidate_id)}">不是这样</button><button class="small-button" data-candidate-action="confirm" data-candidate-id="${escapeHtml(candidate.candidate_id)}">确认记住</button></div></div>`; }

function renderKnowledge() {
  const knowledge = state.memory?.knowledge || [];
  const weakCount = knowledge.filter((item) => item.mastery_status === "weak").length;
  const stableCount = knowledge.filter((item) => item.mastery_status === "stable").length;
  const rows = knowledge.length ? knowledge.map((item) => { const percent = masteryPercent(item); return `<div class="knowledge-row"><div class="knowledge-name"><b>${escapeHtml(item.name || "未命名知识点")}</b><small>${escapeHtml(item.subject || "未分类")} · ${escapeHtml(learningLabels[item.learning_status] || item.learning_status || "学习中")}</small></div><div><div class="progress-track"><div class="progress-bar" style="width:${percent}%"></div></div><div class="progress-meta"><span>掌握进度</span><span>${percent}%</span></div></div><div class="mastery-label"><strong>${masteryLabel(item)}</strong><span>${item.self_rating == null ? "暂无自评" : `自评 ${item.self_rating}/5`}</span></div><div class="table-actions"><button class="small-button" data-archive-link="review">复习</button></div></div>`; }).join("") : '<div class="empty-panel">还没有知识点。可以在 CLI 中添加知识点，或先和助手进行一次学习对话。</div>';
  $("#knowledge-content").innerHTML = `<div class="knowledge-layout"><div class="surface archive-panel"><div class="archive-panel-heading"><div><h2>知识点地图</h2><p>进度根据学习事件和自评结果综合计算。</p></div><span class="status-badge">${knowledge.length} 个知识点</span></div>${rows}</div><div><div class="archive-panel"><div class="archive-panel-heading"><div><h2>掌握情况</h2><p>把注意力放在最需要的地方。</p></div></div><div class="fact-list"><div class="fact-line"><span class="fact-icon">✓</span><div><b>较稳定</b><span>已经形成连续练习</span></div><small>${stableCount} 个</small></div><div class="fact-line"><span class="fact-icon">△</span><div><b>需要加强</b><span>建议安排下一轮复习</span></div><small>${weakCount} 个</small></div></div></div><div class="insight-card" style="margin-top:18px;min-height:160px"><div class="eyebrow" style="color:#b5d9a5">HOW IT WORKS</div><p style="margin-top:10px">每次解释、练习、自评和复习反馈都会成为一条学习证据，帮助进度条逐渐变得更准确。</p></div></div></div>`;
}

function renderReview() {
  const review = state.memory?.review || { items: [], backlog: [], budget_minutes: 0, used_minutes: 0 };
  const items = review.items || [];
  const backlog = review.backlog || [];
  const budget = Number(review.budget_minutes || 0);
  const used = Number(review.used_minutes || 0);
  const percent = budget ? Math.min(100, Math.round((used / budget) * 100)) : 0;
  const reviewRows = items.length ? items.map((item) => `<div class="review-item"><div><b>${escapeHtml(item.name || "知识点")}</b><small>${escapeHtml(item.subject || "")} · 逾期 ${item.overdue_days || 0} 天</small></div><div class="review-date ${item.overdue_days ? "overdue" : ""}">${escapeHtml(formatDate(item.due_at))}</div><span class="status-badge warning">待复习</span></div>`).join("") : '<div class="empty-panel">今天没有到期任务，可以继续学习新内容。</div>';
  const backlogRows = backlog.length ? backlog.map((item) => `<div class="backlog-line"><span>${escapeHtml(item.name || "知识点")}</span><b>${item.estimated_minutes || 5} 分钟</b></div>`).join("") : '<div class="empty-panel">没有超出今日预算的任务。</div>';
  $("#review-content").innerHTML = `<div class="review-layout"><div class="surface archive-panel"><div class="archive-panel-heading"><div><h2>今日复习</h2><p>按照到期时间和薄弱程度排列。</p></div><span class="status-badge warning">${items.length} 项待处理</span></div>${reviewRows}</div><div><div class="budget-card"><h2>今日学习预算</h2><p>合理安排时间，完成比堆积更多任务更重要。</p><div class="budget-number"><strong>${used}</strong><span>/ ${budget} 分钟</span></div><div class="progress-track"><div class="progress-bar" style="width:${percent}%"></div></div><div class="progress-meta"><span>已使用 ${percent}%</span><span>${Math.max(0, budget - used)} 分钟剩余</span></div></div><div class="archive-panel" style="margin-top:18px"><div class="archive-panel-heading"><div><h2>待安排</h2><p>超出今日预算的内容</p></div></div><div class="backlog-list">${backlogRows}</div></div></div></div>`;
}

function formatDate(value) { if (!value) return "待安排"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value).slice(0, 10) : date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" }); }

function updateChatContext() {
  const memory = state.memory || { facts: [], knowledge: [], review: { items: [] } };
  const knowledge = memory.knowledge || [];
  const average = knowledge.length ? Math.round(knowledge.reduce((sum, item) => sum + masteryPercent(item), 0) / knowledge.length) : 0;
  const ring = $("#chat-progress-ring");
  ring.style.setProperty("--progress", `${average}%`);
  $("#chat-progress-value").textContent = knowledge.length ? `${average}%` : "—";
  $("#chat-progress-title").textContent = knowledge.length ? "学习地图已建立" : "还没有学习记录";
  $("#chat-progress-text").textContent = knowledge.length ? "继续完成几次练习，进度会变得更清晰。" : "完成几次学习后，这里会显示你的进度概览。";
  $("#chat-review-count").textContent = `${(memory.review?.items || []).length} 个`;
  $("#chat-fact-count").textContent = `${(memory.facts || []).length} 条`;
}

function fileStatus(status) { return { unindexed: ["待建立索引", "warning"], queued: ["排队中", "warning"], indexing: ["建立中", "warning"], ready: ["可检索", ""], stale: ["需要更新", "danger"], failed: ["失败，可重试", "danger"] }[status] || [status || "未知", ""]; }

async function loadFiles() {
  try { const data = await jsonApi("/v1/files"); state.files = data.items || []; renderFiles(); } catch (error) { toast(error.message, "error"); }
}

function renderFiles() {
  const files = state.files;
  const indexed = files.filter((item) => item.index?.status === "ready").length;
  $("#library-summary").innerHTML = [summaryCard("资料总数", files.length, "当前用户上传的文件"), summaryCard("已建立索引", indexed, "可以在对话中检索"), summaryCard("待处理", Math.max(0, files.length - indexed), "上传后点击建立索引")].join("");
  $("#files-body").innerHTML = files.length ? files.map((item) => { const [label, kind] = fileStatus(item.index?.status); return `<tr><td><span class="table-title">${escapeHtml(item.display_name)}</span><div class="table-subtitle">${escapeHtml(item.media_type || "资料")} · ${formatBytes(item.size_bytes)}</div></td><td><span class="status-badge ${kind}">${escapeHtml(label)}</span></td><td>${item.index?.chunk_count || 0} 个分片</td><td>${escapeHtml(formatDateTime(item.uploaded_at))}</td><td><div class="table-actions">${item.index?.can_index ? `<button class="small-button" data-file-action="index" data-file-id="${escapeHtml(item.id)}">建立索引</button>` : ""}<button class="small-button danger" data-file-action="delete" data-file-id="${escapeHtml(item.id)}">删除</button></div></td></tr>`; }).join("") : '<tr><td colspan="5"><div class="empty-panel">还没有资料。上传一份 PDF、Markdown 或 TXT，开始建立你的个人资料库。</div></td></tr>';
}

function summaryCard(label, value, note) { return `<div class="summary-card"><span class="summary-label">${label}</span><strong class="summary-value">${value}</strong><div class="summary-note">${note}</div></div>`; }
function formatBytes(bytes = 0) { if (bytes < 1024) return `${bytes} B`; if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function formatDateTime(value) { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value).slice(0, 16) : date.toLocaleDateString("zh-CN"); }

async function uploadFile(file) {
  if (!file) return;
  const form = new FormData(); form.append("file", file);
  try { await api("/v1/files", { method: "POST", body: form }); toast("资料已上传，请建立索引", "success"); await loadFiles(); } catch (error) { toast(error.message, "error"); }
  $("#library-upload").value = "";
}

async function indexFile(fileId) {
  try { await api(`/v1/files/${encodeURIComponent(fileId)}/index-jobs`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } }); toast("索引任务已启动", "success"); await loadFiles(); } catch (error) { toast(error.message, "error"); }
}

async function deleteFile(fileId) {
  if (!window.confirm("确定从资料库删除这份资料吗？")) return;
  try { await api(`/v1/files/${encodeURIComponent(fileId)}`, { method: "DELETE", headers: { "Idempotency-Key": crypto.randomUUID() } }); toast("资料已删除", "success"); await loadFiles(); } catch (error) { toast(error.message, "error"); }
}

async function memoryAction(action, candidateId) {
  try { await api(`/v1/memory/candidates/${encodeURIComponent(candidateId)}/${action}`, { method: "POST" }); toast(action === "confirm" ? "已加入个人档案" : "已忽略这条信息", "success"); await loadMemory(true); } catch (error) { toast(error.message, "error"); }
}

async function deleteFact(factId) {
  if (!window.confirm("删除这条档案后，助手将不再使用它。确定删除吗？")) return;
  try { await api(`/v1/memory/facts/${encodeURIComponent(factId)}`, { method: "DELETE" }); toast("档案已删除", "success"); await loadMemory(true); } catch (error) { toast(error.message, "error"); }
}

async function exportMemory() {
  try { const data = await jsonApi("/v1/memory/export?scope=all"); const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }); const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "learning-archive.json"; link.click(); URL.revokeObjectURL(link.href); toast("档案已导出", "success"); } catch (error) { toast(error.message, "error"); }
}

async function deleteAllMemory() {
  if (!window.confirm("确定清空长期学习档案吗？会话本身会保留，但已提取的学习记录会被删除。")) return;
  try { await api("/v1/memory?scope=memory", { method: "DELETE", headers: { "Idempotency-Key": crypto.randomUUID() } }); toast("长期学习档案已清空", "success"); await loadMemory(true); } catch (error) { toast(error.message, "error"); }
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$(".archive-nav-item").forEach((button) => button.addEventListener("click", () => switchArchiveSection(button.dataset.archiveSection)));
  $("#session-list").addEventListener("click", (event) => { const action = event.target.closest("[data-session-action]"); const item = event.target.closest("[data-thread-id]"); if (!item) return; if (action?.dataset.sessionAction === "rename") return openRename(item.dataset.threadId); state.currentThreadId = item.dataset.threadId; loadThread(state.currentThreadId); });
  $("#new-session").addEventListener("click", createSession);
  $("#quick-new-session").addEventListener("click", createSession);
  $("#rename-form").addEventListener("submit", (event) => { event.preventDefault(); renameSession(); });
  $("#composer").addEventListener("submit", sendMessage);
  $("#message-input").addEventListener("input", (event) => autoSizeTextarea(event.target));
  $("#message-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#composer").requestSubmit(); } });
  $("#image-input").addEventListener("change", (event) => { state.selectedImages = [...event.target.files].slice(0, 4); renderSelectedImages(); });
  $("#messages").addEventListener("click", (event) => { const button = event.target.closest(".suggestion-card"); if (!button) return; $("#message-input").value = button.dataset.prompt; autoSizeTextarea($("#message-input")); $("#message-input").focus(); });
  $$("[data-view-link]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.viewLink)));
  $("#refresh-page").addEventListener("click", () => window.location.reload());
  $("#mobile-menu").addEventListener("click", () => $("#sidebar").classList.toggle("is-open"));
  $("#open-settings").addEventListener("click", () => { $("#token-input").value = state.token; $("#settings-modal").showModal(); });
  $("#settings-form").addEventListener("submit", (event) => { event.preventDefault(); state.token = $("#token-input").value.trim() || "dev-token"; localStorage.setItem("learning-agent-token", state.token); $("#settings-modal").close(); toast("工作区设置已保存", "success"); loadThreads(); });
  $("#library-upload").addEventListener("change", (event) => uploadFile(event.target.files[0]));
  $("#refresh-files").addEventListener("click", loadFiles);
  $("#files-body").addEventListener("click", (event) => { const button = event.target.closest("[data-file-action]"); if (!button) return; if (button.dataset.fileAction === "index") indexFile(button.dataset.fileId); else deleteFile(button.dataset.fileId); });
  $("#profile-content").addEventListener("click", (event) => { const fact = event.target.closest("[data-fact-delete]"); if (fact) return deleteFact(fact.dataset.factDelete); const candidate = event.target.closest("[data-candidate-action]"); if (candidate) memoryAction(candidate.dataset.candidateAction, candidate.dataset.candidateId); });
  $("#export-memory").addEventListener("click", exportMemory);
  $("#delete-memory").addEventListener("click", deleteAllMemory);
  $("#overview-content").addEventListener("click", (event) => { const button = event.target.closest("[data-archive-link]"); if (button) switchArchiveSection(button.dataset.archiveLink); });
  $("#knowledge-content").addEventListener("click", (event) => { const button = event.target.closest("[data-archive-link]"); if (button) switchArchiveSection(button.dataset.archiveLink); });
  $("#vision-confirm").addEventListener("click", (event) => { event.preventDefault(); submitVision("confirm"); });
  $("#vision-cancel").addEventListener("click", (event) => { event.preventDefault(); submitVision("cancel"); });
}

async function boot() {
  bindEvents();
  await loadThreads();
  await loadMemory();
}

boot();
