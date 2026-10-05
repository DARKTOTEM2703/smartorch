// Copia la interfaz de chat del servidor (una sola fuente) a la extension.
// El panel nativo de VS Code carga estos mismos archivos como webview propio.
const fs = require("fs");
const path = require("path");

const src = path.join(__dirname, "..", "..", "server", "smartorch", "ui", "static");
const dst = path.join(__dirname, "..", "media", "chat");

if (!fs.existsSync(src)) {
  console.error(`No encuentro la interfaz en ${src}`);
  process.exit(1);
}
fs.mkdirSync(dst, { recursive: true });
for (const name of ["index.html", "app.css", "app.js"]) {
  fs.copyFileSync(path.join(src, name), path.join(dst, name));
}
console.log("Interfaz de chat sincronizada en media/chat");
