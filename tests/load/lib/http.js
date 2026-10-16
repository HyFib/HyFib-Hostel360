// Requests the way the portal SPA and Frappe Desk make them.
import http from "k6/http";

import { BASE_URL, DESK_API_KEY, DESK_API_SECRET, LANGUAGE, SITE_HOST } from "./config.js";

const METHOD_PREFIX = "/api/method/";

function baseHeaders(extra = {}) {
  const headers = {
    Accept: "application/json",
    "Accept-Language": LANGUAGE,
    "X-Requested-With": "XMLHttpRequest",
    ...extra,
  };
  if (SITE_HOST) headers.Host = SITE_HOST;
  return headers;
}

function query(args) {
  const parts = Object.entries(args || {})
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([key, value]) => {
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return `${encodeURIComponent(key)}=${encodeURIComponent(text)}`;
    });
  return parts.length ? `?${parts.join("&")}` : "";
}

// Portal call: POST with a JSON body and the CSRF token (as the SPA does), or GET for the
// few methods it reads that way. tags.name groups the requests in the results.
export function portalCall(method, args, { csrf = "", get = false, flow = "portal" } = {}) {
  const url = `${BASE_URL}${METHOD_PREFIX}${method}`;
  const tags = { name: method.split(".").slice(-1)[0], flow };
  if (get) {
    return http.get(url + query(args), { headers: baseHeaders(), tags });
  }
  const headers = baseHeaders({ "Content-Type": "application/json" });
  if (csrf) headers["X-Frappe-CSRF-Token"] = csrf;
  return http.post(url, JSON.stringify(args || {}), { headers, tags });
}

// The portal's HTML shell (/hostel), rendered by Frappe on every full page load.
export function portalPage(path = "/hostel", flow = "portal") {
  return http.get(`${BASE_URL}${path}`, {
    headers: baseHeaders({ Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" }),
    tags: { name: "portal_page", flow },
  });
}

// Desk call with a staff user's API key (token authentication needs no CSRF token and no
// interactive two-factor sign-in). Form-encoded, as Frappe Desk sends it.
export function deskCall(method, args, { get = false, name = "", flow = "desk" } = {}) {
  const url = `${BASE_URL}${METHOD_PREFIX}${method}`;
  const headers = baseHeaders({ Authorization: `token ${DESK_API_KEY}:${DESK_API_SECRET}` });
  const tags = { name: name || method.split(".").slice(-1)[0], flow };
  if (get) {
    return http.get(url + query(args), { headers, tags });
  }
  const form = {};
  for (const [key, value] of Object.entries(args || {})) {
    if (value !== undefined && value !== null) form[key] = typeof value === "string" ? value : JSON.stringify(value);
  }
  return http.post(url, form, { headers, tags });
}

export function deskResource(doctype, args, name) {
  const url = `${BASE_URL}/api/resource/${encodeURIComponent(doctype)}${query(args)}`;
  const headers = baseHeaders({ Authorization: `token ${DESK_API_KEY}:${DESK_API_SECRET}` });
  return http.get(url, { headers, tags: { name, flow: "setup" } });
}

// {message: ...} of a JSON reply, or undefined.
export function message(response) {
  if (!response || response.status !== 200) return undefined;
  try {
    const body = response.json();
    return body && typeof body === "object" ? body.message : undefined;
  } catch (error) {
    return undefined;
  }
}
