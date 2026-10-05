# Project Structure

Layout of this public repository. Directories excluded from publication are
listed at the end with the reason.

```text
Sign2Text/
├── README.md                      Project overview, architecture, results
├── MY_CONTRIBUTIONS.md            Scoped account of my work, with team credits
├── MODEL_RESULTS.md               Verified metrics and their evidence sources
├── COPYRIGHT.md                   All-rights-reserved notice
├── THIRD_PARTY_ATTRIBUTION.md         Dependencies, datasets, contributor credits
├── CONTRIBUTING.md
├── PROJECT_STRUCTURE.md           This file
│
├── .env.example                   Configuration template (placeholders only)
├── .gitignore
├── .dockerignore
├── Dockerfile
├── pytest.ini
├── playwright.config.ts
├── package.json
├── requirements.runtime.txt       Pinned CUDA runtime deps
├── requirements.runtime-cpu.txt   Pinned CPU-only runtime deps
├── requirements.test.txt
├── requirements.test-cpu.txt
├── valid_words.txt                Letter-mode word-boundary dictionary
├── valid_words-313.txt
│
├── setup.ps1  setup.sh            Environment provisioning
├── start.ps1  start.sh            Local launch
├── start.py                       Multi-service launcher / supervisor
├── start_server.sh                Headless launch
├── tunnel_proxy.py                Single-port reverse proxy
│
├── .github/workflows/ci.yml       Python tests + landing build
│
├── backend/
│   ├── api_server.py              ASL service :8000 — SSE, MJPEG, frame upload
│   ├── stt_backend.py             Voice/auth service :8001 — WS STT, translate
│   ├── letter_server.py           Letter service :8002 — fingerspelling
│   ├── admin_server.py            Admin service :8003 — intake, feedback, CRUD
│   ├── frontend_server.py         Static router :5500
│   ├── runtime_config.py          Ports, URLs, checkpoints, profiles
│   ├── hardware.py                CUDA / CPU capability detection
│   ├── secret_config.py           Credential discovery (no secrets stored)
│   ├── session_config.py          Session behaviour
│   ├── browser_origin.py          CORS / origin resolution
│   ├── new_Inference.py           Gloss buffering + LLM caption generation
│   ├── translate.py               Azure Translator client
│   ├── tts_backend.py             Google TTS routes
│   ├── supabase_client.py         Supabase REST client
│   ├── supabase_repo.py           Repository layer with SQLite fallback
│   ├── auth_supabase.py           JWT guards (public replacement — see header)
│   ├── notifications/             Contributor auto-reply email
│   ├── our_pipeline/              ASL pipeline interface (stub — see header)
│   └── checkpoint/                Model METADATA only — no weights
│       ├── curated600_resume2/          config + 604-class vocab
│       ├── popsign_aslcitizen_semlex6_gcn_big/  config + 347-class labels
│       ├── letter_mlp/                  label order + EVAL_NOTES.md
│       └── letter_mlp_centered/         label order + eval.md
│
├── frontend/web/
│   ├── index.html                 App shell (public replacement — see header)
│   ├── app.js                     Three-mode state machine, sessions, captions
│   ├── camera.js                  Browser camera capture + frame streaming
│   ├── captions.js                Caption rendering
│   ├── googleSttSource.js         WebSocket STT client
│   ├── textSource.js              Text input source
│   ├── supabase-auth.js           Auth helpers (public replacement — see header)
│   ├── admin.html                 Admin dashboard
│   ├── privacy.html  terms.html   Public legal pages
│   └── style.css  legal.css
│
├── landing/                       React 19 + Vite 6 marketing site
│   ├── src/pages/                 Home, About, Contribute
│   ├── src/components/            Navbar, Footer, ParticleHero
│   └── src/styles.css
│
├── tests/                         22 pytest modules
├── tests_e2e/                     7 Playwright specs
├── tools/                         SQLite → Supabase migration
│
└── docs/
    ├── INSTALLATION_GUIDE.md  QUICK_START.md
    ├── PROJECT_FUNCTIONALITY_OVERVIEW.md
    ├── TESTING_STATUS.md          Test suite state, root causes, repair plan
    ├── PRIVACY_POLICY.md  TERMS_OF_USE.md
    ├── tech/     ARCHITECTURE.md, API_REFERENCE.md, CONFIGURATION.md
    ├── ml/       PIPELINE.md, SUPPORTED_SIGNS.md
    ├── ops/      DEPLOYMENT.md, SUPABASE_SETUP.md, TROUBLESHOOTING.md
    └── sql/      Supabase table definitions
```

## Not included

| Path | Reason |
| --- | --- |
| `backend/our_pipeline/` (full implementation) | Teammate-authored; a documented stub preserves the import surface |
| `backend/letter_mlp.py` | Teammate-authored |
| `frontend/mobile/` | Expo app, primarily teammate-authored |
| `backend/checkpoint/**/*.pt`, `*.ckpt`, `*.pkl` | ~185 MB of weights trained on research-licensed datasets |
| `backend/data/hand_signals.csv` | Dataset of undocumented provenance |
| `keys/`, `.env`, `data/` | Credentials, configuration, and runtime data |
| Training / keypoint-extraction scripts | Maintained privately |
| Deployment runbooks, roadmaps, task logs | Internal operational material |

See the *"What is and isn't in this repository"* section of the README for what
this means in practice.
