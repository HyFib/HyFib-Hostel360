/**
 * Razorpay Checkout, loaded only when a payment starts.
 *
 * The script is fetched from Razorpay's CDN on demand so the rest of the portal never loads
 * third-party code. The server decides the order, amount and key; the browser only opens the
 * window and passes Razorpay's signed response back for verification.
 */
import type { CheckoutOptions } from "@/api/types";
import type { CheckoutResponse } from "@/api/portal";

const SCRIPT_URL = "https://checkout.razorpay.com/v1/checkout.js";
const LOAD_TIMEOUT_MS = 20_000;

interface RazorpayInstance {
	open(): void;
	on(event: "payment.failed", handler: (response: { error?: { description?: string } }) => void): void;
}

type RazorpayConstructor = new (options: Record<string, unknown>) => RazorpayInstance;

declare global {
	interface Window {
		Razorpay?: RazorpayConstructor;
	}
}

let loading: Promise<RazorpayConstructor> | null = null;

export function loadRazorpay(): Promise<RazorpayConstructor> {
	if (window.Razorpay) return Promise.resolve(window.Razorpay);
	if (loading) return loading;
	loading = new Promise<RazorpayConstructor>((resolve, reject) => {
		const script = document.createElement("script");
		script.src = SCRIPT_URL;
		script.async = true;
		const timer = window.setTimeout(() => fail(new Error("timeout")), LOAD_TIMEOUT_MS);
		function fail(error: Error) {
			window.clearTimeout(timer);
			script.remove();
			loading = null;
			reject(error);
		}
		script.onload = () => {
			window.clearTimeout(timer);
			if (window.Razorpay) resolve(window.Razorpay);
			else fail(new Error("unavailable"));
		};
		script.onerror = () => fail(new Error("load failed"));
		document.head.appendChild(script);
	});
	return loading;
}

export type CheckoutOutcome = { type: "success"; response: CheckoutResponse } | { type: "dismissed"; failure?: string };

/** Open the checkout window; resolves once the payer completes or closes it. */
export async function openCheckout(checkout: CheckoutOptions, themeColor: string): Promise<CheckoutOutcome> {
	const Razorpay = await loadRazorpay();
	return new Promise<CheckoutOutcome>((resolve) => {
		let lastFailure: string | undefined;
		let settled = false;
		const finish = (outcome: CheckoutOutcome) => {
			if (settled) return;
			settled = true;
			resolve(outcome);
		};
		const instance = new Razorpay({
			...checkout,
			theme: { color: themeColor },
			retry: { enabled: true, max_count: 3 },
			modal: {
				confirm_close: true,
				escape: false,
				ondismiss: () => finish({ type: "dismissed", failure: lastFailure }),
			},
			handler: (response: CheckoutResponse) => finish({ type: "success", response }),
		});
		instance.on("payment.failed", (response) => {
			// Razorpay keeps its window open for a retry; remember why it failed in case the payer gives up.
			lastFailure = response?.error?.description;
		});
		instance.open();
	});
}
