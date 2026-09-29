import assert from "node:assert/strict";
import test from "node:test";

import { getAuthHeaders, storeAuthToken } from "../src/utils/authToken.js";

function createStorage(initialToken = null) {
  const values = new Map(initialToken ? [["token", initialToken]] : []);
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test("a refreshed session token replaces the stale request token", () => {
  const storage = createStorage("expired-token");
  const client = { defaults: { headers: { common: {} } } };

  storeAuthToken("refreshed-token", storage, client);

  assert.deepEqual(getAuthHeaders(storage), {
    headers: { Authorization: "Bearer refreshed-token" },
  });
  assert.equal(client.defaults.headers.common.Authorization, "Bearer refreshed-token");
});

test("clearing a session removes both persisted and default authorization", () => {
  const storage = createStorage("active-token");
  const client = {
    defaults: { headers: { common: { Authorization: "Bearer active-token" } } },
  };

  storeAuthToken(null, storage, client);

  assert.deepEqual(getAuthHeaders(storage), { headers: {} });
  assert.equal(client.defaults.headers.common.Authorization, undefined);
});
