# Visual checklist (P1.18)

The port on the Mac compared with the PS4 look, effect by effect. Each row names the reference shots that show the
effect (`tools/macos/golden_shots.json`, captured with `tools/macos/golden.py`) and what the PS4 shows there. Fill in
the result per backend; MoltenVK artefacts also go to `STATUS.md` known issues.

The PS4 column describes the original. Check it against PS4 footage or a PS4; the shots alone only prove that a backend
draws what the previous one drew.

## How to look

    python3 tools/macos/golden.py capture <label> --exe build/macos/pt
    python3 tools/macos/golden.py list

The images are in `~/personalDEV/pt-game/golden/<label>/<shot>.png` (never in the repository). `list` prints which
shot covers which effect; `golden_shots.json` holds each shot's `look` and `ps4` text in full. For a pass by pass look,
the `hallway`, `bathroom` and `motion` runs also write the render targets of their shots to `targets/` (`--dumps all`
for every run): `<shot>.targets.txt` lists name, width, height and VkFormat of each `<shot>.<name>.bin`.

Result values: `ok` (matches the PS4 look), `differs` (with what), `broken`, `not checked`.

## MoltenVK, first capture

Label `moltenvk-2b92a798-a`: `pt` sha256 `2b92a798…` from `macos` after the `build` merge (`bb97298`), Apple M4 Pro,
macOS 27.2, MoltenVK 1.4.2, Vulkan loader 1.4.363. Against PS4: not checked yet (no PS4 footage at hand); the notes are
what the shots show.

| Effect | Where to look | Shots | Expected PS4 look | MoltenVK | Against PS4 |
|---|---|---|---|---|---|
| Shadows | lamp shadows on walls and floor, contact shadows under objects, characters' shadows | `hall_corridor`, `loop_00_f000`, `loop_01_f010`, `loop_05_f040`, `loop_17_street` | hard lamp shadows, small dark contact shadows, no acne or peter-panning | shadow maps present: radio and objects on the chest have contact shadows, Lisa's shadow on the floor in f040, the barrier's long shadow on the street | not checked |
| Lighting | lamp pools, flashlight cone, red loops, bathroom light | `hall_corridor`, `loop_09_f070`, `loop_10_f080`, `loop_11_f090`, `loop_12_f100`, `loop_15_f160` | warm dim hallway; hard red light in the late loops; a round flashlight spot with a soft edge | all loops lit and readable; red loops saturated red; flashlight spot round and soft (`loop_11_f090`) | not checked |
| Reflections | the polished floor, the bathroom mirror | `hall_corridor`, `loop_02_f005`, `loop_06_f060`, `bath_mirror` | soft blurred floor reflections of lamps and windows; the mirror shows the room behind | floor reflections of the lamp and the end window visible in `hall_corridor` and `loop_02_f005`; `bath_mirror` is mostly the mirror's dirt layer over a dark room: check that the PS4 mirror is this dark there | not checked |
| Subsurface | skin: Lisa's face and arms, the player's face | `loop_04_f030`, `loop_07_f050a`, `loop_13_f110` | pale soft skin with light bleeding through thin parts, no plastic look | Lisa's face in the door gap soft and pale; no seams or black halos at skin edges | not checked |
| VFX | the fake crash glitch, the flare sprites | `loop_14_f120`, `hall_lamp` | the hallway cut into shifted slices during the fake crash | the slice glitch is drawn (geometry repeated in horizontal bands) | not checked |
| Depth of field | photo mode focus at 1 m, f/1.4 | `hall_dof` | the game's own focus blur (zoom, cutscenes): smooth, no banding, no halo around the sharp object | the lamp sharp, the hallway behind blurred smoothly; no banding seen at 1280x720 | not checked |
| Motion blur | a fast mouse turn | `motion_turn` | horizontal blur growing to the frame edges; no ghost copies | strong horizontal blur over the walls and the picture frame, no double images | not checked |
| Lens flare | flare ghosts from the wall lamp | `hall_lamp` | ghosts mirrored through the frame centre from the lamp | the ghost (`fx_sh_flrlgt02_s3`) is faint at this pose (alpha about 0.05 in `PT_FLARE_LOG`); `lens_ghosts = 0` changes 41 % of pixels by more than 8 steps at a nearby pose, so it is drawn | not checked |
| Film grain | dark areas of every game shot | `hall_corridor`, `loop_00_f000`, `loop_05_f040`, `ui_subtitle` | fine, moving grain strongest in the mid-darks | present: `film_grain = 0` in pt.ini changes 99 % of pixels (up to 19 steps) in the hallway shots; repeats exactly between runs | not checked |
| Tonemap / LUT | per loop colour grades | `loop_07_f050a`, `loop_10_f080`, `loop_16_ending`, `loop_17_street` | warm hallway, red late loops, cold desaturated street | grades differ per loop as expected: warm f010, red f080/f100/f110/f160, cold foggy street with sodium lamps | not checked |
| Eye adaptation | a cut from the dark start room to the wall lamp, then 14, 58 and 197 frames later | `exposure_dark`, `exposure_cut`, `exposure_adapt_15`, `exposure_adapt_60`, `exposure_adapt_200` | a blown-out frame after the cut that settles smoothly, no pumping | ev -2.04 in the dark, the cut frame blown out, then -2.56, -3.57, -4.31: a smooth, monotonic fall | not checked |
| UI / fonts | subtitle, options menu, PC settings page | `ui_subtitle`, `ui_options`, `ui_pc_settings` | the original's subtitle font and options screen | subtitle crisp and complete ("You can't trust the tap water."); options and PC settings pages complete, no clipped or missing glyphs, keyboard prompts drawn | not checked |

Shots without a P1.18 effect: `viewer_albedo` and `viewer_normals`, debug views of the static hallway stage for the
Metal bring-up (P4.8c). The stage viewer's lit view is near black on this build (only the light sprites show), so the
shot list uses the albedo and normal views there.

## Other observations

- Every `loops` capture logs the same four Lua errors when the loop browser enters f080 (after `NextFloor f070 -> f080`):
  `trapLightEnable.lua:27: bad argument #1 to 'pairs' (table expected, got nil)`, each followed by
  `script: trapLightEnable.Exec failed or missing`. The shots are unaffected as far as can be seen and identical between
  captures, but a light trap of f080 does not run. Not checked whether the Windows build logs the same.
- Long stalls during captures (about 900 s, and stage loads of about 25 s) were the Mac sleeping, not the game. See K6 in
  `docs/macos/STATUS.md`; `golden.py` now keeps the Mac awake and records the time slept per run.

## Metal (P4.8, P4.11)

Copy the MoltenVK table, compare with `golden.py compare <moltenvk label> <metal label> --profile backend --diff`, and
note every shot that fails or looks different.
