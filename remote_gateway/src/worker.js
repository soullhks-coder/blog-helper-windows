const SESSION_COOKIE = "blog_helper_session";
const SESSION_TTL_SECONDS = 60 * 60 * 24 * 30;
const DEVICE_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 365 * 10;

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
    if (url.pathname === "/api/jobs" && request.method === "GET") {
      return jsonResponse({ jobs: await this.listJobs() });
    }
    if (url.pathname === "/api/jobs" && request.method === "POST") {
      return this.createJob(request);
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
    };
    server.serializeAttachment(attachment);
    this.ctx.acceptWebSocket(server, ["agents", `device:${deviceId}`]);
    const existingDevice = (await this.ctx.storage.get(`device:${deviceId}`)) || {};
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

  async listJobs() {
    const entries = await this.ctx.storage.list({ prefix: "job:", reverse: true, limit: 40 });
    return [...entries.values()].sort((a, b) => Number(b.createdAt || 0) - Number(a.createdAt || 0));
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

function isTerminal(status) {
  return ["completed", "failed", "cancelled"].includes(status);
}

function cleanText(value, maxLength) {
  return String(value || "").trim().slice(0, maxLength);
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
