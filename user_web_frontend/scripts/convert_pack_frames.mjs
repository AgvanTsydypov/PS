// Converts public/pack/frames/*.png -> *.webp (resized, alpha preserved).
// Run: node scripts/convert_pack_frames.mjs
import { readdir, stat } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRAMES_DIR = join(__dirname, "..", "public", "pack", "frames");

const TARGET_WIDTH = 800; // display is ~480px wide; 800 covers HiDPI
const QUALITY = 80;

async function main() {
  const files = (await readdir(FRAMES_DIR))
    .filter((f) => f.toLowerCase().endsWith(".png"))
    .sort();

  if (files.length === 0) {
    console.error("No PNG frames found in", FRAMES_DIR);
    process.exit(1);
  }

  console.log(`Converting ${files.length} PNG -> WebP (width=${TARGET_WIDTH}, q=${QUALITY})`);
  let totalIn = 0;
  let totalOut = 0;

  for (const file of files) {
    const inPath = join(FRAMES_DIR, file);
    const outPath = inPath.replace(/\.png$/i, ".webp");
    const { size: inSize } = await stat(inPath);
    const out = await sharp(inPath)
      .resize({ width: TARGET_WIDTH, withoutEnlargement: true })
      .webp({ quality: QUALITY, alphaQuality: 90, effort: 5 })
      .toFile(outPath);
    totalIn += inSize;
    totalOut += out.size;
    process.stdout.write(
      `\r${file} -> ${(out.size / 1024).toFixed(0)}KB   `
    );
  }

  console.log(
    `\nDone. ${(totalIn / 1024 / 1024).toFixed(1)}MB PNG -> ${(totalOut / 1024 / 1024).toFixed(1)}MB WebP`
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
