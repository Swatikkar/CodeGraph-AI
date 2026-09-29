import assert from "node:assert/strict";
import test from "node:test";

import { getAuthHeaders } from "../src/utils/authToken.js";

test("authenticated API requests include the HttpOnly session cookie", () => {
  assert.deepEqual(getAuthHeaders(), { withCredentials: true });
});
