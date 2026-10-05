# Installation Guide

This project is currently optimized for local Windows + PowerShell usage, but it also includes a Linux headless startup path for Azure/RunPod-style GPU hosts.

The repo supports two runtime install paths:

- `CUDA / NVIDIA` for machines with working NVIDIA drivers plus CUDA-enabled PyTorch and ONNX Runtime
- `CPU` for non-NVIDIA machines or fallback installs

## 1. Common Prerequisites

Before installing, make sure you have:

- Windows PowerShell for the Windows path, or Bash for the Linux VM path
- Python `3.11+` available on `PATH`
- A browser with camera access for the current `ASL` flow, or a local camera only if you intentionally use backend local-camera mode
- A microphone for `Voice` mode
- Ollama (local install or remote hosted endpoint) for the ASL gloss-to-English path
- A compatible ASL checkpoint file
- Google credentials if you want `Voice` mode
- Azure Translator credentials if you want translated voice captions

Important runtime defaults:

- Frontend: `http://127.0.0.1:5500`
- ASL API: `http://127.0.0.1:8000`
- Voice/Auth API: `http://127.0.0.1:8001`
- Default Ollama endpoint: `http://localhost:11434/api/chat`
- Default Ollama model: `qwen2.5:3b-instruct`
- Default checkpoint path: `backend\checkpoint\popsign_aslcitizen_semlex6_gcn_big\best_model.pt`

## 2. NVIDIA / CUDA Setup Path

### Recommended path

```powershell
.\setup.ps1
```

If `nvidia-smi` succeeds, `setup.ps1` selects the CUDA runtime automatically and installs `requirements.runtime.txt`.

To force CUDA explicitly:

```powershell
.\setup.ps1 -Profile cuda
```

### Manual equivalent

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.runtime.txt
```

### Test dependencies on CUDA machines

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.test.txt
```

## 3. Non-NVIDIA CPU Setup Path

Use this path on machines without an NVIDIA GPU, or when you want the simplest fallback install.

### Recommended path

```powershell
.\setup.ps1 -Profile cpu
```

That installs `requirements.runtime-cpu.txt`.

### Manual equivalent

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.runtime-cpu.txt
```

### Test dependencies on CPU-only machines

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.test-cpu.txt
```

### DirectML note

The runtime reports DirectML availability for diagnostics, but the current RTMLib path does not use DirectML. For non-NVIDIA Windows machines, use the CPU profile.

## 4. Runtime Install Commands Summary

CUDA runtime:

```powershell
pip install -r requirements.runtime.txt
```

CPU runtime:

```powershell
pip install -r requirements.runtime-cpu.txt
```

CUDA tests:

```powershell
pip install -r requirements.test.txt
```

CPU tests:

```powershell
pip install -r requirements.test-cpu.txt
```

## 5. Ollama Requirements

The ASL backend performs inference on the host or deployment machine. After sentence boundary detection, the gloss-to-English normalization step can call either a local Ollama instance or a hosted or remote Ollama-compatible endpoint.

If you want to skip normalization entirely, `start.ps1 -NoOllama` sets `ASL_DISABLE_TRANSLATION=1` and leaves the ASL runtime in raw gloss mode while keeping recognition active.

### Option A - Local Ollama (default)

Install and prepare Ollama locally:

```powershell
ollama pull qwen2.5:3b-instruct
ollama serve
```

### Option B - Remote or hosted Ollama endpoint

Set these in your environment or `.env`:

```dotenv
OLLAMA_URL=https://your-hosted-ollama-endpoint/api/chat
OLLAMA_MODEL=qwen2.5:3b-instruct
OLLAMA_AUTH_TOKEN=
OLLAMA_MODE=remote
```

Fill `OLLAMA_AUTH_TOKEN` only if the hosted endpoint requires bearer authentication. `OLLAMA_AUTH_TOKEN` is backend-only and is never exposed to the browser or browser-facing runtime config.

