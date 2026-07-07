# ShreyWS

Personal AI cloud, homelab, and multi-agent platform.

## Vision

ShreyWS is a self-hosted infrastructure platform designed to provide:

* Personal AI assistants
* Multi-agent orchestration
* Local and cloud LLM support
* Research and development environment
* Secure remote access
* Infrastructure monitoring
* Long-term reproducibility through Infrastructure as Code

The goal is for the entire platform to be reproducible from this repository while keeping all persistent data separate.

---

# Repository Structure

```text
infra/
├── compose/      # Docker Compose projects
├── docs/         # Documentation
├── scripts/      # Utility scripts
└── README.md
```

Persistent data is intentionally **not** stored in this repository.

Server layout:

```text
/srv/shreyws
├── infra/        # This repository
├── services/     # Persistent Docker volumes
├── models/       # Local AI models
├── agents/       # Agent definitions, prompts, memories
├── config/       # Shared configuration
├── backups/      # Backups
└── logs/         # Logs
```

---

# Current Services

* Grafana
* Prometheus
* Node Exporter
* cAdvisor

---

# Planned Services

* Traefik
* Authentik
* Hermes
* Open WebUI
* Ollama
* PostgreSQL
* Vector database
* Workflow automation
* Backup and monitoring services

---

# Design Principles

* Infrastructure as Code
* Docker-first deployment
* Persistent data separated from infrastructure
* Reproducible environments
* Modular services
* Security by default
* Long-term maintainability

---

# Deployment

Each service is deployed independently using Docker Compose.

Example:

```bash
cd compose/<service>
docker compose up -d
```

---

# License

This repository contains the infrastructure for the ShreyWS platform.
