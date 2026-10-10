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
> - Coming back after a break: read *Current Status* → *Architecture Decisions* → *Target Layout* → the first unchecked task.

---

## 📍 Current Status

- **Active phase:** Phase 7 — Showcase (Phases 1–6 are complete: MVP, event clips, person detection)
- **Working branch:** `main`
- **Next task:** 7.3 Richer history (7.4, the public demo, is done)
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
| AD-19 | Frontend | **Vite** + **vanilla JS** (ES modules, no framework) + **Tailwind CSS v4** (`@tailwindcss/vite`, tokens in `@theme`). Native `fetch` and `WebSocket`. Types from OpenAPI (`openapi-typescript`; WebSocket messages included) used through JSDoc and checked with `tsc --checkJs` (TypeScript 5.9: the generator needs the JS compiler API, which the native TS 7 lacks); ESLint + Prettier; Vitest for logic. Dev: Vite proxies `/api` (HTTP + WS) to the hub, so the browser sees one origin. Prod: FastAPI serves the built files. Routing: History API with lazy views, so the server needs an SPA fallback (4.4) | Small, fast bundle with no runtime dependencies; the API is the contract. Same-origin in dev and prod: no CORS, tickets and MJPEG `<img>` work unchanged. |
| AD-20 | UI performance | Animate **only `transform` and `opacity`** (short colour transitions only on small controls and status lights); content never waits on an animation (hidden or throttled tabs may never finish one); blurred glass only on a few large static surfaces, cheap unblurred cards for repeated items; `backdrop-filter` on static chrome only, **never above live video** (an MJPEG frame under a blur forces a re-blur every frame); `prefers-reduced-motion` respected; streams pause when off-screen or the tab is hidden | Glass effects stay smooth on phones; idle tabs cost no bandwidth or CPU on the hub. |
| AD-21 | Event clips | **VP8 in WebM**, written by OpenCV in the camera thread at 640 px: a pre-roll ring buffer (3 s) plus the event, capped at 120 s, with frames lightly blurred first. Stored beside the snapshots and served with byte ranges through a signed link | OpenCV's Linux wheels (the Docker image) have no H.264 encoder. VP8 is available everywhere and costs 6–17 ms/frame, only while recording (VP9: 20 ms). OpenCV offers no bitrate or speed control for it, so the blur keeps noisy cameras at 0.6–1.5 MB per 10 s instead of 3.4–7.5 MB. WebM plays in Chrome, Edge, Firefox and Safari 16+/iOS 17.4+. No new dependency, no ffmpeg process |
| AD-22 | Person detection | **YOLOX-s** (OpenCV model zoo ONNX, Apache 2.0, COCO AP 40.5) on **onnxruntime**, run on **one background thread shared by all cameras**. While an event is open: a check when it starts, then every 2 s, at most 5, plus a last look at the frame with the most motion before the event is published. Per camera, alerts go out on *any motion* (default) or *people only*; if the model can't run, alerts go out anyway. onnxruntime telemetry off | onnxruntime runs it 6× faster than OpenCV 5's DNN engine. YOLOv5/v8 are AGPL; NanoDet is cheaper but far less accurate (AP 30.4). A check costs ~280 ms of one core on a typical CPU (45 ms on Apple silicon only), so it can't run in the camera thread without stalling the live view. One shared thread caps the feature at one core. Failing open: a missed intruder costs more than a false alarm |
| AD-23 | Live overlays | The hub sends **detections as data** and the **dashboard draws them** over the video. They ride in each MJPEG part's `X-Detections` header (boxes as fractions of the picture, plus the person score once the open event found one), so the JPEG stays clean. Snapshots and clips are unchanged | A header ties the boxes to the exact frame: a separate WebSocket message would arrive early or late and the boxes would trail the person. Styled boxes (motion vs person) that can be switched off, with no re-encoding. An `<img>` ignores the header, so plain MJPEG clients keep working. One code path for the live view and the public demo |
| AD-24 | Public demo | **GitHub Pages** hosts the real dashboard, unchanged, built in a **demo mode** that adds a hub living in the tab: it replaces `fetch` and `WebSocket` for `/api/v1` and answers the API, the realtime protocol and the MJPEG streams (frames drawn from looping videos, with the detections recorded for that moment). Its data is recorded from the real camera worker (`vision-hub export-demo`) and its responses are typed against the OpenAPI schema. Served under the repository path, deep links through `404.html` | Pages only serves static files. Visitors try the actual app in seconds, with no server to run or pay for. Faking the network rather than the app keeps the demo honest: every view, the router and the realtime client run as in production, and the type check catches drift from the API |
| AD-25 | Demo footage | Four free Pexels clips, **fetched from Pexels at run time** (pinned URLs and SHA-256, `vision-hub demo-footage`) and turned into camera loops on the machine: trimmed, 640 px 16:9 at 15 fps, a crossfade from the last frame to the first, then the empty scene played back and forth. VP8 WebM written by OpenCV | Real people make person detection visible (YOLOX scores the synthetic figure 0.0). Fetching instead of re-hosting keeps the repository small and stays well inside the Pexels licence. A hard cut at the loop point would look like motion; the crossfade and idle scene give one event per visit. No new dependency |

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
- [x] Optional: short clip per event (pre-roll + event) written by the worker — done in Phase 5 (VP8/WebM, AD-21)

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
- [x] Optional detector plug-in: person detection — done in Phase 6 (YOLOX-s on onnxruntime, AD-22)

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
- [x] Scaffold `frontend/`: Vite 8 + Tailwind v4, ES modules, ESLint + Prettier + `tsc --checkJs`; dev proxy for `/api` (HTTP + WebSocket + MJPEG, verified live: login, ticket → WS motion event, 5 fps stream); boot screen with hub status (online / not ready / unreachable) from `/health/ready`, backoff with jitter, paused in hidden tabs; self-hosted Onest font; CI `Dashboard` job (lint, format, types, build), pre-commit hook, Dependabot for npm
- [x] Design system: `@theme static` tokens (night-sky surfaces, text, four meaning colours, radius by element size, shadows, motion), surfaces (`panel` glass / `card` / `panel-solid` over video), buttons (variants, sizes, icon, busy), fields (invalid, password toggle, switch), badge, status dot, spinner, skeleton (transform shimmer), toasts (`ui/toast.js`: FLIP stacking, keyed de-duplication, pausable timer, swipe to dismiss, alert role for alarms); `ui/dom.js` safe DOM builder, `ui/motion.js` (enter/exit with timeout, FLIP); contrast ≥ 4.5:1 for text. Dev-only gallery at `/design.html`
- [x] App shell: History API router (lazy view chunks, view cleanup, scroll restore on Back/Forward, focus moves to the page heading, reload prompt when a chunk fails), responsive layout (glass sidebar from 768 px; opaque top bar + bottom tab bar on phones with safe-area insets; sliding active indicator), skip link, connection pill and hub-lost/recovered toasts from a shared hub monitor (`state/hub.js`), boot screen only when the hub is slow or down, not-found view; Vitest set up (router, store, backoff) and in CI