Use `OLLAMA_MODE=remote` when you want startup and deployment helpers such as `setup_runpod.sh` and `start_server.sh` to skip local Ollama installation or startup. Runtime normalization behavior still comes from the configured `OLLAMA_URL`, `OLLAMA_MODEL`, and optional `OLLAMA_AUTH_TOKEN`.

### Configuration variables

- `OLLAMA_URL=http://localhost:11434/api/chat` - endpoint URL for the ASL normalizer. Local Ollama is the default.
- `OLLAMA_MODEL=qwen2.5:3b-instruct` - model name used for local or remote normalization.
- `OLLAMA_TIMEOUT_SEC=30` - HTTP request timeout in seconds.
- `OLLAMA_AUTH_TOKEN=` - optional backend-only bearer token for hosted endpoints that require auth. Leave blank for local Ollama.
- `OLLAMA_MODE=local` - `local` (default) or `remote`. This affects startup and deployment scripts only.

If a reachable Ollama endpoint is available at startup, request-time normalization failures fall back to the built-in validator and rule-based normalizer. If no endpoint is available at startup or translation is disabled, the ASL runtime can still recognize and buffer signs, but the frontend will not receive final English ASL caption lines.

## 6. Checkpoint Requirements

The ASL runtime expects a checkpoint file.

Default path:

```text
backend\checkpoint\popsign_aslcitizen_semlex6_gcn_big\best_model.pt
```

If your checkpoint lives elsewhere, set:

```powershell
$env:ASL_CHECKPOINT_PATH = "C:\path\to\best_model.pt"
```

Or place it in your `.env`:

```dotenv
ASL_CHECKPOINT_PATH=backend\checkpoint\popsign_aslcitizen_semlex6_gcn_big\best_model.pt
```

The runtime resolves relative checkpoint paths against the project root.

## 7. Credentials And Environment Requirements

You can configure runtime values through a root `.env`, `backend/.env`, or normal environment variables.

### Minimal ASL-only configuration

If you only want the ASL runtime and you are keeping the default checkpoint and Ollama settings, you may not need any extra env vars. Optional overrides:

```dotenv
ASL_CHECKPOINT_PATH=backend\checkpoint\popsign_aslcitizen_semlex6_gcn_big\best_model.pt
OLLAMA_URL=http://localhost:11434/api/chat
OLLAMA_MODEL=qwen2.5:3b-instruct
OLLAMA_TIMEOUT_SEC=30
OLLAMA_AUTH_TOKEN=
OLLAMA_MODE=local
```

For a public deployment set `APP_PUBLIC_HOST` plus `APP_PUBLIC_SCHEME=https` before startup.

### Full auth / cloud-service configuration

Authentication is handled by Supabase. Create a project at https://supabase.com, enable the Google provider (optional) and/or email+password sign-in, then copy the project URL and anon key into `.env`:

```dotenv
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_ANON_KEY=<your-publishable-anon-key>
FRONTEND_ORIGIN=http://127.0.0.1:5500

AZURE_TRANSLATOR_KEY=replace-with-azure-key
AZURE_TRANSLATOR_REGION=westus3
AZURE_TRANSLATOR_ENDPOINT=https://api.cognitive.microsofttranslator.com

GOOGLE_STT_CREDENTIALS=keys\google-stt-service-account.json
GOOGLE_TTS_CREDENTIALS=keys\google-tts-service-account.json

# Optional: enable the keypoints/skeleton SSE stream consumed by the web + mobile overlays.
KEYPOINTS_STREAM_ENABLED=false
```

The backend writes `SUPABASE_URL` and `SUPABASE_ANON_KEY` into `frontend/runtime-config.js` at startup so the browser Supabase JS SDK can read them via `window.APP_CONFIG`. No Google OAuth client ID / secret is required on the backend â€” if you want Google as a sign-in option, configure it as a provider inside your Supabase project.

Supported Google Cloud credential fallbacks in code (used by the Voice service for STT/TTS):

