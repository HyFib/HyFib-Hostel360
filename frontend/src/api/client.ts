/**
 * HTTP client for Frappe whitelisted methods (/api/method/<path>).
 *
 * Every call sends the session cookie and, for writes, the CSRF token from the page's meta
 * tag. Frappe's {"message": …} envelope is unwrapped, and failures become an ApiError with a
 * short message that is safe to show to a student or parent.
 */
import { locale, t } from "@/i18n";
import { htmlToText } from "@/utils/sanitize";

export type ApiErrorKind =
	"offline" | "timeout" | "auth" | "permission" | "validation" | "rate_limit" | "not_found" | "csrf" | "server";

export class ApiError extends Error {
	readonly kind: ApiErrorKind;
	readonly status: number;
	readonly excType?: string;
	readonly serverMessages: string[];

	constructor(
		kind: ApiErrorKind,
		message: string,
		details: { status?: number; excType?: string; serverMessages?: string[] } = {},
	) {
		super(message);
		this.name = "ApiError";
		this.kind = kind;
		this.status = details.status ?? 0;
		this.excType = details.excType;
		this.serverMessages = details.serverMessages ?? [];
	}

	/** True when the problem is the connection rather than the request. */
	get isNetwork(): boolean {
		return this.kind === "offline" || this.kind === "timeout";
	}
}

export interface ApiHandlers {
	/** Connection state as observed by real requests (more reliable than navigator.onLine). */
	onNetworkStatus?: (online: boolean) => void;
	/**
	 * A 403 that may mean the session ended (timeout, sign-out in another tab). Resolves true
	 * when it found the session had ended, so the caller shows that instead of the raw reply.
	 */
	onForbidden?: (error: ApiError) => boolean | void | Promise<boolean | void>;
	/** The server said the session expired (401 SessionExpired). */
	onSessionEnded?: (error: ApiError) => void | Promise<void>;
	/** The session belongs to a record that may no longer use the portal (not validated, closed). */
	onAccessClosed?: (error: ApiError) => void | Promise<void>;
	/** A new version of the privacy notice was published while the user was signed in. */
	onConsentRequired?: (error: ApiError) => void | Promise<void>;
}

/**
 * 403s that are about one feature or one ward, not about the session: re-checking the
 * session for them would only cost a request (and they must never sign the user out).
 */
export const FEATURE_REFUSALS = new Set(["PortalValidationPending", "PortalConsentRequired", "PortalStudentInactive"]);
/** The caller's record may no longer use the portal; the message says why. */
export const ACCESS_CLOSED = "PortalAccessClosed";
/** The privacy notice changed and has to be accepted again before anything else. */
export const CONSENT_REQUIRED = "PortalConsentRequired";
/** Frappe's answer to the first call after the session lapsed. */
const SESSION_EXPIRED = "SessionExpired";

export interface CallOptions {
	method?: "GET" | "POST";
	timeout?: number;
	signal?: AbortSignal;
}

type Args = Record<string, unknown> | FormData | undefined;

const METHOD_PREFIX = "/api/method/";
const DEFAULT_TIMEOUT_MS = 30_000;
const UPLOAD_TIMEOUT_MS = 120_000;
const CSRF_METHOD = "hostel360.api.auth.get_csrf_token";
/** Frappe's generic rate-limit text; replaced with a friendlier one. */
const GENERIC_RATE_LIMIT = /rate limit/i;

let csrfToken = "";
let handlers: ApiHandlers = {};

export function configureApi(next: ApiHandlers): void {
	handlers = { ...handlers, ...next };
}

export function readCsrfFromMeta(doc: Document = document): string {
	const value = doc.querySelector('meta[name="csrf-token"]')?.getAttribute("content")?.trim() ?? "";
	// "None" is what an unauthenticated Jinja render of a missing token looks like.
	if (!value || value.includes("{{") || value === "None") return "";
	return value;
}

export function initCsrf(doc: Document = document): void {
	csrfToken = readCsrfFromMeta(doc);
}

export function setCsrfToken(token: string | null | undefined): void {
	csrfToken = token || "";
	const meta = typeof document !== "undefined" ? document.querySelector('meta[name="csrf-token"]') : null;
	meta?.setAttribute("content", csrfToken);
}

export function getCsrfToken(): string {
	return csrfToken;
}

