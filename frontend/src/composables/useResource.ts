/**
 * Load data for a page, with loading / error state and reloads when the ward changes.
 *
 * Responses that arrive after a newer request started are ignored, so switching wards
 * quickly can never show one student's data under another's name.
 */
import { onMounted, ref, shallowRef, watch, type Ref, type WatchSource } from "vue";
import { ApiError } from "@/api/client";
import { t } from "@/i18n";

export interface Resource<T> {
	data: Ref<T | null>;
	error: Ref<ApiError | null>;
	loading: Ref<boolean>;
	reload: () => Promise<void>;
}

export function useResource<T>(
	loader: () => Promise<T>,
	options: { immediate?: boolean; watch?: WatchSource[] } = {},
): Resource<T> {
	const data = shallowRef<T | null>(null) as Ref<T | null>;
	const error = ref<ApiError | null>(null) as Ref<ApiError | null>;
	const loading = ref(false);
	let generation = 0;

	async function reload(): Promise<void> {
		const current = ++generation;
		loading.value = true;
		error.value = null;
		try {
			const result = await loader();
			if (current === generation) data.value = result;
		} catch (caught) {
			if (current !== generation) return;
			error.value =
				caught instanceof ApiError ? caught : new ApiError("server", t("errors.generic"));
		} finally {
			if (current === generation) loading.value = false;
		}
	}

	if (options.watch?.length) {
		watch(options.watch, () => {
			data.value = null;
			void reload();
		});
	}
	if (options.immediate !== false) onMounted(reload);

	return { data, error, loading, reload };
}
