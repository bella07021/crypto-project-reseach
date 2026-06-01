const state = {
  currentAssessment: null,
  activeRequest: null,
  requestPollTimer: null,
  dashboardRows: [],
};

const form = document.querySelector("#scoreForm");
const submitButton = document.querySelector("#submitButton");
const reportMount = document.querySelector("#reportMount");
const dashboardBody = document.querySelector("#dashboardRows");
const healthStatus = document.querySelector("#healthStatus");

function numberText(value) {
  if (value === undefined || value === null || value === "") return "--";
  return Number(value).toFixed(2);
}

function integerText(value) {
  if (!value) return "0";
  return Number(value).toLocaleString("en-US");
}

function percentText(value) {
  if (value === undefined || value === null || value === "") return "--";
  return Number(value).toFixed(2).replace(/\.00$/, "");
}

function tokenLabel(assessment) {
  return assessment.token_ticker || assessment.project_name || assessment.x_handle || "--";
}

function formPayload() {
  const data = new FormData(form);
  return {
    token_ticker: data.get("token_ticker"),
    project_name: data.get("project_name"),
    x_handle: data.get("x_handle"),
    rootdata_url: data.get("rootdata_url"),
  };
}

function xInputValue(project) {
  const value = String(project?.x_handle || "").trim();
  if (!value || /^https?:\/\//i.test(value)) return value;
  return `https://x.com/${value.replace(/^@/, "")}`;
}

function populateProjectForm(project) {
  if (!project) return;
  const values = {
    token_ticker: project.token_ticker || "",
    project_name: project.project_name || "",
    x_handle: xInputValue(project),
    rootdata_url: project.rootdata_url || "",
  };
  for (const [name, value] of Object.entries(values)) {
    const input = form.elements.namedItem(name);
    if (input) input.value = value;
  }
}

function normalizeHandle(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const candidate = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
    const parsed = new URL(candidate);
    const host = parsed.hostname.replace(/^www\./, "").toLowerCase();
    if (["x.com", "twitter.com", "mobile.twitter.com"].includes(host)) {
      const part = parsed.pathname.split("/").filter(Boolean)[0] || "";
      if (part && !["i", "intent", "search", "share", "home", "explore"].includes(part.toLowerCase())) {
        return part.replace(/^@/, "").toLowerCase();
      }
    }
  } catch {
    // Fall through to raw handle normalization.
  }
  return raw.replace(/^@/, "").toLowerCase();
}

function normalizeRootdataUrl(value) {
  try {
    const parsed = new URL(String(value || "").trim());
    const host = parsed.hostname.replace(/^(cn|www)\./, "");
    const path = parsed.pathname.toLowerCase().replace("/projects/detail/", "/projects/detail/");
    const params = new URLSearchParams(parsed.search);
    const key = params.has("k") ? `?k=${params.get("k") || ""}` : parsed.search;
    return `${host}${path}${key}`.toLowerCase();
  } catch {
    return String(value || "").trim().toLowerCase();
  }
}

function sameProject(left, right) {
  const leftUrl = normalizeRootdataUrl(left.rootdata_url);
  const rightUrl = normalizeRootdataUrl(right.rootdata_url);
  if (leftUrl && rightUrl && leftUrl === rightUrl) return true;
  return normalizeHandle(left.x_handle) && normalizeHandle(left.x_handle) === normalizeHandle(right.x_handle);
}

async function fetchRootdataBrowserHtml(rootdataUrl) {
  try {
    const response = await fetch(`/api/rootdata-browser?url=${encodeURIComponent(rootdataUrl)}`, {
      credentials: "include",
    });
    if (!response.ok) return "";
    const html = await response.text();
    return html.includes("RootData") || html.includes("__next_f") ? html : "";
  } catch {
    return "";
  }
}

function switchView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  document.querySelector(`#${name}View`).classList.add("active");
  if (name === "dashboard") loadDashboard();
}

