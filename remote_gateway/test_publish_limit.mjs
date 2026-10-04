import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { ControlRoom } from "./src/worker.js";

globalThis.crypto ??= webcrypto;

const records = new Map();
const room = new ControlRoom({
  storage: {
    async transaction(callback) {
      return callback({
        get: async (key) => records.get(key),
        put: async (key, value) => records.set(key, value),
      });
    },
  },
}, {});

async function act(deviceId, action, localCount, reservationId = "", key = "a".repeat(64), reservationDate = "") {
  const response = await room.updatePublishLimit(new Request("https://example.test/api/publish-limit", {
    method: "POST",
    headers: { "X-Blog-Helper-Device-Id": deviceId },
    body: JSON.stringify({ key, action, limit: 15, localCount, reservationId, reservationDate }),
  }));
  return { status: response.status, ...(await response.json()) };
}

let result = await act("mom", "status", 8);
assert.equal(result.published, 8);
result = await act("me", "status", 5);
assert.equal(result.published, 13);
result = await act("mom", "status", 9);
assert.equal(result.published, 13, "the same device baseline is applied only once");

const first = await act("mom", "reserve", 9);
const second = await act("me", "reserve", 5);
assert.equal(first.remaining, 1);
assert.equal(second.remaining, 0);
result = await act("mom", "reserve", 9);
assert.equal(result.status, 409, "the 16th simultaneous attempt is refused");
assert.equal(result.pending, 2);

result = await act("me", "cancel", 5, second.reservationId, "a".repeat(64), second.date);
assert.equal(result.remaining, 1);
result = await act("mom", "commit", 9, first.reservationId, "a".repeat(64), first.date);
assert.equal(result.published, 14);
result = await act("me", "reserve", 5);
assert.equal(result.remaining, 0);

result = await act("mom", "status", 0, "", "b".repeat(64));
assert.equal(result.published, 0, "another domain has an independent counter");
console.log("shared Tistory publish limit tests passed");
