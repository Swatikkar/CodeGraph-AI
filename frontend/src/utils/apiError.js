export function formatApiError(error, fallback = "Something went wrong.") {
  if (error?.code === "ECONNABORTED") {
    return "The request timed out before the server responded. Try a smaller ZIP or retry when the backend is awake.";
  }

  if (error?.message === "Network Error" && !error?.response) {
    return "Network error while contacting the backend. This usually happens when the upload is too large for the live demo host, the backend is waking up, or the connection was interrupted.";
  }

  const detail = error?.response?.data?.detail ?? error?.message;

  if (!detail) return fallback;
  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item?.msg) return item.msg;
        return JSON.stringify(item);
      })
      .filter(Boolean);
    return messages.length ? messages.join(" | ") : fallback;
  }

  if (typeof detail === "object") {
    return detail.msg || detail.message || JSON.stringify(detail);
  }

  return String(detail);
}
