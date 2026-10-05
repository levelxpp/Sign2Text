# Contributing to Sign2Text

Welcome to the **Sign2Text** project! This document provides guidelines for future contributors, maintainers, and AI assistants to ensure the repository remains professional, secure, and maintainable.

---

## 🏛️ Project Structure Awareness

Sign2Text is a **multi-service, multi-surface** application. Before making changes, you must understand that several independent surfaces are tightly coupled through a shared routing layer and configuration.

### Surfaces & Services
-   **Main App**: Static files in `frontend/` (entry point `index.html`).
-   **Landing Site**: React/Vite source in `landing/src` (built to `landing/dist`).
-   **Admin Dashboard**: Admin management at `frontend/admin.html`.
-   **Legal**: Markdown sources in `docs/` and standalone HTML in `frontend/`.
-   **API Services**: Four FastAPI services (`ASL`, `Voice`, `Letter`, `Admin`) running on ports `8000-8003`.
-   **Frontend Server**: A custom routing layer in `backend/frontend_server.py` that unifies all these surfaces on port `5500`.

---

## 🚀 Development Workflow

To work on this project locally, follow these established steps:

1.  **Environment Setup**: Use `.\setup.ps1` to create and configure the Python virtual environment (`.venv`).
2.  **Launching**: Use `.\start.ps1` to boot the entire stack. This automatically generates a `runtime-config.js` for the frontend to discover the backend services.
3.  **Testing**: Before submitting changes, run the automated test suite:
    ```powershell
    .\.venv\Scripts\python.exe -m pytest
    ```
4.  **Verification**: After any UI or routing change, verify that all three surfaces (App, Admin, Landing) remain accessible via the `frontend_server.py` routing.

---

## 🛠️ Architectural & Coding Rules

-   **Preserve Routing Behavior**: Do not modify `backend/frontend_server.py` or `backend/runtime_config.py` unless the change is project-wide. Breaking these will break cross-service communication.
-   **Mode Isolation**: Keep the logic for **ASL**, **Voice**, and **Letter** modes separated. Avoid cross-pollinating their backend dependencies.
-   **Security Sensitivity**:
    -   Protect `X-Admin-Token` validation in `admin_server.py`.
    -   Ensure `OLLAMA_AUTH_TOKEN` and other secrets are **never** exposed to the frontend in `runtime-config.js`.
-   **Single Source of Truth**:
    -   Legal content is grounded in `docs/PRIVACY_POLICY.md` and `docs/TERMS_OF_USE.md`.
    -   Configuration defaults are grounded in `backend/runtime_config.py`.

---

## 📝 Documentation Expectations

-   **Repo-Grounded**: Ensure all documentation reflects the actual current code. Avoid "vague" or "future" promises unless clearly marked as a roadmap item.
-   **Index Maintenance**: When adding a new documentation file in `docs/`, update the index in **[docs/README.md](docs/README.md)** immediately.
-   **Consistency**: Keep paths and route names consistent with the architecture documented in `tech/ARCHITECTURE.md`.

---

## ✅ Pull Request Guidelines

1.  **Clear Scope**: Avoid unrelated refactors. Submit one logical change per PR.
2.  **Surface Impact**: List which surfaces are affected (e.g., "Frontend App + Admin API").
3.  **Verification Steps**: Include the specific manual or automated steps taken to verify the change.
4.  **Docs Check**: Confirm that relevant documentation has been updated to reflect the new behavior or configuration.

---

> [!TIP]
> If you are an AI assistant working on this repo, always read the documentation in `docs/` before proposing structural changes. Respect the existing multi-service boundaries.
