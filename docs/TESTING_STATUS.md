# Testing Status

Honest account of what the test suite covers, what is enforced in CI, and what
is not — with the reason for every exclusion.

**No test has been deleted, weakened, or silently skipped.** Every assertion in
the repository still runs. 41 tests sit outside the blocking CI scope; each
carries a written reason at the test itself, and all of them still execute in
the diagnostic workflow.

---

## Current results

| Scope | Result |
| --- | --- |
| **Blocking CI** (`pytest -m "not requires_private_assets and not legacy_auth"`) | **288 passed, 0 failed** |
| Deselected from blocking scope | 41 tests |
| Full inherited suite | 288 pass · 41 cannot pass in this environment by design |

Measured on the published source with no `.env`, no credentials, and no model
weights — what a fresh clone gets.

This repository's first public commit ran **225 passed, 88 failed, 14 errors**.
The section below explains what that was and what changed.

---

## What the 88 failures actually were

A single test-isolation defect accounted for **59 of them**, and all 14 errors
were one stale file path. Neither was a product regression.

### Root cause 1 — a leaked `torch` stub (59 failures)

`tests/test_asl_normalization.py` stubbed the heavy ML packages so it could run
in a minimal environment. Two problems:

1. The guard was `if "torch" not in sys.modules`. It never attempted the real
   import, so the stub was installed **even though `requirements.runtime-cpu.txt`
   pins `torch==2.10.0+cpu`** and real torch was available.
2. The stub was written straight into `sys.modules` at module import time and
   never removed.

Every module importing a service afterwards therefore got the stub.
`backend/api_server.py` and `backend/letter_server.py` configure threading at
import, and the stub had no `set_num_threads`, so eight entire modules failed
with `AttributeError` purely because of collection order.

**Fixed** with an autouse fixture that imports the real package first, stubs
only what is genuinely missing, evicts anything imported while a stub was
active, and restores `sys.modules` exactly — including on assertion failure.

### Root cause 2 — `set_num_interop_threads` is once-per-process

With the stub gone, the same tests hit a real constraint:

```
RuntimeError: cannot set number of interop threads after parallel work
has started or after set_num_interop_threads is called
```

`torch.set_num_threads()` is idempotent; `torch.set_num_interop_threads()` is
not. Both services call it at import, so importing them into one process — or
reloading either, which the tests do — aborted at import time.

**Fixed** in `backend/api_server.py` and `backend/letter_server.py` by
tolerating that specific `RuntimeError`. The first call wins, so the intended
single-thread cap still applies.

> A `hasattr` guard would **not** have fixed this. Both attributes exist in real
> torch; the problem was calling a once-only API twice. Guarding on attribute
> presence would have silently skipped the CPU cap instead.

### Root cause 3 — a stale file path (all 14 errors)

`tests/test_space_key_toggle.py` read `frontend/app.js`. The web frontend lives
at `frontend/web/`, so the module failed at collection with `FileNotFoundError`
and never executed a single assertion. **Fixed** by correcting the path; the
assertions are unchanged. All 14 now run and pass.

### Root cause 4 — expectations that outlived the code

Genuine drift, each fixed by updating the expectation to the current contract —
still strict equality, never relaxed to a subset check:

| Test | Drift |
| --- | --- |
| `test_runtime_config.py` (6 assertions) | `frontend_app_config` gained `KEYPOINTS_ENABLED` |
| `test_stt_backend.py::test_health_endpoint_reports_dependency_status` | `/health` reports `authProvider` instead of three cookie-session fields |
| `test_api_server_eventbus.py` | `translation` is deliberately **not** replayed to new subscribers |
| `test_letter_server.py` (StubDetector) | The detector now publishes `last_landmarks_norm` for the overlay |
| `test_letter_server.py` (transition noise) | A 0.9-confidence frame is a deliberate fast-accept, not noise |
| `test_session_config.py` (4 tests) | Auth is opt-in via `SUPABASE_ENABLED`; the token path must be enabled to be tested |

Two deserve their own note.

**Event replay.** The test asserted that a `translation` event *is* replayed to
a reconnecting client. `EventBus._REPLAYABLE_EVENT_TYPES` exists specifically to
stop that: replaying append-style events duplicated transcript entries when a
user switched modes quickly. The test was re-pointed at the current contract,
and a second test now pins the property the allowlist protects — append-style
events are not replayed, while live subscribers still receive them.

