/*!
 * HyFib Hostel360 — portal sign-in links for the institution website.
 *
 * Put a placeholder where the buttons should appear and load this file once:
 *
 *   <div data-hostel360-portal="https://hostel.accet.ac.in"></div>
 *   <script src="https://hostel.accet.ac.in/assets/hostel360/embed/hostel360-links.js" defer></script>
 *
 * Optional attributes on the placeholder:
 *   data-hostel360-lang    "both" (default: English with Tamil underneath), "en" or "ta"
 *   data-hostel360-layout  "row" (default, wraps on small screens) or "column"
 *   data-hostel360-show    comma-separated subset of "student,parent,staff" (default: all three)
 *   data-hostel360-target  "_self" (default) or "_blank"
 *   data-hostel360-theme   "auto" (default, follows the visitor's light/dark setting), "light" or "dark"
 *
 * The script only draws three links. It sets no cookies, uses no storage, makes no network
 * requests and loads nothing else, so it adds no tracking to the institution's site. It is
 * written for old browsers too (no build step, ES5 syntax).
 */
(function () {
	"use strict";

	var KINDS = ["student", "parent", "staff"];
	var PATHS = {
		student: "/hostel/login?as=student",
		parent: "/hostel/login?as=parent",
		staff: "/login",
	};
	var LABELS = {
		student: { en: "Student login", ta: "மாணவர் உள்நுழைவு" },
		parent: { en: "Parent login", ta: "பெற்றோர் உள்நுழைவு" },
		staff: { en: "Staff login", ta: "பணியாளர் உள்நுழைவு" },
	};
	var NAV_LABEL = { en: "Hostel portal", ta: "விடுதி இணையதளம்" };
	// 24×24 stroke icons: graduation cap, people, briefcase.
	var ICONS = {
		student: ["M22 10 12 5 2 10l10 5 10-5Z", "M6 12v5c3 2 9 2 12 0v-5"],
		parent: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M22 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
		staff: ["M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16", "M4 6h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z"],
	};
	var SVG_NS = "http://www.w3.org/2000/svg";
	var READY_FLAG = "data-hostel360-ready";

	var CSS = [
		":host{display:block}",
		".h360{--h360-brand:#0b4f9c;--h360-brand-dark:#083b75;--h360-accent:#0e7490;--h360-text:#ffffff;--h360-alt-bg:#ffffff;--h360-alt-text:#0b4f9c;",
		"font-family:system-ui,-apple-system,'Segoe UI',Roboto,'Noto Sans','Noto Sans Tamil','Nirmala UI',Latha,Arial,sans-serif;",
		"display:flex;flex-wrap:wrap;gap:.75rem;margin:0;padding:0;list-style:none}",
		".h360.h360-column{flex-direction:column;align-items:stretch}",
		".h360 li{margin:0;padding:0;flex:1 1 12rem;min-width:0}",
		".h360.h360-column li{flex:none}",
		".h360-btn{box-sizing:border-box;display:flex;align-items:center;gap:.75rem;min-height:3rem;padding:.65rem 1rem;border-radius:.75rem;",
		"border:2px solid var(--h360-brand);background:var(--h360-brand);color:var(--h360-text);text-decoration:none;line-height:1.25;",
		"font-weight:600;font-size:1rem;transition:background-color .15s ease,border-color .15s ease}",
		".h360-btn:hover{background:var(--h360-brand-dark);border-color:var(--h360-brand-dark)}",
		".h360-btn:focus-visible{outline:3px solid #f59e0b;outline-offset:2px}",
		".h360-staff{background:var(--h360-alt-bg);color:var(--h360-alt-text)}",
		".h360-staff:hover{background:#e7f0fb;border-color:var(--h360-brand)}",
		".h360-icon{flex:none;width:1.5rem;height:1.5rem}",
		".h360-text{display:flex;flex-direction:column;min-width:0}",
		".h360-sub{font-weight:500;font-size:.85rem;opacity:.9}",
		".h360[data-theme=dark]{--h360-brand:#2f6fbf;--h360-brand-dark:#3d80d4;--h360-alt-bg:#111a2e;--h360-alt-text:#8cbdf5}",
		".h360[data-theme=dark] .h360-staff:hover{background:#17223a}",
		"@media (prefers-color-scheme:dark){.h360[data-theme=auto]{--h360-brand:#2f6fbf;--h360-brand-dark:#3d80d4;--h360-alt-bg:#111a2e;--h360-alt-text:#8cbdf5}",
		".h360[data-theme=auto] .h360-staff:hover{background:#17223a}}",
		"@media (prefers-reduced-motion:reduce){.h360-btn{transition:none}}",
	].join("");

	/** Origin of the portal, accepting https (or http only for local testing). */
	function portalOrigin(value) {
		if (!value) return null;
		var parser = document.createElement("a");
		parser.href = value;
		var protocol = parser.protocol;
		var hostname = parser.hostname;
		var local = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(hostname);
		if (!hostname || !(protocol === "https:" || (protocol === "http:" && local))) return null;
		// Old browsers report default ports in `host`; drop them for a clean origin.
		var host = parser.host.replace(/:(443|80)$/, "");
		return protocol + "//" + host;
	}

	function icon(kind) {
		var svg = document.createElementNS(SVG_NS, "svg");
		svg.setAttribute("class", "h360-icon");
		svg.setAttribute("viewBox", "0 0 24 24");
		svg.setAttribute("fill", "none");
		svg.setAttribute("stroke", "currentColor");
		svg.setAttribute("stroke-width", "2");
		svg.setAttribute("stroke-linecap", "round");
		svg.setAttribute("stroke-linejoin", "round");
		svg.setAttribute("aria-hidden", "true");
		svg.setAttribute("focusable", "false");
		for (var i = 0; i < ICONS[kind].length; i++) {
			var path = document.createElementNS(SVG_NS, "path");
			path.setAttribute("d", ICONS[kind][i]);
			svg.appendChild(path);
		}
		return svg;
	}

	function span(className, text, lang) {
		var element = document.createElement("span");
		element.className = className;
		element.textContent = text;
		if (lang) element.setAttribute("lang", lang);
		return element;
	}

	function button(kind, origin, lang, target) {
		var link = document.createElement("a");
		link.className = "h360-btn h360-" + kind;
		link.href = origin + PATHS[kind];
		if (target === "_blank") {
			link.target = "_blank";
			link.rel = "noopener noreferrer";
		}
		link.appendChild(icon(kind));
		var text = document.createElement("span");
		text.className = "h360-text";
		if (lang === "ta") {
			text.appendChild(span("h360-main", LABELS[kind].ta, "ta"));
		} else {
			text.appendChild(span("h360-main", LABELS[kind].en, lang === "both" ? "en" : null));
			if (lang === "both") text.appendChild(span("h360-sub", LABELS[kind].ta, "ta"));
		}
		link.appendChild(text);
		return link;
	}

	/** Styles go inside a shadow root so the institution's CSS and ours never clash. */
	function attachStyles(root) {
		try {
			if (root.adoptedStyleSheets !== undefined && typeof CSSStyleSheet === "function") {
				var sheet = new CSSStyleSheet();
				sheet.replaceSync(CSS);
				root.adoptedStyleSheets = [sheet];
				return;
			}
		} catch (error) {
			// Fall back to a style element below.
		}
		var style = document.createElement("style");
		style.textContent = root === document ? CSS.replace(":host{display:block}", "") : CSS;
		(root === document ? document.head : root).appendChild(style);
	}

	function pick(value, allowed, fallback) {
		value = (value || "").toLowerCase();
		for (var i = 0; i < allowed.length; i++) if (allowed[i] === value) return value;
		return fallback;
	}

	var lightDomStyled = false;

	function render(host) {
		if (host.getAttribute(READY_FLAG)) return;
		var origin = portalOrigin(host.getAttribute("data-hostel360-portal"));
		if (!origin) {
			if (window.console) window.console.warn("Hostel360: data-hostel360-portal must be an https:// address.");
			return;
		}
		host.setAttribute(READY_FLAG, "1");

		var lang = pick(host.getAttribute("data-hostel360-lang"), ["both", "en", "ta"], "both");
		var layout = pick(host.getAttribute("data-hostel360-layout"), ["row", "column"], "row");
		var target = pick(host.getAttribute("data-hostel360-target"), ["_self", "_blank"], "_self");
		var theme = pick(host.getAttribute("data-hostel360-theme"), ["auto", "light", "dark"], "auto");
		var wanted = {};
		var requested = (host.getAttribute("data-hostel360-show") || KINDS.join(",")).toLowerCase().split(",");
		for (var w = 0; w < requested.length; w++) wanted[requested[w].replace(/^\s+|\s+$/g, "")] = true;

		var nav = document.createElement("nav");
		nav.setAttribute("aria-label", lang === "ta" ? NAV_LABEL.ta : NAV_LABEL.en);
		var list = document.createElement("ul");
		list.className = "h360" + (layout === "column" ? " h360-column" : "");
		list.setAttribute("data-theme", theme);
		for (var i = 0; i < KINDS.length; i++) {
			var kind = KINDS[i];
			if (!wanted[kind]) continue;
			var item = document.createElement("li");
			item.appendChild(button(kind, origin, lang, target));
			list.appendChild(item);
		}
		nav.appendChild(list);

		var root = host;
		if (host.attachShadow) {
			try {
				root = host.shadowRoot || host.attachShadow({ mode: "open" });
			} catch (error) {
				root = host;
			}
		}
		// Anything the page put inside the placeholder (a <noscript> fallback) is replaced.
		while (root.firstChild) root.removeChild(root.firstChild);
		if (root === host) {
			if (!lightDomStyled) {
				attachStyles(document);
				lightDomStyled = true;
			}
		} else {
			attachStyles(root);
		}
		root.appendChild(nav);
	}

	function init() {
		var hosts = document.querySelectorAll("[data-hostel360-portal]");
		for (var i = 0; i < hosts.length; i++) render(hosts[i]);
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", init);
	} else {
		init();
	}
})();
