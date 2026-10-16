import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import LeaveForm from "@/components/LeaveForm.vue";

const NOW = new Date(2026, 9, 15, 9, 0, 0);

function mountForm() {
	return mount(LeaveForm, { props: { reductionMinDays: 3, now: () => NOW }, attachTo: document.body });
}

async function fill(wrapper: ReturnType<typeof mountForm>, values: Record<string, string>) {
	for (const [name, value] of Object.entries(values)) {
		const field = wrapper.find(`[name="${name}"]`);
		await field.setValue(value);
	}
}

describe("LeaveForm validation", () => {
	it("lists every missing field, marks them invalid and does not submit", async () => {
		const wrapper = mountForm();
		await wrapper.find("form").trigger("submit");

		expect(wrapper.emitted("submit")).toBeUndefined();
		expect(wrapper.find('[data-test="summary"]').attributes("role")).toBe("alert");
		for (const name of ["from_datetime", "to_datetime", "destination", "reason", "contact_during_leave"]) {
			const field = wrapper.find(`[name="${name}"]`);
			expect(field.attributes("aria-invalid"), name).toBe("true");
			const describedBy = field.attributes("aria-describedby") ?? "";
			expect(describedBy, name).toMatch(/-error/);
		}
		expect(document.activeElement).toBe(wrapper.find('[name="from_datetime"]').element);
		wrapper.unmount();
	});

	it("rejects a return before the leaving time", async () => {
		const wrapper = mountForm();
		await fill(wrapper, {
			from_datetime: "2026-10-16T17:00",
			to_datetime: "2026-10-16T08:00",
			destination: "Madurai",
			reason: "Sister's wedding",
			contact_during_leave: "9876543210",
		});
		await wrapper.find("form").trigger("submit");
		expect(wrapper.text()).toContain("The return time must be after the leaving time.");
		expect(wrapper.emitted("submit")).toBeUndefined();
		wrapper.unmount();
	});

	it("rejects a leaving time in the past and an invalid contact number", async () => {
		const wrapper = mountForm();
		await fill(wrapper, {
			from_datetime: "2026-10-14T17:00",
			to_datetime: "2026-10-18T17:00",
			destination: "Madurai",
			reason: "Home visit",
			contact_during_leave: "12345",
		});
		await wrapper.find("form").trigger("submit");
		expect(wrapper.text()).toContain("The leaving time can't be in the past.");
		expect(wrapper.text()).toContain("Enter a valid 10-digit mobile number.");
		expect(wrapper.emitted("submit")).toBeUndefined();
		wrapper.unmount();
	});

	it("submits clean values in Frappe's datetime format", async () => {
		const wrapper = mountForm();
		await fill(wrapper, {
			from_datetime: "2026-10-16T17:00",
			to_datetime: "2026-10-19T19:30",
			destination: "  Madurai  ",
			reason: "Home visit for Deepavali",
			contact_during_leave: "+91 98765 43210",
		});
		await wrapper.find('[role="switch"]').trigger("click");
		await wrapper.find("form").trigger("submit");

		expect(wrapper.emitted("submit")).toEqual([
			[
				{
					from_datetime: "2026-10-16 17:00:00",
					to_datetime: "2026-10-19 19:30:00",
					destination: "Madurai",
					reason: "Home visit for Deepavali",
					contact_during_leave: "9876543210",
					apply_mess_reduction: 1,
				},
			],
		]);
		wrapper.unmount();
	});

	it("does not request a mess reduction for leave shorter than the minimum", async () => {
		const wrapper = mountForm();
		await fill(wrapper, {
			from_datetime: "2026-10-16T17:00",
			to_datetime: "2026-10-17T19:00",
			destination: "Karaikudi town",
			reason: "Family function",
			contact_during_leave: "9876543210",
		});
		expect(wrapper.find('[role="switch"]').attributes("disabled")).toBeDefined();
		await wrapper.find("form").trigger("submit");
		expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({ apply_mess_reduction: 0 });
		wrapper.unmount();
	});
});