/** Fetch a fresh token for the current session (after sign-in, or when the server rejects ours). */
export async function refreshCsrfToken(): Promise<string> {
	const result = await call<{ csrf_token?: string }>(CSRF_METHOD, undefined, { method: "GET" });
	setCsrfToken(result?.csrf_token);
	return csrfToken;
}

/**
 * Language for this request's server messages. Frappe reads `_lang` before the signed-in
 * user's saved language, so errors always come back in the language on screen (and a guest's
 * sign-in errors too, whatever the browser sends as Accept-Language).
 */
function requestLanguage(): string {
	return locale.value === "ta" ? "ta" : "en";
}

function serialise(args: Record<string, unknown>): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(args)) {
		if (value !== undefined) out[key] = value;
	}
	if (out._lang === undefined) out._lang = requestLanguage();
	return out;
}

function toQuery(args: Args): string {
	if (args instanceof FormData) return "";
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(serialise(args ?? {}))) {
		if (value === null) continue;
		params.set(key, typeof value === "object" ? JSON.stringify(value) : String(value));
	}
	const query = params.toString();
	return query ? `?${query}` : "";
}

/** Messages Frappe queued with frappe.throw / msgprint, as plain text. */
export function parseServerMessages(raw: unknown): string[] {
	if (typeof raw !== "string" || !raw) return [];
	try {
		const list = JSON.parse(raw);
		if (!Array.isArray(list)) return [];
		return list
			.map((entry) => {
				try {
					const item = typeof entry === "string" ? JSON.parse(entry) : entry;
					return htmlToText(typeof item === "string" ? item : item?.message);
				} catch {
					return htmlToText(String(entry));
				}
			})
			.filter(Boolean);
	} catch {
		return [];
	}
}

/** "frappe.exceptions.ValidationError: Leave overlaps" → "Leave overlaps" */
function messageFromException(raw: unknown): string {
	if (typeof raw !== "string" || !raw) return "";
	const lastLine = raw.trim().split("\n").pop() || "";
	const separator = lastLine.indexOf(": ");
	return separator > -1 ? htmlToText(lastLine.slice(separator + 2)) : "";
}

function kindForStatus(status: number, excType?: string): ApiErrorKind {
	if (status === 400 && excType === "CSRFTokenError") return "csrf";
	if (status === 401) return "auth";
	if (status === 403) return "permission";
	if (status === 404) return "not_found";
	if (status === 429) return "rate_limit";
	if (status >= 500) return "server";
	return "validation";
}

const FALLBACK_KEYS: Record<ApiErrorKind, string> = {
	offline: "errors.network",
	timeout: "errors.timeout",
	auth: "errors.sessionExpired",
	permission: "errors.permission",
	validation: "errors.generic",
	rate_limit: "errors.rateLimit",
	not_found: "errors.notFound",
	csrf: "errors.sessionExpired",
	server: "errors.server",
};

export async function toApiError(response: Response): Promise<ApiError> {
	let body: Record<string, unknown> = {};
	try {
		const text = await response.text();
		body = text ? JSON.parse(text) : {};
	} catch {
		body = {};
	}
	const excType = typeof body.exc_type === "string" ? body.exc_type : undefined;
	const kind = kindForStatus(response.status, excType);
	const serverMessages = parseServerMessages(body._server_messages);
	let message = serverMessages.join("\n") || messageFromException(body.exception);

	// Server errors may carry internals; never show them.
	if (kind === "server") message = "";
	if (kind === "rate_limit" && GENERIC_RATE_LIMIT.test(message)) message = "";
	if (kind === "csrf") message = "";

	return new ApiError(kind, message || t(FALLBACK_KEYS[kind]), {
		status: response.status,
		excType,
		serverMessages,
	});
}

function notifyNetwork(online: boolean): void {
	handlers.onNetworkStatus?.(online);
}

