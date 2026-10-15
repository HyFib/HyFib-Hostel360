/**
 * Money and date formatting for an Indian audience.
 *
 * Amounts use Indian digit grouping (₹12,34,567.50) and dates read "15 Oct 2026". Frappe
 * sends dates as "YYYY-MM-DD" and datetimes as "YYYY-MM-DD HH:mm:ss[.ffffff]" in the site's
 * time zone (Asia/Kolkata); they are parsed as local wall-clock values and never shifted,
 * so a leave starting at 6 pm shows 6 pm on every phone.
 */
import { t } from "@/i18n";

type Amount = number | string | null | undefined;
export type PaiseMode = "auto" | "always" | "never";

function groupIndian(whole: string): string {
	if (whole.length <= 3) return whole;
	const tail = whole.slice(-3);
	let head = whole.slice(0, -3);
	const groups: string[] = [];
	while (head.length > 2) {
		groups.unshift(head.slice(-2));
		head = head.slice(0, -2);
	}
	if (head) groups.unshift(head);
	return [...groups, tail].join(",");
}

export function toNumber(value: Amount): number {
	if (value === null || value === undefined || value === "") return 0;
	const number = typeof value === "number" ? value : Number(String(value).replace(/[₹,\s]/g, ""));
	return Number.isFinite(number) ? number : 0;
}

/**
 * Format an amount in rupees. `paise: "auto"` drops ".00" for whole rupees, which reads
 * better on cards; tables that line amounts up use "always".
 */
export function formatINR(value: Amount, paise: PaiseMode = "auto"): string {
	const amount = Math.round(toNumber(value) * 100) / 100;
	const negative = amount < 0;
	const [whole, fraction] = Math.abs(amount).toFixed(2).split(".");
	const showPaise = paise === "always" || (paise === "auto" && fraction !== "00");
	const text = `₹${groupIndian(whole)}${showPaise ? `.${fraction}` : ""}`;
	return negative ? `−${text}` : text;
}

const DATE_PART = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?/;

/** Parse a Frappe date/datetime string (or a Date) as a local wall-clock value. */
export function parseDate(value: string | Date | null | undefined): Date | null {
	if (!value) return null;
	if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
	const match = DATE_PART.exec(String(value).trim());
	if (!match) return null;
	const [, y, m, d, hh = "0", mm = "0", ss = "0"] = match;
	const date = new Date(Number(y), Number(m) - 1, Number(d), Number(hh), Number(mm), Number(ss));
	// Reject impossible dates such as 2026-02-31 instead of rolling them over.
	if (date.getMonth() !== Number(m) - 1 || date.getDate() !== Number(d)) return null;
	return date;
}

function pad(value: number): string {
	return String(value).padStart(2, "0");
}

function monthName(index: number): string {
	return t(`date.months.${index + 1}`);
}

/** "15 Oct 2026" */
export function formatDate(value: string | Date | null | undefined, fallback = "—"): string {
	const date = parseDate(value);
	if (!date) return fallback;
	return `${pad(date.getDate())} ${monthName(date.getMonth())} ${date.getFullYear()}`;
}

/** "6:30 PM" (12-hour clock, as used across India) */
export function formatTime(value: string | Date | null | undefined, fallback = "—"): string {
	const date = parseDate(value);
	if (!date) return fallback;
	const hours = date.getHours();
	const suffix = hours < 12 ? t("date.am") : t("date.pm");
	const twelve = hours % 12 === 0 ? 12 : hours % 12;
	return `${twelve}:${pad(date.getMinutes())} ${suffix}`;
}

/** "15 Oct 2026, 6:30 PM" */
export function formatDateTime(value: string | Date | null | undefined, fallback = "—"): string {
	const date = parseDate(value);
	if (!date) return fallback;
	return `${formatDate(date)}, ${formatTime(date)}`;
}

/** "15 Oct – 18 Oct 2026", collapsing the repeated year. */
export function formatDateRange(from: string | Date | null | undefined, to: string | Date | null | undefined): string {
	const start = parseDate(from);
	const end = parseDate(to);
	if (!start || !end) return formatDate(start ?? end);
	if (start.getFullYear() === end.getFullYear()) {
		const left = `${pad(start.getDate())} ${monthName(start.getMonth())}`;
		return `${left} – ${formatDate(end)}`;
	}
	return `${formatDate(start)} – ${formatDate(end)}`;
}

