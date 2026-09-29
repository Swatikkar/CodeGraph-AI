const configuredApiUrl = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

// Production traffic stays on the frontend origin and is forwarded by vercel.json.
export const API_URL = import.meta.env.PROD ? "/api" : configuredApiUrl.replace(/\/$/, "");
