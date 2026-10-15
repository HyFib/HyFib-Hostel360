import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";
import { VitePWA } from "vite-plugin-pwa";
import { frappePage } from "./plugins/frappe-page.ts";

/** Public path of the built assets (served by Frappe from hostel360/public). */
const ASSET_BASE = "/assets/hostel360/frontend/";
/** Route of the portal page; the service worker controls everything under it. */
const PORTAL_SCOPE = "/hostel";
const THEME_COLOR = "#0B4F9C";

export default defineConfig(({ command, mode }) => {
	const env = loadEnv(mode, process.cwd(), "H360_");
	const backend = env.H360_BACKEND || "http://localhost:8000";
	const proxy: ProxyOptions = { target: backend, changeOrigin: false, secure: false, ws: false };

	return {
		// The dev server serves the app where Frappe would, so deep links behave the same.
		base: command === "build" ? ASSET_BASE : `${PORTAL_SCOPE}/`,
		resolve: {
			alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
		},
		plugins: [
			vue(),
			tailwindcss(),
			VitePWA({
				registerType: "prompt",
				injectRegister: false,
				scope: PORTAL_SCOPE,
				filename: "sw.js",
				manifestFilename: "manifest.webmanifest",
				includeAssets: ["favicon.svg", "icons/apple-touch-icon.png"],
				manifest: {
					id: `${PORTAL_SCOPE}/`,
					name: "Hostel360 Portal",
					short_name: "Hostel360",
					description: "Hostel fees, mess, leave, grievances and certificates for students and parents.",
					lang: "en",
					dir: "ltr",
					start_url: `${PORTAL_SCOPE}/`,
					scope: PORTAL_SCOPE,
					display: "standalone",
					orientation: "portrait",
					background_color: "#F5F7FB",
					theme_color: THEME_COLOR,
					categories: ["education", "productivity"],
					icons: [
						{ src: `${ASSET_BASE}icons/icon-192.png`, sizes: "192x192", type: "image/png", purpose: "any" },
						{ src: `${ASSET_BASE}icons/icon-512.png`, sizes: "512x512", type: "image/png", purpose: "any" },
						{
							src: `${ASSET_BASE}icons/icon-maskable-512.png`,
							sizes: "512x512",
							type: "image/png",
							purpose: "maskable",
						},
					],
				},
				workbox: {
					// Only the static app shell is cached. The /hostel page itself carries the
					// session's CSRF token and boot data, and API responses carry personal data,
					// so neither is ever written to a cache.
					globPatterns: ["**/*.{js,css,html,svg,png,webmanifest}"],
					navigateFallback: null,
					cleanupOutdatedCaches: true,
					clientsClaim: true,
					runtimeCaching: [
						{
							urlPattern: ({ request, url }) =>
								request.mode === "navigate" &&
								(url.pathname === "/hostel" || url.pathname.startsWith("/hostel/")),
							handler: "NetworkOnly",
							options: {
								precacheFallback: { fallbackURL: `${ASSET_BASE}index.html` },
							},
						},
					],
				},
			}),
			frappePage({ output: fileURLToPath(new URL("../hostel360/www/hostel.html", import.meta.url)) }),
		],
		build: {
			outDir: "../hostel360/public/frontend",
			emptyOutDir: true,
			sourcemap: false,
			chunkSizeWarningLimit: 400,
			rolldownOptions: {
				output: {
					// One long-lived framework chunk: it changes only when Vue is upgraded, so
					// returning visitors keep it cached across portal releases.
					codeSplitting: {
						groups: [{ name: "vue", test: /node_modules[\\/](@vue|vue|vue-router)[\\/]/ }],
					},
				},
			},
		},
		server: {
			port: 8080,
			strictPort: false,
			proxy: {
				"/api": proxy,
				"/assets/frappe": proxy,
				"/files": proxy,
				"/private": proxy,
			},
		},
	};
});
