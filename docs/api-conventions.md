# API Conventions

Rules every endpoint of the IoT Vision Hub API follows. When adding a route, match these; when a
rule has to change, change it here first.

## Versioning and paths

- All endpoints live under **`/api/v1`**. A breaking change ships as `/api/v2`; additive changes
  (new endpoints, new optional fields) do not bump the version.
- Resources are **plural nouns** in `kebab-case`: `/api/v1/devices`, `/api/v1/events`.
- A single resource is addressed by ID: `/api/v1/devices/{device_id}`.
- Actions that are not CRUD use a verb sub-resource with `POST`: `/api/v1/devices/{id}/start`.

## Methods and status codes

| Method | Use | Success |
|--------|-----|---------|
| `GET` | Read a resource or a page of resources | `200` |
| `POST` | Create a resource or trigger an action | `201` + `Location` header (create), `202` (async action), `200` (sync action) |
| `PATCH` | Partial update | `200` with the updated resource |
| `PUT` | Replace a sub-document (e.g. `detection-config`) | `200` |
| `DELETE` | Remove a resource | `204` |

## Payloads

- JSON with **`snake_case`** field names, in both requests and responses.
- Request bodies extend `RequestSchema`: **unknown fields are rejected** (`422`), so client typos
  never pass silently. Strings are whitespace-stripped.
- Response bodies extend `ApiSchema`. Optional fields are present with `null`, not omitted.
- **IDs** are UUIDv7 strings: globally unique and time-sortable.
- **Timestamps** are ISO 8601 in **UTC** with a `Z` suffix (`2026-06-01T12:00:00Z`), using the
  `UtcDateTime` type. Naive datetimes are rejected on input.

## Errors — RFC 9457 Problem Details

Every error (4xx and 5xx) is returned as `application/problem+json`:

```json
{
  "type": "urn:vision-hub:problem:not-found",
  "title": "Resource Not Found",
  "status": 404,
  "detail": "Camera cam-7 not found",
  "instance": "/api/v1/devices/cam-7",
  "request_id": "01a1015e-6468-706e-8d7b-712a6937de48",
  "device_id": "cam-7"
}
```

- `type` is `urn:vision-hub:problem:<code>` for domain errors, or `about:blank` for plain HTTP
  errors (unknown route, wrong method). **Clients branch on `type`, never on `title` or `detail`.**
- Extra members (like `device_id` above) give machine-readable context.
- Validation failures (`422`) list each problem in `errors` as `{loc, msg, type}`. The submitted
  value is **never echoed back**, since it may be a password or token.
- `500` responses never contain exception messages; use `request_id` to find the cause in the logs.

| `type` code | Status | Raised as |
|-------------|--------|-----------|
| `bad-request` | 400 | `BadRequestError` |
| `invalid-cursor` | 400 | `InvalidCursorError` |
| `unauthenticated` | 401 | `AuthenticationError` (adds `WWW-Authenticate: Bearer`) |
| `rate-limited` | 429 | `RateLimitedError` (adds `Retry-After`) |
| `permission-denied` | 403 | `PermissionDeniedError` |
| `not-found` | 404 | `NotFoundError` |
| `conflict` | 409 | `ConflictError` |
| `service-unavailable` | 503 | `ServiceUnavailableError` |
| `internal-error` | 500 | `AppError` |

New error kinds subclass `AppError` in `core/errors.py` and get a row in this table.

## Pagination

Collections use **cursor (keyset) pagination**, which stays correct while new events are being
inserted (offset pagination would skip or repeat items).

```
GET /api/v1/events?limit=50&cursor=eyJhZnRlcl9pZCI6NDJ9
```

```json
{ "items": [ ... ], "next_cursor": "eyJhZnRlcl9pZCI6OTJ9" }
```

- `limit`: 1–200, default 50.
- `next_cursor` is **opaque**: pass it back unchanged; `null` means the last page.
- A malformed cursor returns `400` with type `invalid-cursor`.
- Routes declare `page: PageParamsDep` and return `Page[ItemSchema]`.

## Authentication and authorisation

- **Secure by default:** every route requires a bearer token unless it is listed in
  `PUBLIC_PATHS` (`api/v1/router.py`). New routers go on `protected_router`; a test fails if a
  non-public route is reachable without authentication.
- Clients log in with the **OAuth2 password flow**: `POST /api/v1/auth/token` with a
  form-encoded `username` and `password`, which returns an access token (15 min) and a refresh
  token (7 days). Send the access token as `Authorization: Bearer <token>`.
- `POST /api/v1/auth/refresh` **rotates** tokens: the old refresh token stops working, and
  presenting it again is rejected (and logged as possible theft). `POST /api/v1/auth/logout`
  revokes a refresh token. Access tokens cannot be revoked; they simply expire.
