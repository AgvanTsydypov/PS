import fs from "fs";
import path from "path";

export function getManualContent(slug: string): string | null {
  const filePath = path.join(process.cwd(), "content", "system-manual", `${slug}.md`);
  try {
    return fs.readFileSync(filePath, "utf-8");
  } catch {
    return null;
  }
}
