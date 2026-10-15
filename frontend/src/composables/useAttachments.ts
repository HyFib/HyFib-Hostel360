/**
 * Attachments picked on a form: checked, shrunk and uploaded as soon as they are chosen,
 * so the final submit only sends the file URLs.
 */
import { computed, onBeforeUnmount, reactive } from "vue";
import { errorMessage } from "@/api/client";
import { checkAttachment, isImage, prepareUpload, uploadRules } from "@/api/files";
import { uploadAttachment, type StudentArg } from "@/api/portal";
import { t } from "@/i18n";
import { session } from "@/stores/session";

export interface PendingAttachment {
	id: number;
	name: string;
	size: number;
	preview: string | null;
	status: "uploading" | "done" | "failed";
	error?: string;
	fileUrl?: string;
}

let sequence = 0;

/**
 * `student` gives the ward the form is for (a parent's selected ward): each upload is checked
 * against that ward on the server, not against whichever ward comes first.
 */
export function useAttachments(max: number, student: () => StudentArg = () => undefined) {
	const items = reactive<PendingAttachment[]>([]);
	const limits = () => uploadRules(session.settings.upload);

	const uploading = computed(() => items.some((item) => item.status === "uploading"));
	const fileUrls = computed(() => items.filter((item) => item.status === "done" && item.fileUrl).map((item) => item.fileUrl as string));
	const remaining = computed(() => Math.max(0, max - items.length));

	async function uploadOne(entry: PendingAttachment, file: File) {
		try {
			const prepared = await prepareUpload(file);
			const problem = checkAttachment(prepared, limits());
			if (problem) throw new Error(problem);
			const result = await uploadAttachment(prepared, student());
			entry.fileUrl = result.file_url;
			entry.size = prepared.size;
			entry.status = "done";
		} catch (error) {
			entry.status = "failed";
			entry.error = error instanceof Error && !("kind" in error) ? error.message : errorMessage(error);
		}
	}

	/** Returns messages for files that were refused before uploading. */
	function add(files: FileList | File[]): string[] {
		const problems: string[] = [];
		for (const file of Array.from(files)) {
			if (items.length >= max) {
				problems.push(t("grievances.tooManyFiles", { count: max }));
				break;
			}
			// Type is checked up front; size is checked after shrinking, since a large photo usually becomes small.
			const problem = checkAttachment(new File([], file.name, { type: file.type }), limits());
			if (problem) {
				problems.push(`${file.name}: ${problem}`);
				continue;
			}
			const entry = reactive<PendingAttachment>({
				id: ++sequence,
				name: file.name,
				size: file.size,
				preview: isImage(file) ? URL.createObjectURL(file) : null,
				status: "uploading",
			});
			items.push(entry);
			void uploadOne(entry, file);
		}
		return problems;
	}

	function remove(id: number) {
		const index = items.findIndex((item) => item.id === id);
		if (index < 0) return;
		const [item] = items.splice(index, 1);
		if (item.preview) URL.revokeObjectURL(item.preview);
	}

	onBeforeUnmount(() => {
		for (const item of items) if (item.preview) URL.revokeObjectURL(item.preview);
	});

	return { items, uploading, fileUrls, remaining, add, remove };
}
