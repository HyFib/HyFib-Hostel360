/** Run a form action once at a time and turn failures into a message. */
import { ref } from "vue";
import { errorMessage } from "@/api/client";

export function useSubmit() {
	const busy = ref(false);
	const error = ref("");

	async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
		if (busy.value) return undefined;
		busy.value = true;
		error.value = "";
		try {
			return await action();
		} catch (caught) {
			error.value = errorMessage(caught);
			return undefined;
		} finally {
			busy.value = false;
		}
	}

	return { busy, error, run };
}
