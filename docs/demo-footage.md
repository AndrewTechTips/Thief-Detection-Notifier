# Demo footage

The demo cameras (`devices.demo.toml`) play real footage: four free stock clips from
[Pexels](https://www.pexels.com). `vision-hub demo-footage` fetches each clip from Pexels, checks
it against a pinned SHA-256 and turns it into a camera loop in `data/demo/`. This repository
never hosts the clips.

| Camera | Clip | Author | What it shows |
|---|---|---|---|
| Front door | [Man Pressing the Doorbell](https://www.pexels.com/video/man-pressing-the-doorbell-7701952/) | MART PRODUCTION | A man walks down a hallway and rings the bell: a person alert |
| Lobby | [Man in Shirt Walking in Corridor](https://www.pexels.com/video/man-in-shirt-walking-in-corridor-11903981/) | Jonathan Khoo | Someone walks up a long arcade: a person alert, from far away |
| Driveway | [SUV Exits Modern Residential Garage at Dusk](https://www.pexels.com/video/suv-exits-modern-residential-garage-at-dusk-29734450/) | dp singh Bhullar | The garage opens and a car drives out: motion, no person, no alert |
| Patio | [Palm Tree Shadow on Modern Building Exterior](https://www.pexels.com/video/palm-tree-shadow-on-modern-building-exterior-35084306/) | Nothing Ahead | A palm's shadow sways on a wall: motion, no person, no alert |

All four cameras alert on people only, so the demo shows both sides of person detection.

## Licence

The clips are under the [Pexels license](https://www.pexels.com/license/): free to use and
modify, no attribution required (it is given above anyway). The project fetches them from
Pexels instead of redistributing them, and the people in them only ever appear as "Person" or
"Motion", never as suspects.

## How the loops are made

`src/vision_hub/vision/demo.py`, in about 20 seconds for all four:

1. **Pick the camera-like clips.** Out of 18 candidates, these are the ones shot from a fixed
   point: in 4 frames per clip, most of the picture had to stay still over half a second.
   Handheld, panning and drone shots would be motion everywhere.
2. **Check the detector agrees.** Each clip was played twice through the motion detector and
   YOLOX: people score 0.83–0.95 while in view, the car and the shadows 0.00. Two more were
   dropped: a courier who never leaves the frame (one endless event) and a white door whose
   exposure kept changing (three background resets in 12 s).
3. **Trim** where the clip stops being one camera (the driveway clip cuts to another shot at
   35 s), shrink to 640 px and 15 fps, the hub's working size.
4. **Close the loop.** A hard cut from the last frame back to the first would look like motion,
   so the last frame crossfades into the first over 1.5 s. Then the empty scene at the start plays
   back and forth for 6–12 s, so a visit comes every half minute or so.

Measured on the finished loops, each played twice: one person event per visit on the front door
and the lobby, two quiet events per loop on the driveway (the door, then the car), small quiet
events from the shadow on the patio. The lobby's loop point gives one 0.1 s event without a
person, which a people-only camera ignores.

## The public demo

The [live demo](https://andrewtechtips.github.io/iot-vision-hub/) plays these loops in the
browser. `vision-hub export-demo` runs each one three times through the real camera worker (the
first pass warms the background model up) and keeps the middle pass: what was detected on every
analysed frame, and each event with its snapshots and clip. Its clock is the frames read and
its person checks run inline, so the recording is the same on every run. The workflow then
re-encodes the videos to VP9 with ffmpeg: the same frames at the same times, 35 MB down to 3 MB.
