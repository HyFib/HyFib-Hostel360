// Settings for the Hostel360 load test, all from environment variables (see ../README.md).

function text(name, fallback = "") {
  const value = __ENV[name];
  return value === undefined || value === null || value === "" ? fallback : String(value).trim();
}

function int(name, fallback) {
  const value = text(name);
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be a whole number, not "${value}"`);
  }
  return parsed;
}

function seconds(name, fallback) {
  const value = text(name);
  if (!value) return fallback;
  const match = /^(\d+)(s|m|h)?$/.exec(value);
  if (!match) throw new Error(`${name} must be a duration such as 90s, 15m or 1h, not "${value}"`);
  return Number(match[1]) * { s: 1, m: 60, h: 3600 }[match[2] || "s"];
}

export const BASE_URL = text("BASE_URL").replace(/\/+$/, "");
// Sent as the Host header when BASE_URL addresses a server by IP or by another name.
export const SITE_HOST = text("SITE_HOST");
export const PROFILE = text("PROFILE", "smoke");
// The fixed sign-in code of a staging site (developer_mode and hostel360_fixed_otp).
export const OTP_CODE = text("OTP_CODE");
export const OTP_CHANNEL = text("OTP_CHANNEL", "sms");
// One 10-digit mobile number per line; otherwise numbers are read with the desk API key.
export const MOBILES_FILE = text("MOBILES_FILE");
export const DESK_API_KEY = text("DESK_API_KEY");
export const DESK_API_SECRET = text("DESK_API_SECRET");
export const LANGUAGE = text("PORTAL_LANGUAGE", "en-IN, en;q=0.9");

// Load shapes. "load" is the acceptance profile: 1,500 concurrent portal users for 15 minutes
// at about 150 requests a second (tender TR 15 / design section 10), with desk staff working
// alongside. "smoke" is a functional check of the same journeys with a handful of users.
const PROFILES = {
  smoke: {
    portalVus: 5,
    rampUp: 15,
    hold: 60,
    rampDown: 10,
    deskVus: 1,
    thinkMin: 1,
    thinkMax: 3,
    minPeakRate: 0,
  },
  load: {
    portalVus: 1500,
    // Users arrive over ten minutes, as they would after an announcement: 300 in the first
    // two, the rest over the next eight, so sign-ins peak near 3 a second.
    rampUp: 600,
    hold: 900,
    rampDown: 180,
    deskVus: 40,
    // A screen every 8-14 s per user: about 1,500 / 11 s = 136 screens a second, which with
    // the occasional second call per screen and the desk staff is roughly 150 requests a second.
    thinkMin: 8,
    thinkMax: 14,
    minPeakRate: 140,
  },
};

const base = PROFILES[PROFILE];
if (!base) {
  throw new Error(`PROFILE must be one of ${Object.keys(PROFILES).join(", ")}, not "${PROFILE}"`);
}

export const SHAPE = {
  portalVus: int("PORTAL_VUS", base.portalVus),
  rampUp: seconds("RAMP_UP", base.rampUp),
  hold: seconds("HOLD", base.hold),
  rampDown: seconds("RAMP_DOWN", base.rampDown),
  deskVus: int("DESK_VUS", base.deskVus),
  thinkMin: int("THINK_MIN", base.thinkMin),
  thinkMax: int("THINK_MAX", base.thinkMax),
  minPeakRate: int("MIN_PEAK_RATE", base.minPeakRate),
};

if (SHAPE.thinkMax < SHAPE.thinkMin) {
  throw new Error("THINK_MAX must not be less than THINK_MIN");
}

export function checkSettings() {
  const missing = [];
  if (!BASE_URL) missing.push("BASE_URL (for example https://staging.hostel.example.ac.in)");
  if (SHAPE.portalVus > 0 && !OTP_CODE) missing.push("OTP_CODE (the staging site's fixed sign-in code)");
  if (SHAPE.portalVus > 0 && !MOBILES_FILE && !(DESK_API_KEY && DESK_API_SECRET)) {
    missing.push("MOBILES_FILE, or DESK_API_KEY and DESK_API_SECRET to read the numbers");
  }
  if (SHAPE.deskVus > 0 && !(DESK_API_KEY && DESK_API_SECRET)) {
    missing.push("DESK_API_KEY and DESK_API_SECRET (or DESK_VUS=0)");
  }
  if (missing.length) {
    throw new Error(`Missing settings: ${missing.join("; ")}`);
  }
  if (!/^https?:\/\//.test(BASE_URL)) {
    throw new Error("BASE_URL must start with http:// or https://");
  }
}

export function thinkTime() {
  return SHAPE.thinkMin + Math.random() * (SHAPE.thinkMax - SHAPE.thinkMin);
}

// Mask all but the last two digits wherever a number has to be mentioned.
export function masked(mobile) {
  const digits = String(mobile || "");
  return digits.length > 2 ? `${"x".repeat(digits.length - 2)}${digits.slice(-2)}` : "xx";
}
