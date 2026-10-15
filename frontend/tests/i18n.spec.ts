import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { t, tc, tv } from "@/i18n";
import en from "@/i18n/en.json";
import ta from "@/i18n/ta.json";

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
	const out: Record<string, string> = {};
	for (const [key, value] of Object.entries(tree)) {
		const path = prefix ? `${prefix}.${key}` : key;
		if (typeof value === "string") out[path] = value;
		else Object.assign(out, flatten(value, path));
	}
	return out;
}

const english = flatten(en as Tree);
const tamil = flatten(ta as Tree);
const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();

function sourceFiles(dir: string): string[] {
	return readdirSync(dir).flatMap((name) => {
		const path = join(dir, name);
		if (statSync(path).isDirectory()) return sourceFiles(path);
		return /\.(ts|vue)$/.test(name) ? [path] : [];
	});
}

describe("translations", () => {
	it("have exactly the same keys in English and Tamil", () => {
		expect(Object.keys(tamil).sort()).toEqual(Object.keys(english).sort());
	});

	it("have no empty strings", () => {
		for (const [key, value] of [...Object.entries(english), ...Object.entries(tamil)]) {
			expect(value.trim(), key).not.toBe("");
		}
	});

	it("use the same placeholders in both languages", () => {
		for (const key of Object.keys(english)) {
			expect(placeholders(tamil[key]), key).toEqual(placeholders(english[key]));
		}
	});

	it("are written in Tamil script, not left in English", () => {
		const untranslated = Object.keys(english).filter(
			(key) =>
				tamil[key] === english[key] && /[a-z]{4,}/i.test(english[key]) && !/^[A-Z0-9 /().,-]+$/.test(english[key]),
		);
		// Brand and product names (Hostel360, SMS, WhatsApp, UPI) are the only allowed exceptions.
		expect(
			untranslated.every((key) => /^(app\.name|login\.(sms|whatsapp)|paymentModes\.UPI)$/.test(key)),
			untranslated.join(", "),
		).toBe(true);
	});

	it("cover every key the source code uses", () => {
		// Vitest runs from the frontend directory.
		const srcDir = join(process.cwd(), "src");
		const missing: string[] = [];
		for (const file of sourceFiles(srcDir)) {
			const text = readFileSync(file, "utf8");
			for (const match of text.matchAll(/\bt\(\s*["']([a-zA-Z][\w.]+)["']/g)) {
				if (!(match[1] in english)) missing.push(`${match[1]} (${file.split("/src/")[1]})`);
			}
			for (const match of text.matchAll(/\btc\(\s*["']([a-zA-Z][\w.]+)["']/g)) {
				for (const form of ["one", "other"])
					if (!(`${match[1]}.${form}` in english)) missing.push(`${match[1]}.${form}`);
			}
			for (const match of text.matchAll(/(?:label|title): "([a-z][a-zA-Z]+\.[a-zA-Z.]+)"/g)) {
				if (!(match[1] in english)) missing.push(`${match[1]} (${file.split("/src/")[1]})`);
			}
		}
		expect(missing).toEqual([]);
	});

	it("interpolate parameters and fall back sensibly", () => {
		expect(t("fees.payTotal", { amount: "₹1,500" })).toBe("Pay ₹1,500");
		expect(tc("common.days", 1)).toBe("1 day");
		expect(tc("common.days", 4)).toBe("4 days");
		expect(t("no.such.key")).toBe("no.such.key");
		expect(tv("status", "Pending Parent Approval")).toBe("Waiting for parent");
		expect(tv("status", "Some New Status")).toBe("Some New Status");
	});
});
