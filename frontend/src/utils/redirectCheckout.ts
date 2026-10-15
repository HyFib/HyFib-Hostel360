/**
 * Hosted payment pages (PayU) are reached by posting a signed form to the gateway.
 *
 * The server builds every field, including the hash that signs them, so the browser only
 * copies them into a form and submits it as a top-level navigation. The gateway sends the
 * payer back through the server, which redirects to /hostel/fees?payment=…&status=….
 * No script runs inline and nothing is evaluated, which keeps the strict CSP intact (the
 * site's form-action directive must list the gateway).
 */
import type { RedirectCheckout } from "@/api/types";

export class RedirectCheckoutError extends Error {}

/** Build the form for a redirect checkout (separate from submitting it, so it can be tested). */
export function buildRedirectForm(checkout: RedirectCheckout, doc: Document = document): HTMLFormElement {
	let target: URL;
	try {
		target = new URL(checkout.action_url);
	} catch {
		throw new RedirectCheckoutError("The payment page address is not valid.");
	}
	// Card and bank details are typed on this page; never send anyone to it over plain HTTP.
	if (target.protocol !== "https:") throw new RedirectCheckoutError("The payment page is not secure.");

	const form = doc.createElement("form");
	form.method = String(checkout.method || "POST").toUpperCase() === "GET" ? "get" : "post";
	form.action = target.href;
	form.acceptCharset = "UTF-8";
	form.hidden = true;
	for (const [name, value] of Object.entries(checkout.fields || {})) {
		const input = doc.createElement("input");
		input.type = "hidden";
		input.name = name;
		input.value = value === null || value === undefined ? "" : String(value);
		form.appendChild(input);
	}
	return form;
}

/** Leave the portal for the gateway's payment page. */
export function submitRedirectCheckout(checkout: RedirectCheckout, doc: Document = document): void {
	const form = buildRedirectForm(checkout, doc);
	doc.body.appendChild(form);
	form.submit();
}