function scoreReason(kind, assessment) {
  if (kind === "team") {
    const region = assessment.team_region_summary ? `，${assessment.team_region_summary}` : "";
    const known = Number(assessment.team_known_location_count || 0);
    const knownText = known ? `，已识别地区 ${known} 人` : "";
    return `${assessment.team_background || "unknown"}${region}，实名团队成员 ${assessment.team_member_count || 0} 人${knownText}`;
  }
  if (kind === "funding") {
    const amount = Number(assessment.funding_amount_usd || 0);
    const dateText = assessment.funding_date || "";
    const date = dateText ? new Date(`${dateText}T00:00:00`) : null;
    const validDate = date && !Number.isNaN(date.getTime());
    const days = validDate ? Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000)) : null;
    const months = days === null ? null : Math.round(days / 30);
    const amountPart = Math.min(amount / 500000000, 1) * 50;
    const recencyPart = days === null ? 0 : Math.max(0, 1 - days / 365) * 50;
    const base = `最新融资 $${integerText(amount)}，累计 $${integerText(assessment.funding_total_usd)}`;
    if (!amount || !validDate) return `${base}；缺少金额或时间，融资分偏低`;
    if (recencyPart === 0) return `${base}；距今约 ${months} 个月，超过一年，时间项为 0，拉低得分`;
    if (amountPart >= 40 && recencyPart >= 40) return `${base}；金额接近 $500M 且一年内融资，得分高`;
    if (recencyPart >= 35) return `${base}；融资较新，但金额距离 $500M 仍有差距`;
    return `${base}；金额项 ${percentText(amountPart)}/50，时间项 ${percentText(recencyPart)}/50`;
  }
  const percentile = Number(assessment.social_score || 0);
  const topRank = percentile > 0 ? Math.max(0.01, 100 - percentile) : 100;
  return `${integerText(assessment.x_followers)} followers，同赛道约前 ${percentText(topRank)}%`;
}

function uniqueEvidence(assessment) {
  const seen = new Set();
  const items = [];
  for (const note of assessment.evidence_notes || []) {
    const isFundingNote = note.startsWith("RootData funding round:");
    const normalized = note.replace(/^RootData parsed project: /, "项目: ");
    if (isFundingNote) continue;
    if (!seen.has(normalized)) {
      seen.add(normalized);
      items.push(normalized);
    }
  }
  for (const round of assessment.funding_rounds || []) {
    const line = `融资: ${round.round || "Unknown"} · $${integerText(round.amount_usd)} · ${round.date || ""}`;
    if (!seen.has(line)) {
      seen.add(line);
      items.push(line);
    }
  }
  return items;
}

function renderList(el, items, emptyText) {
  el.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = emptyText;
    el.appendChild(li);
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    el.appendChild(li);
  }
}

function renderTgeEvidence(el, assessment) {
  if (assessment.tge_status !== "已 TGE") {
    renderList(el, [], "暂无 TGE 证据。");
    return;
  }
  const linkedItems = assessment.tge_evidence_links || [];
  if (linkedItems.length) {
    el.innerHTML = "";
    for (const item of linkedItems) {
      const li = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = item.text || "TGE 相关表述";
      li.appendChild(text);
      if (item.url) {
        li.appendChild(document.createTextNode(" · "));
        const anchor = document.createElement("a");
        anchor.href = item.url;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.textContent = "链接";
        li.appendChild(anchor);
      }
      el.appendChild(li);
    }
    return;
  }
  renderList(el, assessment.tge_evidence || [], "暂无 TGE 证据。");
}

function sortedRoadmap(assessment) {
  return [...(assessment.roadmap_events || [])].sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")));
}

function eventTypeLabel(event, assessment) {
  if (event.type === "TGE" && assessment.tge_method) {
    return `TGE · ${assessment.tge_method}`;
  }
  return event.type || "Event";
}

function cmcListedExchanges(assessment) {
  const source = String(assessment.exchange_source || "").toLowerCase();
  if (!source.includes("coinmarketcap") && source !== "cmc") return [];
  return assessment.listed_exchanges || [];
}

function visibleTgeMethod(assessment) {
  if (assessment.tge_status !== "已 TGE") return "";
  const method = String(assessment.tge_method || "").trim();
  if (method.toLowerCase().includes("rootdata")) return "";
  return method;
}

function renderRoadmap(el, assessment) {
  const exchanges = cmcListedExchanges(assessment);
  el.innerHTML = "";
  if (!exchanges.length) {
    el.innerHTML = '<div class="empty-state">暂无 CMC 上线交易所数据。</div>';
    return;
  }
  for (const exchange of exchanges) {
    const item = document.createElement("div");
    item.className = "timeline-item";
    item.innerHTML = `
      <div class="timeline-name">${exchange}</div>
    `;
    el.appendChild(item);
  }
}

