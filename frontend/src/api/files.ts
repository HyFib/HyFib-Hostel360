/**
 * Files going to and coming from the portal: PDF downloads and photo uploads.
 */
import { t } from "@/i18n";
import { formatBytes } from "@/utils/format";

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const MAX_IMAGE_DIMENSION = 1600;
const JPEG_QUALITY = 0.82;
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"]);
const ALLOWED_EXTENSIONS = /\.(jpe?g|png|webp|gif|pdf)$/i;

/**
 * Save or show a downloaded file. A temporary link with `download` works on Android Chrome,
 * desktop browsers and iOS Safari (which opens its preview); window.open after an await is
 * often blocked as a pop-up, so it is not used.
 */
export function saveBlob(blob: Blob, filename: string): void {
	const url = URL.createObjectURL(blob);
	const link = document.createElement("a");
	link.href = url;
	link.download = filename;
	link.rel = "noopener";
	link.style.display = "none";
	document.body.appendChild(link);
	link.click();
	link.remove();
	// Give slow devices time to start reading the blob before it is released.
	window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function isImage(file: Blob & { name?: string }): boolean {
	return file.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|heic|heif)$/i.test(file.name || "");
}

/** Reason the file cannot be attached, or null when it is fine. */
export function checkAttachment(file: File): string | null {
	const typeOk = ALLOWED_TYPES.has(file.type) || (!file.type && ALLOWED_EXTENSIONS.test(file.name));
	if (!typeOk) return t("errors.fileType");
	if (file.size > MAX_UPLOAD_BYTES) {
		return t("errors.fileTooLarge", { name: file.name, size: formatBytes(MAX_UPLOAD_BYTES) });
	}
	return null;
}

async function decode(file: File): Promise<{ source: CanvasImageSource; width: number; height: number; close(): void }> {
	if (typeof createImageBitmap === "function") {
		const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
		return { source: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() };
	}
	const url = URL.createObjectURL(file);
	try {
		const image = new Image();
		image.decoding = "async";
		image.src = url;
		await image.decode();
		return { source: image, width: image.naturalWidth, height: image.naturalHeight, close: () => undefined };
	} finally {
		URL.revokeObjectURL(url);
	}
}

function jpegName(name: string): string {
	const base = name.replace(/\.[^.]+$/, "") || "photo";
	return `${base}.jpg`;
}

/**
 * Shrink photos to at most 1600 px on the long side and re-encode as JPEG.
 *
 * Phone cameras produce 4–12 MB images; the smaller copy uploads quickly on a weak
 * connection and re-encoding drops EXIF metadata such as the GPS position. PDFs, and images
 * the browser cannot decode, are sent unchanged.
 */
export async function prepareUpload(file: File, maxDimension = MAX_IMAGE_DIMENSION): Promise<File> {
	if (!isImage(file) || file.type === "image/gif") return file;
	let decoded;
	try {
		decoded = await decode(file);
	} catch {
		return file;
	}
	try {
		const scale = Math.min(1, maxDimension / Math.max(decoded.width, decoded.height));
		const width = Math.max(1, Math.round(decoded.width * scale));
		const height = Math.max(1, Math.round(decoded.height * scale));
		const canvas = document.createElement("canvas");
		canvas.width = width;
		canvas.height = height;
		const context = canvas.getContext("2d");
		if (!context) return file;
		// JPEG has no transparency; a white base keeps screenshots with alpha readable.
		context.fillStyle = "#ffffff";
		context.fillRect(0, 0, width, height);
		context.drawImage(decoded.source, 0, 0, width, height);
		const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
		if (!blob) return file;
		return new File([blob], jpegName(file.name), { type: "image/jpeg", lastModified: Date.now() });
	} finally {
		decoded.close();
	}
}
