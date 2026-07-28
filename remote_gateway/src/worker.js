const SESSION_COOKIE = "blog_helper_session";
const SESSION_TTL_SECONDS = 60 * 60 * 24 * 30;
const DEVICE_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 365 * 10;
const DAUM_REALTIME_URL =
  "https://m.search.daum.net/search?w=tot"
  + "&q=%EB%8B%A4%EC%9D%8C%20%EC%8B%A4%EC%8B%9C%EA%B0%84%20%EA%B2%80%EC%83%89%EC%96%B4%20%EC%88%9C%EC%9C%84"
  + "&nzq=%EB%8B%A4%EC%9D%8C%20%EC%8B%A4%EC%8B%9C%EA%B0%84%ED%8A%B8%EB%A0%8C%EB%93%9C"
  + "&DA=NSJ";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) {
      return withSecurityHeaders(await env.ASSETS.fetch(request));
    }

    if (url.pathname === "/api/login" && request.method === "POST") {
      if (!env.CONTROL_PASSWORD || !env.SESSION_SECRET) {
        return jsonResponse({ error: "원격 서버 비밀값이 아직 설정되지 않았습니다." }, 503);
      }
      if (!sameOriginRequest(request)) {
        return jsonResponse({ error: "잘못된 요청 출처입니다." }, 403);
      }
      const payload = await readJson(request);
      if (!safeEqual(String(payload.password || ""), String(env.CONTROL_PASSWORD || ""))) {
        return jsonResponse({ error: "비밀번호가 올바르지 않습니다." }, 401);
      }
      const token = await createSessionToken(env.SESSION_SECRET);
      return jsonResponse(
        { ok: true },
        200,
        {
          "Set-Cookie": `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_TTL_SECONDS}`,
        },
      );
    }

    if (url.pathname === "/api/logout" && request.method === "POST") {
      return jsonResponse(
        { ok: true },
        200,
        { "Set-Cookie": `${SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0` },
      );
    }

    if (url.pathname === "/api/agent/pair" && request.method === "POST") {
      if (!env.CONTROL_PASSWORD || !env.SESSION_SECRET) {
        return jsonResponse({ error: "원격 서버 비밀값이 아직 설정되지 않았습니다." }, 503);
      }
      const payload = await readJson(request);
      const password = String(payload.password || "");
      const deviceId = cleanText(payload.deviceId, 80);
      if (!deviceId || !safeEqual(password, String(env.CONTROL_PASSWORD || ""))) {
        return jsonResponse({ error: "관리 비밀번호가 올바르지 않습니다." }, 401);
      }
      const deviceToken = await createDeviceToken(env.SESSION_SECRET, deviceId);
      return jsonResponse({ ok: true, deviceToken });
    }

    const roomId = env.CONTROL_ROOM.idFromName("global");
    const room = env.CONTROL_ROOM.get(roomId);
    if (url.pathname === "/api/agent") {
      if (!env.AGENT_TOKEN || !env.SESSION_SECRET) {
        return jsonResponse({ error: "원격 에이전트 토큰이 아직 설정되지 않았습니다." }, 503);
      }
      const authorization = request.headers.get("Authorization") || "";
      const token = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
      const requestedDeviceId = cleanText(url.searchParams.get("deviceId"), 80);
      const globalTokenMatches = safeEqual(token, String(env.AGENT_TOKEN || ""));
      const deviceTokenMatches = await verifyDeviceToken(token, env.SESSION_SECRET, requestedDeviceId);
      if (!globalTokenMatches && !deviceTokenMatches) {
        return jsonResponse({ error: "에이전트 인증에 실패했습니다." }, 401);
      }
      return room.fetch(markAuthorized(request, "agent"));
    }

    const cookie = readCookie(request.headers.get("Cookie") || "", SESSION_COOKIE);
    if (!(await verifySessionToken(cookie, env.SESSION_SECRET))) {
      return jsonResponse({ error: "로그인이 필요합니다." }, 401);
    }
    if (request.method !== "GET" && !sameOriginRequest(request)) {
      return jsonResponse({ error: "잘못된 요청 출처입니다." }, 403);
    }
    return room.fetch(markAuthorized(request, "web"));
  },
};

