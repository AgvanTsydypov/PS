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

export function slugToLabel(slug: string): string {
  const withoutPrefix = slug.replace(/^\d+-/, "");
  return withoutPrefix.replace(/-/g, " ").toUpperCase();
}

export function getSections(): string[] {
  const contentDir = path.join(process.cwd(), "content", "system-manual");
  try {
    return fs
      .readdirSync(contentDir)
      .filter((name) => fs.statSync(path.join(contentDir, name)).isDirectory())
      .sort();
  } catch {
    return [];
  }
}

export function getSectionChildren(section: string): string[] {
  const sectionDir = path.join(process.cwd(), "content", "system-manual", section);
  try {
    return fs
      .readdirSync(sectionDir)
      .filter((name) => name.endsWith(".md") && name !== "index.md")
      .map((name) => name.replace(/\.md$/, ""))
      .sort();
  } catch {
    return [];
  }
}
