if (window.location.protocol === "file:") {
  window.location.replace("https://ai.lhksoul.com");
}

const TARGET_PREFERENCE_KEY = "blog-helper-remote-targets";
const ALLOWED_TARGETS = new Set(["wordpress", "tistory", "blogspot"]);

const state = {
  devices: [],
  jobs: [],
  queue: [],
  queueUpdatedAt: 0,
  queueRenderSignature: "",
  selectedDeviceId: "",
  daumTrends: [],
  jobSubmitting: false,
  pollTimer: null,
};

const loginView = document.querySelector("#loginView");
const dashboardView = document.querySelector("#dashboardView");
const loginForm = document.querySelector("#loginForm");
const jobForm = document.querySelector("#jobForm");
const submitJob = document.querySelector("#submitJob");
const deviceGrid = document.querySelector("#deviceGrid");
const jobList = document.querySelector("#jobList");
const queueList = document.querySelector("#queueList");
const previewDialog = document.querySelector("#previewDialog");
const previewBody = document.querySelector("#previewBody");
const daumTrendButton = document.querySelector("#daumTrendButton");
const daumTrendAccordion = document.querySelector("#daumTrendAccordion");
const daumTrendList = document.querySelector("#daumTrendList");
const clearJobsButton = document.querySelector("#clearJobsButton");
const targetInputs = [...document.querySelectorAll("input[name='target']")];

restoreTargetPreferences();
targetInputs.forEach((input) => {
  input.addEventListener("change", saveTargetPreferences);
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setText("#loginError", "");
  const password = document.querySelector("#password").value;
  const response = await api("/api/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    setText("#loginError", response.data.error || "로그인하지 못했습니다.");
    return;
  }
  showDashboard();
});

jobForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitKeywordJob(document.querySelector("#keyword").value);
});

async function submitKeywordJob(rawKeyword) {
  setText("#jobError", "");
  const keyword = String(rawKeyword || "").trim();
  const targets = selectedTargets();
  if (!state.selectedDeviceId) {
    setText("#jobError", "작업할 PC를 먼저 선택해 주세요.");
    return false;
  }
  if (!keyword) {
    setText("#jobError", "작성할 키워드를 입력하거나 실시간 검색어를 선택해 주세요.");
    return false;
  }
  if (!targets.length) {
    setText("#jobError", "발행 대상을 하나 이상 선택해 주세요.");
    return false;
  }
  if (state.jobSubmitting) {
    setText("#jobError", "현재 키워드를 PC로 전달하고 있습니다. 잠시만 기다려 주세요.");
    return false;
  }
  saveTargetPreferences();
  state.jobSubmitting = true;
  submitJob.disabled = true;
  submitJob.textContent = "PC로 전달 중...";
  const response = await api("/api/jobs", {
    method: "POST",
    body: JSON.stringify({
      deviceId: state.selectedDeviceId,
      keyword,
      targets,
      action: "queue",
    }),
  });
  if (!response.ok) {
    setText("#jobError", response.data.error || "작업을 전달하지 못했습니다.");
  } else {
    document.querySelector("#keyword").value = "";
    await refreshDashboard();
  }
  state.jobSubmitting = false;
  updateSubmitButton();
  return response.ok;
}

document.querySelector("#refreshButton").addEventListener("click", refreshDashboard);
document.querySelector("#refreshQueueButton").addEventListener("click", async () => {
  setQueueMessage("PC에서 최신 대기열을 불러오는 중입니다.");
  await refreshQueue(true);
  setQueueMessage("최신 대기열을 요청했습니다. 화면은 자동으로 갱신됩니다.");
});
document.querySelector("#logoutButton").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  stopPolling();
  dashboardView.hidden = true;
  loginView.hidden = false;
});
daumTrendButton.addEventListener("click", loadDaumRealtimeTrends);
daumTrendList.addEventListener("change", handleDaumTrendSelection);
clearJobsButton.addEventListener("click", clearRecentJobs);
document.querySelector("#closePreviewButton").addEventListener("click", () => previewDialog.close());
previewDialog.addEventListener("click", (event) => {
  if (event.target === previewDialog) {
    previewDialog.close();
  }
});
queueList.addEventListener("click", handleQueueAction);