export class ControlRoom {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
  }

  async fetch(request) {
    const url = new URL(request.url);
    const authorizedAs = request.headers.get("X-Blog-Helper-Authorized");
    if (!authorizedAs) {
      return jsonResponse({ error: "인증되지 않은 내부 요청입니다." }, 401);
    }

    if (url.pathname === "/api/agent" && authorizedAs === "agent") {
      return this.connectAgent(request, url);
    }
    if (authorizedAs !== "web") {
      return jsonResponse({ error: "웹 사용자 인증이 필요합니다." }, 401);
    }
    if (url.pathname === "/api/devices" && request.method === "GET") {
      return jsonResponse({ devices: await this.listDevices() });
    }
    const deviceMatch = url.pathname.match(/^\/api\/devices\/([^/]+)$/);
    if (deviceMatch && request.method === "DELETE") {
      return this.hideDevice(decodeURIComponent(deviceMatch[1]));
    }
    if (url.pathname === "/api/jobs" && request.method === "GET") {
      return jsonResponse({ jobs: await this.listJobs() });
    }
    if (url.pathname === "/api/jobs" && request.method === "POST") {
      return this.createJob(request);
    }
    if (url.pathname === "/api/jobs" && request.method === "DELETE") {
      return this.clearJobHistory();
    }
    if (url.pathname === "/api/trends/daum" && request.method === "GET") {
      return this.fetchDaumRealtimeTrends();
    }
    if (url.pathname === "/api/queue" && request.method === "GET") {
      return this.listQueue(
        url.searchParams.get("deviceId"),
        url.searchParams.get("refresh") === "1",
      );
    }
    const previewMatch = url.pathname.match(/^\/api\/queue\/([^/]+)\/([^/]+)\/preview$/);
    if (previewMatch && request.method === "GET") {
      return this.getQueuePreview(
        decodeURIComponent(previewMatch[1]),
        decodeURIComponent(previewMatch[2]),
      );
    }
    const scheduleMatch = url.pathname.match(/^\/api\/queue\/([^/]+)\/([^/]+)\/schedule$/);
    if (scheduleMatch && request.method === "POST") {
      return this.updateQueueSchedule(
        request,
        decodeURIComponent(scheduleMatch[1]),
        decodeURIComponent(scheduleMatch[2]),
      );
    }
    const publishMatch = url.pathname.match(/^\/api\/queue\/([^/]+)\/([^/]+)\/publish$/);
    if (publishMatch && request.method === "POST") {
      return this.publishQueueItem(
        decodeURIComponent(publishMatch[1]),
        decodeURIComponent(publishMatch[2]),
      );
    }
    const commandMatch = url.pathname.match(/^\/api\/commands\/([^/]+)$/);
    if (commandMatch && request.method === "GET") {
      return this.getCommandResult(decodeURIComponent(commandMatch[1]));
    }
    const cancelMatch = url.pathname.match(/^\/api\/jobs\/([^/]+)\/cancel$/);
    if (cancelMatch && request.method === "POST") {
      return this.cancelJob(decodeURIComponent(cancelMatch[1]));
    }
    return jsonResponse({ error: "요청한 API를 찾을 수 없습니다." }, 404);
  }

  async connectAgent(request, url) {
    if (request.headers.get("Upgrade") !== "websocket") {
      return jsonResponse({ error: "WebSocket 연결이 필요합니다." }, 426);
    }
    const deviceId = cleanText(url.searchParams.get("deviceId"), 80);
    const name = cleanText(url.searchParams.get("name"), 80);
    if (!deviceId || !name) {
      return jsonResponse({ error: "PC 식별자와 이름이 필요합니다." }, 400);
    }

    for (const existing of this.ctx.getWebSockets(`device:${deviceId}`)) {
      try {
        existing.close(4001, "새 연결로 교체합니다.");
      } catch {
        // Already closed.
      }
    }

    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];
    const attachment = {
      deviceId,
      name,
      platform: cleanText(url.searchParams.get("platform"), 100),
      version: cleanText(url.searchParams.get("version"), 30),
      sessionId: cleanText(url.searchParams.get("sessionId"), 80),
    };
    server.serializeAttachment(attachment);
    this.ctx.acceptWebSocket(server, ["agents", `device:${deviceId}`]);
    let existingDevice = (await this.ctx.storage.get(`device:${deviceId}`)) || {};
    if (
      existingDevice.hiddenSessionId
      && attachment.sessionId
      && attachment.sessionId !== existingDevice.hiddenSessionId
    ) {
      const { hiddenSessionId: _hiddenSessionId, hiddenAt: _hiddenAt, ...visibleDevice } = existingDevice;
      existingDevice = visibleDevice;
    }
    await this.ctx.storage.put(`device:${deviceId}`, {
      ...existingDevice,
      ...attachment,
      lastSeen: Date.now(),
      connectedAt: Date.now(),
    });
    return new Response(null, { status: 101, webSocket: client });
  }

  async createJob(request) {
    const payload = await readJson(request);
    const deviceId = cleanText(payload.deviceId, 80);
    const keyword = cleanText(payload.keyword, 300);
    const targets = normalizeTargets(payload.targets);
    if (!deviceId || !keyword) {
      return jsonResponse({ error: "PC와 키워드를 선택해 주세요." }, 400);
    }

    const sockets = this.ctx.getWebSockets(`device:${deviceId}`);
    if (!sockets.length) {
      return jsonResponse({ error: "선택한 PC가 현재 오프라인입니다." }, 409);
    }
    const deviceKey = `device:${deviceId}`;
    const device = (await this.ctx.storage.get(deviceKey)) || {};
    if (device.busyJobId) {
      return jsonResponse({ error: "선택한 PC는 다른 작업을 진행 중입니다." }, 409);
    }

    const now = Date.now();
    const jobId = crypto.randomUUID();
    const job = {
      id: jobId,
      deviceId,
      deviceName: device.name || deviceId,
      keyword,
      targets,
      action: payload.action === "publish" ? "publish" : "queue",
      status: "sent",
      progress: 0,
      message: "PC로 작업을 전달했습니다.",
      createdAt: now,
      updatedAt: now,
    };
    await this.ctx.storage.put(`job:${jobId}`, job);
    await this.ctx.storage.put(deviceKey, { ...device, busyJobId: jobId, lastSeen: now });

    try {
      sockets[0].send(JSON.stringify({ type: "job", ...job }));
    } catch {
      await this.ctx.storage.put(`job:${jobId}`, {
        ...job,
        status: "failed",
        message: "PC 연결이 끊어져 작업을 전달하지 못했습니다.",
        updatedAt: Date.now(),
      });
      await this.ctx.storage.put(deviceKey, { ...device, busyJobId: "", lastSeen: now });
      return jsonResponse({ error: "PC 연결이 끊어졌습니다. 잠시 후 다시 시도해 주세요." }, 409);
    }
    return jsonResponse({ job }, 201);
  }

  async cancelJob(jobId) {
    const jobKey = `job:${jobId}`;
    const job = await this.ctx.storage.get(jobKey);
    if (!job || isTerminal(job.status)) {
      return jsonResponse({ error: "취소할 작업을 찾을 수 없습니다." }, 404);
    }
    const sockets = this.ctx.getWebSockets(`device:${job.deviceId}`);
    for (const socket of sockets) {
      try {
        socket.send(JSON.stringify({ type: "job.cancel", jobId }));
      } catch {
        // The PC will be released below even if the cancel notice cannot be delivered.
      }
    }
    const updated = { ...job, status: "cancelled", message: "사용자가 작업을 취소했습니다.", updatedAt: Date.now() };
    await this.ctx.storage.put(jobKey, updated);
    await this.releaseDevice(job.deviceId, jobId);
    return jsonResponse({ job: updated });
  }

  async listDevices() {
    const entries = await this.ctx.storage.list({ prefix: "device:" });
    const devices = [];
    for (const [key, value] of entries) {
      const deviceId = key.slice("device:".length);
      if (value.hiddenSessionId && value.hiddenSessionId === value.sessionId) {
        continue;
      }
      const online = this.ctx.getWebSockets(`device:${deviceId}`).length > 0;
      devices.push({
        ...value,
        deviceId,
        online,
        status: online ? (value.busyJobId ? "busy" : "online") : "offline",
      });
    }
    return devices.sort((a, b) => Number(b.online) - Number(a.online) || String(a.name).localeCompare(String(b.name)));
  }

  async hideDevice(rawDeviceId) {
    const deviceId = cleanText(rawDeviceId, 80);
    const deviceKey = `device:${deviceId}`;
    const device = deviceId ? await this.ctx.storage.get(deviceKey) : null;
    if (!device) {
      return jsonResponse({ error: "삭제할 PC를 찾지 못했습니다." }, 404);
    }
    if (device.busyJobId) {
      return jsonResponse({ error: "현재 작업 중인 PC는 작업 완료 후 삭제할 수 있습니다." }, 409);
    }
    const hiddenSessionId = cleanText(device.sessionId, 80) || `legacy-${deviceId}`;
    await this.ctx.storage.put(deviceKey, {
      ...device,
      hiddenSessionId,
      hiddenAt: Date.now(),
      busyJobId: "",
    });
    for (const socket of this.ctx.getWebSockets(`device:${deviceId}`)) {
      try {
        socket.close(4002, "원격 목록에서 PC를 숨겼습니다.");
      } catch {
        // The hidden-session marker still keeps the current process out of the list.
      }
    }
    return jsonResponse({
      ok: true,
      message: "PC를 목록에서 삭제했습니다. 해당 PC의 Blog Helper를 다시 실행하면 자동으로 다시 연결됩니다.",
    });
  }

  async listJobs() {
    const entries = await this.ctx.storage.list({ prefix: "job:", reverse: true, limit: 40 });
    return [...entries.values()].sort((a, b) => Number(b.createdAt || 0) - Number(a.createdAt || 0));
  }

  async clearJobHistory() {
    const entries = await this.ctx.storage.list({ prefix: "job:" });
    const removableKeys = [];
    for (const [key, job] of entries) {
      if (isTerminal(job.status)) {
        removableKeys.push(key);
      }
    }
    if (removableKeys.length) {
      await this.ctx.storage.delete(removableKeys);
    }
    return jsonResponse({
      ok: true,
      cleared: removableKeys.length,
      message: removableKeys.length
        ? `완료된 최근 작업 ${removableKeys.length}개를 초기화했습니다.`
        : "초기화할 완료 작업이 없습니다.",
    });
  }

  async fetchDaumRealtimeTrends() {
    let response;
    try {
      response = await fetch(DAUM_REALTIME_URL, {
        headers: {
          "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
          ),
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
          "Referer": "https://www.daum.net/",
        },
        signal: AbortSignal.timeout(15000),
      });
    } catch {
      return jsonResponse({ error: "다음 실시간 검색어 서버 응답이 늦어 불러오지 못했습니다." }, 504);
    }
    if (!response.ok) {
      return jsonResponse({ error: `다음 실시간 검색어를 불러오지 못했습니다. (${response.status})` }, 502);
    }
    const html = await response.text();
    const trends = parseDaumRealtimeTrends(html);
    if (!trends.length) {
      return jsonResponse({ error: "현재 다음 실시간 검색어 1~10위를 찾지 못했습니다." }, 502);
    }
    return jsonResponse({
      source: "다음 실시간",
      fetchedAt: Date.now(),
      trends,
    });
  }

  async listQueue(rawDeviceId, requestRefresh = false) {
    const deviceId = cleanText(rawDeviceId, 80);
    if (!deviceId) {
      return jsonResponse({ error: "대기열을 확인할 PC를 선택해 주세요." }, 400);
    }
    const device = await this.ctx.storage.get(`device:${deviceId}`);
    if (!device) {
      return jsonResponse({ error: "등록된 PC를 찾지 못했습니다." }, 404);
    }
    const stored = (await this.ctx.storage.get(`queue:${deviceId}`)) || {
      items: [],
      updatedAt: 0,
    };
    const sockets = this.ctx.getWebSockets(`device:${deviceId}`);
    if (sockets.length && requestRefresh) {
      try {
        sockets[0].send(
          JSON.stringify({
            type: "queue.request",
            commandId: crypto.randomUUID(),
          }),
        );
      } catch {
        // 저장된 마지막 대기열은 계속 표시합니다.
      }
    }
    return jsonResponse({
      deviceId,
      deviceName: device.name || deviceId,
      online: sockets.length > 0,
      items: Array.isArray(stored.items) ? stored.items : [],
      updatedAt: Number(stored.updatedAt || 0),
    });
  }

  async getQueuePreview(rawDeviceId, rawItemId) {
    const deviceId = cleanText(rawDeviceId, 80);
    const itemId = cleanText(rawItemId, 100);
    if (!deviceId || !itemId) {
      return jsonResponse({ error: "미리보기 글을 찾지 못했습니다." }, 400);
    }
    const preview = await this.ctx.storage.get(`queue-preview:${deviceId}:${itemId}`);
    if (!preview) {
      return jsonResponse({ error: "아직 미리보기 본문이 PC에서 전송되지 않았습니다." }, 404);
    }
    return jsonResponse(preview);
  }

  async updateQueueSchedule(request, rawDeviceId, rawItemId) {
    const deviceId = cleanText(rawDeviceId, 80);
    const itemId = cleanText(rawItemId, 100);
    const payload = await readJson(request);
    const scheduledAt = Number(payload.scheduledAt || 0);
    if (!deviceId || !itemId || !Number.isFinite(scheduledAt) || scheduledAt <= 0) {
      return jsonResponse({ error: "올바른 등록 예정시간을 입력해 주세요." }, 400);
    }
    return this.sendQueueCommand(deviceId, {
      type: "queue.schedule.update",
      itemId,
      scheduledAt,
    });
  }

  async publishQueueItem(rawDeviceId, rawItemId) {
    const deviceId = cleanText(rawDeviceId, 80);
    const itemId = cleanText(rawItemId, 100);
    if (!deviceId || !itemId) {
      return jsonResponse({ error: "즉시발행할 글을 찾지 못했습니다." }, 400);
    }
    return this.sendQueueCommand(deviceId, {
      type: "queue.publish.now",
      itemId,
    });
  }

  async sendQueueCommand(deviceId, command) {
    const sockets = this.ctx.getWebSockets(`device:${deviceId}`);
    if (!sockets.length) {
      return jsonResponse({ error: "선택한 PC가 현재 오프라인입니다." }, 409);
    }
    const commandId = crypto.randomUUID();
    try {
      sockets[0].send(JSON.stringify({ ...command, commandId }));
    } catch {
      return jsonResponse({ error: "PC 연결이 끊어져 요청을 전달하지 못했습니다." }, 409);
    }
    return jsonResponse(
      {
        ok: true,
        commandId,
        message: command.type === "queue.publish.now"
          ? "PC에 즉시발행을 요청했습니다."
          : "PC에 등록 예정시간 변경을 요청했습니다.",
      },
      202,
    );
  }

  async getCommandResult(rawCommandId) {
    const commandId = cleanText(rawCommandId, 80);
    if (!commandId) {
      return jsonResponse({ error: "원격 명령을 찾지 못했습니다." }, 400);
    }
    const result = await this.ctx.storage.get(`command:${commandId}`);
    if (!result) {
      return jsonResponse({ pending: true }, 202);
    }
    return jsonResponse({ pending: false, ...result });
  }

  async webSocketMessage(socket, message) {
    let payload;
    try {
      payload = JSON.parse(typeof message === "string" ? message : new TextDecoder().decode(message));
    } catch {
      return;
    }
    const attachment = socket.deserializeAttachment() || {};
    const deviceId = attachment.deviceId;
    if (!deviceId) {
      return;
    }
    const deviceKey = `device:${deviceId}`;
    const device = (await this.ctx.storage.get(deviceKey)) || attachment;
    if (payload.type === "ready" || payload.type === "pong") {
      await this.ctx.storage.put(deviceKey, { ...device, ...attachment, lastSeen: Date.now() });
      return;
    }
    if (payload.type === "queue.snapshot") {
      const previous = (await this.ctx.storage.get(`queue:${deviceId}`)) || { items: [] };
      const normalized = normalizeQueueSnapshot(payload.items);
      const activeIds = new Set(normalized.map((item) => item.id));
      for (const oldItem of previous.items || []) {
        const oldId = cleanText(oldItem.id, 100);
        if (oldId && !activeIds.has(oldId)) {
          await this.ctx.storage.delete(`queue-preview:${deviceId}:${oldId}`);
        }
      }
      const summaries = [];
      for (const item of normalized) {
        await this.ctx.storage.put(`queue-preview:${deviceId}:${item.id}`, {
          id: item.id,
          title: item.title,
          articleHtml: item.articleHtml,
          updatedAt: Date.now(),
        });
        const { articleHtml, ...summary } = item;
        summaries.push(summary);
      }
      const updatedAt = Number(payload.updatedAt || Date.now());
      await this.ctx.storage.put(`queue:${deviceId}`, {
        items: summaries,
        updatedAt,
      });
      await this.ctx.storage.put(deviceKey, {
        ...device,
        ...attachment,
        queueCount: summaries.length,
        queueUpdatedAt: updatedAt,
        lastSeen: Date.now(),
      });
      return;
    }
    if (payload.type === "command.result") {
      const commandId = cleanText(payload.commandId, 80);
      if (commandId) {
        await this.ctx.storage.put(`command:${commandId}`, {
          commandId,
          deviceId,
          ok: Boolean(payload.ok),
          message: cleanText(payload.message, 500),
          updatedAt: Date.now(),
        });
      }
      await this.ctx.storage.put(deviceKey, { ...device, ...attachment, lastSeen: Date.now() });
      return;
    }
    if (payload.type === "queue.published") {
      const publishedUrl = cleanHttpUrl(payload.publishedUrl);
      const queueId = cleanText(payload.queueId, 100);
      let jobId = cleanText(payload.jobId, 80);
      let job = jobId ? await this.ctx.storage.get(`job:${jobId}`) : null;
      if ((!job || job.deviceId !== deviceId) && queueId) {
        const jobs = await this.ctx.storage.list({ prefix: "job:", reverse: true, limit: 80 });
        job = [...jobs.values()].find(
          (candidate) => (
            candidate.deviceId === deviceId
            && cleanText(candidate.result && candidate.result.queueId, 100) === queueId
          ),
        );
        jobId = job ? cleanText(job.id, 80) : "";
      }
      if (job && jobId && publishedUrl) {
        await this.ctx.storage.put(`job:${jobId}`, {
          ...job,
          status: "completed",
          progress: 1,
          message: "블로그 발행을 완료했습니다. 눌러서 실제 글을 확인할 수 있습니다.",
          result: {
            ...(job.result || {}),
            queueId,
            publishedUrl,
            title: cleanText(payload.title, 300) || cleanText(job.result && job.result.title, 300),
          },
          updatedAt: Date.now(),
        });
      }
      await this.ctx.storage.put(deviceKey, { ...device, ...attachment, lastSeen: Date.now() });
      return;
    }
    if (!String(payload.type || "").startsWith("job.")) {
      return;
    }
    const jobId = cleanText(payload.jobId, 80);
    const jobKey = `job:${jobId}`;
    const job = await this.ctx.storage.get(jobKey);
    if (!job || job.deviceId !== deviceId) {
      return;
    }
    const statusMap = {
      "job.accepted": "running",
      "job.progress": "running",
      "job.completed": "completed",
      "job.failed": "failed",
    };
    const status = statusMap[payload.type] || job.status;
    const updated = {
      ...job,
      status,
      progress: Math.max(0, Math.min(Number(payload.progress || 0), 1)),
      message: cleanText(payload.message, 500),
      result: payload.result && typeof payload.result === "object" ? payload.result : job.result,
      updatedAt: Date.now(),
    };
    await this.ctx.storage.put(jobKey, updated);
    await this.ctx.storage.put(deviceKey, { ...device, lastSeen: Date.now(), busyJobId: isTerminal(status) ? "" : jobId });
  }

  async webSocketClose(socket) {
    await this.markDisconnected(socket);
  }

  async webSocketError(socket) {
    await this.markDisconnected(socket);
  }

  async markDisconnected(socket) {
    const attachment = socket.deserializeAttachment() || {};
    const deviceId = attachment.deviceId;
    if (!deviceId) {
      return;
    }
    const deviceKey = `device:${deviceId}`;
    const device = (await this.ctx.storage.get(deviceKey)) || attachment;
    if (device.busyJobId) {
      const jobKey = `job:${device.busyJobId}`;
      const job = await this.ctx.storage.get(jobKey);
      if (job && !isTerminal(job.status)) {
        await this.ctx.storage.put(jobKey, {
          ...job,
          status: "failed",
          message: "작업 중 PC 연결이 끊어졌습니다.",
          updatedAt: Date.now(),
        });
      }
    }
    await this.ctx.storage.put(deviceKey, { ...device, busyJobId: "", lastSeen: Date.now() });
  }

  async releaseDevice(deviceId, jobId) {
    const key = `device:${deviceId}`;
    const device = await this.ctx.storage.get(key);
    if (device && device.busyJobId === jobId) {
      await this.ctx.storage.put(key, { ...device, busyJobId: "", lastSeen: Date.now() });
    }
  }
}

