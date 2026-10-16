/**
 * Writes the Frappe web page (hostel360/www/hostel.html) from the built index.html.
 *
 * Frappe renders www pages through Jinja, so the built HTML is wrapped in raw blocks and
 * only two values are left for the server to fill: the CSRF token (a meta tag) and the boot
 * payload (a JSON data block). Neither is executable script, which keeps the page compatible
 * with a strict Content-Security-Policy.
 *
 * The index.html left in the output directory keeps empty values and doubles as the
 * offline shell cached by the service worker; it never contains personal data.
 */
import { promises as fs } from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";

const CSRF_TAG = /<meta\s+name="csrf-token"\s+content="[^"]*"\s*\/?>/g;
const BOOT_TAG = /<script\s+type="application\/json"\s+id="h360-boot">[\s\S]*?<\/script>/g;

export const CSRF_PLACEHOLDER = '<meta name="csrf-token" content="{{ csrf_token }}">';
export const BOOT_PLACEHOLDER = '<script type="application/json" id="h360-boot">{{ boot_json }}</script>';

const HEADER =
	"{#- Built from frontend/index.html by `yarn build` in the app root. Edit the source, not this file. -#}\n";

function single(html: string, pattern: RegExp, label: string): { start: number; end: number } {
	const matches = [...html.matchAll(pattern)];
	if (matches.length !== 1) {
		throw new Error(`frappe-page: expected exactly one ${label} in index.html, found ${matches.length}`);
	}
	const match = matches[0];
	return { start: match.index ?? 0, end: (match.index ?? 0) + match[0].length };
}

function raw(segment: string): string {
	if (!segment) return "";
	if (/\{%-?\s*endraw/.test(segment)) {
		throw new Error("frappe-page: index.html contains an endraw tag and cannot be wrapped safely");
	}
	return `{% raw %}${segment}{% endraw %}`;
}

/** Turn the built index.html into a Jinja template with the two server-filled values. */
export function toJinjaTemplate(html: string): string {
	const csrf = single(html, CSRF_TAG, "csrf-token meta tag");
	const boot = single(html, BOOT_TAG, "h360-boot data block");
	const slots = [
		{ ...csrf, value: CSRF_PLACEHOLDER },
		{ ...boot, value: BOOT_PLACEHOLDER },
	].sort((a, b) => a.start - b.start);

	let cursor = 0;
	let output = HEADER;
	for (const slot of slots) {
		output += raw(html.slice(cursor, slot.start)) + slot.value;
		cursor = slot.end;
	}
	output += raw(html.slice(cursor));
	return output;
}

export function frappePage(options: { output: string }): Plugin {
	let outDir = "";
	return {
		name: "hostel360:frappe-page",
		apply: "build",
		configResolved(config) {
			outDir = path.resolve(config.root, config.build.outDir);
		},
		async writeBundle() {
			const source = await fs.readFile(path.join(outDir, "index.html"), "utf8");
			const target = path.resolve(options.output);
			await fs.mkdir(path.dirname(target), { recursive: true });
			await fs.writeFile(target, toJinjaTemplate(source), "utf8");
			this.info?.(`wrote ${path.relative(process.cwd(), target)}`);
		},
	};
}