function showDashboard() {
  loginView.hidden = true;
  dashboardView.hidden = false;
  refreshDashboard();
  stopPolling();
  state.pollTimer = window.setInterval(refreshDashboard, 2500);
}

async function refreshDashboard() {
  const [devicesResponse, jobsResponse] = await Promise.all([api("/api/devices"), api("/api/jobs")]);
  if (devicesResponse.status === 401 || jobsResponse.status === 401) {
    stopPolling();
    dashboardView.hidden = true;
    loginView.hidden = false;
    return;
  }
  state.devices = devicesResponse.data.devices || [];
  state.jobs = jobsResponse.data.jobs || [];
  if (!state.devices.some((device) => device.deviceId === state.selectedDeviceId && device.online)) {
    state.selectedDeviceId = "";
    state.queue = [];
    state.queueUpdatedAt = 0;
    state.queueRenderSignature = "";
  }
  renderDevices();
  renderJobs();
  updateSubmitButton();
  await refreshQueue(false);
}

function renderDevices() {
  const onlineCount = state.devices.filter((device) => device.online).length;
  setText("#onlineCount", `온라인 ${onlineCount}대`);
  if (!state.devices.length) {
    deviceGrid.innerHTML = `<div class="empty">연결된 PC가 없습니다.<br />PC의 Blog Helper에서 원격 연결을 켜 주세요.</div>`;
    return;
  }
  deviceGrid.innerHTML = state.devices
    .map((device) => {
      const disabled = !device.online;
      const selected = state.selectedDeviceId === device.deviceId;
      const statusLabel = device.status === "busy" ? "작업 중" : device.status === "online" ? "사용 가능" : "오프라인";
      const platformLabel = String(device.platform || "").startsWith("Windows")
        ? `Windows · ${device.platform}`
        : String(device.platform || "").startsWith("Darwin")
          ? `Mac · ${device.platform}`
          : device.platform || "운영체제 확인 중";
      return `
        <article class="device-card ${selected ? "selected" : ""}">
          <button class="device-select" type="button"
            data-device-id="${escapeHtml(device.deviceId)}" ${disabled ? "disabled" : ""}>
            <i class="status-dot ${escapeHtml(device.status)}"></i>
            <strong>${escapeHtml(device.name || "이름 없는 PC")}</strong>
            <span>${escapeHtml(platformLabel)}</span>
            <span>Blog Helper v${escapeHtml(device.version || "-")} · ${statusLabel}</span>
          </button>
          <button class="device-delete" type="button" data-delete-device-id="${escapeHtml(device.deviceId)}"
            aria-label="${escapeHtml(device.name || "PC")} 삭제">PC 삭제</button>
        </article>`;
    })
    .join("");
  deviceGrid.querySelectorAll(".device-select:not(:disabled)").forEach((button) => {
    button.addEventListener("click", async () => {
      state.selectedDeviceId = button.dataset.deviceId || "";
      state.queue = [];
      state.queueUpdatedAt = 0;
      state.queueRenderSignature = "";
      setQueueMessage("선택한 PC의 대기열을 불러오는 중입니다.");
      renderDevices();
      updateSubmitButton();
      await refreshQueue(true);
      setQueueMessage("선택한 PC의 대기열을 불러왔습니다.");
    });
  });
  deviceGrid.querySelectorAll(".device-delete").forEach((button) => {
    button.addEventListener("click", () => deleteDevice(button.dataset.deleteDeviceId || ""));
  });
}

async function deleteDevice(deviceId) {
  const device = state.devices.find((item) => item.deviceId === deviceId);
  if (!device) {
    return;
  }
  const message = (
    `'${device.name || "선택한 PC"}'를 원격 목록에서 삭제할까요?\n\n`
    + "이 PC에서 Blog Helper를 다시 실행하면 자동으로 목록에 다시 연결됩니다."
  );
  if (!window.confirm(message)) {
    return;
  }
  const response = await api(`/api/devices/${encodeURIComponent(deviceId)}`, { method: "DELETE" });
  if (!response.ok) {
    window.alert(response.data.error || "PC를 삭제하지 못했습니다.");
    return;
  }
  if (state.selectedDeviceId === deviceId) {
    state.selectedDeviceId = "";
    state.queue = [];
    state.queueUpdatedAt = 0;
    state.queueRenderSignature = "";
  }
  state.devices = state.devices.filter((item) => item.deviceId !== deviceId);
  renderDevices();
  updateSubmitButton();
  await refreshDashboard();
  window.alert(response.data.message || "PC를 목록에서 삭제했습니다.");
}

