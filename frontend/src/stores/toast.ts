/** Short confirmations and errors shown at the bottom of the screen. */
import { reactive } from "vue";

export type ToastTone = "success" | "error" | "info";

export interface Toast {
	id: number;
	message: string;
	tone: ToastTone;
	actionLabel?: string;
	action?: () => void;
	timeout: number;
}

const MAX_VISIBLE = 3;
let sequence = 0;
const timers = new Map<number, ReturnType<typeof setTimeout>>();

export const toasts = reactive<Toast[]>([]);

export function dismissToast(id: number): void {
	const index = toasts.findIndex((toast) => toast.id === id);
	if (index > -1) toasts.splice(index, 1);
	const timer = timers.get(id);
	if (timer) clearTimeout(timer);
	timers.delete(id);
}

export function showToast(
	message: string,
	options: { tone?: ToastTone; timeout?: number; actionLabel?: string; action?: () => void } = {},
): number {
	const tone = options.tone ?? "success";
	// Errors stay a little longer: they usually need reading twice.
	const timeout = options.timeout ?? (tone === "error" ? 7000 : 4000);
	const toast: Toast = { id: ++sequence, message, tone, timeout, actionLabel: options.actionLabel, action: options.action };
	toasts.push(toast);
	while (toasts.length > MAX_VISIBLE) dismissToast(toasts[0].id);
	if (timeout > 0) timers.set(toast.id, setTimeout(() => dismissToast(toast.id), timeout));
	return toast.id;
}

export const toast = {
	success: (message: string) => showToast(message, { tone: "success" }),
	error: (message: string) => showToast(message, { tone: "error" }),
	info: (message: string) => showToast(message, { tone: "info" }),
};
