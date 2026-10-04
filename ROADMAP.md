# 🗺️ IoT Vision Hub — Roadmap

> **Source of truth** for the migration from the single-script *Thief Detection Notifier* into an
> asynchronous, modular **IoT Vision Hub** (FastAPI + OpenCV). The MVP runs as a **single process on a
> single node**; every infrastructure boundary sits behind an interface so it can scale out later.
>
> **How to use this file**
> - Tick a box (`- [x]`) only when the task is done and verified (linters/tests pass where applicable).
> - Commit each finished task (or small group) directly to `main` using Conventional Commits.
> - A phase is complete when all of its **exit criteria** are ticked.
> - Work top-to-bottom inside a phase.
> - If a decision changes, update the *Architecture Decisions* table first, then the tasks it affects.
> - After a context reset, read *Current Status* → *Architecture Decisions* → *Target Layout* → the first unchecked task.

---

## 📍 Current Status

- **Active phase:** Phase 4 — Frontend Dashboard (Phase 3 complete)
- **Working branch:** `main`
- **Next task:** 4.1 → "Scaffold `frontend/`"
- **Legacy code:** removed. The original script is the reference for porting the detector in Phase 2:
  `git show 14af13a:main.py` / `git show 14af13a:emailing.py`.

---

## 🧭 Architecture Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| AD-1 | Language / runtime | **Python 3.14**, `uv` for env + lockfile, `pyproject.toml` (PEP 621) | Already the local interpreter; stdlib `uuid.uuid7()`; `uv.lock` gives reproducible installs. |
| AD-2 | Web framework | **FastAPI** + **Uvicorn**, app factory + `lifespan` | Async-native, Pydantic v2 integration, first-class WebSockets. |
| AD-3 | Validation / config | **Pydantic v2** models, **pydantic-settings** (`.env`, `SecretStr`) | Typed config, secrets never logged. |
| AD-4 | Dependency injection | FastAPI `Depends` with `Annotated[...]` aliases; services built once in `lifespan` and exposed through typed lifespan state (`request.state.container`) | No module-level globals; trivially overridable in tests. |
| AD-5 | Layering | `api` → `services` → `domain` (pure) ← `infra` adapters, via `typing.Protocol` ports | Business logic testable without FastAPI, camera hardware, SMTP or a DB. |
| AD-6 | Ingestion model | **Pull**: the hub opens each video source (USB webcam, RTSP/HTTP IP camera, video file, synthetic) and runs detection locally | Cameras stay dumb; no edge agents to deploy or authenticate. |
| AD-7 | OpenCV concurrency | **One dedicated thread per camera** (OpenCV releases the GIL in its C++ calls) bridged to asyncio with `loop.call_soon_threadsafe`; OpenCV's own thread pool disabled | Never blocks the event loop; a long-lived thread keeps the capture device open. Benchmarked in 3.4: same throughput as a process per camera, 3x a process pool per frame (`docs/performance.md`). |
| AD-8 | Deployment topology | **Single process**: API + `CameraManager` in one Uvicorn process, run with **exactly one worker** | Cameras are owned by the process — multiple Uvicorn workers would each open every camera. |
| AD-9 | Internal messaging | `EventBus` port with an **in-memory** implementation only (asyncio fan-out, bounded per-subscriber queues). Redis is deferred to *Future* | Zero extra infrastructure for the MVP; the port keeps a later swap cheap. |
| AD-10 | Real-time transport | **WebSocket** for alerts/state (JSON envelopes, discriminated unions); **MJPEG** over HTTP for live video (WS binary frames as an option) | MJPEG works in a plain `<img>` tag and is cheap; WebRTC is a possible later upgrade. |
| AD-11 | Back-pressure | Frames: **latest-frame-wins** (drop stale frames per client). Events: **never dropped** — persisted to the DB *before* publishing, replayable with a cursor | Slow clients can't stall cameras or the loop; the in-memory bus isn't the system of record. |
| AD-12 | Auth | **Users:** OAuth2 password flow → short-lived JWT (+ rotating refresh tokens with reuse detection), Argon2id hashing (verified off the event loop). **Rate limiting:** `limits` library behind an injected `RateLimiter`, in memory. **WebSockets:** short-lived single-use ticket (in-memory TTL store), never long-lived tokens in URLs. **Cameras:** no inbound auth (pull model); their source credentials are stored as secrets and never returned by the API | Matches the pull model — only humans call the hub. |
| AD-13 | Database | **PostgreSQL 17** + **SQLAlchemy 2.1 async** (`asyncpg`) + **Alembic** (migrations on startup); SQLite (`aiosqlite`) only for tests. Secrets encrypted at rest (`MultiFernet`) | Relational device/event/user model; `JSONB` for flexible detection metadata; mature async story. |
| AD-14 | Snapshot storage | `SnapshotStore` port with a **local filesystem** implementation (`data/snapshots/`). DB stores metadata + relative path only. JPEG (q≈85), not PNG. S3/MinIO deferred to *Future* | Blobs out of the DB; ~10× smaller files than the current 2 MB PNGs; no extra infra. |
| AD-15 | Notifications | `Notifier` port: **async SMTP (`aiosmtplib`)** first, webhook/Telegram later. **DB-backed outbox**: one row per alert and channel, sent by a dispatcher with exponential retries and a max age; delivery is at-least-once and survives restarts | Replaces the fire-and-forget thread + bare `except`; an intrusion alert must not die with the process. |
| AD-16 | Quality gates | **ruff** (lint + format, replaces black), **mypy --strict**, **pytest** + `pytest-asyncio`, `httpx2.AsyncClient` + `asgi-lifespan`, **pre-commit**, GitHub Actions | |
| AD-17 | Observability | **structlog** (JSON in prod), request-ID middleware, `/health/live` + `/health/ready`, Prometheus `/metrics` (`prometheus-client`, per-app registry, scrape token) | Counters and histograms, rates derived in PromQL; route templates as labels keep cardinality bounded. |
| AD-18 | Packaging / deploy | Multi-stage **Docker** image (`opencv-python-headless`), `docker compose` with **api + postgres** only | Headless OpenCV: no `imshow` on a server. Minimal moving parts. |
| AD-19 | Frontend | **Vite** + **vanilla JS** (ES modules, no framework) + **Tailwind CSS v4** (`@tailwindcss/vite`, tokens in `@theme`). Native `fetch` and `WebSocket`. Types from OpenAPI (`openapi-typescript`) used through JSDoc and checked with `tsc --checkJs`; ESLint + Prettier; Vitest for logic. Dev: Vite proxies `/api` (HTTP + WS) to the hub, so the browser sees one origin. Prod: FastAPI serves the built files | Small, fast bundle with no runtime dependencies; the API is the contract. Same-origin in dev and prod: no CORS, tickets and MJPEG `<img>` work unchanged. |
| AD-20 | UI performance | Animate **only `transform` and `opacity`** (plus short `filter` fades); `backdrop-filter` on static chrome only, **never above live video** (an MJPEG frame under a blur forces a re-blur every frame); `prefers-reduced-motion` respected; streams pause when off-screen or the tab is hidden | Glass effects stay smooth on phones; idle tabs cost no bandwidth or CPU on the hub. |

