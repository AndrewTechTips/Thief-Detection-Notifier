<div align="center">

  <h1>🚨 IoT Vision Hub</h1>

  <p>
    An asynchronous <strong>camera monitoring hub</strong> built with FastAPI and OpenCV.<br />
    It pulls video from your cameras, detects motion in real time, stores evidence snapshots,
    and pushes alerts and live feeds to your devices.
  </p>

  <p>
    <a href="https://github.com/AndrewTechTips/Thief-Detection-Notifier/actions/workflows/ci.yml"><img src="https://github.com/AndrewTechTips/Thief-Detection-Notifier/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  </p>

  <p>
    <img src="https://img.shields.io/badge/Python_3.14-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.14" />
    <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
    <img src="https://img.shields.io/badge/Pydantic_v2-E92063?style=for-the-badge&logo=pydantic&logoColor=white" alt="Pydantic v2" />
    <img src="https://img.shields.io/badge/OpenCV-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white" alt="OpenCV" />
    <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL" />
    <img src="https://img.shields.io/badge/uv-DE5FE9?style=for-the-badge&logo=uv&logoColor=white" alt="uv" />
  </p>

</div>

<br />

> 🚧 **Work in progress.** This project is being rebuilt from a single OpenCV script
> (*Thief Detection Notifier*) into a modular, async platform. Progress is tracked in
> [`ROADMAP.md`](ROADMAP.md).

---

## ✨ Planned Features

- **Multi-camera ingestion** — USB webcams, RTSP/HTTP IP cameras and video files, each on its own worker thread so the API never blocks
- **Smarter motion detection** — adaptive background model, debounced events, ROI masks and best-frame selection
- **Real-time delivery** — WebSocket alerts and MJPEG live streams
- **Notifications** — email alerts with the evidence snapshot attached, with retries and per-camera cooldowns
- **History** — events and snapshots stored in PostgreSQL and on disk, with a REST API to query them
- **Secure by default** — JWT auth, strict production config validation, secrets never logged

---

## 🏗️ Architecture

```
src/vision_hub/
├── core/       # Configuration, logging, security, errors, service container
├── api/        # FastAPI routers, dependencies, middleware
├── schemas/    # Pydantic request/response and WebSocket models
├── domain/     # Pure domain models and ports (Protocols)
├── services/   # Application logic orchestrating the ports
├── vision/     # Frame sources, motion detector, camera workers
├── realtime/   # WebSocket connections and live frame broadcasting
└── infra/      # Adapters: event bus, notifiers, storage, database
```

Key design decisions are recorded in [`ROADMAP.md`](ROADMAP.md#-architecture-decisions), and the rules every
endpoint follows (errors, pagination, timestamps, IDs) in [`docs/api-conventions.md`](docs/api-conventions.md).

---

## 🚀 Development

**Requirements:** [uv](https://docs.astral.sh/uv/) (it installs Python 3.14 automatically if needed).

```bash
git clone https://github.com/AndrewTechTips/Thief-Detection-Notifier.git
cd Thief-Detection-Notifier
uv sync                       # create .venv and install all dependencies
uv run pre-commit install     # enable lint/type checks on every commit
cp .env.example .env          # then adjust values
```

### Configuration

All settings are environment variables prefixed with `VISION_HUB_`, with `__` separating nested groups
(for example `VISION_HUB_SMTP__PASSWORD`). Every option is documented in [`.env.example`](.env.example).

When `VISION_HUB_APP__ENV=prod`, the app refuses to start without an explicit JWT secret, admin
password hash and database password, and rejects debug mode or wildcard CORS/hosts.

### Running the server

```bash
uv run vision-hub hash-password   # prompts for an admin password, prints its Argon2 hash
# put the hash in .env as VISION_HUB_SECURITY__ADMIN_PASSWORD_HASH='...'
uv run vision-hub serve --reload  # http://localhost:8000/docs
```

In Swagger UI, use **Authorize** with the admin username and password to call protected
endpoints. All routes except health probes and login require a bearer token.

### Cameras

Cameras are defined in a TOML file; [`devices.example.toml`](devices.example.toml) runs two
simulated cameras out of the box:

```bash
VISION_HUB_VISION__DEVICES_FILE=devices.example.toml uv run vision-hub serve
```

Each camera runs in its own thread (capture, motion detection, JPEG encoding), so the async API
is never blocked. Supported sources: `synthetic`, `webcam`, `rtsp` (reconnects automatically)
and `video_file`. Keep a real `devices.toml` private: it can contain camera passwords and is
git-ignored.

### Running with Docker

```bash
docker compose up --build         # http://localhost:8000/docs
```

The image is multi-stage, runs as a non-root user on a read-only filesystem, and has a built-in
health check. Configuration comes from `.env` (optional); snapshots persist in the `hub-data`
volume.

### Quality checks

```bash
uv run ruff check          # lint
uv run ruff format         # format
uv run mypy                # strict type checking
uv run pytest              # tests + coverage (minimum 85 %)
```

The same checks, plus a Docker build and smoke test, run in GitHub Actions on every push and pull
request.

---

## 📬 Contact

* **LinkedIn:** [Andrei Condrea](https://www.linkedin.com/in/andrei-condrea-b32148346)
* **Email:** condrea.andrey777@gmail.com

<p align="center">
  <i>"It won't stop them — but they'll know you know." 🎯</i>
</p>
