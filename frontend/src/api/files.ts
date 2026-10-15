/**
 * Files going to and coming from the portal: PDF downloads and photo uploads.
 */
import { t } from "@/i18n";
import { formatBytes } from "@/utils/format";

/**
 * The server's limits for portal uploads (hostel360.security.files: 5 MB; PDF, JPG, PNG,
 * WEBP). get_session sends the live values; these are only the fallback before it answers.
 * Checking first means a file the server would refuse is never sent over mobile data.
 */
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
export const DEFAULT_UPLOAD_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "webp"] as const;
export const MAX_IMAGE_DIMENSION = 1600;
const JPEG_QUALITY = 0.82;
const MIME_TYPES: Record<string, string> = {
	pdf: "application/pdf",
	jpg: "image/jpeg",
	jpeg: "image/jpeg",
	png: "image/png",
	webp: "image/webp",
};

export interface UploadLimits {
	maxBytes: number;
	extensions: string[];
}

/** Limits from get_session, or the defaults when the server has not said. */
export function uploadRules(rules?: Readonly<{ max_bytes?: number; extensions?: readonly string[] }> | null): UploadLimits {
	const maxBytes = Number(rules?.max_bytes) > 0 ? Number(rules?.max_bytes) : MAX_UPLOAD_BYTES;
	const extensions = (Array.isArray(rules?.extensions) && rules.extensions.length ? rules.extensions : DEFAULT_UPLOAD_EXTENSIONS)
		.map((extension) => String(extension).toLowerCase().replace(/^\./, ""))
		.filter(Boolean);
	return { maxBytes, extensions };
}

/** Value for <input type="file" accept>, so the picker offers only files the server takes. */
export function acceptAttribute(limits: UploadLimits = uploadRules()): string {
	const types = new Set(limits.extensions.map((extension) => MIME_TYPES[extension]).filter(Boolean));
	return [...types].join(",");
}

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

function extensionOf(name: string): string {
	const match = /\.([A-Za-z0-9]+)$/.exec(name || "");
	return match ? match[1].toLowerCase() : "";
}

/** Reason the file cannot be attached, or null when it is fine. */
export function checkAttachment(file: File, limits: UploadLimits = uploadRules()): string | null {
	const allowedTypes = new Set(limits.extensions.map((extension) => MIME_TYPES[extension]).filter(Boolean));
	const extensionOk = limits.extensions.includes(extensionOf(file.name));
	// The server goes by the file name's extension; the browser's type must not contradict it.
	const typeOk = extensionOk && (!file.type || allowedTypes.has(file.type));
	if (!typeOk) return t("errors.fileType");
	if (file.size > limits.maxBytes) {
		return t("errors.fileTooLarge", { name: file.name, size: formatBytes(limits.maxBytes) });
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
