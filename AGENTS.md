# Repository Guidelines

## Project Structure & Module Organization

CamFX is a Windows 11 desktop camera-effects app. Main Python code lives in `camfx/`; `main.py` starts the app. Key modules include `camfx/pipeline.py` for capture/effects orchestration, `camfx/segmentation.py` for ONNX background blur, `camfx/framing.py` for auto-framing, `camfx/virtualcam.py` for shared-memory output, and `camfx/ui/` for the WebView HTML/JS UI. Assets are in `assets/`, installer files in `installer/`, Media Foundation virtual camera code in `mfref/` and `mfcam/`, and helper/test scripts in `tools/`. Treat `build/`, `dist/`, `dist2/`, and `__pycache__/` as generated output unless a release task explicitly requires them.

## Build, Test, and Development Commands

Create and activate a local environment before installing dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Use `pip install -r requirements-faceswap.txt` only when working on the optional face-swap feature. Run `python build.py` to create the PyInstaller onedir app under `dist\CamFX\`; set `$env:CAMFX_DISTPATH='dist2'` if `dist` is locked. Build the installer with `ISCC installer\camfx.iss`. Driver/helper builds require Visual Studio Build Tools and Windows SDK; see `README.md` for the `msbuild` flow.

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, and clear module-level constants. Keep Windows-specific paths and process handling explicit. Prefer small, focused modules that fit the existing pipeline/UI/driver boundaries. Comments should explain non-obvious threading, Media Foundation, packaging, or model-loading behavior.

## Testing Guidelines

There is no central pytest configuration; tests are executable scripts. Run focused checks such as:

```powershell
python smoke_test.py
python tools\test_demand_logic.py
python tools\test_single_instance.py
python tools\test_providers.py
```

Add new regression checks under `tools/test_*.py` when covering specific bugs. Keep tests runnable without a physical camera when possible; document Windows-only requirements in the script docstring.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commits, often with scopes, for example `fix(pipeline): ...`, `test(sim): ...`, and `build: ...`. Follow that style and write subjects in the imperative or concise result form. Pull requests should describe the user-visible change, list commands run, mention Windows/driver/installer impact, and include screenshots only for UI changes. Link related issues or release notes when changing packaging or installer behavior.

## Security & Configuration Tips

Do not commit downloaded AI models, local virtual environments, or packaged app output. Face-swap dependencies and models are optional and carry non-commercial model constraints; keep changes to that path isolated and documented.
