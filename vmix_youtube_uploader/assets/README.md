# Application icons

`app.svg` is the editable master: a red YouTube-style play button with a green upload arrow over its upper-right corner, on a transparent background.

- `app.ico`: Windows application, executable, shortcut, tray, and installer icon. Contains 16, 20, 24, 32, 40, 48, 64, 128, and 256 pixel frames.
- `app-16.png` through `app-256.png`: individual transparent exports for standard and high-DPI UI sizes.
- `app-512.png`: large transparent export for release artwork.

The running app uses `app.ico` for its tray icon and application-wide window icon. `packaging/uploader.spec` bundles it as runtime data and embeds it in the executable; `packaging/installer.iss` uses it for the installer. Run the Windows release task to build both.

Regenerate all exports from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\build_icons.py
```

The export script uses Qt SVG rendering and supersampling. No image-generation service or extra image dependencies are needed.
