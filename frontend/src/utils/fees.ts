import type { Invoice } from "@/api/types";
import { toNumber } from "./format";

/** Invoices that can be paid online now: money still due and no payment already running. */
export function canPay(invoice: Invoice): boolean {
	return (
		toNumber(invoice.outstanding_amount) > 0 &&
		!invoice.payment_in_progress &&
		!["Paid", "Cancelled", "Draft"].includes(invoice.status)
	);
}

/** Sum of outstanding amounts in paise-exact arithmetic. */
export function totalOutstanding(invoices: Invoice[]): number {
	return invoices.reduce((sum, invoice) => sum + Math.round(toNumber(invoice.outstanding_amount) * 100), 0) / 100;
}
