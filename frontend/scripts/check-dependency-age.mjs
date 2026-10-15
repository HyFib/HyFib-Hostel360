/**
 * Fails when yarn.lock contains a package version published less than N days ago (default 14).
 *
 * Fresh releases are where hijacked or broken packages show up first, so new versions are
 * only taken once they have been public for two weeks. Run before committing a lockfile
 * change and in CI:  `yarn deps:age`  or  `node scripts/check-dependency-age.mjs 14`.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const minDays = Number(process.argv[2] || 14);
const lockfile = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "yarn.lock"), "utf8");
const cutoff = Date.now() - minDays * 86_400_000;

const wanted = new Map();
for (const block of lockfile.split("\n\n")) {
	const head = block.split("\n").find((line) => line && !line.startsWith("#"));
	const version = /^ {2}version "([^"]+)"/m.exec(block);
	if (!head || !version) continue;
	const first = head.split(",")[0].trim().replace(/^"|"$/g, "").replace(/:$/, "");
	const name = first.slice(0, first.lastIndexOf("@"));
	if (!wanted.has(name)) wanted.set(name, new Set());
	wanted.get(name).add(version[1]);
}

const names = [...wanted.keys()];
const fresh = [];
const failed = [];
async function check(name) {
	try {
		const response = await fetch(`https://registry.npmjs.org/${name.replace("/", "%2F")}`, {
			headers: { Accept: "application/json" },
		});
		const time = (await response.json()).time || {};
		for (const version of wanted.get(name)) {
			const published = Date.parse(time[version] || "");
			if (!published || published > cutoff)
				fresh.push(`${name}@${version} (${time[version]?.slice(0, 10) || "unknown"})`);
		}
	} catch (error) {
		failed.push(`${name}: ${error.message}`);
	}
}
for (let i = 0; i < names.length; i += 16) await Promise.all(names.slice(i, i + 16).map(check));

console.log(`Checked ${names.length} packages against a ${minDays}-day minimum age.`);
if (failed.length) console.log(`Could not check:\n  ${failed.join("\n  ")}`);
if (fresh.length) {
	console.log(`Too new:\n  ${fresh.sort().join("\n  ")}`);
	process.exit(1);
}
if (failed.length) process.exit(2);
