# My Contributions to Sign2Text

**Koko Jamgotchian**

Sign2Text was built by a four-person team. This document describes the work I
personally did. Where a subsystem was built by a teammate, it is credited to them
rather than claimed here — see
[`THIRD_PARTY_ATTRIBUTION.md`](THIRD_PARTY_ATTRIBUTION.md) for full team
attribution.

My contribution spanned two areas that are often held by different people on a
team: I built the full-stack application, and I trained and evaluated the
machine-learning models that it runs.

> **The distinction that matters most in this document.** The ASL recognition
> pipeline and the ML training-framework code were written by Stepan Karapetyan.
> **Running the training, designing and executing the experiments, monitoring
> optimization, comparing and selecting checkpoints, evaluating the models, and
> verifying the published results was my work.** Authoring a training framework
> and using it to produce trained models are separate responsibilities, and this
> repository credits them separately.

---

## Full-stack development

### System and architecture

- Designed and built the multi-service architecture that separates ASL
  inference, voice/auth, letter recognition, and admin behind a single shared
  frontend, with one static server routing all four browser surfaces.
- Built the runtime configuration layer (`backend/runtime_config.py`) that
  resolves ports, public URLs, credential paths, checkpoint paths, and hardware
  profile behaviour before any service starts.
- Built the launcher (`start.py`) that supervises five processes, generates a
  frontend runtime-config bridge so the browser discovers service endpoints
  instead of hardcoding them, and handles restart-safe shutdown.
- Implemented CUDA/CPU profile detection (`backend/hardware.py`) that requires
  **both** PyTorch and ONNX Runtime CUDA support before selecting the GPU path,
  falling back to CPU when either is incomplete — the failure mode that
  actually occurs in practice.
- Added headless and reverse-proxy startup paths (`start_server.sh`,
  `tunnel_proxy.py`) for hosted and demo environments.

### Backend services

- **ASL service** (`backend/api_server.py`, ~2,100 lines, ~80% mine) — pipeline
  lifecycle management, status and runtime diagnostics endpoints, SSE event
  delivery, MJPEG video streaming, WebRTC signalling, and remote frame upload. I
  integrated the ASL recognition pipeline behind this service; the pipeline
  implementation itself was written by Stepan Karapetyan.
- **Voice/auth service** (`backend/stt_backend.py`, ~890 lines) — WebSocket STT
  streaming, Google Speech-to-Text integration, Azure Translator requests,
  mounted TTS routes, transcript sync endpoints, and health reporting.
- **Letter service** (`backend/letter_server.py`, ~740 lines, ~81% mine) —
  receives browser frames, runs MediaPipe hand-landmark detection, classifies
  handshapes, and publishes letter and word events over SSE with repeat
  suppression and idle word completion. The MLP loader module was written by
  Stepan Karapetyan.
- **Admin service** (`backend/admin_server.py`, ~1,030 lines) — public
  contributor intake, public feedback collection, rate limiting, honeypot
  handling, lead lifecycle management, CSV export, and an optional
  acknowledgement email path.
- **Persistence layer** (`backend/supabase_client.py`,
  `backend/supabase_repo.py`, `tools/migrate_admin_sqlite_to_supabase.py`) —
  optional Supabase-backed storage for profiles, transcripts, leads, and
  feedback, with automatic SQLite fallback when Supabase is unavailable, plus a
  migration tool between the two.
- **Supporting modules** — credential discovery (`backend/secret_config.py`),
  browser-origin/CORS resolution (`backend/browser_origin.py`), session
  configuration, translation (`backend/translate.py`), and TTS
  (`backend/tts_backend.py`).

### Web frontend

- Built the multi-mode browser application (`frontend/web/app.js`, ~2,900
  lines) — a three-mode state machine coordinating ASL, Voice, and Letter
  workflows through one shared caption and session model.
- Implemented the live caption panel, rolling display behaviour, caption-size
  control, transcript export, and browser-local session history with a New Chat
  flow that archives non-empty sessions without changing modes.