function selectedTargets() {
  return targetInputs.filter((input) => input.checked).map((input) => input.value);
}

function restoreTargetPreferences() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(TARGET_PREFERENCE_KEY) || "null");
    if (!Array.isArray(saved)) {
      return;
    }
    const selected = new Set(saved.filter((target) => ALLOWED_TARGETS.has(target)));
    targetInputs.forEach((input) => {
      input.checked = selected.has(input.value);
    });
  } catch {
    // Keep the HTML defaults when private browsing blocks local storage.
  }
}

function saveTargetPreferences() {
  try {
    window.localStorage.setItem(TARGET_PREFERENCE_KEY, JSON.stringify(selectedTargets()));
  } catch {
    // The current selection still works when storage is unavailable.
  }
}

async function loadDaumRealtimeTrends() {
  setText("#jobError", "");
  daumTrendButton.disabled = true;
  daumTrendButton.textContent = "불러오는 중...";
  setText("#daumTrendStatus", "다음 실시간 검색어 1~10위를 확인하고 있습니다.");
  const response = await api("/api/trends/daum");
  daumTrendButton.disabled = false;
  daumTrendButton.textContent = "다음 실시간";
  if (!response.ok) {
    state.daumTrends = [];
    daumTrendAccordion.hidden = true;
    daumTrendList.innerHTML = "";
    setText("#daumTrendStatus", response.data.error || "실시간 검색어를 불러오지 못했습니다.");
    return;
  }
  state.daumTrends = Array.isArray(response.data.trends) ? response.data.trends.slice(0, 10) : [];
  renderDaumTrends();
  setText(
    "#daumTrendStatus",
    state.daumTrends.length
      ? "키워드를 선택하면 선택한 PC에서 바로 글쓰기를 시작합니다."
      : "표시할 실시간 검색어가 없습니다.",
  );
}

function renderDaumTrends() {
  daumTrendAccordion.hidden = !state.daumTrends.length;
  daumTrendAccordion.open = true;
  daumTrendList.innerHTML = state.daumTrends
    .map((trend, index) => {
      const rank = Number(trend.rank || index + 1);
      return `
        <label class="trend-item">
          <input type="radio" name="daum-trend" value="${escapeHtml(trend.keyword)}" />
          <strong>${rank}위</strong>
          <span>${escapeHtml(trend.keyword)}</span>
        </label>`;
    })
    .join("");
}

async function handleDaumTrendSelection(event) {
  const radio = event.target.closest("input[name='daum-trend']");
  if (!radio || !radio.checked) {
    return;
  }
  const keyword = String(radio.value || "").trim();
  document.querySelector("#keyword").value = keyword;
  if (!state.selectedDeviceId) {
    setText("#jobError", "실시간 키워드를 보내려면 작업할 PC를 먼저 선택해 주세요.");
    radio.checked = false;
    document.querySelector("#keyword").focus();
    return;
  }
  daumTrendList.querySelectorAll("input").forEach((input) => {
    input.disabled = true;
  });
  setText("#daumTrendStatus", `'${keyword}' 작업을 선택한 PC로 보내고 있습니다.`);
  const sent = await submitKeywordJob(keyword);
  daumTrendList.querySelectorAll("input").forEach((input) => {
    input.disabled = false;
  });
  if (sent) {
    setText("#daumTrendStatus", `'${keyword}' 글쓰기를 시작했습니다. 최근 작업에서 진행 상황을 확인해 주세요.`);
  } else {
    radio.checked = false;
    setText("#daumTrendStatus", "작업을 시작하지 못했습니다. PC 상태와 발행 대상을 확인해 주세요.");
  }
}

