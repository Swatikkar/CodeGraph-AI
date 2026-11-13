export function getAuthHeaders() {
  return { withCredentials: true };
}

export function isAuthenticationFailure(error) {
  return error?.response?.status === 401;
}
