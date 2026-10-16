import { describe, expect, it } from "vitest";
import { parseBoot } from "@/utils/boot";
import { sanitizeHtml } from "@/utils/sanitize";

describe("parseBoot", () => {
	it("reads the JSON data block", () => {
		expect(parseBoot('{"is_logged_in": true, "kind": "parent"}')).toEqual({ is_logged_in: true, kind: "parent" });
	});

	it("survives empty, placeholder and HTML-escaped payloads", () => {
		expect(parseBoot("")).toEqual({});
		expect(parseBoot("{{ boot_json }}")).toEqual({});
		expect(parseBoot("{&#34;lang&#34;: &#34;ta&#34;}")).toEqual({ lang: "ta" });
		expect(parseBoot("{&quot;name&quot;: &quot;A &amp; B &lt;x&gt; &#x41;&#39;&quot;}")).toEqual({
			name: "A & B <x> A'",
		});
		// Decoding is string work only: markup inside the payload stays inert text.
		expect(parseBoot("{&quot;logo&quot;: &quot;&lt;img src=x onerror=alert(1)&gt;&quot;}")).toEqual({
			logo: "<img src=x onerror=alert(1)>",
		});
		expect(parseBoot("[1, 2]")).toEqual({});
	});
});

describe("sanitizeHtml", () => {
	it("keeps formatting and drops anything executable", () => {
		const html = sanitizeHtml(
			'<p onclick="x()">Hello <strong>there</strong><script>alert(1)</script></p><img src=x onerror=alert(1)><a href="javascript:alert(1)">bad</a><a href="https://example.org">good</a>',
		);
		expect(html).toBe(
			'<p>Hello <strong>there</strong></p><a>bad</a><a href="https://example.org" target="_blank" rel="noopener noreferrer">good</a>',
		);
	});
});