async function clearRecentJobs() {
  if (!state.jobs.length) {
    setText("#jobsMessage", "초기화할 최근 작업이 없습니다.");
    return;
  }
  if (!window.confirm("완료되었거나 실패한 최근 작업 내역을 초기화할까요?\n진행 중인 작업은 그대로 유지됩니다.")) {
    return;
  }
  clearJobsButton.disabled = true;
  clearJobsButton.textContent = "초기화 중...";
  const response = await api("/api/jobs", { method: "DELETE" });
  clearJobsButton.disabled = false;
  clearJobsButton.textContent = "내역 초기화";
  setText(
    "#jobsMessage",
    response.data.message || response.data.error || "최근 작업 내역 초기화를 완료했습니다.",
  );
  if (response.ok) {
    await refreshDashboard();
  }
}

async function refreshQueue(requestFreshSnapshot = false) {
  const selectedDevice = state.devices.find((item) => item.deviceId === state.selectedDeviceId);
  if (!selectedDevice) {
    state.queue = [];
    state.queueUpdatedAt = 0;
    renderQueue();
    return;
  }
  const refreshQuery = requestFreshSnapshot ? "&refresh=1" : "";
  const response = await api(
    `/api/queue?deviceId=${encodeURIComponent(state.selectedDeviceId)}${refreshQuery}`,
  );
  if (response.status === 401) {
    stopPolling();
    dashboardView.hidden = true;
    loginView.hidden = false;
    return;
  }
  if (!response.ok) {
    setQueueMessage(response.data.error || "대기열을 불러오지 못했습니다.", true);
    return;
  }
  state.queue = response.data.items || [];
  state.queueUpdatedAt = Number(response.data.updatedAt || 0);
  renderQueue();
  if (requestFreshSnapshot && response.data.online) {
    window.setTimeout(() => refreshQueue(false), 900);
  }
}

function renderQueue() {
  const device = state.devices.find((item) => item.deviceId === state.selectedDeviceId);
  if (!device) {
    setText("#queueDeviceName", "PC를 선택하면 해당 PC의 대기열을 표시합니다.");
    setText("#queueUpdatedAt", "");
    queueList.innerHTML = `<div class="empty">작업할 PC를 먼저 선택해 주세요.</div>`;
    return;
  }
  setText("#queueDeviceName", `${device.name || "선택한 PC"} 대기열 ${state.queue.length}개`);
  setText(
    "#queueUpdatedAt",
    state.queueUpdatedAt ? `마지막 동기화 ${formatDateTime(state.queueUpdatedAt / 1000)}` : "동기화 대기 중",
  );
  const signature = JSON.stringify(
    state.queue.map((item) => [
      item.id,
      item.title,
      item.status,
      item.scheduledAt,
      item.excerpt,
      item.hasThumbnail,
      item.cardnewsCount,
      item.targetPlatforms,
    ]),
  );
  if (signature === state.queueRenderSignature && queueList.children.length) {
    return;
  }
  state.queueRenderSignature = signature;
  if (!state.queue.length) {
    queueList.innerHTML = `<div class="empty">이 PC의 자동화 대기열이 비어 있습니다.</div>`;
    return;
  }
  const platformNames = {
    wordpress: "워드프레스",
    tistory: "티스토리",
    blogspot: "블로그스팟",
  };
  const deviceBusy = device.status === "busy";
  queueList.innerHTML = [...state.queue]
    .sort((left, right) => Number(left.scheduledAt || 0) - Number(right.scheduledAt || 0))
    .map((item) => {
      const platforms = (item.targetPlatforms || [])
        .map((platform) => platformNames[platform] || platform)
        .join(" · ");
      const mediaText = [
        item.hasThumbnail ? "썸네일 있음" : "썸네일 없음",
        `카드뉴스 ${Number(item.cardnewsCount || 0)}장`,
      ].join(" · ");
      const publishing = item.status === "업로드 중";
      return `
        <article class="queue-item" data-item-id="${escapeHtml(item.id)}">
          <div class="queue-item-head">
            <div>
              <span class="queue-state">${escapeHtml(item.status || "대기 중")}</span>
              <h3>${escapeHtml(item.title || "제목 없는 글")}</h3>
              <p>${escapeHtml(item.excerpt || "미리보기에서 본문을 확인할 수 있습니다.")}</p>
            </div>
            <button class="queue-preview-button" data-action="preview" type="button">미리보기</button>
          </div>
          <div class="queue-meta">
            <span>${escapeHtml(platforms || "발행 대상 미정")}</span>
            <span>${escapeHtml(mediaText)}</span>
          </div>
          <div class="schedule-editor">
            <label for="schedule-${escapeHtml(item.id)}">등록 예정시간</label>
            <input id="schedule-${escapeHtml(item.id)}" data-role="schedule" type="datetime-local"
              value="${escapeHtml(toDateTimeLocal(item.scheduledAt))}" ${publishing ? "disabled" : ""} />
            <button data-action="schedule" type="button" ${publishing ? "disabled" : ""}>시간 변경</button>
          </div>
          <button class="publish-now" data-action="publish" type="button"
            ${publishing || deviceBusy || !device.online ? "disabled" : ""}>
            ${publishing ? "발행 중..." : deviceBusy ? "PC 작업 종료 후 가능" : "대기 없이 즉시발행"}
          </button>
        </article>`;
    })
    .join("");
}