- Wired consent handling, auth-aware UI state, and per-user scoping of local
  storage.
- Built browser camera capture and frame streaming (`frontend/web/camera.js`)
  over both WebSocket and HTTP upload paths, with automatic fallback when a
  proxy blocks socket upgrades.
- Built the standalone legal pages and the shared legal stylesheet.

### Admin dashboard

- Built the admin dashboard (`frontend/web/admin.html`, ~3,900 lines) — lead and
  feedback views, summary cards, search and status filtering, inline editing,
  CSV export, and a guarded delete-all flow, with explicit loading, empty,
  filtered-empty, and error states.

### Landing site

- Built the entire landing site (`landing/`, React 19 + Vite 6, ~3,300 lines) —
  Home, About, and Contribute pages, navigation, footer, and a canvas-based
  particle hero animation.
- Implemented the contributor interest form and public feedback form with
  client-side validation and inline loading, success, error, and rate-limit
  states.

---

## Testing and quality assurance

- Wrote the test suite: 22 pytest modules (~7,000 lines) and 7 Playwright
  end-to-end specs (~1,000 lines).
- Coverage includes runtime configuration and profile resolution, launcher
  behaviour, the ASL event bus and transport status, inference helpers,
  letter-recognition endpoints, per-service auth guards, translation and TTS
  paths, the Supabase client and repository layers, the SQLite→Supabase
  migration, and admin CRUD including rate limiting and honeypot behaviour.
- Set up the GitHub Actions CI pipeline: the marker-scoped public test run, a
  byte-compile syntax check, the landing-site build, and a relative-Markdown-link
  checker that fails the build on a broken documentation link.
- **Diagnosed and fixed the test-isolation defects** that the first public commit
  exposed. A `torch` stub leaking into `sys.modules` accounted for 59 failures
  across eight unrelated modules, and a once-per-process
  `set_num_interop_threads` call accounted for more. Both are documented with
  root causes in [`docs/TESTING_STATUS.md`](docs/TESTING_STATUS.md).
- Separated the permanently-failing-by-design tests into explicit markers rather
  than deleting them, and put the complete suite behind a scheduled diagnostic
  workflow so nothing is hidden.

**Current state:** the blocking CI scope runs **288 passed, 0 failed**, with 41
tests deselected under two documented markers — 26 that need excluded assets or a
physical camera, and 15 that assert the cookie-session auth contract Supabase JWT
replaced and are pending rewrite. No test was deleted or weakened to reach that
number.

---

## Machine-learning training and evaluation

This is my work, distinct from authorship of the training framework and pipeline
code, which is credited to Stepan Karapetyan above.

- **Prepared and ran the training jobs** for the published model variants,
  including the data configuration, augmentation schedule, and optimizer and
  scheduler settings recorded in the published training configs.
- **Ran the experiments** that produced the model variants compared in
  [`MODEL_RESULTS.md`](MODEL_RESULTS.md).
- **Monitored training and validation metrics** across runs — Top-1 accuracy,
  Top-5 accuracy, and loss — and captured the TensorBoard dashboard published at
  [`assets/images/tensorboard-training-metrics.png`](assets/images/tensorboard-training-metrics.png).
- **Compared checkpoints and selected the strong ones**, including the
  best-epoch checkpoints reported for `curated600_resume2` (epoch 181) and
  `popsign_aslcitizen_semlex6_gcn_big` (epoch 1,119).
- **Evaluated model performance** and reviewed Top-1 and Top-5 results against
  their validation splits.
- **Evaluated and replaced the fingerspelling classifier.** The original Letter
  mode used logistic regression; I benchmarked alternatives on an identical
  stratified 80/20 split with a fixed seed and moved it to a 3×256 MLP, taking
  test accuracy from 92.66% to **99.56%**, and to **99.68%** with a
  wrist-centred, palm-normalized variant that generalizes across signers.
