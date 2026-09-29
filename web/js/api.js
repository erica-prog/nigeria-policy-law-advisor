// Thin client for docs/contracts/web-api.md. One error path: every non-2xx
// response becomes an ApiError with the server's code and message. The
// session cookie is HttpOnly; this module never sees or stores a secret.

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details || [];
  }
}

async function request(method, path, { json, form } = {}) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form !== undefined) {
    init.body = form;
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the server.");
  }
  if (response.status === 204) return null;
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const error = (body && body.error) || {};
    throw new ApiError(
      response.status,
      error.code || "error",
      error.message || "Request failed.",
      error.details,
    );
  }
  return body;
}

export const api = {
  health: () => request("GET", "/api/health"),
  me: () => request("GET", "/api/me"),
  login: (username, password) => request("POST", "/api/auth/login", { json: { username, password } }),
  logout: () => request("POST", "/api/auth/logout"),
  matters: () => request("GET", "/api/matters"),
  // No id: the server generates one. The user only ever sees the title.
  createMatter: (body = {}) => request("POST", "/api/matters", { json: body }),
  matter: (id) => request("GET", `/api/matters/${encodeURIComponent(id)}`),
  documents: (id) => request("GET", `/api/matters/${encodeURIComponent(id)}/documents`),
  upload: (id, file, jurisdiction) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (jurisdiction) form.append("jurisdiction", jurisdiction);
    return request("POST", `/api/matters/${encodeURIComponent(id)}/documents`, { form });
  },
  removeDocument: (id, name) =>
    request("DELETE", `/api/matters/${encodeURIComponent(id)}/documents/${encodeURIComponent(name)}`),
  ask: (id, body) => request("POST", `/api/matters/${encodeURIComponent(id)}/ask`, { json: body }),
  analyze: (id, body) => request("POST", `/api/matters/${encodeURIComponent(id)}/analyze`, { json: body }),
  chat: (id, body) => request("POST", `/api/matters/${encodeURIComponent(id)}/chat`, { json: body }),
  chatHistory: (id) => request("GET", `/api/matters/${encodeURIComponent(id)}/chat`),
};
