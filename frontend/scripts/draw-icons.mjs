/**
 * Draws the portal's app icons (PWA, Android home screen, iOS) as PNG files.
 *
 * No image tooling is needed on the build machine: shapes are rasterised here with 4×4
 * supersampling and written with Node's zlib. Run `yarn icons` after changing the design;
 * the PNGs are committed under public/icons.
 */
import { writeFileSync, mkdirSync } from "node:fs";
import { deflateSync } from "node:zlib";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "icons");
const TOP = [0x08, 0x3b, 0x75];
const BOTTOM = [0x0e, 0x74, 0x90];
const RING = [0x67, 0xe8, 0xf9];
const WHITE = [0xff, 0xff, 0xff];
const SAMPLES = 4;

const CRC_TABLE = new Uint32Array(256).map((_, n) => {
	let c = n;
	for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
	return c >>> 0;
});

function crc32(buffer) {
	let crc = 0xffffffff;
	for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
	return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
	const length = Buffer.alloc(4);
	length.writeUInt32BE(data.length);
	const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
	const crc = Buffer.alloc(4);
	crc.writeUInt32BE(crc32(body));
	return Buffer.concat([length, body, crc]);
}

function encodePng(size, rgba) {
	const header = Buffer.alloc(13);
	header.writeUInt32BE(size, 0);
	header.writeUInt32BE(size, 4);
	header[8] = 8; // bit depth
	header[9] = 6; // RGBA
	const raw = Buffer.alloc((size * 4 + 1) * size);
	for (let y = 0; y < size; y++) {
		raw[y * (size * 4 + 1)] = 0; // no filter
		rgba.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
	}
	return Buffer.concat([
		Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
		chunk("IHDR", header),
		chunk("IDAT", deflateSync(raw, { level: 9 })),
		chunk("IEND", Buffer.alloc(0)),
	]);
}

/** Shapes in unit coordinates (0..1). `scale` shrinks the glyph for maskable icons. */
function shapes(rounded, scale) {
	const radius = rounded ? 0.22 : 0;
	const inBackground = (x, y) => {
		if (!rounded) return true;
		const cx = Math.min(Math.max(x, radius), 1 - radius);
		const cy = Math.min(Math.max(y, radius), 1 - radius);
		return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2;
	};
	const toGlyph = (v) => (v - 0.5) / scale + 0.5;
	const inRing = (x, y) => {
		const gx = toGlyph(x) - 0.5;
		const gy = toGlyph(y) - 0.5;
		const r = Math.hypot(gx, gy);
		if (r < 0.315 || r > 0.375) return false;
		// Leave a gap at the upper right so the ring reads as a turning arrow (360°).
		const angle = Math.atan2(-gy, gx);
		return !(angle > 0.35 && angle < 1.05);
	};
	const inH = (x, y) => {
		const gx = toGlyph(x);
		const gy = toGlyph(y);
		const inside = (x0, y0, x1, y1) => gx >= x0 && gx <= x1 && gy >= y0 && gy <= y1;
		return inside(0.36, 0.33, 0.44, 0.67) || inside(0.56, 0.33, 0.64, 0.67) || inside(0.36, 0.465, 0.64, 0.535);
	};
	return { inBackground, inRing, inH };
}

function render(size, { rounded, scale }) {
	const { inBackground, inRing, inH } = shapes(rounded, scale);
	const pixels = Buffer.alloc(size * size * 4);
	for (let py = 0; py < size; py++) {
		for (let px = 0; px < size; px++) {
			let r = 0;
			let g = 0;
			let b = 0;
			let a = 0;
			for (let sy = 0; sy < SAMPLES; sy++) {
				for (let sx = 0; sx < SAMPLES; sx++) {
					const x = (px + (sx + 0.5) / SAMPLES) / size;
					const y = (py + (sy + 0.5) / SAMPLES) / size;
					if (!inBackground(x, y)) continue;
					const t = Math.min(1, Math.max(0, (x + y) / 2));
					let colour = TOP.map((c, i) => c + (BOTTOM[i] - c) * t);
					if (inRing(x, y)) colour = RING;
					if (inH(x, y)) colour = WHITE;
					r += colour[0];
					g += colour[1];
					b += colour[2];
					a += 1;
				}
			}
			const offset = (py * size + px) * 4;
			if (a) {
				pixels[offset] = Math.round(r / a);
				pixels[offset + 1] = Math.round(g / a);
				pixels[offset + 2] = Math.round(b / a);
			}
			pixels[offset + 3] = Math.round((a / (SAMPLES * SAMPLES)) * 255);
		}
	}
	return encodePng(size, pixels);
}

mkdirSync(OUT, { recursive: true });
const icons = [
	["icon-192.png", 192, { rounded: true, scale: 1 }],
	["icon-512.png", 512, { rounded: true, scale: 1 }],
	["icon-maskable-512.png", 512, { rounded: false, scale: 0.72 }],
	["apple-touch-icon.png", 180, { rounded: false, scale: 0.86 }],
];
for (const [name, size, options] of icons) {
	writeFileSync(join(OUT, name), render(size, options));
	console.log(`icons: ${name} (${size}×${size})`);
}