async function handleQueueAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const itemElement = button.closest(".queue-item");
  const itemId = itemElement && itemElement.dataset.itemId;
  const item = state.queue.find((entry) => entry.id === itemId);
  if (!item || !state.selectedDeviceId) {
    setQueueMessage("선택한 대기열 글을 찾지 못했습니다.", true);
    return;
  }
  const action = button.dataset.action;
  if (action === "preview") {
    await openQueuePreview(item);
    return;
  }
  if (action === "schedule") {
    const input = itemElement.querySelector("input[data-role='schedule']");
    if (!input.value) {
      setQueueMessage("변경할 날짜와 시간을 입력해 주세요.", true);
      input.focus();
      return;
    }
    const timestamp = new Date(input.value).getTime() / 1000;
    if (!Number.isFinite(timestamp) || timestamp <= 0) {
      setQueueMessage("올바른 등록 예정시간을 입력해 주세요.", true);
      return;
    }
    button.disabled = true;
    const response = await api(queueActionPath(itemId, "schedule"), {
      method: "POST",
      body: JSON.stringify({ scheduledAt: timestamp }),
    });
    const result = response.ok && response.data.commandId
      ? await waitForCommand(response.data.commandId)
      : response;
    setQueueMessage(
      result.data.message || result.data.error || "등록 예정시간 변경 요청을 보냈습니다.",
      !result.ok,
    );
    await refreshQueue(true);
    return;
  }
  if (action === "publish") {
    if (!window.confirm(`'${item.title}' 글을 예정시간까지 기다리지 않고 지금 발행할까요?`)) {
      return;
    }
    button.disabled = true;
    button.textContent = "PC에 발행 요청 중...";
    const response = await api(queueActionPath(itemId, "publish"), {
      method: "POST",
      body: "{}",
    });
    const result = response.ok && response.data.commandId
      ? await waitForCommand(response.data.commandId)
      : response;
    setQueueMessage(
      result.data.message || result.data.error || "즉시발행 요청을 보냈습니다.",
      !result.ok,
    );
    await refreshQueue(true);
  }
}

async function openQueuePreview(item) {
  setText("#previewTitle", item.title || "글 미리보기");
  previewBody.innerHTML = `<p class="preview-loading">PC에서 미리보기를 불러오는 중입니다...</p>`;
  previewDialog.showModal();
  const response = await api(queueActionPath(item.id, "preview"));
  if (!response.ok) {
    previewBody.innerHTML = `<p class="error">${escapeHtml(response.data.error || "미리보기를 불러오지 못했습니다.")}</p>`;
    return;
  }
  previewBody.innerHTML = sanitizePreviewHtml(response.data.articleHtml);
}

function queueActionPath(itemId, action) {
  return `/api/queue/${encodeURIComponent(state.selectedDeviceId)}/${encodeURIComponent(itemId)}/${action}`;
}

function sanitizePreviewHtml(value) {
  const parser = new DOMParser();
  const documentFragment = parser.parseFromString(String(value || ""), "text/html");
  documentFragment
    .querySelectorAll("script, style, iframe, object, embed, form, meta, link, base")
    .forEach((element) => element.remove());
  documentFragment.querySelectorAll("*").forEach((element) => {
    [...element.attributes].forEach((attribute) => {
      const name = attribute.name.toLowerCase();
      const attributeValue = attribute.value.trim().toLowerCase();
      if (name.startsWith("on") || ((name === "href" || name === "src") && attributeValue.startsWith("javascript:"))) {
        element.removeAttribute(attribute.name);
      }
    });
  });
  return documentFragment.body.innerHTML || "<p>표시할 본문이 없습니다.</p>";
}