function renderReport(assessment, workbook) {
  state.currentAssessment = assessment;
  state.activeRequest = null;
  stopRequestPolling();
  populateProjectForm(assessment);
  const fragment = document.querySelector("#reportTemplate").content.cloneNode(true);
  const get = (name) => fragment.querySelector(`[data-field="${name}"]`);
  get("tokenTicker").textContent = tokenLabel(assessment);
  get("projectMeta").textContent = `${assessment.project_name || "--"} · @${assessment.x_handle || "--"} · ${assessment.bucket || "unknown"} · ${assessment.website || "no website"}`;
  get("totalScore").textContent = numberText(assessment.total_score);
  get("teamScore").textContent = numberText(assessment.team_score);
  get("fundingScore").textContent = numberText(assessment.funding_score);
  get("socialScore").textContent = numberText(assessment.social_score);
  get("teamReason").textContent = scoreReason("team", assessment);
  get("fundingReason").textContent = scoreReason("funding", assessment);
  get("socialReason").textContent = scoreReason("social", assessment);
  get("fetchStatus").textContent = assessment.fetch_status || "unknown";
  get("tgeStatus").textContent = assessment.tge_status || "--";
  get("tgeProbability").textContent = assessment.tge_status === "已 TGE" ? "已 TGE" : "未 TGE";
  get("tgeDate").textContent = assessment.tge_status === "已 TGE"
    ? `TGE 日期: ${assessment.tge_date || "待确认"}`
    : "未 TGE";
  const tgeMethod = visibleTgeMethod(assessment);
  get("tgeMethod").textContent = tgeMethod ? `方式: ${tgeMethod}` : "方式: --";
  renderList(get("evidenceList"), uniqueEvidence(assessment), "暂无证据。");
  renderTgeEvidence(get("tgeEvidenceList"), assessment);
  renderRoadmap(get("roadmapList"), assessment);
  reportMount.innerHTML = "";
  reportMount.appendChild(fragment);
}

function renderRequestStatus(request, created) {
  state.activeRequest = request;
  populateProjectForm(request);
  const statusText = request.status === "processing"
    ? "项目正在抓取与评分中，通常约 1 分钟内完成。"
    : request.status === "failed"
      ? `处理失败：${request.error || "--"}`
      : "已加入本地抓取队列，通常约 1 分钟内返回结果。";
  const helpText = request.status === "failed"
    ? "可联系负责人检查项目链接或重新提交。"
    : "若超过 5 分钟仍未返回结果，请联系负责人重启本地处理脚本。";
  reportMount.innerHTML = `
    <section class="report-section">
      <div class="section-title">
        <h3>${created ? "新项目已提交" : "项目已在队列中"}</h3>
        <span>${request.status || "pending"}</span>
      </div>
      <div class="detail-grid">
        <div><span>Token Symbol</span><strong>${request.token_ticker || "--"}</strong></div>
        <div><span>项目名</span><strong>${request.project_name || "--"}</strong></div>
        <div><span>X</span><strong>@${request.x_handle || "--"}</strong></div>
        <div><span>RootData</span><strong>${request.rootdata_url || "--"}</strong></div>
      </div>
      <ul class="clean-list">
        <li>${statusText}</li>
        <li>${helpText}</li>
      </ul>
    </section>
  `;
}

function stopRequestPolling() {
  if (state.requestPollTimer) {
    window.clearInterval(state.requestPollTimer);
    state.requestPollTimer = null;
  }
}

async function refreshActiveRequestStatus() {
  if (!state.activeRequest?.request_id) return;
  const response = await fetch(`/api/request-status?id=${encodeURIComponent(state.activeRequest.request_id)}`);
  const payload = await response.json().catch(() => ({}));
  if (!payload.ok) return;
  if (payload.assessment) {
    renderReport(payload.assessment, payload.assessment.workbook);
    await loadDashboard();
    return;
  }
  renderRequestStatus(payload.request || state.activeRequest, false);
  await loadDashboard();
}

function startRequestPolling(request) {
  state.activeRequest = request;
  stopRequestPolling();
  state.requestPollTimer = window.setInterval(() => {
    refreshActiveRequestStatus().catch(() => {});
  }, 10000);
  refreshActiveRequestStatus().catch(() => {});
}

