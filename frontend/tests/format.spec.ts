import { describe, expect, it } from "vitest";
import { loadLocale, locale } from "@/i18n";
import {
	formatDate,
	formatDateRange,
	formatDateTime,
	formatINR,
	formatMobile,
	fromDatetimeLocal,
	inclusiveDays,
	normalizeMobile,
	parseDate,
} from "@/utils/format";

describe("formatINR", () => {
	it("uses Indian digit grouping", () => {
		expect(formatINR(1234567.5)).toBe("₹12,34,567.50");
		expect(formatINR(100000)).toBe("₹1,00,000");
		expect(formatINR(12345678)).toBe("₹1,23,45,678");
		expect(formatINR(1000)).toBe("₹1,000");
		expect(formatINR(999)).toBe("₹999");
	});

	it("drops .00 for whole rupees unless asked to keep paise", () => {
		expect(formatINR(2500)).toBe("₹2,500");
		expect(formatINR(2500, "always")).toBe("₹2,500.00");
		expect(formatINR(2500.4, "never")).toBe("₹2,500");
	});

	it("handles empty, string and negative values", () => {
		expect(formatINR(null)).toBe("₹0");
		expect(formatINR(undefined)).toBe("₹0");
		expect(formatINR("1,500.25")).toBe("₹1,500.25");
		expect(formatINR(-2500)).toBe("−₹2,500");
		expect(formatINR("not a number")).toBe("₹0");
	});

	it("rounds to paise without floating point drift", () => {
		expect(formatINR(0.1 + 0.2, "always")).toBe("₹0.30");
		expect(formatINR(1999.995, "always")).toBe("₹2,000.00");
	});
});

describe("dates", () => {
	it("formats dates as DD MMM YYYY", () => {
		expect(formatDate("2026-10-15")).toBe("15 Oct 2026");
		expect(formatDate("2026-01-05")).toBe("05 Jan 2026");
	});

	it("formats Frappe datetimes on a 12-hour clock without shifting the time zone", () => {
		expect(formatDateTime("2026-10-15 18:30:00.000000")).toBe("15 Oct 2026, 6:30 PM");
		expect(formatDateTime("2026-10-15 00:05:00")).toBe("15 Oct 2026, 12:05 AM");
		expect(formatDateTime("2026-10-15 12:00:00")).toBe("15 Oct 2026, 12:00 PM");
	});

	it("rejects missing and impossible dates", () => {
		expect(formatDate(null)).toBe("—");
		expect(formatDate("")).toBe("—");
		expect(formatDate("2026-02-31")).toBe("—");
		expect(parseDate("31/12/2026")).toBeNull();
	});

	it("collapses the year in a range within one year", () => {
		expect(formatDateRange("2026-10-03", "2026-10-05")).toBe("03 Oct – 05 Oct 2026");
		expect(formatDateRange("2026-12-30", "2027-01-02")).toBe("30 Dec 2026 – 02 Jan 2027");
	});

	it("uses Tamil month names in Tamil", async () => {
		await loadLocale("ta");
		locale.value = "ta";
		expect(formatDate("2026-10-15")).toBe("15 அக். 2026");
		expect(formatDateTime("2026-10-15 09:15:00")).toBe("15 அக். 2026, 9:15 முற்பகல்");
	});

	it("counts days inclusively", () => {
		expect(inclusiveDays("2026-10-03", "2026-10-05")).toBe(3);
		expect(inclusiveDays("2026-10-03", "2026-10-03")).toBe(1);
		expect(inclusiveDays("2026-10-05", "2026-10-03")).toBe(0);
		expect(inclusiveDays("2026-10-31 18:00:00", "2026-11-02 09:00:00")).toBe(3);
	});

	it("converts datetime-local values to Frappe's format", () => {
		expect(fromDatetimeLocal("2026-10-15T18:30")).toBe("2026-10-15 18:30:00");
		expect(fromDatetimeLocal("")).toBe("");
	});
});

describe("mobile numbers", () => {
	it("normalises common ways of writing an Indian mobile number", () => {
		expect(normalizeMobile("98765 43210")).toBe("9876543210");
		expect(normalizeMobile("+91 98765-43210")).toBe("9876543210");
		expect(normalizeMobile("09876543210")).toBe("9876543210");
	});

	it("rejects numbers that are not Indian mobiles", () => {
		expect(normalizeMobile("12345")).toBe("");
		expect(normalizeMobile("5876543210")).toBe("");
		expect(normalizeMobile("")).toBe("");
	});

	it("groups ten digits for display", () => {
		expect(formatMobile("9876543210")).toBe("98765 43210");
	});
});
