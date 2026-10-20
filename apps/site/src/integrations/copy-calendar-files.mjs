import { fileURLToPath } from "node:url";
import { readdir, copyFile, mkdir } from "node:fs/promises";
import path from "node:path";

/**
 * Copies the pipeline's per-event calendar files into the built site.
 *
 * The pipeline writes one .ics per edition to data/generated/events/ —
 * matching the site's /events/<slug>-<year>.ics URLs — but that directory is
 * outside apps/site/public/, so Astro would never ship them and every
 * "Add to calendar" download button would 404. Running in `astro build`'s
 * hooks, this bridges the two.
 */
export function copyCalendarFiles() {
  /** @type {import('astro').AstroIntegration} */
  const integration = {
    name: "copy-calendar-files",
    hooks: {
      "astro:build:done": async ({ logger, dir }) => {
        const outDir = fileURLToPath(dir);
        // dist -> site -> apps -> repo root
        const repoRoot = path.resolve(outDir, "..", "..", "..");
        const generatedEvents = path.join(repoRoot, "data", "generated", "events");
        const target = path.join(outDir, "events");

        let copied = 0;
        try {
          const files = await readdir(generatedEvents);
          await mkdir(target, { recursive: true });
          await Promise.all(
            files
              .filter((file) => file.endsWith(".ics"))
              .map(async (file) => {
                await copyFile(path.join(generatedEvents, file), path.join(target, file));
                copied += 1;
              }),
          );
        } catch (error) {
          logger.warn(
            `Per-event .ics files not copied (${error instanceof Error ? error.message : String(error)}) — "Add to calendar" buttons will 404.`,
          );
          return;
        }
        logger.info(`Copied ${copied} per-event .ics files into dist/events/`);
      },
    },
  };
  return integration;
}
