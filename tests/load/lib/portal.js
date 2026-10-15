// A student on the portal: sign in with a one-time code once, then move between screens
// the way the SPA does, with a pause between screens.
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";
import exec from "k6/execution";
import { Counter } from "k6/metrics";

import { MOBILES_FILE, OTP_CHANNEL, OTP_CODE, masked, thinkTime } from "./config.js";
import { deskCall, deskResource, message, portalCall, portalPage } from "./http.js";

const AUTH = "hostel360.api.auth";
const PORTAL = "hostel360.api.portal";

export const signInFailures = new Counter("portal_sign_in_failures");
export const signIns = new Counter("portal_sign_ins");

// Numbers from MOBILES_FILE: loaded once in the init stage and shared by all VUs.
const fileMobiles = MOBILES_FILE
  ? new SharedArray("mobiles", () =>
      Array.from(
        new Set(
          open(MOBILES_FILE)
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => /^\d{10}$/.test(line)),
        ),
      ),
    )
  : null;

// Students who may sign in to the portal (tender 3.1): validated, living in the hostel, with a
// mobile number. Ordered so that user N always gets the same, distinct number.
const ELIGIBLE = [
  ["validation_status", "=", "Validated"],
  ["status", "=", "Active"],
  ["mobile_number", "is", "set"],
];

export function countMobiles() {
  if (fileMobiles) return fileMobiles.length;
  const response = deskCall(
    "frappe.client.get_count",
    { doctype: "Hostel Student", filters: ELIGIBLE },
    { get: true, name: "student_count", flow: "setup" },
  );
  const total = message(response);
  if (total === undefined) {
    throw new Error(`Counting students with the desk API key failed (HTTP ${response.status})`);
  }
  return Number(total) || 0;
}

// This user's number: line N of MOBILES_FILE, or the N-th eligible student read with the desk
// API key. It stays in this VU's memory only.
function mobileForThisUser() {
  const index = exec.vu.idInTest - 1;
  if (fileMobiles) return fileMobiles[index % fileMobiles.length];
  const response = deskResource(
    "Hostel Student",
    { fields: ["mobile_number"], filters: ELIGIBLE, order_by: "register_number asc", limit_start: index, limit_page_length: 1 },
    "student_number",
  );
  const rows = response.status === 200 ? response.json().data || [] : [];
  return rows.length ? String(rows[0].mobile_number || "").trim() : "";
}

// Per-VU state; the session cookie stays in the VU's cookie jar (noCookiesReset).
const state = { mobile: "", csrf: "", signedIn: false, failedSignIns: 0 };

// Screen mix of a typical visit (weights add up to 100).
const SCREENS = [
  { weight: 35, name: "home", run: home },
  { weight: 20, name: "fees", run: fees },
  { weight: 20, name: "menu", run: menu },
  { weight: 10, name: "leave", run: leave },
  { weight: 10, name: "notifications", run: notifications },
  { weight: 5, name: "reload", run: reload },
];

function pickScreen() {
  let roll = Math.random() * 100;
  for (const screen of SCREENS) {
    roll -= screen.weight;
    if (roll < 0) return screen;
  }
  return SCREENS[0];
}

function ok(response, what) {
  return check(response, { [`${what} answered 200`]: (r) => r.status === 200 && message(r) !== undefined });
}

function signIn(mobile, flow = "sign_in") {
  portalPage("/hostel/login", flow);

  const sent = portalCall(`${AUTH}.send_otp`, { mobile, channel: OTP_CHANNEL }, { flow });
  if (!check(sent, { "sign-in code requested": (r) => r.status === 200 && message(r) && message(r).sent === true })) {
    return false;
  }

  let verified = portalCall(`${AUTH}.verify_otp`, { mobile, otp: OTP_CODE }, { flow });
  let result = message(verified);
  // A number registered for both a student and a parent: the SPA asks, the test signs in as the student.
  if (result && result.ok === false && Array.isArray(result.choose) && result.choose.length) {
    const kind = result.choose.includes("student") ? "student" : result.choose[0];
    verified = portalCall(`${AUTH}.verify_otp`, { mobile, otp: OTP_CODE, kind }, { flow });
    result = message(verified);
  }
  if (!check(verified, { "sign-in code accepted": () => Boolean(result && result.ok === true) })) {
    return false;
  }

  const token = message(portalCall(`${AUTH}.get_csrf_token`, {}, { get: true, flow }));
  state.csrf = (token && token.csrf_token) || "";
  if (!check(state.csrf, { "CSRF token issued": (value) => value.length > 0 })) {
    return false;
  }

  const sessionResponse = portalCall(`${PORTAL}.get_session`, {}, { csrf: state.csrf, flow });
  const session = message(sessionResponse);
  if (!check(sessionResponse, { "portal session loaded": () => Boolean(session && session.kind) })) {
    return false;
  }

  // First sign-in, as the SPA walks it: accept the privacy notice, then confirm the details.
  if (session.consent_required && session.privacy_notice_version) {
    const accepted = portalCall(
      `${PORTAL}.accept_privacy_notice`,
      { version: session.privacy_notice_version },
      { csrf: state.csrf, flow },
    );
    if (!ok(accepted, "privacy notice accepted")) return false;
  }
  if (session.kind === "student" && !session.profile_confirmed) {
    ok(portalCall(`${PORTAL}.get_profile`, {}, { csrf: state.csrf, flow }), "profile loaded");
    ok(portalCall(`${PORTAL}.confirm_profile`, { updates: {} }, { csrf: state.csrf, flow }), "details confirmed");
  }
  return true;
}

function home() {
  ok(portalCall(`${PORTAL}.get_dashboard`, {}, { csrf: state.csrf }), "dashboard");
}

function fees() {
  ok(portalCall(`${PORTAL}.get_fees`, {}, { csrf: state.csrf }), "fees");
}

function menu() {
  ok(portalCall(`${PORTAL}.get_menu`, {}, { csrf: state.csrf }), "menu");
}

function leave() {
  ok(portalCall(`${PORTAL}.list_leaves`, {}, { csrf: state.csrf }), "leave list");
}

function notifications() {
  ok(portalCall(`${PORTAL}.list_notifications`, { limit: 20 }, { csrf: state.csrf }), "notifications");
}

// A full page load: the HTML shell, then the session the SPA reads at start-up.
function reload() {
  check(portalPage("/hostel"), { "portal page loaded": (r) => r.status === 200 });
  ok(portalCall(`${PORTAL}.get_session`, {}, { csrf: state.csrf }), "session");
}

export function portalUser() {
  if (!state.mobile) {
    // Each VU keeps its own number for the whole run: two sessions racing for one number's
    // code would only measure the resend cooldown.
    state.mobile = mobileForThisUser();
    if (!check(state.mobile, { "student number found": (value) => /^\d{10}$/.test(value) })) {
      signInFailures.add(1);
      state.mobile = "";
      sleep(60);
      return;
    }
  }

  if (!state.signedIn) {
    if (signIn(state.mobile)) {
      state.signedIn = true;
      state.failedSignIns = 0;
      signIns.add(1);
    } else {
      state.failedSignIns += 1;
      signInFailures.add(1);
      if (state.failedSignIns >= 3) {
        console.warn(`VU ${exec.vu.idInTest}: sign-in for ${masked(state.mobile)} failed three times; this user stops`);
        sleep(3600);
      } else {
        sleep(30);
      }
      return;
    }
    sleep(thinkTime());
  }

  pickScreen().run();
  sleep(thinkTime());
}
