export function formatApiError(error, fallback = "Something went wrong.") {
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
