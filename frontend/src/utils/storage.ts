/**
 * localStorage that never throws.
 *
 * Private windows, blocked site data and some embedded browsers throw on access, so every
 * read and write is guarded. Only small per-device preferences are stored here (language,
 * selected ward); nothing that must survive or be shared.
 */

const PREFIX = "h360.";

export function getItem(key: string): string | null {
	try {
		return window.localStorage.getItem(PREFIX + key);
	} catch {
		return null;
	}
}

export function setItem(key: string, value: string): void {
	try {
		window.localStorage.setItem(PREFIX + key, value);
	} catch {
		// Storage unavailable or full: the preference simply is not remembered.
	}
}

export function removeItem(key: string): void {
	try {
		window.localStorage.removeItem(PREFIX + key);
	} catch {
		// Nothing to clean up if storage is unavailable.
	}
}
