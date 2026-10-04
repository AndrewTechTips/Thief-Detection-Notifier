// Writes Brotli (.br) and gzip (.gz) copies of the built text assets, so the hub can send them
// compressed without compressing on every request (or touching its streaming responses).

import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

const DIST = new URL("../dist/", import.meta.url).pathname;
const TEXT = new Set([".html", ".js", ".css", ".svg", ".json", ".webmanifest", ".txt"]);
const MIN_BYTES = 1024; // smaller files gain nothing from compression

/** @param {string} directory @returns {AsyncGenerator<string>} */
async function* files(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) yield* files(path);
    else yield path;
  }
}

let count = 0;
let before = 0;
let after = 0;
for await (const path of files(DIST)) {
  if (!TEXT.has(extname(path)) || (await stat(path)).size < MIN_BYTES) continue;
  const data = await readFile(path);
  const brotli = brotliCompressSync(data, {
    params: {
      [constants.BROTLI_PARAM_QUALITY]: constants.BROTLI_MAX_QUALITY,
      [constants.BROTLI_PARAM_SIZE_HINT]: data.length,
    },
  });
  await writeFile(`${path}.br`, brotli);
  await writeFile(`${path}.gz`, gzipSync(data, { level: 9 }));
  count += 1;
  before += data.length;
  after += brotli.length;
}
console.log(
  `compressed ${count} files: ${(before / 1024).toFixed(1)} kB → ${(after / 1024).toFixed(1)} kB (brotli)`,
);