- `GOOGLE_STT_CREDENTIALS`
- `STT_KEY_PATH`
- `GOOGLE_TTS_CREDENTIALS`
- `TTS_KEY_PATH`
- `GOOGLE_APPLICATION_CREDENTIALS`

The secret loader also supports `*_FILE` variants, for example:

- `AZURE_TRANSLATOR_KEY_FILE`

### Credential file discovery

If you do not set the credential paths directly, the backend looks in `keys\` for common names, including:

- `google-stt-service-account.json`
- `google-tts-service-account.json`
- `google-service-account.json`
- legacy `stt-service-account.json`
- legacy `tts-service-account.json`

Use your own local credentials. Do not publish real secrets to GitHub.

### Optional contributor auto-reply configuration

The admin service can send an optional acknowledgement email after a
successful brand-new contributor lead is inserted.

```dotenv
CONTRIB_AUTOREPLY_ENABLED=false
CONTRIB_SMTP_HOST=smtp.example.com
CONTRIB_SMTP_PORT=587
CONTRIB_SMTP_USERNAME=mailer@example.com
CONTRIB_SMTP_PASSWORD=replace-with-smtp-password
CONTRIB_SMTP_USE_TLS=true
CONTRIB_FROM_EMAIL=noreply@example.com
CONTRIB_FROM_NAME=Sign2Text Team
```

Variable meanings:

- `CONTRIB_AUTOREPLY_ENABLED`: turns the optional acknowledgement email on or off
- `CONTRIB_SMTP_HOST`: SMTP server hostname
- `CONTRIB_SMTP_PORT`: SMTP port for a normal SMTP connection; defaults to `587`
- `CONTRIB_SMTP_USERNAME`: SMTP auth username; leave blank only if your relay does not require auth
- `CONTRIB_SMTP_PASSWORD`: SMTP auth password; the secret loader also supports `CONTRIB_SMTP_PASSWORD_FILE`
- `CONTRIB_SMTP_USE_TLS`: when `true`, upgrades the SMTP connection with STARTTLS
- `CONTRIB_FROM_EMAIL`: visible sender address shown to contributors
- `CONTRIB_FROM_NAME`: friendly sender name; defaults to `Sign2Text Team`

Operator notes:

- If the feature is disabled, contributor leads still save normally and no email is attempted.
- If config is incomplete, contributor leads still save normally and the helper skips sending.
- If SMTP send fails, contributor leads still save normally, the request still succeeds, and the failure is only logged safely.
- `CONTRIB_SMTP_USE_TLS=true` means STARTTLS on `smtplib.SMTP`.
- Implicit SSL, `SMTP_SSL`, and port `465` are not implemented by the current code.
- `CONTRIB_SMTP_USERNAME` and `CONTRIB_SMTP_PASSWORD` must both be set or both be empty.
- Some SMTP providers require `CONTRIB_FROM_EMAIL` to match the authenticated SMTP account or a verified alias.
- Delivery is best-effort via FastAPI `BackgroundTasks`; it is not a durable queue or retry system.

### Manual verification for contributor auto-reply

1. Set `CONTRIB_AUTOREPLY_ENABLED=true` and configure a real SMTP test account.
2. Start the stack normally.
3. Submit a brand-new contributor email through the live Contribute form for the first send test so the duplicate path does not interfere with verification.
4. Confirm the lead appears in the admin DB or admin UI.
5. Confirm the acknowledgement email is received.
6. Re-submit the same email and confirm the duplicate path still returns success but does not send a second email.
7. Submit an invalid email and confirm validation still rejects it with no email send.
8. Trigger the honeypot path in a controlled local test and confirm safe success with no email send.
9. Disable `CONTRIB_AUTOREPLY_ENABLED` and confirm a new lead still saves without sending email.
10. If your SMTP provider enforces sender restrictions, verify that `CONTRIB_FROM_EMAIL` is a matching or approved alias.

## 8. Azure Linux GPU VM Startup Path

This is the recommended Azure deployment path for the current repo. Use the
existing coordinated stack directly on the VM; Docker remains a secondary
option.

Task 6 summary:

- use the Azure portal as the primary VM-creation path
- use Azure CLI only for quota and SKU preflight
- preferred VM size: `Standard_NC4as_T4_v3`
- fallback order: `Standard_NC8as_T4_v3`, then `Standard_NC16as_T4_v3`
- preferred image: `Ubuntu Server 24.04 LTS - x64 Gen2`
- fallback image: `Ubuntu Server 22.04 LTS - x64 Gen2` only if you can still
  provide Python `3.11+` before setup
- Security type: `Standard`
- keep Trusted Launch, Secure Boot, and vTPM off
- use a `128 GB` or larger managed `Premium SSD` OS disk
- use SSH public-key auth and a `Standard` static public IP
- keep inbound `22` open for SSH only during the first bounded smoke
- keep `80`, `443`, `8080`, and the internal service ports closed publicly for
  the first smoke

Minimal runtime sequence on the VM:

1. Clone or copy the repo onto persistent managed storage at a path such as
   `/home/<user>/sign2text`.
2. Place `.env`, the ASL checkpoint, and any optional Google credential files
   on disk before startup.
3. Verify Python `3.11+`, `python3 -m venv`, free disk space, and the NVIDIA
   driver path if you expect CUDA.
4. Run:

```bash
bash setup_runpod.sh
```

5. Start the stack with:

```bash
bash start_server.sh --proxy
```

6. From your workstation, open an SSH local port forward such as:

```bash
ssh -L 8080:127.0.0.1:8080 <user>@<vm-public-ip-or-dns>
```

7. Use your local browser against:

- `http://localhost:8080/`
- `http://localhost:8080/index.html`
- `http://localhost:8080/admin.html`

