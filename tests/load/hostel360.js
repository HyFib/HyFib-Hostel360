// HyFib Hostel360 load test: students on the portal and office staff on the desk together.
//
//   PROFILE=smoke | load (default smoke); see README.md for every setting and for how to
//   prepare a staging site. Run it with ./run.sh, which keeps the results.
//
// Pass/fail (thresholds): 95 % of requests under 800 ms and fewer than 1 % failed, overall
// and for each of sign-in, portal screens and desk lists; with PROFILE=load also at least
// MIN_PEAK_RATE requests a second sustained while all users are on line.
import exec from "k6/execution";

import { DESK_API_KEY, PROFILE, SHAPE, checkSettings } from "./lib/config.js";
import { deskUser } from "./lib/desk.js";
import { deskResource } from "./lib/http.js";
import { countMobiles, portalUser } from "./lib/portal.js";

checkSettings();

const holdStart = SHAPE.rampUp;
const holdEnd = SHAPE.rampUp + SHAPE.hold;
const total = SHAPE.rampUp + SHAPE.hold + SHAPE.rampDown;

function portalStages() {
  if (PROFILE === "load" && SHAPE.portalVus >= 1500) {
    const early = Math.round(SHAPE.portalVus / 5);
    return [
      { duration: `${Math.round(SHAPE.rampUp / 5)}s`, target: early },
      { duration: `${SHAPE.rampUp - Math.round(SHAPE.rampUp / 5)}s`, target: SHAPE.portalVus },
      { duration: `${SHAPE.hold}s`, target: SHAPE.portalVus },
      { duration: `${SHAPE.rampDown}s`, target: 0 },
    ];
  }
  return [
    { duration: `${SHAPE.rampUp}s`, target: SHAPE.portalVus },
    { duration: `${SHAPE.hold}s`, target: SHAPE.portalVus },
    { duration: `${SHAPE.rampDown}s`, target: 0 },
  ];
}

const scenarios = {};
if (SHAPE.portalVus > 0) {
  scenarios.portal = {
    executor: "ramping-vus",
    exec: "portal",
    startVUs: 0,
    stages: portalStages(),
    gracefulRampDown: "30s",
    gracefulStop: "30s",
  };
}
if (SHAPE.deskVus > 0) {
  scenarios.desk = {
    executor: "constant-vus",
    exec: "desk",
    vus: SHAPE.deskVus,
    duration: `${total}s`,
    gracefulStop: "30s",
  };
}

const thresholds = {
  http_req_failed: ["rate<0.01"],
  http_req_duration: ["p(95)<800"],
  checks: ["rate>0.99"],
  // Under 1 % of the users may fail to sign in (the smoke profile tolerates none).
  portal_sign_in_failures: [`count<${Math.max(1, Math.ceil(SHAPE.portalVus * 0.01))}`],
  "http_req_duration{flow:sign_in}": ["p(95)<800"],
  "http_req_failed{flow:sign_in}": ["rate<0.01"],
  "http_req_duration{flow:portal}": ["p(95)<800"],
  "http_req_failed{flow:portal}": ["rate<0.01"],
};
if (SHAPE.deskVus > 0) {
  thresholds["http_req_duration{flow:desk}"] = ["p(95)<800"];
  thresholds["http_req_failed{flow:desk}"] = ["rate<0.01"];
}
if (SHAPE.minPeakRate > 0) {
  // Requests made while every user is on line (the hold stage, less a minute at each end).
  const window = Math.max(SHAPE.hold - 120, 60);
  thresholds["http_reqs{phase:peak}"] = [`count>=${SHAPE.minPeakRate * window}`];
}

export const options = {
  scenarios,
  thresholds,
  // Each VU is one browser: the session cookie must survive from one iteration to the next.
  noCookiesReset: true,
  setupTimeout: "180s",
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
  userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36 Hostel360-load-test",
  tags: { profile: PROFILE },
};

// Student numbers are never handed from setup() to the users: k6 copies setup data into its
// summary export, and result files are kept. setup() only checks there are enough of them;
// each user reads its own number on its first iteration (see lib/portal.js).
export function setup() {
  if (SHAPE.portalVus > 0) {
    const available = countMobiles();
    if (!available) {
      throw new Error("No portal-eligible students (validated, Active, with a mobile number) found");
    }
    if (available < SHAPE.portalVus) {
      // Users sharing a number would be refused by the per-number resend cooldown and measure
      // that instead of the system.
      throw new Error(
        `${SHAPE.portalVus} portal users need as many distinct student numbers; only ${available} found. ` +
          "Prepare more validated students on the staging site or lower PORTAL_VUS.",
      );
    }
  }
  if (SHAPE.deskVus > 0 && DESK_API_KEY) {
    // Fail before the run, not 40 users into it, when the key is wrong or lacks permission.
    const probe = deskResource("Room Allotment", { fields: ["name"], limit_page_length: 1 }, "desk_probe");
    if (probe.status !== 200) {
      throw new Error(`The desk API key cannot read Room Allotment (HTTP ${probe.status}); use a Hostel Office Staff user's key`);
    }
  }
  console.log(
    `profile ${PROFILE}: ${SHAPE.portalVus} portal users, ${SHAPE.deskVus} desk users, ` +
      `ramp ${SHAPE.rampUp}s, hold ${SHAPE.hold}s, ramp-down ${SHAPE.rampDown}s, think ${SHAPE.thinkMin}-${SHAPE.thinkMax}s`,
  );
  return {};
}

// Requests in the middle of the hold stage are tagged phase=peak for the throughput threshold.
function tagPhase() {
  const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
  const inPeak = elapsed >= holdStart + 60 && elapsed <= holdEnd - 60;
  exec.vu.metrics.tags.phase = inPeak ? "peak" : "ramp";
}

export function portal() {
  tagPhase();
  portalUser();
}

export function desk() {
  tagPhase();
  deskUser();
}