/** "YYYY-MM-DD" in local time, for <input type="date"> and API calls. */
export function toISODate(date: Date): string {
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Value for <input type="datetime-local"> ("YYYY-MM-DDTHH:mm"). */
export function toDatetimeLocal(date: Date): string {
	return `${toISODate(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** "YYYY-MM-DDTHH:mm" from a datetime-local input to Frappe's "YYYY-MM-DD HH:mm:ss". */
export function fromDatetimeLocal(value: string): string {
	const date = parseDate(value);
	return date ? `${toISODate(date)} ${pad(date.getHours())}:${pad(date.getMinutes())}:00` : "";
}

/** Whole days between two dates, counting both ends (a 3–5 Oct absence is 3 days). */
export function inclusiveDays(from: string | Date | null | undefined, to: string | Date | null | undefined): number {
	const start = parseDate(from);
	const end = parseDate(to);
	if (!start || !end) return 0;
	const startDay = Date.UTC(start.getFullYear(), start.getMonth(), start.getDate());
	const endDay = Date.UTC(end.getFullYear(), end.getMonth(), end.getDate());
	const days = Math.round((endDay - startDay) / 86_400_000) + 1;
	return days > 0 ? days : 0;
}

/** The hostel's time zone; the server sends it in the session settings. */
export const SITE_TIME_ZONE = "Asia/Kolkata";

/**
 * The hostel's wall-clock time now, as a Date whose local fields read like a clock in
 * `timeZone` (the way parseDate reads every server value). Leave and mess dates are site
 * times, so a phone set to another zone must compare against this, not its own clock.
 */
export function siteNow(timeZone: string = SITE_TIME_ZONE, at: Date = new Date()): Date {
	try {
		const parts = Object.fromEntries(
			new Intl.DateTimeFormat("en-CA", {
				timeZone,
				hourCycle: "h23",
				year: "numeric",
				month: "2-digit",
				day: "2-digit",
				hour: "2-digit",
				minute: "2-digit",
				second: "2-digit",
			})
				.formatToParts(at)
				.map((part) => [part.type, part.value]),
		);
		return new Date(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
	} catch {
		// An unknown zone name: the device clock is the best remaining guess.
		return new Date(at.getTime());
	}
}

/** True when the device's clock reads differently from the hostel's (a phone abroad). */
export function deviceOffSiteTime(timeZone: string = SITE_TIME_ZONE, at: Date = new Date()): boolean {
	return Math.abs(siteNow(timeZone, at).getTime() - at.getTime()) > 60_000;
}

/**
 * Full mess days missed during a leave, by the server's rule (Home Visit Leave): the day of
 * leaving counts only when leaving before `cutoffHour`, the day of return only when returning
 * at or after it. A Friday 6 pm to Monday 10 am leave is 2 mess days (Saturday, Sunday).
 */
export function messDaysAway(
	from: string | Date | null | undefined,
	to: string | Date | null | undefined,
	cutoffHour = 12,
): number {
	const start = parseDate(from);
	const end = parseDate(to);
	if (!start || !end || end.getTime() <= start.getTime()) return 0;
	const first = Date.UTC(
		start.getFullYear(),
		start.getMonth(),
		start.getDate() + (start.getHours() >= cutoffHour ? 1 : 0),
	);
	const last = Date.UTC(end.getFullYear(), end.getMonth(), end.getDate() - (end.getHours() < cutoffHour ? 1 : 0));
	const days = Math.round((last - first) / 86_400_000) + 1;
	return days > 0 ? days : 0;
}

/** "YYYY-MM" for the current month (feedback period). */
export function currentPeriod(now = new Date()): string {
	return `${now.getFullYear()}-${pad(now.getMonth() + 1)}`;
}

/** Morning / afternoon / evening greeting key for the hour. */
export function greetingKey(now = new Date()): "morning" | "afternoon" | "evening" {
	const hour = now.getHours();
	if (hour < 12) return "morning";
	if (hour < 17) return "afternoon";
	return "evening";
}

/** Indian mobile number shown as "98765 43210". */
export function formatMobile(value: string | null | undefined): string {
	const digits = String(value || "")
		.replace(/\D/g, "")
		.slice(-10);
	return digits.length === 10 ? `${digits.slice(0, 5)} ${digits.slice(5)}` : String(value || "");
}

/** Normalise user input to a 10-digit Indian mobile number, or "" when it is not one. */
export function normalizeMobile(value: string | null | undefined): string {
	let digits = String(value || "").replace(/\D/g, "");
	if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
	if (digits.length === 11 && digits.startsWith("0")) digits = digits.slice(1);
	return /^[6-9]\d{9}$/.test(digits) ? digits : "";
}

/** Human file size, e.g. "2.4 MB". */
export function formatBytes(bytes: number): string {
	if (!Number.isFinite(bytes) || bytes <= 0) return "0 KB";
	if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
