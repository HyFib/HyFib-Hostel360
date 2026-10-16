/**
 * Start-up data rendered into the page by hostel360/www/hostel.py.
 *
 * The page carries it as a JSON data block (not executable script) so the portal works
 * under a strict Content-Security-Policy. The offline shell and the dev server carry an
 * empty object, in which case everything comes from get_session instead.
 */

/** One published demonstration sign-in (every record on a demonstration site is fictitious). */
export interface DemoAccount {
	kind: "student" | "parent";
	name: string;
	/** The role in English, as the server words it; the portal words its own from the fields below. */
	role?: string | null;
	mobile: string;
	hostel?: string | null;
	/** What the account is there to show: "dues", "leave", "approve_leave", "two_wards". */
	story?: string | null;
	wards?: string[];
}

/** Present only on a demonstration site (see hostel360.demo on the server). */
export interface DemoBoot {
	info_url?: string;
	otp?: string;
	accounts?: DemoAccount[];
}

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
	/** Only on a demonstration site: the banner and the published demo sign-ins. */
	demo?: DemoBoot | null;
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
		return asObject(JSON.parse(decodeEntities(source)));
	} catch {
		return {};
	}
}

const NAMED_ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

/** Undo HTML entity escaping as plain string work; the payload is never parsed as HTML. */
function decodeEntities(text: string): string {
	return text.replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z]+);/gi, (match, code: string) => {
		if (code[0] === "#") {
			const hex = code[1] === "x" || code[1] === "X";
			const point = Number.parseInt(code.slice(hex ? 2 : 1), hex ? 16 : 10);
			return Number.isInteger(point) && point >= 0 && point <= 0x10ffff ? String.fromCodePoint(point) : match;
		}
		return NAMED_ENTITIES[code.toLowerCase()] ?? match;
	});
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
