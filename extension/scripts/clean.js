// Borra la carpeta de compilacion para que no queden modulos viejos en el paquete.
const fs = require("fs");
const path = require("path");
fs.rmSync(path.join(__dirname, "..", "out"), { recursive: true, force: true });
