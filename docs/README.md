# Sign2Text Documentation

Documentation for **Sign2Text**, a multi-surface live captioning application.
This directory covers architecture, configuration, setup, machine learning, and
legal compliance.

---

## 🛠️ Technical Documentation

How the system is built and configured.

- **[System Architecture](tech/ARCHITECTURE.md)**: Multi-service design, frontend routing, and data flow.
- **[Runtime Configuration](tech/CONFIGURATION.md)**: Environment variables, runtime profiles, and service ports.
- **[API Reference](tech/API_REFERENCE.md)**: Routes, service endpoints, and event patterns.
- **[Project Functionality Overview](PROJECT_FUNCTIONALITY_OVERVIEW.md)**: How the stack is wired together.

## 🔧 Operational Guides

Running and managing the project.

- **[Deployment Guide](ops/DEPLOYMENT.md)**: Local and cloud deployment workflows, and startup readiness.
- **[Supabase Setup Guide](ops/SUPABASE_SETUP.md)**: Optional Supabase integration, SQL apply order, and migration tooling.
- **[Troubleshooting Guide](ops/TROUBLESHOOTING.md)**: Common failure points, configuration resets, and recovery steps.

## 🧠 Machine Learning

The recognition engine and model lifecycle.

- **[ML Pipeline](ml/PIPELINE.md)**: Extraction, training, and real-time inference overview.
- **[Supported Signs](ml/SUPPORTED_SIGNS.md)**: Recognition inventory and gloss-level model scope.
- **[Model Results](../MODEL_RESULTS.md)**: Verified evaluation metrics with their evidence sources.

## 🚀 Setup & Installation

- **[Installation Guide](INSTALLATION_GUIDE.md)**: Detailed setup for Windows and Linux.
- **[Quick Start](QUICK_START.md)**: The shortest path to booting the project locally.

## ⚖️ Contributing & Governance

- **[Contributor Guide](../CONTRIBUTING.md)**: Project structure, development workflow, and coding expectations.
- **[Testing Status](TESTING_STATUS.md)**: What the test suite covers, what currently passes, known failures and their root causes.

## ⚖️ Legal & Compliance

- **[Privacy Policy](PRIVACY_POLICY.md)**: How contributor and user data is handled.
- **[Terms of Use](TERMS_OF_USE.md)**: Terms governing use of the application and contributor program.
- **[Copyright Notice](../COPYRIGHT.md)**: Licensing position for this repository.
- **[Third-Party Attribution](../THIRD_PARTY_ATTRIBUTION.md)**: Dependencies, datasets, and contributor attribution.

---

## Not included in this repository

This is a curated public subset. The following documentation is maintained
privately and is intentionally not published:

- Admin operations guide (documents live admin authentication details)
- Deployment runbooks and infrastructure plans (Azure VM, RunPod)
- Public-tunnel setup instructions
- Internal roadmaps, task logs, and readiness audits
- Migration and feature implementation plans

See the *"What is and isn't in this repository"* section of the
**[root README](../README.md)** for the full list of exclusions and the reason
for each.

---

## 📂 Repository Structure

See **[PROJECT_STRUCTURE.md](../PROJECT_STRUCTURE.md)** for the full file layout.
