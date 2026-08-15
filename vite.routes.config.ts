// Отдельная сборка карты /marshruty/: маленький IIFE-бандл с фиксированными
// именами файлов, чтобы серверный генератор страницы (scripts/routes_page.py)
// мог сослаться на них без манифеста. Основную сборку не трогает:
// emptyOutDir выключен, всё складывается в dist/assets рядом с ней.
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: false,
    cssCodeSplit: false,
    rollupOptions: {
      input: "src/marshruty/main.ts",
      output: {
        format: "iife",
        entryFileNames: "assets/marshruty-map.js",
        assetFileNames: "assets/marshruty-map[extname]"
      }
    }
  }
});
