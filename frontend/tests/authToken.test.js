import assert from "node:assert/strict";
import test from "node:test";

import { getAuthHeaders, isAuthenticationFailure } from "../src/utils/authToken.js";

test("authenticated API requests include the HttpOnly session cookie", () => {
  assert.deepEqual(getAuthHeaders(), { withCredentials: true });
});

test("only HTTP 401 responses expire the frontend session", () => {
  assert.equal(isAuthenticationFailure({ response: { status: 401 } }), true);
  assert.equal(isAuthenticationFailure({ response: { status: 403 } }), false);
  assert.equal(isAuthenticationFailure(new Error("network unavailable")), false);
});
