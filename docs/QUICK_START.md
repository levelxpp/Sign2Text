# Quick Start

This is the shortest practical path to boot the current project on a local Windows machine.

## 1. Fastest Path To Run The App

From the repo root:

```powershell
.\setup.ps1
ollama pull qwen2.5:3b-instruct
ollama serve
.\start.ps1
```

This is the default local Ollama path. If you are using a hosted or remote Ollama-compatible endpoint instead, keep `.\setup.ps1` and `.\start.ps1`, but set `OLLAMA_URL`, `OLLAMA_MODEL`, optional backend-only `OLLAMA_AUTH_TOKEN`, and `OLLAMA_MODE=remote` before startup. That remote path changes normalization only; ASL inference still runs on this machine.

If you want to skip Ollama normalization entirely and work in raw gloss mode:

```powershell
.\setup.ps1
.\start.ps1 -NoOllama
```

Then open:

```text
http://127.0.0.1:5500/index.html
```

If `landing/dist/index.html` exists, `http://127.0.0.1:5500/` will open the landing site and the app will remain available at `/index.html`.

In the UI:

1. Choose `ASL` for the simplest first run.
2. Accept the in-app consent prompt.
3. Press `Start`.

`ASL` mode runs inference in the backend started by `start.py`. The current frontend default sends browser camera frames to that backend, and backend-local camera mode still exists as a fallback. `Voice` mode is the path that needs browser microphone permission.

If you want `Voice` mode, finish the credential and OAuth setup in [INSTALLATION_GUIDE.md](INSTALLATION_GUIDE.md) first.

## 2. How To Use `setup.ps1`

`setup.ps1`:

- finds Python on `PATH`
- creates `.venv` if needed
- auto-detects `cuda` vs `cpu` with `nvidia-smi`
- installs the matching runtime requirements

Examples:

```powershell
.\setup.ps1
.\setup.ps1 -Profile cuda
.\setup.ps1 -Profile cpu
```

Profile meanings:

- `auto`: choose CUDA when available, otherwise CPU
- `cuda`: force a CUDA attempt
- `cpu`: force CPU-only install

## 3. How To Use `start.ps1`

`start.ps1`:

- sets `ASL_RUNTIME_PROFILE`
- activates `.venv`
- runs `start.py`
- starts the frontend plus ASL, Voice, Letter, and Admin services

Examples:

```powershell
.\start.ps1
.\start.ps1 -Profile auto
.\start.ps1 -Profile cuda
.\start.ps1 -Profile cpu
.\start.ps1 -NoOllama
.\start.ps1 -Tunnel
```

Default service URLs:

- Frontend root: `http://127.0.0.1:5500/`
- Main app: `http://127.0.0.1:5500/index.html`
- ASL API: `http://127.0.0.1:8000`
- Voice/Auth API: `http://127.0.0.1:8001`
- Letter API: `http://127.0.0.1:8002`
- Admin API: `http://127.0.0.1:8003`

## 4. Verify The Runtime With `/runtime`

Once the stack is running, check the ASL hardware profile:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/runtime | Format-List
```

Look for fields such as:

- `profile`
- `source`
- `torch_cuda`
- `onnx_cuda`
- `onnx_providers`
- `gpu_name`
- `directml_supported`

Expected behavior:

- NVIDIA machine with full CUDA support: `profile` should be `cuda`
- Non-NVIDIA or incomplete CUDA install: `profile` should be `cpu`
- `directml_supported` is currently `False`

## 5. How To Run Tests

If you used the default CUDA setup:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.test.txt
.\.venv\Scripts\python.exe -m pytest
```

If you installed the CPU profile:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.test-cpu.txt
.\.venv\Scripts\python.exe -m pytest
```

## 6. Before You Start Troubleshooting

Check these local prerequisites first:

- checkpoint file available at the default path, or `ASL_CHECKPOINT_PATH` set
- reachable Ollama endpoint available for normalization (`OLLAMA_URL` local by default, or a hosted endpoint if configured)
- webcam connected for `ASL`
- microphone connected for `Voice`
- Google and Azure credentials configured if you want `Voice` mode
