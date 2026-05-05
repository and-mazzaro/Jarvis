# Electron Assets

Place the following files here before running `npm run build`:

| File | Purpose |
|------|---------|
| `icon.ico` | App icon (Windows) — Recommended 256×256 px |
| `icon.png` | App icon (Linux/macOS) |

You can create `icon.ico` from any PNG using online tools such as:
- https://www.icoconverter.com/
- https://convertio.co/png-ico/

If you skip this step, `electron-builder` will use the default Electron icon.
The app will still run fine in development (`npm start`) without any icon file.
