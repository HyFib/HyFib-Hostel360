import { describe, expect, it } from "vitest";
import { BOOT_PLACEHOLDER, CSRF_PLACEHOLDER, toJinjaTemplate } from "../plugins/frappe-page";

const BUILT = `<!doctype html>
<html><head>
<meta name="csrf-token" content="">
<script type="module" src="/assets/hostel360/frontend/assets/index.js"></script>
<style>.x{--a:{{not-jinja}}}</style>
</head><body><div id="app"></div>
<script type="application/json" id="h360-boot">{}</script>
</body></html>`;

describe("Frappe page template", () => {
	it("keeps only the CSRF token and boot payload for Jinja", () => {
		const template = toJinjaTemplate(BUILT);
		expect(template).toContain(`{% endraw %}${CSRF_PLACEHOLDER}{% raw %}`);
		expect(template).toContain(`{% endraw %}${BOOT_PLACEHOLDER}{% raw %}`);
		// Anything else that looks like Jinja stays inside a raw block.
		const outsideRaw = template.replace(/\{% raw %\}[\s\S]*?\{% endraw %\}/g, "");
		expect(outsideRaw).not.toContain("not-jinja");
		expect(outsideRaw.match(/\{\{/g)).toHaveLength(2);
		expect(template).not.toMatch(/<script>(?!\s*<\/script>)/);
	});

	it("refuses a page without exactly one boot block", () => {
		expect(() => toJinjaTemplate(BUILT.replace(/<script type="application\/json"[\s\S]*?<\/script>/, ""))).toThrow(/h360-boot/);
	});
});