async function loadDashboard() {
  const response = await fetch("/api/dashboard");
  const payload = await response.json();
  state.dashboardRows = payload.rows || [];
  renderDashboard();
  syncActiveRequestWithDashboard();
}

function syncActiveRequestWithDashboard() {
  if (!state.activeRequest) return;
  const completed = state.dashboardRows.find((row) => !row.request_status && sameProject(state.activeRequest, row));
  if (completed) {
    renderReport(completed.assessment || completed, completed.assessment?.workbook);
  }
}

function roadmapSummary(row) {
  const exchanges = cmcListedExchanges(row);
  const hasCmcData = exchanges.length > 0;
  const score = hasCmcData ? Number(row.exchange_score || 0) : 0;
  const progress = hasCmcData ? Math.max(0, Math.min(100, Number(row.exchange_progress || 0))) : 0;
  const chips = exchanges.length
    ? exchanges.map((exchange) => `<span>${exchange}</span>`).join("")
    : "<span>暂无 CMC 上线交易所</span>";
  return `
    <div class="exchange-progress">
      <div class="progress-head">
        <strong>${numberText(score)}</strong>
        <em>/ 100</em>
      </div>
      <div class="progress-track" aria-label="交易所进度 ${numberText(score)} / 100">
        <div style="width: ${progress}%"></div>
      </div>
      <div class="exchange-chips">${chips}</div>
    </div>
  `;
}

function tgeSummary(row) {
  if (row.request_status) {
    if (row.request_status === "failed") return `队列失败 · ${row.error || "--"}`;
    if (row.request_status === "processing") return "队列处理中";
    return "队列等待中";
  }
  if (row.tge_status === "已 TGE") return row.tge_date || "--";
  return "未 TGE";
}

function renderDashboard() {
  dashboardBody.innerHTML = "";
  if (!state.dashboardRows.length) {
    dashboardBody.innerHTML = '<tr><td colspan="8">暂无项目。</td></tr>';
    return;
  }
  for (const row of state.dashboardRows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><button type="button" class="ticker-link">${row.token_ticker || "--"}</button></td>
      <td>${row.project_name || "--"}</td>
      <td>${numberText(row.total_score)}</td>
      <td>${numberText(row.team_score)}</td>
      <td>${numberText(row.funding_score)}</td>
      <td>${numberText(row.social_score)}</td>
      <td>${tgeSummary(row)}</td>
      <td>${roadmapSummary(row)}</td>
    `;
    tr.querySelector(".ticker-link").addEventListener("click", () => {
      switchView("add");
      if (row.request_status) {
        renderRequestStatus(row.assessment || row, false);
      } else {
        renderReport(row.assessment, row.assessment?.workbook);
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    dashboardBody.appendChild(tr);
  }
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    const payload = await response.json();
    healthStatus.textContent = payload.ok ? "Ready" : "Offline";
  } catch {
    healthStatus.textContent = "Offline";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submitButton.disabled = true;
  submitButton.textContent = "抓取 RootData";
  try {
    const payloadToScore = formPayload();
    const requestResponse = await fetch("/api/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadToScore),
    });
    const requestPayload = await requestResponse.json().catch(() => ({}));
    if (requestResponse.ok && requestPayload.ok) {
      renderRequestStatus(requestPayload.request, requestPayload.created);
      startRequestPolling(requestPayload.request);
      await loadDashboard();
      return;
    }

    const rootdataHtml = await fetchRootdataBrowserHtml(payloadToScore.rootdata_url);
    if (rootdataHtml) payloadToScore.rootdata_html = rootdataHtml;
    submitButton.textContent = "评分中";
    const response = await fetch("/api/score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadToScore),
    });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "评分失败");
    renderReport(payload.assessment, payload.workbook);
    await loadDashboard();
  } catch (error) {
    reportMount.innerHTML = `<section class="report-section"><h3>评分失败</h3><p>${error.message}</p></section>`;
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "开始更新评分";
  }
});

document.querySelectorAll(".nav-item").forEach((item) => {
  item.addEventListener("click", () => switchView(item.dataset.view));
});
document.querySelector("#refreshDashboard").addEventListener("click", loadDashboard);

checkHealth();
loadDashboard();
