# Third-Party Notices and Attribution

Sign2Text depends on third-party open-source software, third-party research
datasets, and the work of other team members. This document records those
attributions.

---

## 1. Team contributors

Sign2Text was built by a four-person team. **This public repository contains
only the portions authored by Koko Jamgotchian.** Work by other team members
remains their property and is not published here.

Two different responsibilities are recorded separately below, because they were
held by different people. **Writing the ML pipeline and training-framework code**
was Stepan Karapetyan's work. **Running the training, conducting the experiments,
monitoring optimization, comparing and selecting checkpoints, evaluating the
resulting models, and verifying the reported results** was Koko Jamgotchian's
work.

| Contributor | Contribution | In this repository? |
| --- | --- | --- |
| **Koko Jamgotchian** | Full-stack development: backend services (ASL API, Voice/auth, Letter, Admin), web frontend, admin dashboard, landing site, runtime configuration and launcher, test suite and CI, documentation. Machine learning: model training runs, experiments, training monitoring, checkpoint comparison and selection, evaluation, and results verification | **Yes** — source only; no weights or datasets |
| **Stepan Karapetyan** | ASL recognition pipeline implementation (`our_pipeline/`), ML training-framework and architecture code, Supabase auth module, letter MLP loader, mobile app | **No** — excluded |
| **Christopher Casas** | Mobile application | **No** — excluded |
| **armbotzzz** | Project contributions | **No** — excluded |

Where a Koko-authored service required a teammate-authored module in order to
import, an **independently written, interface-compatible replacement** is
supplied and clearly marked as such in its file header:

- `backend/auth_supabase.py`
- `backend/our_pipeline/__init__.py`, `backend/our_pipeline/adapter.py` (stub)
- `frontend/web/supabase-auth.js`
- `frontend/web/index.html`

No teammate-authored source is published in this repository.

---

## 2. Runtime dependencies

All consumed through package managers; none are vendored into this repository.
Each remains governed by its own licence.

### Python

| Package | Purpose | Licence |
| --- | --- | --- |
| FastAPI | Web framework for all four services | MIT |
| Uvicorn | ASGI server | BSD-3-Clause |
| Starlette | ASGI toolkit underlying FastAPI | BSD-3-Clause |
| Pydantic | Request/response validation | MIT |
| PyTorch | Model inference | BSD-3-Clause |
| ONNX Runtime | Pose-estimation inference backend | MIT |
| RTMLib | RTMW/RTMPose whole-body pose estimation | Apache-2.0 |
| MediaPipe | Hand landmark detection (Letter mode) | Apache-2.0 |
| OpenCV | Frame capture and image processing | Apache-2.0 |
| NumPy | Numerical arrays | BSD-3-Clause |
| scikit-learn | Letter-mode feature scaling and baselines | BSD-3-Clause |
| pandas | Dataset handling | BSD-3-Clause |
| httpx | HTTP client for auth and service calls | BSD-3-Clause |
| google-cloud-speech | Speech-to-Text | Apache-2.0 |
| google-cloud-texttospeech | Text-to-Speech | Apache-2.0 |
| pytest | Test framework | MIT |

### JavaScript

| Package | Purpose | Licence |
| --- | --- | --- |
| React 19 | Landing site UI | MIT |
| React DOM | Landing site rendering | MIT |
| React Router | Landing site routing | MIT |
| Vite 6 | Landing site build tooling | MIT |
| Framer Motion | Landing site animation | MIT |
| Lucide React | Icon set | ISC |
| `@supabase/supabase-js` | Browser auth client | MIT |
| `@google-cloud/text-to-speech` | Node TTS client | Apache-2.0 |
| Playwright | End-to-end testing | Apache-2.0 |
| Tailwind CSS | Utility styling (via CDN) | MIT |

### External services

Google Cloud Speech-to-Text · Google Cloud Text-to-Speech · Azure AI Translator
· Supabase (auth and Postgres) · Ollama-compatible LLM endpoint.

Each is used under its own terms of service. No credentials for any of them are
included in this repository.

### Fonts and icons

- **Manrope** — Google Fonts, SIL Open Font License 1.1
- **Material Symbols Outlined** — Google, Apache-2.0

---

## 3. Training datasets

**No datasets are distributed in this repository.** The models described in
[`MODEL_RESULTS.md`](MODEL_RESULTS.md) were trained on third-party research
datasets, each governed by its own licence and redistribution terms:

| Dataset | Used for | Notes |
| --- | --- | --- |
| **ASL Citizen** (Microsoft Research) | Word-level sign recognition | Released for research use under its own licence terms. |
| **Sem-Lex** | Word-level sign recognition | Research dataset with its own licence terms. |
| **PopSign** | Word-level sign recognition | Research dataset with its own licence terms. |

Anyone wishing to reproduce this work must obtain these datasets directly from
their original sources under their respective licences.

The fingerspelling dataset used for Letter mode was supplied by a team member,
and its origin is not documented in the project. It is therefore **not**
published here.

---

## 4. Model weights

**No trained model weights are distributed in this repository.**

Whether weights trained on research-licensed datasets constitute a derivative
work subject to those dataset licences is unsettled and varies by dataset. Given
that uncertainty, only architecture definitions, hyperparameters, label
mappings, and evaluation results are published. This is deliberate, not an
oversight.

---

## 5. Architectural references

The word-recognition model builds on published research architectures:

- **CTR-GCN** — Chen et al., *Channel-wise Topology Refinement Graph Convolution
  for Skeleton-Based Action Recognition*, ICCV 2021.
- **Conformer** — Gulati et al., *Conformer: Convolution-augmented Transformer
  for Speech Recognition*, INTERSPEECH 2020.
- **Transformer** — Vaswani et al., *Attention Is All You Need*, NeurIPS 2017.
- **RTMPose / RTMW** — Jiang et al., *RTMPose: Real-Time Multi-Person Pose
  Estimation*, 2023.

These are cited as design references. Implementations were written by the
project team.

---

## 6. Reporting an attribution problem

If you believe something here is attributed incorrectly, or that content has
been included without the right to publish it, contact
**contact@sign2text.dev** and it will be corrected or removed.