function normalizeTargets(value) {
  const allowed = new Set(["wordpress", "tistory", "blogspot"]);
  const targets = Array.isArray(value) ? value.filter((item) => allowed.has(item)) : [];
  return targets.length ? [...new Set(targets)] : ["wordpress"];
}

function normalizeQueueSnapshot(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .slice(-30)
    .map((item) => {
      const id = cleanText(item && item.id, 100);
      if (!id) {
        return null;
      }
      const scheduledAt = Number(item.scheduledAt || 0);
      return {
        id,
        title: cleanText(item.title, 300) || "제목 없는 글",
        keyword: cleanText(item.keyword, 300),
        status: cleanText(item.status, 60) || "대기 중",
        scheduledAt: Number.isFinite(scheduledAt) ? scheduledAt : 0,
        createdAt: cleanText(item.createdAt, 40),
        targetPlatforms: normalizeTargets(item.targetPlatforms),
        excerpt: cleanText(item.excerpt, 280),
        hasThumbnail: Boolean(item.hasThumbnail),
        cardnewsCount: Math.max(0, Math.min(Number(item.cardnewsCount || 0), 20)),
        articleHtml: cleanPreviewHtml(item.articleHtml),
      };
    })
    .filter(Boolean);
}

function cleanPreviewHtml(value) {
  let html = String(value || "").slice(0, 8000);
  html = html.replace(
    /<(script|style|iframe|object|embed|form|meta|link)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,
    "",
  );
  html = html.replace(/<(script|style|iframe|object|embed|form|meta|link)\b[^>]*\/?>/gi, "");
  html = html.replace(/\son[a-z]+\s*=\s*(["'])[\s\S]*?\1/gi, "");
  html = html.replace(/\s(href|src)\s*=\s*(["'])\s*javascript:[\s\S]*?\2/gi, ' $1="#"');
  return html;
}

function isTerminal(status) {
  return ["completed", "failed", "cancelled"].includes(status);
}

function cleanText(value, maxLength) {
  return String(value || "").trim().slice(0, maxLength);
}

function cleanHttpUrl(value) {
  const text = cleanText(value, 2000);
  if (!text) {
    return "";
  }
  try {
    const parsed = new URL(text);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
  } catch {
    return "";
  }
}

function parseDaumRealtimeTrends(html) {
  const trends = [];
  const seen = new Set();
  const anchors = String(html || "").match(
    /<a\b[^>]*\bclass\s*=\s*(?:"[^"]*\blink_trend\b[^"]*"|'[^']*\blink_trend\b[^']*')[^>]*>/gis,
  ) || [];
  for (const anchor of anchors) {
    const keyword = cleanText(decodeHtmlEntities(readHtmlAttribute(anchor, "data-keyword")), 120);
    const rank = Number.parseInt(readHtmlAttribute(anchor, "data-rank"), 10);
    if (!keyword || seen.has(keyword)) {
      continue;
    }
    seen.add(keyword);
    trends.push({
      rank: Number.isFinite(rank) && rank > 0 ? rank : trends.length + 1,
      keyword,
      status: cleanText(decodeHtmlEntities(readHtmlAttribute(anchor, "data-status")), 40),
      url: `https://m.search.daum.net/search?w=tot&q=${encodeURIComponent(keyword)}`,
    });
  }
  return trends
    .sort((left, right) => left.rank - right.rank)
    .slice(0, 10);
}

function readHtmlAttribute(tag, name) {
  const match = String(tag || "").match(
    new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, "i"),
  );
  return match ? (match[1] ?? match[2] ?? "") : "";
}

function decodeHtmlEntities(value) {
  return String(value || "")
    .replace(/&#(\d+);/g, (_match, code) => String.fromCodePoint(Number(code) || 0))
    .replace(/&#x([0-9a-f]+);/gi, (_match, code) => String.fromCodePoint(Number.parseInt(code, 16) || 0))
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&");
}

async function readJson(request) {
  try {
    return await request.json();
  } catch {
    return {};
  }
}

function markAuthorized(request, role) {
  const headers = new Headers(request.headers);
  headers.set("X-Blog-Helper-Authorized", role);
  return new Request(request, { headers });
}

function sameOriginRequest(request) {
  const origin = request.headers.get("Origin");
  if (!origin) {
    return true;
  }
  return new URL(origin).host === new URL(request.url).host;
}

function readCookie(header, name) {
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      return rest.join("=");
    }
  }
  return "";
}

async function createSessionToken(secret) {
  const expiresAt = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const nonce = base64Url(crypto.getRandomValues(new Uint8Array(18)));
  const body = `${expiresAt}.${nonce}`;
  const signature = await hmac(secret, body);
  return `${body}.${signature}`;
}

async function verifySessionToken(token, secret) {
  if (!token || !secret) {
    return false;
  }
  const parts = token.split(".");
  if (parts.length !== 3) {
    return false;
  }
  const expiresAt = Number(parts[0]);
  if (!Number.isFinite(expiresAt) || expiresAt < Math.floor(Date.now() / 1000)) {
    return false;
  }
  const expected = await hmac(secret, `${parts[0]}.${parts[1]}`);
  return safeEqual(parts[2], expected);
}

async function createDeviceToken(secret, deviceId) {
  const expiresAt = Math.floor(Date.now() / 1000) + DEVICE_TOKEN_TTL_SECONDS;
  const encodedDeviceId = base64Url(new TextEncoder().encode(deviceId));
  const body = `${expiresAt}.${encodedDeviceId}`;
  const signature = await hmac(secret, body);
  return `${body}.${signature}`;
}

async function verifyDeviceToken(token, secret, deviceId) {
  if (!token || !secret || !deviceId) {
    return false;
  }
  const parts = token.split(".");
  if (parts.length !== 3) {
    return false;
  }
  const expiresAt = Number(parts[0]);
  const expectedDeviceId = base64Url(new TextEncoder().encode(deviceId));
  if (
    !Number.isFinite(expiresAt)
    || expiresAt < Math.floor(Date.now() / 1000)
    || !safeEqual(parts[1], expectedDeviceId)
  ) {
    return false;
  }
  const expected = await hmac(secret, `${parts[0]}.${parts[1]}`);
  return safeEqual(parts[2], expected);
}

async function hmac(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(String(secret || "")),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  return base64Url(new Uint8Array(signature));
}

function base64Url(bytes) {
  let binary = "";
  for (const value of bytes) {
    binary += String.fromCharCode(value);
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function safeEqual(left, right) {
  const a = String(left || "");
  const b = String(right || "");
  if (a.length !== b.length) {
    return false;
  }
  let mismatch = 0;
  for (let index = 0; index < a.length; index += 1) {
    mismatch |= a.charCodeAt(index) ^ b.charCodeAt(index);
  }
  return mismatch === 0;
}

function jsonResponse(payload, status = 200, extraHeaders = {}) {
  return withSecurityHeaders(
    new Response(JSON.stringify(payload), {
      status,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        ...extraHeaders,
      },
    }),
  );
}

function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Referrer-Policy", "no-referrer");
  headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  headers.set(
    "Content-Security-Policy",
    "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  );
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}