### 4.2 Data layer
- [x] Types generated from the OpenAPI schema (`openapi-typescript` → `src/api/schema.d.ts`), used through JSDoc via `src/api/types.js` aliases; `vision-hub openapi` exports the schema to `frontend/openapi.json`; WebSocket messages published as `WsServerMessage`/`WsClientMessage` components; response fields with defaults marked required; pytest fails when `openapi.json` is stale, CI/pre-commit (`api:check`) when the types are; `npm run api:sync` refreshes both
- [x] `api/client.js`: `fetch` wrapper (bearer token, JSON/form bodies, query building, one timeout covering headers and body, caller cancellation passes through as `AbortError`), problem+json → `ApiError` (`kind` http/network/timeout, `code` from the problem type, `fieldErrors`, `retryAfter`, request id; a bare proxy 502/504 counts as unreachable), `describeError` for human copy; single-flight refresh on 401 (reuses a token refreshed meanwhile, retries once, refreshes first when only the refresh token survived a reload), sign-out hook when the session can't continue, network failures never sign out. 20 Vitest cases; verified live against the hub (concurrent refresh, logout → expiry, 404/422/429 problems, timeout, unreachable)
- [x] Session (`state/session.js`, wired in `state/auth.js`): access token in memory only; refresh token in localStorage with “Keep me signed in”, else sessionStorage (memory if storage is blocked); refreshes under a cross-tab Web Lock that re-reads the stored token, results shared over BroadcastChannel so tabs never reuse a rotated token (verified live: simultaneous refresh in two tabs, no reuse); proactive refresh a minute before expiry in visible tabs; sign-out (real menu) signs out every tab and revokes on the hub; sign-in reaches tabs waiting at sign-in; expiry ends only the affected tab; startup restore retries after outages and honours `Retry-After`; account row (sidebar) and account menu (phone), admin-only nav hidden for viewers. 15 Vitest cases (lock bypass mutation caught)
- [x] Login view (moved up from 4.3: the session needs it): full-screen layout sharing the boot lens (breathes while signing in, rose on error); route guard (signed out → `/login?next=…`, signed in → `next`, validated same-origin only via `safeRedirect`); sign-in/out in any tab moves every tab; empty-field, wrong-password (password selected for retyping), unreachable and rate-limit states (button counts down from `Retry-After`); password toggle, `autocomplete` username/current-password, “Keep me signed in” (choice remembered); expired/signed-out notice; verified live with real typing on desktop and phone widths
- [x] `realtime/socket.js` (+ `realtime/live.js`): fresh ticket per connection (covers 4401, which browsers only see as 1006); states `connecting/live/reconnecting/offline/stopped`; backoff 1 s → 30 s with ±20 % jitter, reset after a healthy connection; `pong` to every ping plus a 50 s silence watchdog (half-open sockets after sleep); `resume` from just before the oldest event still in progress (replay is id-ordered, so an event that ended during a drop comes back as ended), duplicates filtered; device subscription kept across reconnects; offline pause, immediate retry on `online`/visible tab/hub back, never two attempts at once; runs while signed in; connection pill shows live-event status. 15 Vitest cases; verified live from Node (70 s past the idle timeout on pongs, offline gap mid-event replayed, hub restart) and in the browser (offline/online, hub restart with toasts and backoff timeline)

