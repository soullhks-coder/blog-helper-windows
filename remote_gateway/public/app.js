const state = {
  devices: [],
  jobs: [],
  selectedDeviceId: "",
  pollTimer: null,
};

const loginView = document.querySelector("#loginView");
const dashboardView = document.querySelector("#dashboardView");
const loginForm = document.querySelector("#loginForm");
const jobForm = document.querySelector("#jobForm");
const submitJob = document.querySelector("#submitJob");
const deviceGrid = document.querySelector("#deviceGrid");
const jobList = document.querySelector("#jobList");

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
  setText("#jobError", "");
  const keyword = document.querySelector("#keyword").value.trim();
  const targets = [...document.querySelectorAll("input[name='target']:checked")].map((item) => item.value);
  if (!state.selectedDeviceId) {
    setText("#jobError", "작업할 PC를 먼저 선택해 주세요.");
    return;
  }
  if (!targets.length) {
    setText("#jobError", "발행 대상을 하나 이상 선택해 주세요.");
    return;
  }
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
  updateSubmitButton();
});

document.querySelector("#refreshButton").addEventListener("click", refreshDashboard);
document.querySelector("#logoutButton").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  stopPolling();
  dashboardView.hidden = true;
  loginView.hidden = false;
});

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
  if (!state.devices.some((device) => device.deviceId === state.selectedDeviceId && device.status === "online")) {
    state.selectedDeviceId = "";
  }
  renderDevices();
  renderJobs();
  updateSubmitButton();
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
      const disabled = device.status !== "online";
      const selected = state.selectedDeviceId === device.deviceId;
      const statusLabel = device.status === "busy" ? "작업 중" : device.status === "online" ? "사용 가능" : "오프라인";
      return `
        <button class="device ${selected ? "selected" : ""}" type="button"
          data-device-id="${escapeHtml(device.deviceId)}" ${disabled ? "disabled" : ""}>
          <i class="status-dot ${escapeHtml(device.status)}"></i>
          <strong>${escapeHtml(device.name || "이름 없는 PC")}</strong>
          <span>${escapeHtml(device.platform || "")}</span>
          <span>Blog Helper v${escapeHtml(device.version || "-")} · ${statusLabel}</span>
        </button>`;
    })
    .join("");
  deviceGrid.querySelectorAll(".device:not(:disabled)").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedDeviceId = button.dataset.deviceId || "";
      renderDevices();
      updateSubmitButton();
    });
  });
}

function renderJobs() {
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
      return `
        <article class="job">
          <div class="job-head">
            <div>
              <h3>${escapeHtml(job.keyword)}</h3>
              <p>${escapeHtml(job.deviceName || job.deviceId)} · ${escapeHtml(job.message || "")}</p>
            </div>
            <span class="job-state ${failedClass}">${statusLabel[job.status] || job.status}</span>
          </div>
          <div class="progress-track"><div class="progress-value" style="width:${progress}%"></div></div>
        </article>`;
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
