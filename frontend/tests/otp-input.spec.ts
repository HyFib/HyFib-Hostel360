import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import OtpInput from "@/components/OtpInput.vue";

function mountOtp(props: Record<string, unknown> = {}) {
	return mount(OtpInput, { props: { length: 6, modelValue: "", ...props }, attachTo: document.body });
}

function boxes(wrapper: ReturnType<typeof mountOtp>) {
	return wrapper.findAll("input");
}

describe("OtpInput", () => {
	it("renders one numeric box per digit and offers SMS autofill on the first", () => {
		const wrapper = mountOtp();
		const inputs = boxes(wrapper);
		expect(inputs).toHaveLength(6);
		expect(inputs[0].attributes("autocomplete")).toBe("one-time-code");
		expect(inputs[1].attributes("autocomplete")).toBe("off");
		for (const input of inputs) {
			expect(input.attributes("inputmode")).toBe("numeric");
			expect(input.attributes("aria-label")).toMatch(/Digit \d of 6/);
		}
		wrapper.unmount();
	});

	it("moves to the next box after a digit", async () => {
		const wrapper = mountOtp();
		const inputs = boxes(wrapper);
		await inputs[0].setValue("4");
		expect(document.activeElement).toBe(inputs[1].element);
		expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual(["4"]);
		wrapper.unmount();
	});

	it("ignores letters and symbols", async () => {
		const wrapper = mountOtp();
		const inputs = boxes(wrapper);
		await inputs[0].setValue("a");
		expect((inputs[0].element as HTMLInputElement).value).toBe("");
		expect(document.activeElement).not.toBe(inputs[1].element);
		wrapper.unmount();
	});

	it("spreads a pasted code across the boxes and reports completion", async () => {
		const wrapper = mountOtp();
		const inputs = boxes(wrapper);
		await inputs[2].trigger("paste", { clipboardData: { getData: () => " 123-456 " } });

		expect(inputs.map((input) => (input.element as HTMLInputElement).value)).toEqual(["1", "2", "3", "4", "5", "6"]);
		expect(wrapper.emitted("complete")).toEqual([["123456"]]);
		expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual(["123456"]);
		wrapper.unmount();
	});

	it("handles an SMS autofill that drops the whole code into the first box", async () => {
		const wrapper = mountOtp();
		await boxes(wrapper)[0].setValue("654321");
		expect(wrapper.emitted("complete")).toEqual([["654321"]]);
		wrapper.unmount();
	});

	it("goes back and clears the previous digit on backspace in an empty box", async () => {
		const wrapper = mountOtp();
		const inputs = boxes(wrapper);
		await inputs[0].setValue("7");
		await inputs[1].trigger("keydown", { key: "Backspace" });

		expect(document.activeElement).toBe(inputs[0].element);
		expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([""]);
		wrapper.unmount();
	});

	it("does not report completion for a partial code", async () => {
		const wrapper = mountOtp();
		const inputs = boxes(wrapper);
		await inputs[0].trigger("paste", { clipboardData: { getData: () => "123" } });
		expect(wrapper.emitted("complete")).toBeUndefined();
		expect(document.activeElement).toBe(inputs[3].element);
		wrapper.unmount();
	});
});
