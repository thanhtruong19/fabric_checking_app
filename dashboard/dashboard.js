const pipeline = document.body.dataset.pipeline;
const labels = {
  done: "Hoàn tất",
  processing: "Đang chạy",
  pending: "Chờ xử lý",
  retrying: "Chờ thử lại",
  blocked: "Cần xử lý",
  error: "Lỗi",
};

let payload = null;
let activeFilter = "all";

const elements = {
  body: document.querySelector("#status-rows"),
  empty: document.querySelector("#empty-state"),
  search: document.querySelector("#search"),
  refreshed: document.querySelector("#refreshed-at"),
  runnerText: document.querySelector("#runner-text"),
  runnerDot: document.querySelector("#runner-dot"),
  progress: document.querySelector("#progress-fill"),
};

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

function formatDate(value) {
  if (!value) return "—";
  const parsed = new Date(value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(parsed);
}

function statusBadge(state, errorType) {
  const variants = {
    done: "text-bg-success",
    processing: "text-bg-primary",
    pending: "text-bg-secondary",
    retrying: "text-bg-warning text-dark",
    blocked: "text-bg-warning text-dark",
    error: "text-bg-danger",
  };
  const detail = errorType ? `<span class="error-detail small text-danger d-block mt-1" title="${escapeHtml(errorType)}">${escapeHtml(errorType)}</span>` : "";
  return `<span class="badge rounded-pill ${variants[state] || "text-bg-secondary"}">${labels[state] || state}</span>${detail}`;
}

function thumbnail(url, alt) {
  if (!url) return `<span class="thumb-placeholder">—</span>`;
  return `<img class="thumb" loading="lazy" src="${escapeHtml(url)}" alt="${escapeHtml(alt)}">`;
}

function sourceCell(item) {
  return `<div class="media-cell">${thumbnail(item.sourceUrl, item.sku)}<span class="file-name" title="${escapeHtml(item.sourceName)}">${escapeHtml(item.sourceName || "Chưa có")}</span></div>`;
}

function outputCell(item) {
  const previews = item.previewUrls?.length ? item.previewUrls.slice(0, 4) : (item.outputUrl ? [item.outputUrl] : []);
  const images = previews.length
    ? `<div class="preview-stack">${previews.map((url, index) => thumbnail(url, `${item.sku} output ${index + 1}`)).join("")}</div>`
    : `<span class="thumb-placeholder">—</span>`;
  return `<div class="media-cell">${images}<span class="file-name">${escapeHtml(item.outputName || "Chưa có")}</span></div>`;
}

function providersCell(providers) {
  if (!providers?.length) return `<span class="text-secondary">—</span>`;
  return `<div class="provider-list">${providers.map((provider) =>
    `<span class="badge bg-light border text-secondary provider-chip ${escapeHtml(provider.state)}" title="${escapeHtml(labels[provider.state] || provider.state)}">${escapeHtml(provider.label)}</span>`
  ).join("")}</div>`;
}

function itemMatches(item) {
  const query = elements.search.value.trim().toLowerCase();
  const searchable = `${item.sku} ${item.sourceName || ""} ${item.errorType || ""}`.toLowerCase();
  if (query && !searchable.includes(query)) return false;
  if (activeFilter === "all") return true;
  if (activeFilter === "attention") return ["blocked", "retrying", "error"].includes(item.state);
  return item.state === activeFilter;
}

function renderRows() {
  if (!payload) return;
  const items = payload.items.filter(itemMatches);
  elements.empty.hidden = items.length > 0;
  elements.body.innerHTML = items.map((item) => `
    <tr>
      <td><span class="sku">${escapeHtml(item.sku)}</span></td>
      <td>${statusBadge(item.state, item.errorType)}</td>
      <td>${sourceCell(item)}</td>
      <td>${outputCell(item)}</td>
      <td>${providersCell(item.providers)}</td>
      <td>${item.retries || 0}</td>
      <td><span class="text-secondary text-nowrap">${formatDate(item.updatedAt || item.completedAt || item.startedAt)}</span></td>
    </tr>
  `).join("");
}

function setMetric(id, value) {
  const element = document.querySelector(`#metric-${id}`);
  if (element) element.textContent = value;
}

function renderSummary() {
  const counts = payload.counts;
  const percent = counts.total ? Math.round((counts.ready / counts.total) * 100) : 0;
  setMetric("total", counts.total);
  setMetric("ready", counts.ready);
  setMetric("processing", counts.processing);
  setMetric("attention", counts.attention);
  setMetric("remaining", counts.remaining);
  document.querySelector("#metric-percent").textContent = `${percent}% hoàn thành`;
  elements.progress.style.width = `${percent}%`;
  elements.runnerDot.classList.toggle("active", payload.runnerActive);
  const activeItems = payload.items.filter((item) => item.state === "processing").map((item) => item.sku);
  if (payload.runnerActive && activeItems.length) {
    elements.runnerText.textContent = `Runner đang hoạt động · SKU ${activeItems.slice(0, 3).join(", ")}`;
  } else if (payload.runnerActive) {
    elements.runnerText.textContent = "Runner đang hoạt động · đang chuẩn bị tác vụ";
  } else if (activeItems.length) {
    elements.runnerText.textContent = `Không thấy runner · ${activeItems.length} trạng thái processing có thể đã cũ`;
  } else {
    elements.runnerText.textContent = "Runner hiện không hoạt động";
  }
  elements.refreshed.textContent = `Dữ liệu lúc ${formatDate(payload.generatedAt)} · tự làm mới 5 giây`;
}

async function refresh() {
  try {
    const response = await fetch(`/api/status/${pipeline}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    payload = await response.json();
    renderSummary();
    renderRows();
  } catch (error) {
    elements.runnerText.textContent = `Không đọc được dữ liệu: ${error.message}`;
    elements.runnerDot.classList.remove("active");
  }
}

document.querySelectorAll(".filter-button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter-button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    activeFilter = button.dataset.filter;
    renderRows();
  });
});

elements.search.addEventListener("input", renderRows);
refresh();
setInterval(refresh, 5000);
