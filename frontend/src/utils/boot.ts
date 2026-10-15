/**
 * Start-up data rendered into the page by hostel360/www/hostel.py.
 *
 * The page carries it as a JSON data block (not executable script) so the portal works
 * under a strict Content-Security-Policy. The offline shell and the dev server carry an
 * empty object, in which case everything comes from get_session instead.
 */

export interface BootData {
	site_name?: string;
	institution_name?: string;
	institution_short_name?: string;
	logo?: string | null;
	office_phone?: string | null;
	office_email?: string | null;
	support_phone?: string;
	support_email?: string;
	is_logged_in?: boolean;
	kind?: "student" | "parent" | null;
	lang?: string;
	/** The sign-in page may offer codes by e-mail (a site-wide setting). */
	otp_email?: boolean;
}

let cached: BootData | null = null;

function asObject(value: unknown): BootData {
	return value && typeof value === "object" && !Array.isArray(value) ? (value as BootData) : {};
}

export function parseBoot(text: string | null | undefined): BootData {
	const source = (text || "").trim();
	// An unrendered template (opened straight from disk) still has its placeholder.
	if (!source || source.startsWith("{{")) return {};
	try {
		return asObject(JSON.parse(source));
	} catch {
		// Fall through: some template setups HTML-escape the payload.
	}
	try {
		const decoder = document.createElement("textarea");
		decoder.innerHTML = source;
		return asObject(JSON.parse(decoder.value));
	} catch {
		return {};
	}
}

export function readBoot(): BootData {
	if (!cached) {
		cached = parseBoot(document.getElementById("h360-boot")?.textContent);
	}
	return cached;
}

/** Update the cached copy after sign-in or sign-out so later reads agree with the server. */
export function updateBoot(values: Partial<BootData>): void {
	cached = { ...readBoot(), ...values };
}

export function resetBoot(): void {
	cached = null;
}
