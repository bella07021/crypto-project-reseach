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

function scoreRing(value, label = "基本面总分") {
  const score = Number(value || 0);
  const progress = Math.max(0, Math.min(100, score));
  return `
    <div class="score-ring-wrap" aria-label="${label} ${numberText(score)} / 100">
      <span class="score-ring" style="--score: ${progress}%;"></span>
      <strong>${numberText(score)}</strong>
    </div>
  `;
}

function exchangeDisplayName(exchange) {
  const name = String(exchange || "");
  if (name === "Upbit 韩元现货") return "Upbit";
  if (name === "Bithumb 韩元现货") return "Bithumb";
  return name;
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
    const base = `最新融资 $${integerText(amount)}，累计 $${integerText(assessment.funding_total_usd)}`;
    if (assessment.funding_sector_rank) {
      const sector = assessment.funding_sector || "赛道";
      const rank = assessment.funding_sector_rank;
      const rankScore = percentText(assessment.funding_rank_score);
      const amountBonus = percentText(assessment.funding_amount_bonus);
      const ageMultiplier = percentText(assessment.funding_age_multiplier);
      return `${base}；${sector}融资排名 #${rank}，排名分 ${rankScore}，金额加成 ${amountBonus}，时间系数 ${ageMultiplier}`;
    }
    const dateText = assessment.funding_date || "";
    const date = dateText ? new Date(`${dateText}T00:00:00`) : null;
    const validDate = date && !Number.isNaN(date.getTime());
    const days = validDate ? Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000)) : null;
    const months = days === null ? null : Math.round(days / 30);
    const amountPart = Math.min(amount / 500000000, 1) * 50;
    const recencyPart = days === null ? 0 : Math.max(0, 1 - days / 365) * 50;
    if (!amount || !validDate) return `${base}；缺少金额或时间，融资分偏低`;
    if (recencyPart === 0) return `${base}；距今约 ${months} 个月，超过一年，时间项为 0，拉低得分`;
    if (amountPart >= 40 && recencyPart >= 40) return `${base}；金额接近 $500M 且一年内融资，得分高`;
    if (recencyPart >= 35) return `${base}；融资较新，但金额距离 $500M 仍有差距`;
    return `${base}；金额项 ${percentText(amountPart)}/50，时间项 ${percentText(recencyPart)}/50`;
  }
  if (kind === "investor") {
    const investors = assessment.investor_highlights || [];
    return investors.length ? investors.join("、") : "暂无明确投资方信息";
  }
  if (kind === "chain") {
    const chains = assessment.chains || [];
    return chains.length ? chains.join("、") : "暂无明确链生态信息";
  }
  if (kind === "exchange") {
    const signals = assessment.pre_tge_listing_signals || [];
    if (!signals.length) return "暂无 exchange listings 预上线信号";
    return signals
      .map((item) => exchangeDisplayName(item.exchange) || "--")
      .join("、");
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

function exchangeListingDetails(assessment) {
  const details = assessment.exchange_listing_details || [];
  if (details.length) return details;
  return cmcListedExchanges(assessment).map((exchange) => ({
    exchange,
    listed_at: "",
    days_after_tge: null,
  }));
}

function shouldShowExchangeTime(exchange) {
  const name = String(exchange || "");
  return [
    "Coinbase",
    "BN 现货",
    "BN 合约",
    "Upbit 韩元现货",
    "Bithumb 韩元现货",
  ].includes(name);
}

function exchangeTimeText(item) {
  if (!shouldShowExchangeTime(item.exchange) || !item.listed_at) return "";
  const dayText = item.days_after_tge === undefined || item.days_after_tge === null
    ? ""
    : ` · TGE 后 ${item.days_after_tge} 天`;
  return `${item.listed_at}${dayText}`;
}

function deleteProjectPayload(assessment) {
  return {
    token_ticker: assessment.token_ticker || "",
    project_name: assessment.project_name || "",
    x_handle: assessment.x_handle || "",
    rootdata_url: assessment.rootdata_url || "",
  };
}

async function deleteCurrentProject(assessment) {
  const label = tokenLabel(assessment);
  if (!window.confirm(`确认删除 ${label} 的项目数据？`)) return;
  const response = await fetch("/api/project/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(deleteProjectPayload(assessment)),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || "删除失败");
  }
  state.currentAssessment = null;
  reportMount.innerHTML = `
    <section class="report-section">
      <h3>项目已删除</h3>
      <p>${label} 的评分历史已移除。</p>
    </section>
  `;
  await loadDashboard();
  switchView("dashboard");
}

function visibleTgeMethod(assessment) {
  if (assessment.tge_status !== "已 TGE") return "";
  const method = String(assessment.tge_method || "").trim();
  if (method.toLowerCase().includes("rootdata")) return "";
  return method;
}

function renderRoadmap(el, assessment) {
  const exchanges = exchangeListingDetails(assessment);
  el.innerHTML = "";
  if (!exchanges.length) {
    el.innerHTML = '<div class="empty-state">暂无 CMC 上线交易所数据。</div>';
    return;
  }
  for (const exchange of exchanges) {
    const meta = exchangeTimeText(exchange);
    const item = document.createElement("div");
    item.className = meta ? "timeline-item exchange-timeline-item" : "timeline-item exchange-timeline-item name-only";
    item.innerHTML = `
      <div class="timeline-name">${exchangeDisplayName(exchange.exchange)}</div>
      ${meta ? `<div class="timeline-meta">${meta}</div>` : ""}
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
  get("totalScore").parentElement.style.setProperty("--score", `${Math.max(0, Math.min(100, Number(assessment.total_score || 0)))}%`);
  get("teamScore").textContent = numberText(assessment.team_score);
  get("fundingScore").textContent = numberText(assessment.funding_score);
  get("investorScore").textContent = numberText(assessment.investor_score);
  get("socialScore").textContent = numberText(assessment.social_score);
  get("chainScore").textContent = numberText(assessment.chain_score);
  get("preTgeExchangeScore").textContent = numberText(assessment.pre_tge_exchange_score || assessment.exchange_score);
  get("teamReason").textContent = scoreReason("team", assessment);
  get("fundingReason").textContent = scoreReason("funding", assessment);
  get("investorReason").textContent = scoreReason("investor", assessment);
  get("socialReason").textContent = scoreReason("social", assessment);
  get("chainReason").textContent = scoreReason("chain", assessment);
  get("exchangeReason").textContent = scoreReason("exchange", assessment);
  const deleteButton = fragment.querySelector('[data-action="deleteProject"]');
  deleteButton.addEventListener("click", () => {
    deleteCurrentProject(assessment).catch((error) => {
      window.alert(error.message);
    });
  });
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
    ? "数据正在更新中，完成前不会展示旧评分。"
    : request.status === "failed"
      ? `处理失败：${request.error || "--"}`
      : "数据正在更新中，已加入抓取队列。";
  const helpText = request.status === "failed"
    ? "可联系负责人检查项目链接或重新提交。"
    : "抓取完成后会自动替换为最新评分。";
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
  if (["pending", "processing"].includes(String(state.activeRequest.status || ""))) return;
  const completed = state.dashboardRows.find((row) => !row.request_status && sameProject(state.activeRequest, row));
  if (completed) {
    renderReport(completed.assessment || completed, completed.assessment?.workbook);
  }
}

function scoreBreakdown(row) {
  const items = [
    ["团队", row.team_score],
    ["融资", row.funding_score],
    ["投资方", row.investor_score],
    ["社媒", row.social_score],
    ["链生态", row.chain_score],
    ["TGE前交易所", row.pre_tge_exchange_score],
  ];
  return `
    <div class="score-breakdown" aria-label="基本面拆分">
      ${items.map(([label, value]) => `
        <div>
          <span>${label}</span>
          <strong>${numberText(value)}</strong>
        </div>
      `).join("")}
    </div>
  `;
}

function listedExchangeSummary(row) {
  const exchanges = exchangeListingDetails(row);
  const chips = exchanges.length
    ? exchanges.map((item) => `<span>${exchangeDisplayName(item.exchange) || "--"}</span>`).join("")
    : "<span>暂无上线数据</span>";
  return `<div class="exchange-chips exchange-chips-compact">${chips}</div>`;
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
    dashboardBody.innerHTML = '<tr><td colspan="6">暂无项目。</td></tr>';
    return;
  }
  for (const row of state.dashboardRows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><button type="button" class="ticker-link">${row.token_ticker || "--"}</button></td>
      <td>${row.project_name || "--"}</td>
      <td>${scoreRing(row.total_score)}</td>
      <td>${scoreBreakdown(row)}</td>
      <td>${tgeSummary(row)}</td>
      <td>${listedExchangeSummary(row)}</td>
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