- Login and refresh are **rate-limited per client IP** (`VISION_HUB_SECURITY__AUTH_RATE_LIMIT`,
  default `5/minute`); exceeding it returns `429` with `Retry-After`.
- Roles: `viewer` (read, live view) < `admin` (manage). Guard a route with
  `principal: AdminPrincipal` or `Depends(require_role(Role.VIEWER))`; a too-low role gets `403`.
- Auth responses send `Cache-Control: no-store`. Tokens and passwords never appear in logs.

## Real time

- **Tickets:** browsers cannot set headers on a WebSocket or an `<img>`, so they call
  `POST /api/v1/auth/tickets` and pass the result as `?ticket=`. Tickets are single use and
  expire after `VISION_HUB_SECURITY__TICKET_TTL_SECONDS` (30 s). Fetch one right before
  connecting.
- **`WS /api/v1/ws/events`:** every server message has `{type, v, ts, device_id?, data?}`.
  `type` is one of `subscription`, `motion.started`, `motion.ended`, `device.status`, `ping`,
  `replay.done` or `error`. Clients send `{"type": "subscribe", "devices": [...] | null}`,
  `{"type": "unsubscribe", "devices": [...]}` and `{"type": "pong"}`. New connections are
  subscribed to every device. Images are not pushed over the socket; fetch snapshots over HTTP.
- **Replay:** after reconnecting, send `{"type": "resume", "after": "<last event id>"}`. Events
  recorded since then (for subscribed devices, oldest first, at most 100) arrive with
  `"replay": true`, followed by `{"type": "replay.done", "data": {"count", "truncated"}}`. If
  `truncated` is true, page through `GET /api/v1/events` for the rest. An event still in
  progress is replayed as `motion.started`.
- **Close codes:** `4401` missing/invalid ticket (before accept), `4408` idle (no client
  message within `REALTIME__IDLE_TIMEOUT_SECONDS`; answer pings), `1013` client too slow to keep
  up (reconnect), `1001`/`1012` server shutting down or restarting (reconnect with backoff, then
  `resume`), `1011` internal error.
- **MJPEG:** `GET /api/v1/devices/{id}/stream` returns `multipart/x-mixed-replace` with one JPEG
  per part, newest frame first and capped by `?fps=`. It accepts a bearer token or a ticket, and
  ends when the camera stops or the server shuts down (reconnect with a new ticket).

- **Signed links:** event responses carry snapshot URLs with `expires` and `signature`
  query parameters (HMAC-SHA256, valid for `SECURITY__SIGNED_URL_TTL_SECONDS`, default 1 h).
  They are bound to the event and image kind, need no token, and return `401` once expired or
  altered. A bearer token works on the same route.

## Request tracing

- Every response carries an **`X-Request-ID`** header, also present in every log line and in
  problem bodies.
- Clients may send their own `X-Request-ID` (1–128 chars of `A-Z a-z 0-9 . _ : -`) to correlate
  across systems; anything else is replaced with a generated UUIDv7.
- Query strings are never logged, so short-lived tokens in URLs (e.g. WebSocket tickets) stay
  out of log files. Prefer headers for credentials regardless.
- Changes made by admins are written to the audit trail (`GET /api/v1/audit`) with the same
  `request_id`, so an entry leads straight to the request's log lines. Services take the acting
  user explicitly (`actor=principal.username`); entries record field names, never values.

## Health probes

| Endpoint | Meaning | Codes |
|----------|---------|-------|
| `GET /api/v1/health/live` | Process is up. Never checks dependencies. | `200` |
| `GET /api/v1/health/ready` | All dependencies reachable; safe to route traffic. Turns `503` as soon as shutdown begins, so load balancers drain the hub. | `200` / `503` |

- Public (no auth), `Cache-Control: no-store`, and logged at DEBUG unless they fail.
- They are the one exception to the error format: `503` returns the `Readiness` body (which
  checks failed) instead of a problem document. Failure **reasons** are only logged, never returned.

## OpenAPI

- Served at `/openapi.json`, with Swagger UI at `/docs` and ReDoc at `/redoc`, **disabled in
  production** unless `VISION_HUB_APP__DOCS_ENABLED=true`.
- Operation IDs are `<tag>_<function_name>` (e.g. `health_ready`) for readable generated clients.
  Route function names must therefore be unique within a tag.
- Error responses are documented as `application/problem+json` with the `ProblemDetail` schema.
  Every operation documents `500`; routes add their specific errors via
  `responses={404: {"model": ProblemDetail}}`.
- Every route has a `tags` entry, and every tag is described in `OPENAPI_TAGS` (`main.py`).
