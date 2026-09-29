import axios from "axios";

export function storeAuthToken(activeToken, storage = globalThis.localStorage, client = axios) {
  if (activeToken) {
    storage.setItem("token", activeToken);
    client.defaults.headers.common.Authorization = `Bearer ${activeToken}`;
    return activeToken;
  }

  storage.removeItem("token");
  delete client.defaults.headers.common.Authorization;
  return null;
}

export function getAuthHeaders(storage = globalThis.localStorage) {
  const activeToken = storage.getItem("token");
  return activeToken
    ? { headers: { Authorization: `Bearer ${activeToken}` } }
    : { headers: {} };
}