- **Verified and documented every published result** against its source
  artifact — checkpoint-embedded metrics dictionaries and written evaluation
  notes. See [`MODEL_RESULTS.md`](MODEL_RESULTS.md), which also records one
  internal figure I could **not** verify and therefore excluded rather than
  reporting.
- **Integrated the trained checkpoints into the live runtime**, including
  config-driven model loading so training and inference stay aligned, and built
  the gloss-to-English normalization path (`backend/new_Inference.py`, ~1,890
  lines, ~97% mine) — gloss buffering, sentence-boundary detection, and LLM-based
  caption generation through an Ollama-compatible endpoint.

What I am **not** claiming: I did not author the ML pipeline implementation
(`backend/our_pipeline/`) or the training-framework and model-architecture code.
Those were written by Stepan Karapetyan and are not published here.

---

## Documentation and public release

- Wrote the architecture, API reference, configuration reference, installation,
  and quick-start documentation, plus the project overview under `docs/`.
- Wrote [`MODEL_RESULTS.md`](MODEL_RESULTS.md), tracing every reported metric to
  a specific stored artifact and explicitly excluding figures that could not be
  verified.
- Prepared this sanitized public repository: separating publishable work from
  teammate-owned and licence-restricted material, writing interface-compatible
  public replacements where a published service needed a teammate module to
  import, and labelling each one in its file header.
- Carried out the security and privacy review for publication — confirming no
  credentials, service-account files, datasets, model weights, or user data are
  present, and pinning that property with a test asserting the Supabase
  service-role key never reaches the browser config.
- Established a document-first legal infrastructure using Markdown as the single
  source of truth, with standalone public HTML pages reusing the project design
  system, and consistent legal links across the landing site, main app, admin
  dashboard, and contributor form.

---

## Collaborative and teammate-owned work

Sign2Text is a team project, and this repository is deliberately a subset of it.

| Area | Who |
| --- | --- |
| ASL recognition pipeline implementation (`backend/our_pipeline/`) | Stepan Karapetyan — excluded from this repository |
| ML training-framework and model-architecture code | Stepan Karapetyan — excluded |
| Supabase auth module | Stepan Karapetyan — public replacement written independently by me so the services stay importable |
| Letter MLP loader (`backend/letter_mlp.py`) | Stepan Karapetyan — excluded, with no public replacement |
| Mobile application | Stepan Karapetyan and Christopher Casas — excluded |
| Model training, experiments, evaluation, checkpoint selection, results analysis | **Me** |
| Backend services, web frontend, admin dashboard, landing site, runtime and launcher tooling, tests and CI, documentation | **Me** |

Integration work between my services and teammate-authored modules was
necessarily collaborative. Files marked "public replacement" in their headers are
original work written to satisfy the same interface — not stripped-down copies of
the private implementations.

---

## Key technical highlights

- Coordinated three distinct real-time transports where each fits best:
  WebSocket for bidirectional audio and camera frames, SSE for one-way event
  push, MJPEG for annotated video.
- Built a single application surface unifying three very different input
  modalities behind one caption and session model.
- Made runtime behaviour configurable across local, headless, CPU, CUDA, and
  proxied environments without branching the service layout.
- Took a fingerspelling classifier from 92.66% to 99.68% test accuracy through
  controlled benchmarking on a fixed split, rather than by changing the
  evaluation.
- Backed the product with a test suite covering both unit behaviour and
  end-to-end user flows, and kept the failing-by-design cases visible instead of
  deleting them.

---

## Team

| Contributor | Area |
| --- | --- |
| **Koko Jamgotchian** | Full-stack development, backend services, web frontend, admin dashboard, landing site, runtime and launcher tooling, testing and CI, documentation, and the machine-learning work: model training, experiments, evaluation, checkpoint selection, and results analysis |
| **Stepan Karapetyan** | ASL recognition pipeline implementation, ML training-framework and architecture code, Supabase auth module, letter MLP loader, mobile app |
| **Christopher Casas** | Mobile app |
| **armbotzzz** | Project contributions |