### 4.3 Views
- [x] Device grid: live MJPEG tiles read with `fetch` and painted on a canvas (`realtime/mjpeg.js`: Content-Length part parser, latest-frame-wins decoding with `createImageBitmap`, 8 s stall watchdog, backoff, “ended” when the camera stops) instead of `<img>`, so the dashboard knows when frames stop and can cancel instantly; ticket per stream, 8 fps in the grid; streams run only for on-screen tiles in visible tabs (verified: the hub logs the stream closing on scroll-away); status chips (Live/Connecting/Reconnecting/Starting/Failed/Stopped, last frame dimmed while retrying), amber motion ring and chip, “Motion 4 minutes ago”; updates from live events, list reloaded after every reconnect (status changes are not replayed); skeletons, empty and error states
- [x] Real-time alert toasts + live event feed: one toast per camera (“Motion on X” while it lasts, then duration + snapshot thumbnail, “Watch”/“View”), an older event never overwrites newer motion, replays summed up in one “While you were away” toast, an alert left open by a drop is finished by its replayed end, unseen-alert count in the title of hidden tabs; Events page lists recent events by day with live rows (ticking duration, thumbnail on end), replayed ones tagged “Missed”, no duplicates, `?event=` highlight from an alert; shared camera directory (`state/devices.js`); toasts drop in from the top wherever the phone tab bar shows
- [x] Event history: camera select + time range (all/today/24 h/7 days) kept in the address bar, live events outside the filters stay out, infinite scroll on the cursor (verified past 480 events, newest first, no duplicates), filtered empty state with “Show all events”; snapshot viewer in a native modal `<dialog>` (motion boxes on/off remembered, previous/next by buttons, arrow keys or swipe, neighbours preloaded, download named camera + local time, expired signed links refreshed once, focus returns to the last event viewed); an alert’s “View” opens its snapshot directly
- [x] Device detail: full-frame-rate live view (shared `ui/live-view.js`, also used by grid tiles), details, latest motion; admins: start/stop, connection test of the saved source (new `POST /devices/{id}/test`, stored credentials included; for stopped cameras), detection settings in plain words (log-scale sensitivity, small-change filter, confirm frames, end delay; save/discard, hot reload), watch areas drawn over the live picture (tap corners, close on the first, drag corners, select/delete, presets; outside dimmed), viewers see read-only; live status wins over a slower action response (start/save race found live); camera directory loaded at sign-in
- [x] Audit log view (admins): entries as sentences (“You stopped Demo garage”, “The hub created the account admin”, field names in words, never values), cameras linked, Everything/Cameras/Accounts filter in the address bar, infinite scroll, “Admins only” for a 403; sentence rules unit-tested

