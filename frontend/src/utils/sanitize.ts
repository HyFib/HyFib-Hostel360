/**
 * Minimal HTML sanitiser for rich text written by hostel administrators (privacy notice).
 *
 * The text comes from a trusted desk user, but it is still reduced to a small allow-list of
 * formatting tags with no attributes other than safe link targets, so a pasted snippet can
 * never run script or load remote content inside the portal.
 */

const ALLOWED_TAGS = new Set([
	"P",
	"BR",
	"STRONG",
	"B",
	"EM",
	"I",
	"U",
	"UL",
	"OL",
	"LI",
	"H2",
	"H3",
	"H4",
	"BLOCKQUOTE",
	"A",
	"SPAN",
	"DIV",
]);
const DROP_WITH_CONTENT = new Set([
	"SCRIPT",
	"STYLE",
	"IFRAME",
	"OBJECT",
	"EMBED",
	"TEMPLATE",
	"NOSCRIPT",
	"SVG",
	"MATH",
]);
const SAFE_LINK = /^(https?:|mailto:|tel:)/i;

function clean(node: Node, doc: Document): Node | null {
	if (node.nodeType === Node.TEXT_NODE) return doc.createTextNode(node.textContent || "");
	if (node.nodeType !== Node.ELEMENT_NODE) return null;

	const element = node as Element;
	const tag = element.tagName.toUpperCase();
	if (DROP_WITH_CONTENT.has(tag)) return null;

	const children = Array.from(element.childNodes)
		.map((child) => clean(child, doc))
		.filter((child): child is Node => child !== null);

	if (!ALLOWED_TAGS.has(tag)) {
		const fragment = doc.createDocumentFragment();
		children.forEach((child) => fragment.appendChild(child));
		return fragment;
	}

	const copy = doc.createElement(tag.toLowerCase());
	if (tag === "A") {
		const href = (element.getAttribute("href") || "").trim();
		if (SAFE_LINK.test(href)) {
			copy.setAttribute("href", href);
			if (/^https?:/i.test(href)) {
				copy.setAttribute("target", "_blank");
				copy.setAttribute("rel", "noopener noreferrer");
			}
		}
	}
	children.forEach((child) => copy.appendChild(child));
	return copy;
}

export function sanitizeHtml(html: string | null | undefined): string {
	if (!html) return "";
	const parsed = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
	const output = document.implementation.createHTMLDocument("");
	const container = output.createElement("div");
	Array.from(parsed.body.childNodes).forEach((child) => {
		const safe = clean(child, output);
		if (safe) container.appendChild(safe);
	});
	return container.innerHTML;
}

/** Plain text of an HTML fragment (server messages sometimes contain <b> or <br>). */
export function htmlToText(html: string | null | undefined): string {
	if (!html) return "";
	const withBreaks = String(html).replace(/<br\s*\/?>/gi, "\n");
	try {
		const parsed = new DOMParser().parseFromString(withBreaks, "text/html");
		return (parsed.body.textContent || "").replace(/[ \t]+/g, " ").trim();
	} catch {
		return withBreaks.replace(/<[^>]*>/g, "").trim();
	}
}
