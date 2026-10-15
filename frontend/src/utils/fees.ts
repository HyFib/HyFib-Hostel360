import type { Invoice } from "@/api/types";
import { toNumber } from "./format";

/**
 * Invoices that can be paid online now: money still due and not cancelled.
 *
 * A payment already running for an invoice (`payment_in_progress`) does not block it: after
 * the checkout window is closed, Pay again gets the same open order back from the server
 * (no second order is created), and the server refuses an overlapping one with its own
 * message. Blocking here locked payers out until the abandoned order expired.
 */
export function canPay(invoice: Invoice): boolean {
	return toNumber(invoice.outstanding_amount) > 0 && !["Paid", "Cancelled", "Draft"].includes(invoice.status);
}

/** Sum of outstanding amounts in paise-exact arithmetic. */
export function totalOutstanding(invoices: Invoice[]): number {
	return invoices.reduce((sum, invoice) => sum + Math.round(toNumber(invoice.outstanding_amount) * 100), 0) / 100;
}