### 4.4 Delivery & quality
- [x] Serve the built assets from FastAPI (`api/dashboard.py`, `APP__DASHBOARD_DIR`): SPA fallback for page paths only (missing files, `/api`, docs and `..` escapes are 404 problem+json), strict page CSP (`script-src`/`style-src 'self'`, no inline; style attributes moved to the CSSOM), `immutable` year-long caching for hashed `assets/`, `no-cache` elsewhere; Brotli/gzip copies written at build time (`scripts/compress.mjs`, 132.7 → 40.7 kB) and served by `Accept-Encoding`, so nothing is compressed per request and MJPEG streams are untouched; Node build stage in the Dockerfile; CI smoke check. Verified in the browser from the hub (no CSP violations) and in the compose stack
- [x] Installable PWA: manifest (standalone, shortcuts), lens icons rendered by `scripts/make-icons.py` (any, maskable, Apple); service worker generated per build by a Vite plugin (exact hashed precache list, version from its hash, build fails if a placeholder survives): hashed assets cache-first, pages network-first with a 3 s fallback to the cached shell (the app opens when the hub is down and says so), `/api`, docs and metrics never intercepted (verified: no API response cached after streams and 29 snapshots); production builds only; “Update ready, Reload” prompt (verified with a real new build); “Install app” button when the browser offers it. The hub sends the page CSP with `sw.js`: a worker obeys its script's CSP, and the API's blocked every fetch (found live)
- [x] Playwright end-to-end tests (desktop Chromium + Pixel 7): a throwaway hub per run (`e2e/start-hub.sh`: SQLite, synthetic cameras with a visit every 9 s, test-only admin, the built dashboard, port 8765); sign-in with redirect and wrong password, sign-out, live tiles with real frames and states, motion alert → snapshot → viewer with the hub-to-toast latency measured from real WebSocket frames (17–444 ms over 3 repeats), camera page stop/start, offline start from the service worker, not-found page; 42/42 over `--repeat-each=3`, no flakes; CI job with the report uploaded on failure. Found a real bug: the service worker missed every cached script offline (`Vary: Origin` vs `crossorigin` requests), now matched with `ignoreVary`
- [x] Performance & accessibility pass (`docs/performance.md#dashboard`):
  - **Lighthouse mobile, applied throttling:** sign-in 97, live 95, events 100, camera 95. Accessibility and best practices 100, CLS ≤ 0.001, TBT 0.
  - **Startup no longer runs in series:** session restore and the page's code load with the health check, the user comes from the token's claims, and the boot screen pauses only after a real problem. Live LCP 12.4 → 2.9 s.
  - **LCP and layout shift:** a first-frame poster `<img>` gives LCP an element. Tiles are sized from the stream, and toasts stack with `translate` (CLS 0.125 → 0.001).
  - **Animation:** 60 fps at 4× CPU slowdown (opt-in `e2e/perf.e2e.js`).
  - **Accessibility checks:** axe WCAG 2.2 AA plus best practices on every page and dialog, desktop and phone (`e2e/a11y.e2e.js`); keyboard-only flows (`e2e/keyboard.e2e.js`).
  - **Bugs found:** the skip link did nothing (the router swallowed `#main`), the sign-in and boot screens had no `main` landmark, and axe read contrast mid-fade.