function toDateTimeLocal(timestamp) {
  const value = Number(timestamp || 0);
  if (!value) {
    return "";
  }
  const date = new Date(value * 1000);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatDateTime(timestamp) {
  const date = new Date(Number(timestamp || 0) * 1000);
  if (Number.isNaN(date.getTime())) {
    return "미정";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function setQueueMessage(message, isError = false) {
  const element = document.querySelector("#queueMessage");
  element.textContent = message || "";
  element.classList.toggle("error", Boolean(isError));
}

async function waitForCommand(commandId) {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    await delay(attempt === 0 ? 250 : 500);
    const response = await api(`/api/commands/${encodeURIComponent(commandId)}`);
    if (response.status !== 202) {
      if (response.ok && response.data.pending === false) {
        return {
          ok: Boolean(response.data.ok),
          status: response.data.ok ? 200 : 409,
          data: response.data,
        };
      }
      return response;
    }
  }
  return {
    ok: true,
    status: 202,
    data: { message: "PC가 요청을 처리 중입니다. 대기열 상태가 곧 갱신됩니다." },
  };
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function renderJobs() {
  clearJobsButton.disabled = !state.jobs.some((job) => ["completed", "failed", "cancelled"].includes(job.status));
  if (!state.jobs.length) {
    jobList.innerHTML = `<div class="empty">아직 원격 작업 기록이 없습니다.</div>`;
    return;
  }
  const statusLabel = {
    sent: "전달됨",
    running: "작성 중",
    completed: "완료",
    failed: "실패",
    cancelled: "취소",
  };
  jobList.innerHTML = state.jobs
    .slice(0, 20)
    .map((job) => {
      const progress = Math.round(Number(job.progress || 0) * 100);
      const failedClass = ["failed", "cancelled"].includes(job.status) ? "failed" : "";
      const publishedUrl = job.status === "completed"
        ? safeExternalUrl(job.result && job.result.publishedUrl)
        : "";
      const openingTag = publishedUrl
        ? `<a class="job job-link" href="${escapeHtml(publishedUrl)}" target="_blank" rel="noopener noreferrer">`
        : `<article class="job">`;
      const closingTag = publishedUrl ? "</a>" : "</article>";
      return `
        ${openingTag}
          <div class="job-head">
            <div>
              <h3>${escapeHtml(job.keyword)}</h3>
              <p>${escapeHtml(job.deviceName || job.deviceId)} · ${escapeHtml(job.message || "")}</p>
            </div>
            <span class="job-state ${failedClass}">${statusLabel[job.status] || job.status}</span>
          </div>
          <div class="progress-track"><div class="progress-value" style="width:${progress}%"></div></div>
          ${publishedUrl ? '<span class="job-open-hint">발행된 블로그 글 보러가기 →</span>' : ""}
        ${closingTag}`;
    })
    .join("");
}

function updateSubmitButton() {
  const device = state.devices.find((item) => item.deviceId === state.selectedDeviceId);
  if (!device) {
    submitJob.disabled = true;
    submitJob.textContent = "PC를 먼저 선택해 주세요";
    return;
  }
  if (device.status !== "online") {
    submitJob.disabled = true;
    submitJob.textContent = device.status === "busy" ? "선택한 PC가 작업 중입니다" : "선택한 PC가 오프라인입니다";
    return;
  }
  submitJob.disabled = false;
  submitJob.textContent = `${device.name}에서 글 작성 시작`;
}

async function api(path, options = {}) {
  try {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, data };
  } catch {
    return { ok: false, status: 0, data: { error: "서버에 연결하지 못했습니다." } };
  }
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.textContent = value;
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeExternalUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
  } catch {
    return "";
  }
}

const sessionCheck = await api("/api/devices");
if (sessionCheck.ok) {
  showDashboard();
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // The remote page still works normally when installation is unavailable.
    });
  });
}
