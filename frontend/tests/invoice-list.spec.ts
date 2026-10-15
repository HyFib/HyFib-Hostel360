import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import type { Invoice } from "@/api/types";
import InvoiceList from "@/components/InvoiceList.vue";

function invoice(name: string, outstanding: number, extra: Partial<Invoice> = {}): Invoice {
	return {
		name,
		invoice_type: "Hostel Fee",
		posting_date: "2026-10-01",
		due_date: "2026-10-20",
		total_amount: outstanding || 1000,
		paid_amount: 0,
		outstanding_amount: outstanding,
		status: outstanding > 0 ? "Unpaid" : "Paid",
		items: [{ fee_head: "Hostel Rent", amount: outstanding || 1000 }],
		...extra,
	};
}

const invoices = [
	invoice("INV-2026-00001", 1500.5),
	invoice("INV-2026-00002", 2500, { invoice_type: "Mess Bill", status: "Overdue" }),
	invoice("INV-2026-00003", 800, { payment_in_progress: true }),
	invoice("INV-2026-00004", 0),
	invoice("INV-2026-00005", 600, { status: "Cancelled" }),
];

function mountList() {
	return mount(InvoiceList, {
		props: {
			invoices,
			payable: true,
			modelValue: [] as string[],
			"onUpdate:modelValue": (value: string[]) => wrapper.setProps({ modelValue: value }),
		},
	});
}
let wrapper: ReturnType<typeof mountList>;

describe("InvoiceList selection", () => {
	it("offers checkboxes only for invoices that can be paid now", () => {
		wrapper = mountList();
		const boxes = wrapper.findAll('input[type="checkbox"]');
		expect(boxes).toHaveLength(3);
		expect(wrapper.find('[data-invoice="INV-2026-00004"] input[type="checkbox"]').exists()).toBe(false);
		expect(wrapper.find('[data-invoice="INV-2026-00005"] input[type="checkbox"]').exists()).toBe(false);
		expect(wrapper.find('[data-test="selected-total"]').exists()).toBe(false);
	});

	it("keeps an invoice whose checkout was abandoned payable, marked as in progress", () => {
		// The server hands back the same open order, so the payer is never locked out for an hour.
		wrapper = mountList();
		const row = wrapper.find('[data-invoice="INV-2026-00003"]');
		expect(row.find('input[type="checkbox"]').exists()).toBe(true);
		expect(row.text()).toContain("Payment in progress");
	});

	it("adds up the outstanding amounts of the selected invoices", async () => {
		wrapper = mountList();
		await wrapper.find('[data-invoice="INV-2026-00001"] input').setValue(true);
		expect(wrapper.find('[data-test="selected-total"]').text()).toBe("₹1,500.50");

		await wrapper.find('[data-invoice="INV-2026-00002"] input').setValue(true);
		expect(wrapper.find('[data-test="selected-total"]').text()).toBe("₹4,000.50");
		expect(wrapper.find('[data-test="pay-selected"]').text()).toBe("Pay ₹4,000.50");

		await wrapper.find('[data-invoice="INV-2026-00001"] input').setValue(false);
		expect(wrapper.find('[data-test="selected-total"]').text()).toBe("₹2,500.00");
	});

	it("selects all payable invoices at once and pays them", async () => {
		wrapper = mountList();
		await wrapper.find('[data-test="select-all"]').trigger("click");
		expect(wrapper.props("modelValue")).toEqual(["INV-2026-00001", "INV-2026-00002", "INV-2026-00003"]);

		await wrapper.find('[data-test="pay-selected"]').trigger("click");
		expect(wrapper.emitted("pay")).toEqual([[["INV-2026-00001", "INV-2026-00002", "INV-2026-00003"]]]);
	});
});
