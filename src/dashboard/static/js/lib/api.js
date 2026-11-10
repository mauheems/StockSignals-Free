// All server calls go through here, so errors reach pages as readable messages.

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("Can't reach the dashboard server. Is it still running?");
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    /* handled below: a failed response without JSON still gets a message */
  }
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `The server answered with an error (${response.status}).`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return body;
}

export const api = {
  get: (path) => request(path),
  post: (path, body = {}) =>
    request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  patch: (path, body = {}) =>
    request(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  del: (path) => request(path, { method: "DELETE" }),
};

export function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