- [x] Web push notifications, with no new dependency: RFC 8291 encryption and RFC 8292 VAPID signing on the existing `cryptography` and PyJWT, checked against the RFC's worked example.
  - **Backend:** a `push` alert channel beside email, through the same outbox and retries. Expired subscriptions are removed, and the event id as notification tag makes a retried alert replace the first. Channels with nobody to notify are skipped.
  - **Security:** subscriptions only to known push services over HTTPS, with no redirects (no SSRF).
  - **Endpoints and setup:** `/push` endpoints (key, subscribe, unsubscribe your own, test); `vision-hub vapid-key`, or a key file created once.
  - **Dashboard:** a "Notifications" switch in the sidebar and phone menu. It explains blocked permissions and the iPhone Home Screen requirement, and repairs subscriptions the browser dropped or that were made with an old key. Signing out unsubscribes the device.
  - **Service worker:** shows the alert with the snapshot, stays quiet while the dashboard is in front, and opens the event in place when tapped.
  - **Tests:** 7 e2e tests in full Chromium (the headless shell has no notifications) and an end-to-end hub test (motion → encrypted push → signed snapshot loads).
  - **Not verified here:** delivery through Google's, Mozilla's or Apple's real push services, which needs a real browser on HTTPS.

**✅ Phase 4 exit criteria**
- [x] Dashboard usable on a phone; alerts appear within 1 s of motion ending (e2e on Pixel 7: 17–444 ms from the hub's `motion.ended` to the toast)
- [x] Live view and alerts recover on their own after a hub restart, with missed events replayed (verified live by restarting the hub under an open dashboard)
- [x] Lighthouse mobile performance and accessibility ≥ 90 (applied throttling: 95–100 and 100; simulated 75 on stream pages is a Lantern artifact, see `docs/performance.md`)

---

## Phase 5 — Event Clips

**Goal:** every motion event gets a short video, with the seconds before the motion was detected, that you can watch in the dashboard and download as evidence. Design: AD-21.

### 5.1 Recording
- [x] Clip recorder in the camera thread (`vision/clip.py`): the pre-roll buffer feeds a VP8/WebM writer opened when motion starts. Frames keep their real timing (repeated when the camera falls behind, no freeze across a reconnect), the length is capped, and the size is fixed for the whole clip. A recording failure drops the clip, never the event (tests decode the real video)
  - **Size and CPU:** a noisy camera made 3.4–7.5 MB per 10 s at 22–40 ms a frame. OpenCV ignores bitrate and speed options for VP8, so clip frames get a 5×5 blur: 0.6–1.5 MB and 6–17 ms
- [x] `MotionEndedEvent` carries the clip; the recorder stores it next to the snapshots (a `clip` snapshot kind, migration 0005, checked on PostgreSQL too); retention deletes it with them; settings `VISION_HUB_CLIPS__*` (on/off, pre-roll, length, width)

### 5.2 Delivery
- [x] API: `clip` link on events (signed, like snapshots), `GET /events/{id}/clip` with byte ranges (Safari needs them) and a download name. Signed links now cover the path and every other query parameter
- [x] Dashboard: the event viewer opens on the clip (snapshot as poster, no autoplay), with a remembered Snapshot/Clip choice, a download of whichever is shown, and arrows/swipes that seek on the clip. Event rows carry a clip mark. Found by the e2e tests: the page CSP blocked all media (`media-src 'none'`)
- [x] Verified (`docs/performance.md#event-clips`):
  - **Playback:** e2e in Chromium on desktop and phone. The clip decodes and plays, and starts before the detection.
  - **Docker image:** records clips on a read-only filesystem (5 fps camera: 70 frames, 640×480, Range 206) and leaves no temporary files.
  - **CPU:** the two demo cameras, recording most of the time, took the hub from 15 % to 28 % of a core.
  - **Also fixed:** `frontend/e2e/devices.toml` had never been committed (the `devices.toml` ignore rule hid it), and the camera stop/start test ran on desktop and phone at once on a shared camera. It now has its own camera; 114/114 passed over 3 repeats

**✅ Phase 5 exit criteria**
- [x] A motion event recorded by the Docker image plays in the dashboard, including the seconds before detection
- [x] Recording adds no measurable delay to alerts (closing a clip: ~1 ms; alerts 229–448 ms after the event in e2e), and clips are removed by retention (tested)

---

## Phase 6 — Person Detection

**Goal:** tell people from wind, shadows, rain and pets, so cameras can alert on people only. Design: AD-22.

### 6.1 Detection
- [x] Person detector (`vision/persons.py`): YOLOX-s on onnxruntime (the one new dependency), letterboxing and box decoding, a pinned model file checked by SHA-256, `vision-hub download-model`; settings `VISION_HUB_PERSONS__*`. Tested on a public-domain photo (Grace Hopper's Navy portrait: 0.93) and empty scenes
- [x] Checks off the camera threads (`PersonChecker`): one shared background thread; checks when motion starts, every 2 s, at most 5, stopping at the first person, plus a last look at the best frame before publishing. Events record `person`, the best score and `alert`. A missing or failing model never stops detection
  - **Found by measuring in Docker:** a check costs ~280 ms on Linux (45 ms only on Apple silicon). The first design, in the camera thread, would have stalled the live view and doubled CPU. onnxruntime's telemetry (HTTPS uploads) is turned off

### 6.2 Alerts and history
- [x] Per-camera `alert_on`: any motion (default) or people only, failing open when the model is unavailable. Email, push and dashboard toasts follow the same decision, and alerts say "Person detected / Person at …". Quiet events skip the cooldown
- [x] Stored and served: `person`, `person_confidence` and `alert` on events (migration 0006, checked on PostgreSQL), in the WebSocket messages and the API, with a `person` filter
- [x] Dashboard: "Person" tag on events, a "People only" filter, the person verdict in the viewer, and "Alert on: Any motion / People" in the camera's detection settings. People-only cameras never toast for motion alone

### 6.3 Delivery
- [x] Docker image ships the model (verified at build, pinned in step with the hub by a test); CI caches the model so the real-model tests run there. Measured in the image (`docs/performance.md#person-detection`): two busy demo cameras 24 % → 35 % of a core; an alert waits for at most one check

**✅ Phase 6 exit criteria**
- [x] A camera set to people only stays quiet for motion without people and alerts for a person (real model, in tests and in the Docker image: yard 0 of 12 events alerted, door 5 of 5)
- [x] With the model missing, every camera still alerts (and says why in the logs: `person_model_missing` with the fix)

---

## Phase 7 — Showcase

**Goal:** make the project look as good as it is built: realistic demo cameras, detections drawn by the
dashboard, richer history, and a public demo anyone can open. Designs: AD-23, AD-24.

### 7.1 Live overlays
- [x] Detections travel with each live frame (`X-Detections` part header, AD-23): motion boxes as fractions of the picture, and the person score once the open event found a person
- [x] Live MJPEG without burnt-in boxes (snapshots keep their annotated copy, clips stay clean). Encoding a live frame no longer draws anything (`docs/performance.md`)
- [x] Dashboard overlay on live tiles and the camera page: corner-bracket boxes, amber for motion and red once a person is found, fading out when motion ends; the motion chip reads "Person 87 %". One "Motion boxes" switch on the camera page, shared with the event viewer and remembered. AD-20 holds (only opacity animates, no blur over video). Watch areas were already drawn by the camera page
  - **Found while testing live:** YOLOX scores the synthetic figure 0.0, so the demo cameras can't show people-only mode: 7.2 fixes that. Labelling *which* box is the person needs tracking (7.5): person checks run every 2 s, and motion boxes split and merge between frames
- [x] Tests: header format and fractions, boxes in stream pixels, the person score on live frames (pytest); header parsing and box layout (Vitest); boxes on the grid and the camera page, switched off and remembered (e2e)

### 7.2 Realistic demo cameras
- [x] Footage: four fixed-camera Pexels clips (front door, lobby, driveway, patio), chosen from 18 by measuring how still each camera is and running each through the motion detector and YOLOX twice; sources, credits, licence and the selection in `docs/demo-footage.md` (AD-25)
- [x] `vision-hub demo-footage`: fetches the pinned clips (SHA-256, through a shared `fetch_verified` that the model download uses too) and makes the loops in `data/demo/` in ~20 s. Nothing is re-hosted, so no Release asset
  - **Found while building it:** Pexels' CDN refuses urllib's default agent (403); downloads now say `vision-hub/<version>`. Its "sd" file names don't match their sizes (a "640×338" file is 426×226), so two clips come from their HD files
- [x] `devices.demo.toml` (every camera people-only) and `docker-compose.demo.yml`, an override rather than a profile: it changes the hub's command and fleet. Checked in the Docker image with compose's hardening: the first start makes the loops, a restart reuses them. The synthetic source stays for tests and CI
- [x] People-only mode shown working, on the running hub: per loop one person alert at the front door and one in the lobby (0.88–0.96), while the driveway (garage door, car) and the patio (shadows) only record quiet events
- [x] README screenshots retaken with the demo cameras; evidence snapshots now use the overlay's look (amber corner brackets) instead of green rectangles
  - **Found while taking them:** a camera page opened in the middle of an event showed red boxes but no motion chip, because the hub reports an event only once it ends. The live view now also counts motion from frames that carry detections

### 7.3 Richer history
- [ ] Activity heatmap (hour × weekday) per camera, from an aggregate endpoint (no rows shipped to the browser)
- [ ] Clip timeline with markers (motion start, person found) so the viewer can jump to the moment
- [ ] Events-per-day sparkline on each live tile

### 7.4 Public demo (GitHub Pages)
- [x] Repository renamed to `iot-vision-hub`; badges, links and the git remote updated
- [x] Demo build (`npm run build:demo`, AD-24): `frontend/src/demo/` replaces `fetch` and `WebSocket` for `/api/v1` with a hub in the tab (API, realtime protocol with replay, MJPEG with `X-Detections`), typed against `schema.d.ts`; first visit signed in, any sign-in works; settings and camera starts and stops work in memory; a dismissible "run it yourself" note; push and the service worker off. Normal builds contain none of it
  - **Found while building it:** the realtime client kept the `WebSocket` constructor from module load, before the demo could swap it; it is now looked up per connection
- [x] `vision-hub export-demo`: the real `CameraWorker` over each loop (three passes, the middle one kept), clock from frames read and person checks inline, so the recording is deterministic: per-frame detections, events with snapshots and clips (36 s for all four)
- [x] Served under the repository path: `paths.js` (`href`, `appPath`) for links and the router, `404.html` for deep links
- [x] `.github/workflows/demo.yml`: footage, recording, VP9 re-encode (35 MB → 3 MB, same frames), build, Playwright, deploy to Pages; the demo link and an animated preview at the top of the README (WebP, 0.9 MB: GIF was 4 MB, over the 1 MB large-file guard)
- [x] e2e and accessibility checks against the demo build (`npm run e2e:demo`, desktop and phone): no request reaches a server, a person alert opens its clip, boxes on the camera page, filters and deep links, in-memory controls

### 7.5 Smarter detection *(pick what is worth it)*
- [ ] Tamper detection: covered, moved or blurred camera raises its own alert (brightness, sharpness, background shift)
- [ ] Object tracking (IoU matching, numpy only) and zone rules: someone staying in a zone for N seconds, crossing a line

### 7.6 Integrations & deployment *(pick what is worth it)*
- [ ] Multi-arch image (amd64, arm64) on GHCR, and a Raspberry Pi guide
- [ ] Privacy masks: areas blurred in the live view, snapshots and clips
- [ ] MQTT with Home Assistant discovery (new dependency: decide first)
- [ ] Telegram or webhook notifier behind the existing `Notifier` port
- [ ] Grafana dashboard for the Prometheus metrics, in an optional Compose profile

**✅ Phase 7 exit criteria**
- [x] The live view draws detections from data, and the stream has no burnt-in boxes
- [x] The demo cameras show real scenes, and people-only mode visibly ignores non-people
- [x] A public demo link works on desktop and phone, deep links included, with no backend (checked on the built site locally; live once Pages is switched on)

---

## 🔭 Future — Horizontal Scaling *(deferred, only if needed)*

Not part of the MVP. The ports introduced above (`EventBus`, `SnapshotStore`, repositories) are the seams for this work.

- [ ] `RedisEventBus` (Pub/Sub for frames, Streams + consumer groups for events) as a drop-in for `InMemoryEventBus`
- [ ] Split roles: standalone camera-worker entrypoint (`worker_main.py`); run modes `all` / `api` / `worker`
- [ ] Camera ownership leases in Redis (`SET NX PX` + renewal) so each camera runs on exactly one worker
- [ ] Control channel: API publishes `start/stop/config` commands; owning worker applies them
- [ ] Redis-backed rate limiting and WS ticket store (required once there is more than one API replica)
- [ ] `S3SnapshotStore` (`aioboto3`, MinIO locally) with presigned URLs
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