---

## 🗂️ Target Layout

```
.
├── pyproject.toml            # deps, ruff, mypy, pytest config
├── uv.lock
├── .env.example              # every setting documented, no real secrets
├── Dockerfile
├── docker-compose.yml        # api + postgres
├── alembic/                  # migrations (Phase 3)
├── src/vision_hub/
│   ├── main.py               # create_app() factory + lifespan
│   ├── core/                 # config, logging, security, errors, container, tasks, lifecycle
│   ├── api/
│   │   ├── deps.py           # Annotated DI aliases (CurrentUser, DeviceSvc, ...)
│   │   ├── middleware.py     # request-id, security headers
│   │   └── v1/
│   │       ├── router.py
│   │       └── routes/       # health.py, auth.py, devices.py, events.py, stream.py, ws.py
│   ├── schemas/              # Pydantic request/response + WS message models
│   ├── domain/               # pure dataclasses/enums + ports (Protocols): EventBus, Notifier, SnapshotStore, repos
│   ├── services/             # device_service.py, event_service.py, notification_service.py, auth_service.py
│   ├── vision/
│   │   ├── sources.py        # FrameSource protocol: WebcamSource, RtspSource, VideoFileSource, SyntheticSource
│   │   ├── detector.py       # MotionDetector — pure: frame in → DetectionResult out
│   │   ├── tracker.py        # event state machine (debounce, cooldown, best-frame selection)
│   │   ├── worker.py         # CameraWorker thread: source → detector → tracker → bus
│   │   └── manager.py        # CameraManager: start/stop/supervise workers, restart with backoff
│   ├── realtime/             # ConnectionManager, per-client queues, frame broadcaster
│   └── infra/
│       ├── bus/              # memory.py
│       ├── notifiers/        # email.py, webhook.py
│       ├── storage/          # local.py
│       └── db/               # session.py, models.py, repositories/
├── data/                     # runtime snapshots (git-ignored)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/             # short test videos, synthetic frame generators
└── frontend/                 # Phase 4 (AD-19)
    ├── index.html
    ├── vite.config.js        # Tailwind plugin, /api proxy (HTTP + WS) to the hub
    ├── public/               # icons, manifest
    └── src/
        ├── main.js           # boot: session restore, router, realtime
        ├── styles/           # main.css: Tailwind + @theme tokens, components layer
        ├── api/              # client.js (fetch + refresh), schema.d.ts (generated), endpoints
        ├── realtime/         # socket.js: ticket → WS, reconnect + resume
        ├── state/            # tiny observable stores (session, devices, events)
        ├── ui/               # DOM helpers and components (toast, badge, dialog, tile)
        └── views/            # login, dashboard, events, device, audit
```