Important first-smoke notes:

- `start.py` refuses to launch if the resolved ASL checkpoint file is missing.
- `SUPABASE_URL` and `SUPABASE_ANON_KEY` must be set â€” sign-in fails without them.
- `ADMIN_ACCESS_TOKEN` is required only if you plan to use `/admin.html` or the
  admin APIs.
- Voice features remain optional and require their Google and Azure credentials
  before those routes will work.
- The SSH-tunneled `localhost` path is recommended before TLS because browser
  camera and microphone APIs require a secure context.

For the authoritative Task 6 operator guide, including the exact Azure portal
checklist, `az vm list-usage` / `az vm list-skus` preflight commands, asset copy
examples, VM-side `curl` checks, and likely first failures, use
`docs/SIGN2TEXT_AZURE_VM_FIRST_RUNBOOK.md`.

## 9. Common Setup Notes

- `setup.ps1` installs the runtime profile only. It does not install pytest by itself.
- `start.ps1 -Profile auto|cuda|cpu` sets `ASL_RUNTIME_PROFILE` before launching `start.py`.
- In `auto`, CUDA is chosen only when both PyTorch CUDA and ONNX Runtime CUDA are available.
- The generated `frontend/runtime-config.js` keeps the frontend aligned with configured ports and backend URLs.
- No frontend build step is required for the shipped UI.
- The current `ASL` app flow uses the browser camera, uploads frames to the backend, and still supports backend-local camera mode as a fallback.
- If you need non-default ports or public URLs, the runtime config supports `ASL_API_PORT`, `VOICE_API_PORT`, `LETTER_API_PORT`, `ADMIN_API_PORT`, `FRONTEND_PORT`, `APP_PUBLIC_HOST`, `APP_PUBLIC_SCHEME`, `FRONTEND_PUBLIC_URL`, `ASL_API_BASE_URL`, `VOICE_API_BASE_URL`, `LETTER_API_BASE_URL`, `ADMIN_API_BASE_URL`, `VOICE_WS_URL`, and `SERVICE_TIMEOUT_SEC`.
- The voice backend can start even when Google or Azure credentials are missing, but the corresponding features will remain unavailable until configured.
- The repo includes offline extraction and training scripts, but there is no dedicated pinned requirements file for that research path yet. Expect to add extra packages such as dataset-processing and training-tooling dependencies before using those scripts.
