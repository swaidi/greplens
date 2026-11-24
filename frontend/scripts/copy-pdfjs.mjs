// Copies pdf.js character maps and standard fonts so Arabic and CJK PDFs render offline.
import { cpSync, mkdirSync } from "node:fs";
mkdirSync("public/pdfjs", { recursive: true });
for (const dir of ["cmaps", "standard_fonts"]) cpSync(`node_modules/pdfjs-dist/${dir}`, `public/pdfjs/${dir}`, { recursive: true });
