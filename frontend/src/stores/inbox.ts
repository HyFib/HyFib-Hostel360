/** Unread notification count for the bell in the header. */
import { ref } from "vue";
import { listNotifications } from "@/api/portal";

export const unreadCount = ref(0);
let lastFetched = 0;
const MIN_INTERVAL_MS = 60_000;

export async function refreshUnread(force = false): Promise<void> {
	if (!force && Date.now() - lastFetched < MIN_INTERVAL_MS) return;
	lastFetched = Date.now();
	try {
		const items = await listNotifications(30);
		unreadCount.value = (items || []).filter((item) => !item.read).length;
	} catch {
		// The bell simply keeps its last count; the inbox page shows errors properly.
	}
}

export function setUnread(count: number): void {
	unreadCount.value = Math.max(0, count);
}

export function resetInbox(): void {
	unreadCount.value = 0;
	lastFetched = 0;
}
