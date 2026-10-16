/** Download a PDF from the portal API with a busy state and an error toast. */
import { ref } from "vue";
import { errorMessage, type DownloadedFile } from "@/api/client";
import { saveBlob } from "@/api/files";
import { t } from "@/i18n";
import { showToast } from "@/stores/toast";

export function useDownload() {
	const busyKey = ref<string | null>(null);

	async function download(key: string, fetchFile: () => Promise<DownloadedFile>): Promise<void> {
		if (busyKey.value) return;
		busyKey.value = key;
		try {
			const file = await fetchFile();
			saveBlob(file.blob, file.filename);
			showToast(t("common.downloadStarted"), { tone: "info" });
		} catch (error) {
			showToast(errorMessage(error), { tone: "error" });
		} finally {
			busyKey.value = null;
		}
	}

	return { busyKey, download };
}
