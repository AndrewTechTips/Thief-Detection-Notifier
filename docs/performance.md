# Performance

How much a camera costs, where the time goes, and why the hub runs one thread per camera.
Every number here can be reproduced:

```bash
uv run python benchmarks/pipeline.py --opencv-threads 0   # stage costs, CPU per camera
uv run python benchmarks/concurrency.py --opencv-threads 0  # threads vs processes
```

Measured on an Apple M4 (10 cores), Python 3.14, OpenCV 5.0. Frames come from the built-in
simulator (a figure walking through a noisy scene), generated up front and replayed, so the
numbers measure the hub rather than the frame generator. Decoding RTSP/H.264 or USB video is
camera-specific; a JPEG decode stands in for it.

## Summary

| Finding | Change | Effect |
|---------|--------|--------|
| Cameras running at the target rate lost up to half their frames: a frame 99 ms after the last one missed the 100 ms interval, so the next analysed frame came 200 ms later | Frames are analysed on a fixed schedule with a quarter interval of jitter tolerance | 10 fps target: **6.3 → 10.0** analysed fps (30 fps camera: 8.4 → 10.0) |
| `INTER_AREA` has a fast path only for an exact 2x reduction. 1080p → 640 px costs 1.3 ms, 1440p → 960 px 20 ms, 720p → 960 px 5.9 ms | `resize_to_width` halves with `INTER_AREA`, then finishes with `INTER_LINEAR` (mean difference to a pure area average < 3 grey levels) | 720p live frame **6.5 → 0.8 ms**; 1080p detection resize 1.3 → 0.14 ms |
| OpenCV's own thread pool competes with the camera threads | `VISION_HUB_VISION__OPENCV_THREADS=0` (default): every OpenCV call runs sequentially in its camera's thread | **~25 % less CPU** per camera (8.7 → 6.6 % of a core at 1080p) |
| Live frames were annotated at full resolution, then downscaled | Downscale first, draw the boxes on the small copy; frames without motion are not copied at all | 1080p: 0.73 → 0.65 ms per live frame |

Already in place since Phase 2, and confirmed by profiling: detection runs on a 640 px copy
with boxes scaled back to the original frame, and each live JPEG is encoded once and shared by
every viewer (`LatestFrame`), so viewers cost bandwidth, not CPU.

## Where the time goes (per frame)

Median / 95th percentile in milliseconds, one thread:

| Stage | 720p | 1080p |
|-------|------|-------|
| JPEG decode (camera-side proxy) | 1.26 / 1.36 | 2.96 / 3.03 |
| Detect @ 320 px | 0.14 / 0.18 | 0.21 / 0.22 |
| **Detect @ 640 px (default)** | 0.35 / 0.37 | 0.47 / 0.49 |
| Detect @ 960 px | 0.83 / 0.92 | 0.76 / 0.80 |
| Detect @ native resolution | 1.15 / 1.24 | 2.60 / 2.63 |
| Resize to 960 px, `INTER_AREA` | 5.93 / 7.66 | 0.07 / 0.07 |
| Resize to 960 px, `INTER_LINEAR` | 0.17 / 0.18 | 0.20 / 0.21 |
| Live frame: annotate, downscale, encode (before) | 6.52 / 6.87 | 0.73 / 0.77 |
| **Live frame: downscale, annotate, encode (now)** | 0.76 / 0.78 | 0.65 / 0.66 |
| Live frame without motion (now) | 0.74 / 0.94 | 0.62 / 0.64 |

Detection at 640 px keeps the cost nearly independent of the camera's resolution; decoding is
what grows with it. `processing_width` (per device) trades sensitivity to small, distant
movement for CPU.

## CPU per camera

Percent of one core, 10 analysed frames per second:

| Cameras | Resolution | Camera fps | Live viewer | Before (Phase 3.3) | Now | Analysed fps before → now |
|---------|------------|------------|-------------|-------------------:|----:|--------------------------:|
| 1 | 720p | 10 | yes | 12.4 % | 6.5 % | 6.3 → 10.1 |
| 1 | 1080p | 30 | no (1 frame/s) | 10.3 % | 4.2 % | 1.0 → 1.0 |
| 1 | 1080p | 30 | yes | 12.1 % | 6.6 % | 8.5 → 10.0 |
| 4 | 1080p | 30 | yes | 10.5 % | 6.8 % | 8.5 → 10.0 |
| 8 | 1080p | 30 | yes | 8.8 % | 6.4 % | 8.4 → 10.1 |

"Now" includes all four changes above (OpenCV's thread pool off); with the pool on, the same
cases cost 9.1, 6.3, 8.7, 7.5 and 6.3 %. The hub analyses more frames than before for about
half the CPU. Without a viewer, live
frames are encoded once a second (for snapshots), which is why an unwatched camera is cheaper.
Add the camera's own decoding on top: for a 1080p stream, roughly 3 ms per decoded frame.

## Threads, `to_thread` or processes? (AD-7)

Each simulated camera loops as fast as it can over decode, detect and encode, so this is the
saturated worst case. Event-loop lag is how late a 5 ms timer fires: the delay every HTTP
request and WebSocket message would see on top of its own work.

| Strategy | Cameras | Frames/s (total) | Loop lag p50 / p99 (ms) |
|----------|--------:|-----------------:|------------------------:|
| **Thread per camera (the hub)** | 1 | 236 | 0.2 / 0.6 |
| | 4 | 773 | 0.1 / 0.7 |
| | 8 | 1047 | 0.2 / 2.2 |
| | 16 | 1197 | 0.5 / 5.2 |
| `asyncio.to_thread` per frame | 4 | 744 | 0.3 / 0.7 |
| | 16 | 1251 | 0.6 / 2.4 |
| Process per camera | 4 | 779 | 0.1 / 0.7 |
| | 16 | 1141 | 0.4 / 5.6 |
| Process pool per frame | 4 | 483 | 0.1 / 0.5 |
| | 16 | 382 | 0.2 / 5.4 |

- **Threads scale like processes.** OpenCV releases the GIL inside its C++ calls, so camera
  threads run in parallel; the hub's own Python per frame is tiny. Saturated runs vary by about
  ±5 %, so the first three strategies are equivalent in throughput.
- **Processes buy nothing here** and cost memory, start-up time and inter-process copies of
  every live frame. They would matter only for Python-heavy per-frame work (e.g. a pure-Python
  detector), and a free-threaded Python build would be the simpler answer then.
- **A process pool per frame is the worst option**: each 6 MB 1080p frame is pickled to a
  worker, cutting throughput by two thirds.
- `to_thread` matches dedicated threads, but holds a thread-pool slot per camera anyway and
  adds a hop through the event loop for every frame. A dedicated thread also keeps the capture
  device open in one place, which some backends require.
- The event loop stays responsive (p99 under 1 ms) until cameras outnumber cores.

At the default 10 fps a 1080p camera needs about 7 % of a core for the hub's work plus its
decoding, so CPU, not the architecture, sets the limit on cameras per machine.

## Watching it in production

`GET /metrics` exposes what is needed to see this live: per-camera
`rate(vision_hub_camera_frames_analysed_total[1m])` (analysed fps),
`vision_hub_camera_processing_seconds` (time per frame), `vision_hub_camera_last_frame_age_seconds`
(a stalled camera), `vision_hub_event_loop_lag_seconds`, and process CPU and memory (Linux).
See the README for a scrape configuration.