---

## Phase 1 — Backend Foundation & Security

**Goal:** a typed, tested, secured FastAPI skeleton with configuration, logging, auth and CI — no OpenCV yet.

### 1.0 Housekeeping & safety
- [x] Add `images/`, `snapshots/`, `data/` and `.idea/` to `.gitignore` (intruder photos must never be committed)
- [x] Remove `black` and its transitive deps from `requirements.txt` (dev tool mixed into runtime deps)
- [x] Create branch `feat/vision-hub` (merged into `main`; legacy script removed, kept in history at `14af13a`)
- [x] Document the legacy behaviour and its known defects (*Appendix A*)

### 1.1 Project tooling
- [x] Install `uv` and initialise `pyproject.toml` with `uv init --package` (name `vision-hub`, `requires-python = ">=3.14"`)
- [x] Add runtime deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `structlog`
- [x] Add dev deps: `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `httpx`, `pre-commit`, `pytest-cov`
- [x] Adopt `src/` layout (`src/vision_hub/`) as described in *Target Layout*
- [x] Configure `ruff` (lint rules incl. `I`, `UP`, `B`, `S`, `ASYNC`, `PT`; formatter on)
- [x] Configure `mypy --strict` with the Pydantic plugin
- [x] Configure `pytest` (`asyncio_mode = "auto"`, `--import-mode=importlib`, warnings as errors, 85 % coverage gate)
- [x] Add `.pre-commit-config.yaml` (ruff/mypy as local `uv run` hooks, `uv lock --check`, large-file guard, detect-private-key)
- [x] Replace `requirements.txt` with `uv.lock`

### 1.2 Configuration & environment
- [x] `core/config.py`: frozen `Settings(BaseSettings)` with nested groups (`app`, `security`, `smtp`, `db`, `storage`, `vision`)
- [x] Use `SecretStr` for every secret; `env_prefix="VISION_HUB_"`, `env_nested_delimiter="__"`; comma-separated lists
- [x] Validate at startup: prod fails fast listing *all* problems (explicit JWT secret, Argon2 admin hash, DB password, no debug, no `*` CORS/hosts)
- [x] Reject misspelled nested variables (`extra="forbid"` per group) and never echo input values in validation errors (`hide_input_in_errors`)
- [x] Expose settings through a cached `get_settings()` dependency (overridable in tests)
- [x] Write `.env.example` documenting every variable; a test keeps it in sync with `Settings`
- [x] SMTP credentials via `VISION_HUB_SMTP__USERNAME` / `VISION_HUB_SMTP__PASSWORD`; sender/recipients default to the username (legacy `EMAIL` / `PASSWORD` dropped)

### 1.3 Application core
- [x] `main.py`: `create_app(settings: Settings | None = None) -> FastAPI` factory
- [x] `lifespan` async context manager that builds and tears down the service container
- [x] `core/container.py`: frozen `Container` dataclass passed via typed lifespan state (no module globals)
- [x] `api/deps.py`: `Annotated` aliases (`SettingsDep`, `ContainerDep`), usable from HTTP and WebSocket routes
- [x] `core/logging.py`: structlog config (console in dev, JSON in prod, `LOG_FORMAT` override), stdlib + uvicorn log capture; HTTP-client loggers quieted so URLs with tokens are not logged
- [x] Request-ID middleware (pure ASGI; accept safe `X-Request-ID` or generate UUIDv7, bind to log context, one access-log line per request without query strings)
- [x] `core/errors.py`: domain exception hierarchy + handlers returning RFC 9457 `application/problem+json`; unhandled exceptions → generic 500 with request ID; validation errors never echo input
- [x] `vision-hub` CLI entrypoint (`--reload`; always a single Uvicorn worker, uvicorn logging routed through structlog)

### 1.4 Routing & API conventions
- [x] Versioned router mounted at `/api/v1`
- [x] `GET /api/v1/health/live` (process up) and `GET /api/v1/health/ready` (dependencies up): `HealthCheck` protocol registry run concurrently with per-check timeout, 503 without leaking failure details, `Cache-Control: no-store`, DEBUG-level access logs unless failing
- [x] Conventions doc in `docs/api-conventions.md`: plural nouns, cursor pagination envelope, UTC ISO-8601 timestamps, UUIDv7 ids, problem+json errors
- [x] Shared schemas: `Page[T]` generic + `PageParamsDep` + opaque cursors, `ApiSchema` (`from_attributes`) / `RequestSchema` (`extra="forbid"`), `UtcDateTime`; `ProblemDetail` done in 1.3
- [x] OpenAPI metadata (title, version, summary, tags, readable operation IDs, errors documented as problem+json); docs hidden in prod unless `APP__DOCS_ENABLED=true`

### 1.5 Security baseline
- [x] `core/security.py`: Argon2id password hashing (`pwdlib[argon2]`, verified in a worker thread, constant-time for unknown users), JWT encode/decode (`pyjwt`, algorithm pinned, required claims)
- [x] `POST /api/v1/auth/token` — OAuth2 password flow; bootstrap a single admin from settings (hashed password in env) until Phase 3 adds a users table
- [x] Short access-token TTL (15 min) + refresh token; `iss`/`aud`/`exp`/`typ` validated; `POST /auth/refresh` rotates with reuse detection, `POST /auth/logout` revokes (in-memory store, persisted in Phase 3)
- [x] `CurrentPrincipal` dependency; every non-public route protected by default (router-level dependency, enforced by a test); `GET /auth/me`
- [x] Role model: `admin` (manage devices/config) and `viewer` (read + live view); `AdminPrincipal` / `require_role()` guards
- [x] CORS with explicit allow-list from settings (credentials disabled: bearer tokens only)
- [x] Trusted-host middleware (problem+json errors) + security headers (HSTS in prod, `nosniff`, `X-Frame-Options`, `Referrer-Policy`, strict CSP on API paths)
- [x] Rate limiting on `/auth/token` and `/auth/refresh` per client IP (`limits` via an injected `RateLimiter`; replaced `slowapi`, whose module-global limiter conflicts with AD-4)
- [x] Never log secrets/tokens; structlog processor redacts sensitive keys at any depth; query strings and HTTP-client URLs stay out of logs
- [x] `vision-hub hash-password` CLI to generate the admin hash without echoing the password

### 1.6 Testing & CI
- [x] `tests/conftest.py`: isolated env, settings factory, app fixture, `httpx2.AsyncClient` with `ASGITransport` + lifespan, in-memory JSON log capture
- [x] Tests: health endpoints, settings validation, token issue/refresh/expiry, unauthorized access → 401/403 (244 tests, 100 % branch coverage)
- [x] GitHub Actions: `uv lock --check` → `uv sync --frozen` → ruff → mypy → pytest with coverage, then Docker build + smoke test + non-root check; actions pinned to commit SHAs, read-only token; Dependabot for uv, actions and Docker
- [x] `Dockerfile` (multi-stage, non-root user, `uv` install, healthcheck, single worker via `vision-hub serve`, graceful SIGTERM shutdown)
- [x] `docker-compose.yml` with the `api` service (read-only root fs, dropped capabilities, data volume; `postgres` added in Phase 3)
- [x] Health probes exempt from the trusted-host check (orchestrators probe with the container IP)

**✅ Phase 1 exit criteria**
- [x] `docker compose up` serves `/api/v1/health/live` and Swagger UI (dev)
- [x] CI green: lint, strict types, tests ≥ 85 % coverage on `src/` (first GitHub run on `4585699`: both jobs passed)
- [x] Protected routes reject unauthenticated requests

---

## Phase 2 — IoT Engine & Real-Time

**Goal:** port the motion pipeline into testable, non-blocking components; pull from multiple (mocked) cameras; push alerts and live video over WebSockets/MJPEG.

### 2.1 Vision domain (pure, no I/O)
- [x] `domain/` models: `Device`, `SourceKind`, `DeviceStatus`, `MotionEvent`, `DetectionResult`, `BoundingBox` (pure dataclasses, no numpy)
- [x] `vision/detector.py`: `MotionDetector.process(frame) -> DetectionResult` (port of the legacy grayscale → blur → diff → threshold → dilate → contours chain), run on a 640 px copy with boxes scaled back (<1 ms/frame at 1080p)
- [x] Replace the frozen `first_frame` with an adaptive background (`accumulateWeighted`) — fixes false alarms from lighting drift; sudden whole-scene changes reset the background; warm-up frames ignored
- [x] Per-device `DetectionConfig` (min motion area as a **fraction of the frame**, blur kernel, threshold, normalised ROI polygons) as a frozen Pydantic model, defaults from settings
- [x] `vision/tracker.py`: event state machine `IDLE → ARMING → ACTIVE → ENDING` with hysteresis on both edges (N consecutive motion frames to start; quiet for the grace period to end) and a max event duration — fixes one walk-through producing many alerts. Alert throttling moved to the notification layer (2.4) so no real event is ever dropped
- [x] Best-frame selection by **largest moving area**, keeping one frame copy in memory instead of 150
- [x] Bounded pre-roll ring buffer (`collections.deque(maxlen=…)`) storing downscaled frames only
- [x] Draw annotations on a *copy* so the stored evidence frame stays clean (annotated version optional)
- [x] Unit tests with synthetic frames (moving objects, lighting drift vs. frozen background, sudden light change, sensor noise, ROI, 1080p scaling)

### 2.2 Frame sources (pull)
- [x] `FrameSource` protocol: `open()`, `read() -> Frame` (raises `SourceError`), `close()`, `fps`, `resolution`, log-safe `name`
- [x] `WebcamSource` (device index), `RtspSource` (FFmpeg/TCP, open/read timeouts, fast probe), `VideoFileSource` (looping, paced to file fps); all over an injectable `VideoCapture` factory
- [x] Reconnect with exponential backoff + jitter as a `ReconnectingSource` wrapper for any source: status callback (`STARTING`/`ONLINE`/`RECONNECTING`/`FAILED`), `ONLINE` only after a real frame, stop interrupts backoff immediately; verified live against MediaMTX (publisher killed and restarted)
- [x] `SyntheticSource`: deterministic room with a figure walking through on a schedule (no hardware needed)
- [x] Source factory from `SourceConfig` (Pydantic discriminated union on `kind`, exhaustiveness checked by mypy)
- [x] Source credentials (e.g. RTSP user/password) held as `SecretStr`, rejected inside URLs, redacted in logs and errors; FFmpeg/OpenCV stderr logging silenced (it prints stream URLs)

### 2.3 Non-blocking camera workers
- [x] `vision/worker.py`: `CameraWorker` runs `source → detector → tracker` in a dedicated daemon thread (wrapped in `ReconnectingSource`)
- [x] Frame-rate limiting / frame skipping to cap CPU per camera (configurable target FPS, per-device override); live sources are still drained so latency never builds up
- [x] JPEG encoding (`cv2.imencode`) inside the worker thread, so the event loop only handles bytes: live frames every processed frame while someone watches, ~1/s otherwise; clean + annotated snapshot per motion event
- [x] Thread → asyncio bridge (`LoopBridge`): frames are latest-frame-wins with at most one pending loop callback per camera (`LatestFrame` + `asyncio.Event`); events go through `call_soon_threadsafe` one by one and are never dropped (rare, so no bounded queue)
- [x] Graceful stop via `threading.Event` (also interrupts reconnect backoff); join with timeout via `asyncio.to_thread`; an event open at stop is closed and delivered
- [x] `vision/manager.py`: `CameraManager` — start/stop/restart workers, track `DeviceStatus`, supervise crashes with backoff (reset once online), forward `CameraEvent`s
- [x] Wire `CameraManager` into `lifespan` (start enabled fleet devices on boot, stop all on shutdown; invalid fleet fails startup)
- [x] Single-owner guard: `flock` lock file; a second process fails startup with a clear error (verified live)
- [x] Assert no blocking calls on the loop: asyncio debug mode test with live cameras finds no callback slower than 50 ms; `ruff` `ASYNC` rules

### 2.4 Event bus & notifications
- [x] `EventBus` port: non-blocking `publish(topic, message)`, `subscribe(*glob_patterns) -> Subscription` (async iterator + context manager)
- [x] `InMemoryEventBus` (asyncio fan-out; per-subscriber queue size and overflow policy: unbounded for internal services, `drop_oldest`/`drop_newest` for live clients, with drop counters)
- [x] Topics: `motion.started.<id>`, `motion.ended.<id>`, `device.status.<id>`; camera events routed in by the `CameraManager`. Live frames stay on `LatestFrame` (latest-wins + viewer counting); a `frames.<id>` topic only pays off with Redis (Future)
- [x] `Notifier` port + `EmailNotifier` (`aiosmtplib`, `starttls`/`implicit`/`none` TLS, optional credentials, HTML body with inline snapshot + JPEG attachment from memory — no temp file; never calls `getfqdn()`, which stalled sends by 5 s)
- [x] `NotificationService`: subscribes before cameras start, per-device cooldown, one task per delivery, retries transient errors with backoff (not permanent ones), drains queued events and in-flight sends on shutdown
- [x] SMTP tests run against an in-repo asyncio fake SMTP server (`aiosmtpd` does not work on Python 3.14); end-to-end test: synthetic camera → email received
- [x] Remove the legacy write-PNG → email → `os.remove` flow (deleted together with the legacy script)

### 2.5 Device management (mocked multi-device)
- [x] `DeviceRepository` port + in-memory implementation seeded from the fleet file; API changes last until restart (DB in Phase 3). `DeviceService` keeps stored definitions and running cameras consistent (mutations serialised)
- [x] Load the fleet from a TOML file (`VISION_HUB_VISION__DEVICES_FILE`, stdlib `tomllib` instead of YAML: no dependency) with per-device detection overrides; `devices.example.toml` validated by a test (done in 2.3)
- [x] `GET /api/v1/devices` (cursor-paginated), `GET /api/v1/devices/{id}` (status, stream size, last event) — any authenticated user
- [x] `POST /api/v1/devices` (201 + `Location`), `PATCH /api/v1/devices/{id}` (partial; capture changes restart the camera), `DELETE /api/v1/devices/{id}` (admin)
- [x] `POST /api/v1/devices/{id}/start` (202) / `stop` and `PUT /api/v1/devices/{id}/detection-config` (hot reload)
- [x] `POST /api/v1/devices/test` — probe a source (open + grab one frame) in a worker thread with timeout and concurrency cap; failures reported in the body
- [x] `GET /api/v1/devices/{id}/snapshot` → latest JPEG (`no-store`; 503 + `Retry-After` until the first frame)
- [x] RTSP passwords are write-only (`exclude=True` on the model; responses and OpenAPI show `has_password`); `video_file` paths confined to `VISION__MEDIA_DIR` (absolute, `..` and symlink escapes rejected)

### 2.6 Real-time delivery
- [x] `schemas/ws.py`: message envelope `{type, v, ts, device_id, data}` as a Pydantic discriminated union (`motion.started`, `motion.ended`, `device.status`, `subscription`, `ping`, `error`)
- [x] `realtime/ConnectionManager`: per-connection bounded bus subscription, single send lock, send timeout; slow consumers closed with 1013; unexpected errors 1011; shutdown 1001
- [x] `POST /api/v1/auth/tickets` → short-lived single-use ticket (in-memory TTL store); WS validates it before accepting (4401 otherwise); MJPEG accepts ticket or bearer
- [x] `WS /api/v1/ws/events` with client-side subscribe/unsubscribe to devices (verified live)
- [x] Heartbeat ping/pong + idle timeout (4408)
- [x] `GET /api/v1/devices/{id}/stream` — MJPEG `StreamingResponse` (`multipart/x-mixed-replace`), latest-frame-wins, `?fps=` cap, counts as a viewer, ends when the camera stops or the client leaves (verified live)
- [ ] Optional `WS /api/v1/ws/devices/{id}/feed` binary JPEG frames
- [x] Configurable stream quality (`VISION__STREAM_MAX_WIDTH`, `STREAM_JPEG_QUALITY`, `REALTIME__STREAM_MAX_FPS`) separate from detection resolution

### 2.7 Tests & cleanup
- [x] Integration tests: WS client receives live device events; MJPEG endpoint yields valid JPEG parts (done in 2.6)
- [x] Notification tests with a fake SMTP server (asyncio, in `tests/conftest.py`; done in 2.4)
- [x] Load smoke test (`tests/load`): real uvicorn, 5 synthetic cameras + 20 WS clients + 2 MJPEG viewers + API traffic; asserts loop lag p99 < 50 ms (measured 2 ms), API p95 < 100 ms (6.5 ms), every client gets every motion event; `LOAD_SMOKE_SECONDS` for soaks
- [x] Update README with features, architecture diagram, quick start, API overview, browser real-time example and a real alert snapshot
- [x] Rejected WebSocket handshakes are logged (`ws_rejected`) — found while testing from a real browser

**✅ Phase 2 exit criteria**
- [x] Multiple mocked cameras run concurrently; API stays responsive under load (load smoke test; 30 s soak: 50/50 events to all 20 clients)
- [x] Live MJPEG feed and real-time WS alerts work from a browser (verified in a real browser: `<img>` stream, ticket → WebSocket, live statuses and motion events, ticket reuse refused)
- [x] Email alert fires once per motion event (not per flicker) (tracker flicker regression tests, end-to-end SMTP test, live run: one email per event)

---

## Phase 3 — Persistence & Optimization

**Goal:** durable storage for devices, events, users and snapshots; harden and optimise the single-node hub.

### 3.1 Database
- [x] Add `sqlalchemy[asyncio]` (2.1), `asyncpg`, `alembic`, `cryptography`; `aiosqlite` (tests)
- [x] `infra/db/engine.py`: async engine + `async_sessionmaker`; repositories open a short session per operation (services are long-lived singletons, so no per-request session dependency)
- [x] ORM models (typed `Mapped[...]`, UTC-aware timestamps on every backend, `JSONB` on PostgreSQL): `users`, `devices`, `revoked_tokens`. Tables are added by the phase that uses them: `motion_events`/`snapshots` in 3.2, `notifications`/`audit_log` in 3.3
- [x] Encrypt stored source credentials at rest (`MultiFernet` with rotatable `SECURITY__ENCRYPTION_KEYS`; required in prod, a `0600` key file outside prod)
- [x] Alembic setup with async env (scripts ship in the package); first migration; upgrade on startup with DB wait/retry; a test fails if models and migrations drift
- [x] SQL repositories implementing the ports (devices, users, atomic refresh-token revocation); swapped in via the container. The fleet file now seeds devices the database does not have
- [x] Move users from env bootstrap to DB (`ADMIN_PASSWORD_HASH` creates the admin on first start; `vision-hub create-user NAME --role ...` creates or resets users)
- [x] Add `postgres` service (with volume + healthcheck, no published port) to `docker-compose.yml`; the API waits for it
- [x] Integration tests against real PostgreSQL (repository and migration tests run on SQLite and PostgreSQL; CI `postgres` service); CI Docker job runs the full compose stack and checks `/health/ready`

### 3.2 Snapshots & event history
- [x] `motion_events` and `snapshots` tables + migration; indexes on `(device_id, started_at)` and `started_at` (plain columns: SQLite cannot reflect `DESC` expression indexes, and B-trees scan both ways); boxes as JSON
- [x] `LocalSnapshotStore` (date-partitioned dirs under `data/snapshots/`, atomic writes), path-traversal-safe
- [x] Persist clean frame, annotated frame and thumbnail per event; save metadata row
- [x] Persist each event to the DB *before* publishing it on the bus (DB is the system of record); a failed write is logged and the event is still published, so alerts are never lost
- [x] `GET /api/v1/events` (cursor pagination; filter by device, time range)
- [x] `GET /api/v1/events/{id}` and `GET /api/v1/events/{id}/snapshot` (`FileResponse`; bearer token or an expiring HMAC-signed link, so `<img>` tags work)
- [x] WS replay: client sends `{"type": "resume", "after": "<event id>"}` on reconnect and receives missed events from the DB, then `replay.done`
- [x] Retention policy job (delete events/snapshots older than *N* days, `retention_days` per device; history of deleted cameras follows the default)
- [ ] Optional: short MP4 clip per event (pre-roll + event) written by the worker — deferred to *Future*

### 3.3 Reliability (single node)
- [x] Notification outbox: `notifications` table + migration; pending notifications stored there; background loop dispatches and retries, surviving restarts (cooldowns restored from the outbox; alerts older than `max_age_hours` dropped; direct send from memory if the database is down)
- [x] `audit_log` table: who changed which device or user, and when (`GET /api/v1/audit`, admins only; field names, never values; CLI and bootstrap changes included)
- [x] Supervised background tasks with structured error logging and restart policy (`TaskSupervisor`, not `asyncio.TaskGroup`: one crashing child would cancel its siblings)
- [x] Graceful shutdown audit (drain WS, flush notifications, release cameras, close DB pool). Found and fixed: an open MJPEG stream blocked shutdown forever (uvicorn waits for connections before the lifespan); `vision-hub serve` now signals shutdown first, readiness turns 503, compose allows 30 s
- [x] Startup recovery: resume configured cameras, re-dispatch pending notifications, flag events left open by a crash as `interrupted` (device status is never persisted, so every camera starts offline by design)

### 3.4 Optimization & observability
- [x] Profile per-camera CPU; run detection on downscaled frames (e.g. 640 px wide) and scale boxes back (`benchmarks/pipeline.py`). Found and fixed: the frame-rate limiter lost up to half the frames of a camera running at the target rate (6.3 → 10 fps); `INTER_AREA` resizes at non-2x factors cost up to 20 ms (progressive halving now); OpenCV's thread pool fought the camera threads (disabled: ~25 % less CPU)
- [x] Benchmark `asyncio.to_thread` vs dedicated threads vs processes per camera; document results (`benchmarks/concurrency.py`, `docs/performance.md`: threads kept)
- [x] Reuse buffers / avoid redundant `frame.copy()`; encode JPEG once per frame and share across subscribers (live frames are downscaled before annotation and not copied without motion; one JPEG per frame was already shared by all viewers)
- [x] Prometheus `/metrics`: FPS per camera, processing latency, event-loop lag, WS clients, queue drops, notification failures (plus camera status, frame age, restarts, alerts pending, HTTP by route, process CPU/memory; scrape token)
- [ ] Optional detector plug-in: person detection (e.g. ONNX/YOLO) to filter motion events, behind the same `Detector` protocol — deferred to *Future*

**✅ Phase 3 exit criteria**
- [x] Events and snapshots survive restarts and are queryable via the API
- [x] Pending notifications are delivered after a crash/restart
- [x] Metrics show per-camera health

---

## Phase 4 — Frontend Dashboard

**Goal:** a premium, mobile-ready dashboard (dark glass UI, smooth state animations) that consumes the existing APIs only. Stack and performance rules: AD-19, AD-20.

**Working rules for this phase**
- Every task is verified live in a browser against a running hub (desktop and phone viewport) before it is ticked.
- UI copy is short and plain: say what happened and what to do (“Camera offline. Retrying…”), no filler.
- No runtime dependencies unless a task justifies one; dev tooling only.

### 4.1 Foundation
- [x] Choose the stack and record it as AD-19 (Vite + vanilla JS + Tailwind CSS v4); UI performance rules as AD-20
- [ ] Scaffold `frontend/`: Vite + Tailwind v4, ES modules, ESLint + Prettier + `tsc --checkJs`; dev proxy for `/api` (HTTP + WebSocket) to the hub; boot screen that checks the hub is reachable; CI job (lint, format, types, build)
- [ ] Design system: `@theme` tokens (palette, glass surfaces, radii, shadows, motion durations/easings), base components (panel, button, input, badge, status dot, toast, skeleton), reduced motion, AD-20 rules
- [ ] App shell: router, responsive layout (sidebar on desktop, bottom tab bar on phones, safe-area insets), connection indicator, not-found view

### 4.2 Data layer
- [ ] Types generated from the OpenAPI schema (`openapi-typescript` → `src/api/schema.d.ts`), used through JSDoc; CI fails when the file is stale
- [ ] `api/client.js`: `fetch` wrapper (bearer token, timeouts via `AbortController`, problem+json → `ApiError`, `Retry-After`), single-flight refresh on 401, sign-out when refresh fails
- [ ] Session: access token in memory only, refresh token persistence (“Keep me signed in”), proactive refresh before expiry, sign-out synced across tabs
- [ ] `realtime/socket.js`: ticket → WebSocket; states `connecting/live/reconnecting/offline`; backoff with jitter; `resume` with the last event id; `pong`; close codes (4401 new ticket, 4408, 1013, 1001); pauses while offline and reconnects on `online`/`visibilitychange`; Vitest tests with a fake socket

### 4.3 Views
- [ ] Login (rate-limit and error states, password visibility toggle, autofill friendly)
- [ ] Device grid: live MJPEG tiles (ticket per stream, paused off-screen and in hidden tabs, retry when a stream ends), status badges, motion highlight
- [ ] Real-time alert toasts + live event feed (replayed events marked, no duplicates)
- [ ] Event history: device/time filters, cursor pagination (infinite scroll), snapshot lightbox (clean/annotated)
- [ ] Device detail: start/stop, source test, detection-config editor (sensitivity, ROI drawing); admin-only controls hidden for viewers
- [ ] Audit log view (admins)

### 4.4 Delivery & quality
- [ ] Serve the built assets from FastAPI (`StaticFiles`, SPA fallback, page CSP, immutable caching for hashed files); Node build stage in the Dockerfile
- [ ] Installable PWA (manifest, icons, app-shell service worker that never caches API responses)
- [ ] Playwright end-to-end tests: login → live view → alert (desktop and mobile viewport)
- [ ] Performance & accessibility pass: Lighthouse (mobile), animation frame timing in a performance trace, keyboard navigation, contrast
- [ ] Optional: web-push notifications (needs VAPID keys and a new backend dependency; ask first)

**✅ Phase 4 exit criteria**
- [ ] Dashboard usable on a phone; alerts appear within 1 s of motion ending
- [ ] Live view and alerts recover on their own after a hub restart, with missed events replayed
- [ ] Lighthouse mobile performance and accessibility ≥ 90

---

## 🔭 Future — Horizontal Scaling *(deferred, only if needed)*

Not part of the MVP. The ports introduced above (`EventBus`, `SnapshotStore`, repositories) are the seams for this work.

- [ ] `RedisEventBus` (Pub/Sub for frames, Streams + consumer groups for events) as a drop-in for `InMemoryEventBus`
- [ ] Split roles: standalone camera-worker entrypoint (`worker_main.py`); run modes `all` / `api` / `worker`
- [ ] Camera ownership leases in Redis (`SET NX PX` + renewal) so each camera runs on exactly one worker
- [ ] Control channel: API publishes `start/stop/config` commands; owning worker applies them
- [ ] Redis-backed rate limiting and WS ticket store (required once there is more than one API replica)
- [ ] `S3SnapshotStore` (`aioboto3`, MinIO locally) with presigned URLs
- [ ] Short MP4 clip per event (pre-roll ring buffer + event) written by the worker
- [ ] Person detection (ONNX/YOLO via `onnxruntime`) to filter motion events, behind the `Detector` protocol
- [ ] Multiple API replicas behind a reverse proxy; verify rebalancing on worker loss
- [ ] Optional push-mode edge devices (device API keys or MQTT) alongside the pull model

---

## Appendix A — Legacy baseline (as of commit `14af13a`)

The legacy script was removed from the tree; read it with `git show 14af13a:main.py`.
Behaviour to preserve or deliberately improve:

| Area | Legacy behaviour | Problem | Fixed in |
|------|------------------|---------|----------|
| Background model | `first_frame` captured once, never updated | Lighting changes cause permanent false motion | 2.1 |
| Event trigger | Fires on any 1 → 0 status transition between consecutive frames | One frame without contours ends the event → repeated emails for one intruder | 2.1 |
| Frame buffer | Up to 150 full-res frames in RAM; extra frames dropped | "Middle frame" is the middle of the first ~5 s, not of the event | 2.1 |
| Evidence image | Annotated frame saved as ~2 MB PNG, then emailed and deleted | Large files; boxes burnt into evidence; disk round-trip not needed | 2.1, 2.4 |
| File naming | `intruder_{event_count}.png`, counter resets each run | Overwrites earlier files; snapshots in `images/` were not git-ignored | 1.0 ✅, 3.2 |
| Email | New thread per event, `smtplib`, Gmail hard-coded, sends to self, bare `except` + `print` | No retry, rate limit, or structured logging | 2.4, 3.3 |
| Credentials | Read from env at import time | No validation, secrets not typed | 1.2 |
| UI | `cv2.imshow` + `waitKey` on the main loop | Requires a display; incompatible with a server | 2.3 |
| Dependencies | `requirements.txt` mixed `black` (dev) with runtime deps | No lockfile; dev/runtime not separated | 1.0 ✅, 1.1 |
