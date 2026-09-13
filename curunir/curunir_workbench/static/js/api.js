// API client. The bearer token lives in sessionStorage; errors carry the server's detail.

export class ApiError extends Error {
  constructor(status, detail, payload) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.payload = payload;
  }
}

export function token() { return sessionStorage.getItem("curunir-token") || ""; }
export function setToken(value) { sessionStorage.setItem("curunir-token", value); }
export function clearToken() { sessionStorage.removeItem("curunir-token"); }

// Every request names its client, so the harness can tell a UI action from a
// scripted one.
export const CLIENT = "workbench-ui";

async function request(method, path, body) {
  const headers = { "Authorization": `Bearer ${token()}`, "X-Curunir-Client": CLIENT };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, {
      method, headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(0, `backend unreachable: ${err.message}`, null);
  }
  let payload = null;
  const text = await response.text();
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!response.ok) {
    const detail = payload && payload.detail !== undefined ? payload.detail : response.statusText;
    throw new ApiError(response.status, detail, payload);
  }
  return payload;
}

export const get = (path) => request("GET", path);
export const post = (path, body) => request("POST", path, body ?? {});
