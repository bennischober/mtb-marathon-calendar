import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

import tailwindcss from "@tailwindcss/vite";
import { copyCalendarFiles } from "./src/integrations/copy-calendar-files.mjs";

export default defineConfig({
  // GitHub Pages serves project sites at <user>.github.io/<repo>/, so every
  // URL must carry the repo path prefix. Also used to build absolute
  // canonical/sitemap URLs.
  site: "https://bennischober.github.io",
  base: "/mtb-marathon-calendar",
  vite: {
    plugins: [tailwindcss()]
  },
  integrations: [sitemap(), copyCalendarFiles()]
});