async function send(path: string, args: Args, options: CallOptions, attempt = 0): Promise<Response> {
	const method = options.method ?? "POST";
	const headers: Record<string, string> = {
		Accept: "application/json",
		"X-Requested-With": "XMLHttpRequest",
		"Accept-Language": locale.value === "ta" ? "ta, en;q=0.8" : "en-IN, en;q=0.9",
	};
	let url = METHOD_PREFIX + path;
	let body: BodyInit | undefined;

	if (method === "GET") {
		url += toQuery(args);
	} else if (args instanceof FormData) {
		if (!args.has("_lang")) args.append("_lang", requestLanguage());
		body = args;
	} else {
		headers["Content-Type"] = "application/json";
		body = JSON.stringify(serialise(args ?? {}));
	}
	if (method !== "GET" && csrfToken) {
		headers["X-Frappe-CSRF-Token"] = csrfToken;
	}

	const controller = new AbortController();
	let timedOut = false;
	const timer = setTimeout(() => {
		timedOut = true;
		controller.abort();
	}, options.timeout ?? DEFAULT_TIMEOUT_MS);
	const forwardAbort = () => controller.abort();
	if (options.signal?.aborted) controller.abort();
	options.signal?.addEventListener("abort", forwardAbort, { once: true });

	let response: Response;
	try {
		response = await fetch(url, {
			method,
			headers,
			body,
			credentials: "include",
			cache: "no-store",
			redirect: "follow",
			signal: controller.signal,
		});
	} catch (error) {
		if (options.signal?.aborted) throw error;
		if (timedOut) throw new ApiError("timeout", t("errors.timeout"));
		notifyNetwork(false);
		throw new ApiError("offline", t("errors.network"));
	} finally {
		clearTimeout(timer);
		options.signal?.removeEventListener("abort", forwardAbort);
	}

	notifyNetwork(true);
	if (response.ok) return response;

	const error = await toApiError(response);
	if (error.kind === "csrf" && attempt === 0 && path !== CSRF_METHOD) {
		await refreshCsrfToken();
		return send(path, args, options, attempt + 1);
	}
	throw await afterRefusal(error);
}

/**
 * Let the app react to a refusal that concerns the whole session, and decide what the caller
 * sees. A wrong sign-in code is also a 401, but an ordinary one: only Frappe's SessionExpired
 * means the session lapsed. A 403 after the session lapsed carries Frappe's internal
 * "not whitelisted" text, which is replaced by the plain "session ended" message.
 */
async function afterRefusal(error: ApiError): Promise<ApiError> {
	if (error.kind === "auth" && error.excType === SESSION_EXPIRED) {
		void handlers.onSessionEnded?.(error);
		return error;
	}
	if (error.kind !== "permission") return error;
	if (error.excType === ACCESS_CLOSED) {
		void handlers.onAccessClosed?.(error);
		return error;
	}
	if (error.excType === CONSENT_REQUIRED) {
		void handlers.onConsentRequired?.(error);
		return error;
	}
	if (error.excType && FEATURE_REFUSALS.has(error.excType)) return error;
	const ended = await handlers.onForbidden?.(error);
	if (ended) {
		return new ApiError("auth", t("errors.sessionExpired"), { status: error.status, excType: error.excType });
	}
	return error;
}

/** Call a whitelisted method and return its `message`. */
export async function call<T>(path: string, args?: Args, options: CallOptions = {}): Promise<T> {
	const response = await send(path, args, options);
	const text = await response.text();
	if (!text) return undefined as T;
	try {
		return (JSON.parse(text) as { message: T }).message;
	} catch {
		throw new ApiError("server", t("errors.server"), { status: response.status });
	}
}

export interface DownloadedFile {
	blob: Blob;
	filename: string;
}

function filenameFrom(response: Response, fallback: string): string {
	const disposition = response.headers.get("Content-Disposition") || "";
	const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
	if (encoded) {
		try {
			return decodeURIComponent(encoded[1].trim());
		} catch {
			// fall through to the plain filename
		}
	}
	const plain = /filename="?([^";]+)"?/i.exec(disposition);
	return plain ? plain[1].trim() : fallback;
}

/** Call a method that answers with a file (receipts, certificates). */
export async function callFile(path: string, args: Args, fallbackName: string): Promise<DownloadedFile> {
	const response = await send(path, args, { timeout: 60_000 });
	const type = response.headers.get("Content-Type") || "";
	if (type.includes("application/json")) {
		// A JSON answer to a file request means the server could not produce the file.
		throw new ApiError("server", t("errors.downloadFailed"), { status: response.status });
	}
	return { blob: await response.blob(), filename: filenameFrom(response, fallbackName) };
}

/** Multipart upload to a whitelisted method. */
export async function upload<T>(path: string, form: FormData): Promise<T> {
	return call<T>(path, form, { timeout: UPLOAD_TIMEOUT_MS });
}

/** Message to show for any error thrown by the client or by code around it. */
export function errorMessage(error: unknown): string {
	if (error instanceof ApiError) return error.message;
	return t("errors.generic");
}