**Supabase config exposure.** `test_supabase_fields_never_in_frontend_app_config`
asserted that no key containing "supabase" reaches the browser. That contradicts
the design: the client SDK needs `SUPABASE_URL` and `SUPABASE_ANON_KEY` to sign
a user in, and the anon key is a public identifier constrained by row-level
security. The assertion now names the property that actually matters — the
**service-role key**, which bypasses row-level security, must never appear as a
key or a value, in the config dict or the rendered JavaScript. That is a
narrower and stronger security assertion, not a weaker one.

---

## Blocking CI scope

Defined by pytest markers in `pytest.ini`, not by a list of filenames:

```
pytest -m "not requires_private_assets and not legacy_auth"
```

Everything without one of those two markers is enforced. A test is never marked
because it fails.

### `requires_private_assets` — 26 tests

Needs something intentionally excluded from this repository, or hardware a CI
runner does not have.

| Tests | Requirement not available here |
| --- | --- |
| `test_api_server_auth.py` (15) | Builds a TestClient over the real ASL app, so the FastAPI lifespan opens the **host camera**, contacts **Ollama**, and loads `backend/checkpoint/.../best_model.pt` — an excluded weight file. Locally these hang for minutes on hardware and network access. |
| `test_letter_server_auth.py` (8) | Same pattern for the Letter app: excluded letter-model weights plus camera access. |
| `test_letter_server.py` (2) | `LetterPipeline.start()` imports `backend/letter_mlp.py`, a teammate-owned module that is not published. |
| `test_linux_helper_scripts.py` (1) | Runs `setup_runpod.sh`, private GPU-host provisioning tooling that is not published. |

`setup_runpod.sh` was **not** recreated as a stub. A wrapper script would make
the test green while asserting nothing about real behaviour. The equivalent test
for the published `start_server.sh` stays in the blocking scope.

### `legacy_auth` — 15 tests

All in `tests/test_stt_backend.py`. Authentication moved from cookie-backed
Google OAuth sessions to Supabase JWT. The published module deliberately no
longer defines `SESSION_SECRET`, `SESSION_COOKIE_SECURE`, `SessionMiddleware`,
or the OAuth state-token helpers, and the JWT guard reports
`"Missing Bearer token."` rather than `"Not authenticated"`.

These tests describe the removed design. They are **not** satisfied by adding a
session secret or a development fallback secret — that would reintroduce
precisely the insecure configuration the sanitized public build removes. They
are kept unmodified, pending rewrite against the JWT contract.

---

## Diagnostic workflow

`.github/workflows/diagnostic-tests.yml` runs the **entire** suite, including
both marked groups. It is triggered manually and weekly, deliberately not on
push or pull request: a check that is permanently red teaches people to ignore
CI.

It records the real pytest exit code, writes a GitHub step summary, and uploads
the full log as an artifact. Failures there are expected and categorised. **A
failure outside the two marked groups is a real regression** and will also be
failing the blocking `CI` workflow.

---

## Repair plan

Remaining work, highest value first:

1. **Rewrite the `legacy_auth` tests against the JWT contract** (15 tests).
   Fixtures should enable `SUPABASE_ENABLED`, stub token verification, and
   assert the JWT guard's behaviour. These tests assert the right *intent* —
   protected routes must reject unauthenticated callers — against the wrong
   implementation, so this is re-expression, not relaxation.
2. **Make the service apps testable without hardware.** The auth tests are
   integration tests wearing unit-test clothing: verifying that a route returns
   401 should not require a camera or a checkpoint. Injecting the pipeline
   dependency would move ~23 tests back into the blocking scope.
3. **Re-scope `test_linux_helper_scripts.py`** so it covers only published
   scripts.

---

## Running the tests

```bash
pytest -m "not requires_private_assets and not legacy_auth"   # blocking scope
pytest                                                        # everything
pytest -m requires_private_assets --collect-only              # see what is excluded
```

End-to-end Playwright specs (`tests_e2e/`) are not run in CI: they need all five
services live, a browser install, and camera permissions.
