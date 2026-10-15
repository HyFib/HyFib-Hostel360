/**
 * English / Tamil strings for the portal.
 *
 * A small in-house translator rather than a library: messages are plain JSON trees, lookups
 * are reactive through `locale`, and nothing is compiled at runtime (so no eval, which keeps
 * the strict Content-Security-Policy intact).
 */
import { ref } from "vue";
import { getItem, setItem } from "@/utils/storage";
import en from "./en.json";

export type Locale = "en" | "ta";
type MessageTree = { [key: string]: string | MessageTree };
type Params = Record<string, string | number | null | undefined>;

export const LOCALES: ReadonlyArray<{ code: Locale; label: string }> = [
	{ code: "en", label: "English" },
	{ code: "ta", label: "தமிழ்" },
];

const STORAGE_KEY = "lang";
const messages: Partial<Record<Locale, MessageTree>> = { en: en as MessageTree };
const loaders: Record<Locale, () => Promise<MessageTree>> = {
	en: async () => en as MessageTree,
	ta: () => import("./ta.json").then((module) => module.default as MessageTree),
};

export const locale = ref<Locale>("en");

function isLocale(value: unknown): value is Locale {
	return value === "en" || value === "ta";
}

function lookup(tree: MessageTree | undefined, key: string): string | undefined {
	let node: string | MessageTree | undefined = tree;
	for (const part of key.split(".")) {
		if (!node || typeof node === "string") return undefined;
		node = node[part];
	}
	return typeof node === "string" ? node : undefined;
}

function interpolate(template: string, params?: Params): string {
	if (!params) return template;
	return template.replace(/\{(\w+)\}/g, (match, name: string) => {
		const value = params[name];
		return value === undefined || value === null ? match : String(value);
	});
}

/** Translate a key; falls back to English, then to the key itself. */
export function t(key: string, params?: Params): string {
	const template = lookup(messages[locale.value], key) ?? lookup(messages.en, key);
	return template === undefined ? key : interpolate(template, params);
}

/** Choose `<key>.one` or `<key>.other` by count; `{count}` is filled in. */
export function tc(key: string, count: number, params?: Params): string {
	return t(`${key}.${count === 1 ? "one" : "other"}`, { count, ...params });
}

/** True when the key exists (in the current locale or English). */
export function te(key: string): boolean {
	return lookup(messages[locale.value], key) !== undefined || lookup(messages.en, key) !== undefined;
}

/**
 * Label for a value that comes from the server (status, category, reason…). Unknown values
 * are shown as they are, so a new option added on the server never shows a raw key.
 */
export function tv(group: string, value: string | null | undefined): string {
	if (!value) return "";
	const key = `${group}.${value}`;
	return te(key) ? t(key) : value;
}

function applyDocumentLanguage(code: Locale): void {
	if (typeof document !== "undefined") {
		document.documentElement.lang = code;
	}
}

/** Make sure a catalogue is available (Tamil is loaded on first use). */
export async function loadLocale(code: Locale): Promise<void> {
	if (!messages[code]) messages[code] = await loaders[code]();
}

export async function setLocale(code: Locale): Promise<void> {
	if (!isLocale(code)) return;
	try {
		await loadLocale(code);
	} catch {
		// Offline before the catalogue was ever fetched: stay in the current language.
		return;
	}
	locale.value = code;
	setItem(STORAGE_KEY, code);
	applyDocumentLanguage(code);
}

/** Saved choice first, then the language the server suggests, then the browser's. */
export async function initI18n(serverLang?: string | null): Promise<Locale> {
	const saved = getItem(STORAGE_KEY);
	const browser = typeof navigator !== "undefined" ? navigator.language?.slice(0, 2) : undefined;
	const server = serverLang?.slice(0, 2);
	const chosen = [saved, server, browser].find(isLocale) ?? "en";
	try {
		await loadLocale(chosen);
		locale.value = chosen;
	} catch {
		locale.value = "en";
	}
	applyDocumentLanguage(locale.value);
	return locale.value;
}

export function useI18n() {
	return { t, tc, te, tv, locale, setLocale, locales: LOCALES };
}
