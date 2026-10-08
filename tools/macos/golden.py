#!/usr/bin/env python3
"""Reference screenshots of the Apple Silicon port (docs/macos/PLAN.md P1.19).

capture <label>      runs the shot list (golden_shots.json) headless and writes the images, the optional render target
                     dumps and a manifest to $PT_GOLDEN_DIR/<label>/ (default ~/personalDEV/pt-game/golden)
compare <a> <b>      compares b against a shot by shot with the limits of a profile; exit 1 when a shot fails or is missing
list                 the runs, the shots and which shot covers which effect of P1.18
selftest             checks the compare metrics on synthetic images and the shot list's rules

The images and dumps are game content: they stay outside the repository (WORKFLOW.md rule 7).

Profiles (differences in 8-bit steps per channel, 0..255; a pixel's difference is that of its most changed channel; a
shot passes when both limits hold):

  exact     no pixel may change. Two captures of the same build, and a Phase 3 step that should draw the same picture:
            the runs have a fixed tick, seed and input, so they give the same bytes.
  refactor  at most 0.05 % of pixels more than 2 steps off, and no 16x16 block more than 1 step off on average. For
            changes that may reorder floating point work (a pass moved, a shader rebuilt): the output then moves by a
            step here and there, and a depth or shadow test flips on a few edge pixels.
  backend   at most 0.5 % of pixels more than 8 steps off, and no 16x16 block more than 4 steps off on average. Metal
            against MoltenVK (Phase 4): another shader translation and other rounding, the same picture.

The pixel limit says how much of the frame changed visibly; the block limit catches what that share averages away: a
lost 16x16 detail is 0.03 % of a 720p frame, and a slight shift of the whole image changes every pixel by less than the
tolerance. PSNR is reported, not judged: a few flipped edge pixels, which both profiles allow, already bring it under
40 dB. The backend numbers are a first guess, to be set from the first Metal captures (P4.8).

Alpha is checked, not compared: the game writes opaque screenshots, and a shot with another alpha fails in every profile.
With --targets the render target dumps count too: a shot dumped on one side only, a missing or malformed index, a file
of the wrong size or an unknown format fails, and so does a file changed after its capture (the manifest keeps hashes).
Non-finite values compare by kind (NaN, +Inf, -Inf): a change of kind fails exact and refactor, and counts as an over
texel in backend.

Each run records its own provenance (executable, tools, platform, run definition), so a label retaken in part with
another build says so, and compare names the runs whose builds or definitions differ.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
SHOTS = Path(__file__).resolve().with_name("golden_shots.json")
GOLDEN = Path(os.environ.get("PT_GOLDEN_DIR", "~/personalDEV/pt-game/golden")).expanduser()
DEFAULT_EXE = REPO / "build" / "macos" / "pt"
DEFAULT_GAME = Path(os.environ.get("PT_GAME_DIR", "~/personalDEV/pt-game/CUSA01127")).expanduser()

# docs/macos/PLAN.md P1.18; the shot list covers each one at least once
EFFECTS = ("shadows", "lighting", "reflections", "subsurface", "vfx", "depth_of_field", "motion_blur", "lens_flare",
           "film_grain", "tonemap_lut", "ui_fonts")

# target_tolerance: the render target dumps, relative to max(1, |value|) per channel (they hold floats and depth);
# max_kind_changes: values that may change kind (finite, NaN, +Inf, -Inf), None for no own limit
PROFILES = {
    "exact": {"tolerance": 0, "max_over_pct": 0.0, "max_block": 0.0, "target_tolerance": 0.0, "max_target_over_pct": 0.0, "max_kind_changes": 0},
    "refactor": {"tolerance": 2, "max_over_pct": 0.05, "max_block": 1.0, "target_tolerance": 1e-3, "max_target_over_pct": 0.05,
                 "max_kind_changes": 0},
    "backend": {"tolerance": 8, "max_over_pct": 0.5, "max_block": 4.0, "target_tolerance": 1e-2, "max_target_over_pct": 0.5,
                "max_kind_changes": None},
}
BLOCK = 16

# VkFormat values of the targets SceneRenderer::DumpTargets writes: element type and channels
DUMP_FORMATS = {
    9: (np.uint8, 1),       # R8_UNORM
    16: (np.uint8, 2),      # R8G8_UNORM
    37: (np.uint8, 4),      # R8G8B8A8_UNORM
    43: (np.uint8, 4),      # R8G8B8A8_SRGB
    44: (np.uint8, 4),      # B8G8R8A8_UNORM
    50: (np.uint8, 4),      # B8G8R8A8_SRGB
    83: (np.float16, 2),    # R16G16_SFLOAT
    97: (np.float16, 4),    # R16G16B16A16_SFLOAT
    100: (np.float32, 1),   # R32_SFLOAT
    109: (np.float32, 4),   # R32G32B32A32_SFLOAT
    126: (np.float32, 1),   # D32_SFLOAT
}

# {shot:id} becomes the shot's .png path (sshot), {burst:id} its path without extension (sburst writes id-00.png)
SHOT_TOKEN = re.compile(r"\{(shot|burst):([a-z0-9_]+)\}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def command_output(cmd, cwd=None, timeout=30):
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def guarded(path, follow=True, mkdir=False):
    """Every path golden.py writes, renames or deletes passes here first: with its symlinks resolved (the last one too
    unless follow is off, to delete a link itself) it must lie in the golden folder and outside the repository."""
    root = GOLDEN.resolve()
    if root == REPO or REPO in root.parents:
        sys.exit(f"{root} is inside the repository; reference images are game content and stay outside it (PT_GOLDEN_DIR)")
    path = Path(path)
    real = path.resolve() if follow else path.parent.resolve() / path.name
    if real != root and root not in real.parents:
        sys.exit(f"{path} leads to {real}, outside {root}: refused")
    if real == REPO or REPO in real.parents:
        sys.exit(f"{path} leads into the repository ({real}): refused")
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def label_path(label):
    if not label or "/" in label or label.startswith("."):
        sys.exit(f"label {label!r}: a plain folder name under {GOLDEN}")
    label_dir = guarded(GOLDEN.resolve() / label)
    if label_dir.resolve() == GOLDEN.resolve():
        sys.exit(f"label {label!r} leads to the golden folder itself: refused")
    return label_dir


# --- shot list ---------------------------------------------------------------------------------------------------------


def load_shots(path=SHOTS):
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = check_shots(data)
    if problems:
        sys.exit(f"{path}:\n  " + "\n  ".join(problems))
    return data


def check_shots(data):
    """The shot list's rules: unique ids, every shot taken exactly once by its own run, every P1.18 effect covered."""
    problems = []
    run_ids = set()
    seen = set()
    covered = set()
    for run in data.get("runs", []):
        rid = run.get("id", "?")
        if rid in run_ids:
            problems.append(f"run {rid}: id used twice")
        run_ids.add(rid)
        shots = run.get("shots", [])
        ids = [s.get("id") for s in shots]
        if not shots:
            problems.append(f"run {rid}: no shots")
        if "viewer" in run:
            if len(shots) != 1:
                problems.append(f"run {rid}: a viewer run takes exactly one shot (--screenshot)")
            if not run["viewer"].get("stage"):
                problems.append(f"run {rid}: viewer without a stage")
        else:
            lines = route_lines(run, data)
            taken = [m.group(2) for line in lines for m in SHOT_TOKEN.finditer(line)]
            for i, line in enumerate(lines):
                # a shot is rendered after the frame's whole sequence ran: a camera or menu step right after it would be in the picture
                if " sshot " in line and i + 1 < len(lines) and lines[i + 1].split()[1] not in ("swait", "squit"):
                    problems.append(f"run {rid}: {line.split()[-1]} is followed by {lines[i + 1].split()[1]}, not by swait or squit")
            for line in lines:
                for m in SHOT_TOKEN.finditer(line):
                    # normalize_burst handles the one frame of sburst 1
                    if m.group(1) == "burst" and line.split()[1:3] != ["sburst", "1"]:
                        problems.append(f"run {rid}: {{burst:{m.group(2)}}} needs 'sburst 1'")
            for sid in ids:
                if taken.count(sid) != 1:
                    problems.append(f"run {rid}: shot {sid} is taken {taken.count(sid)} times by the route")
            for sid in taken:
                if sid not in ids:
                    problems.append(f"run {rid}: the route takes {sid}, which is not one of its shots")
        for shot in shots:
            sid = shot.get("id", "?")
            if not re.fullmatch(r"[a-z0-9_]+", sid):
                problems.append(f"shot {sid}: ids are lower case letters, digits and _")
            if sid in seen:
                problems.append(f"shot {sid}: id used twice")
            seen.add(sid)
            if not isinstance(shot.get("effects"), list):
                problems.append(f"shot {sid}: effects is a list (empty for a debug view)")
            for key in ("look", "ps4"):
                if not shot.get(key):
                    problems.append(f"shot {sid}: no {key}")
            for effect in shot.get("effects", []):
                if effect not in EFFECTS:
                    problems.append(f"shot {sid}: unknown effect {effect}")
                covered.add(effect)
    for effect in EFFECTS:
        if effect not in covered:
            problems.append(f"no shot covers {effect}")
    return problems


def run_options(run, data):
    options = dict(data.get("defaults", {}).get("options", {}))
    options.update(run.get("options", {}))
    return options


def route_lines(run, data):
    """The run's input script; an entry holds one or more commands joined by | (as kLoopPreviewPoses in main.cpp).
    Lines without a frame number start at start_frame, where the sequenced (s...) commands queue up in order."""
    start = run_options(run, data).get("start_frame", 700)
    lines = []
    for entry in run.get("route", []):
        for line in entry.split("|"):
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line if line[0].isdigit() else f"{start} {line}")
    return lines


def settings_ini(settings):
    text = "; written by tools/macos/golden.py: a reference run never reads the user's own pt.ini\n"
    for section, values in settings.items():
        text += f"\n[{section}]\n"
        for key, value in values.items():
            text += f"{key} = {int(value) if isinstance(value, bool) else value}\n"
    return text


# --- capture -----------------------------------------------------------------------------------------------------------


def git_state(path):
    folder = path if path.is_dir() else path.parent
    rev = command_output(["git", "rev-parse", "HEAD"], cwd=folder)
    if not rev:
        return None
    return {"rev": rev, "branch": command_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=folder),
            "dirty": bool(command_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=folder)),
            "top": command_output(["git", "rev-parse", "--show-toplevel"], cwd=folder)}


def platform_info():
    info = {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": platform.python_version()}
    if sys.platform == "darwin":
        info["macos"] = platform.mac_ver()[0]
        info["cpu"] = command_output(["sysctl", "-n", "machdep.cpu.brand_string"])
        profile = command_output(["system_profiler", "SPDisplaysDataType", "-json"], timeout=60)
        try:
            displays = json.loads(profile).get("SPDisplaysDataType", []) if profile else []
            info["gpu"] = [{"model": d.get("sppci_model"), "cores": d.get("sppci_cores"), "metal": d.get("spdisplays_mtlgpufamilysupport")}
                           for d in displays]
        except ValueError:
            pass
        brew = command_output(["brew", "list", "--versions", "molten-vk", "vulkan-loader"])
        if brew:
            info["brew"] = brew.splitlines()
    summary = command_output(["vulkaninfo", "--summary"], timeout=60)
    if summary:
        wanted = ("apiVersion", "driverVersion", "deviceName", "driverID", "driverName", "driverInfo", "conformanceVersion")
        info["vulkaninfo"] = sorted({line.strip() for line in summary.splitlines() if line.strip().startswith(wanted)})
    return info


def log_findings(text):
    lines = text.splitlines()
    expectations = [line for line in lines if re.search(r"input script: \d+ expectations, \d+ failed", line)]
    return {
        "errors": [line for line in lines if re.search(r"\]\s+error\s", line)][:50],
        "skipped": [line for line in lines if "dropped, loop not reached" in line or "gave up" in line or "stuck at" in line][:50],
        "expect_failed": [line for line in lines if "expect FAILED" in line][:50],
        "expectations": expectations[-1] if expectations else None,
        "device": [line for line in lines if re.search(r"vulkan: |MoltenVK", line)][:20],
        "exit": next((line for line in reversed(lines) if "exit: code" in line), None),
    }


def build_command(run, data, exe, game, work, label_dir):
    options = run_options(run, data)
    cmd = [str(exe), "--game", str(game), "--headless", "--log", str(work / "pt.log"),
           "--width", str(options["width"]), "--height", str(options["height"])]
    if "viewer" in run:
        viewer = run["viewer"]
        cmd += ["--stage", viewer["stage"], "--frames", str(viewer.get("frames", 120)),
                "--screenshot", str(label_dir / f"{run['shots'][0]['id']}.png")]
        if viewer.get("top"):
            cmd.append("--top")
        if viewer.get("camera"):
            cmd += ["--camera", *[str(v) for v in viewer["camera"]]]
    else:
        cmd += ["--frames", str(run.get("frames", options.get("frames", 20000))), "--input-script", str(work / "route.txt"),
                "--settings", str(work / "pt.ini"), "--seed", str(options["seed"]), "--tick-rate", str(options["tick_rate"]),
                "--demo-rate", str(options["demo_rate"]), "--shot-warmup", str(options["shot_warmup"])]
        if options.get("shot_settle"):
            cmd.append("--shot-settle")
        if options.get("render_all"):
            cmd.append("--render-all")
        if run.get("start_floor"):
            cmd += ["--start-floor", run["start_floor"]]
    return cmd + data.get("defaults", {}).get("args", []) + run.get("args", [])


def keep_awake():
    # an idle Mac sleeps in the middle of a capture and freezes the game for minutes (K6); -s holds on AC power, where
    # macOS turns the idle assertion (-i) off in maintenance wakes, and -w ends it with this process
    if sys.platform == "darwin":
        subprocess.Popen(["caffeinate", "-s", "-i", "-w", str(os.getpid())])


def capture_run(run, data, exe, game, label_dir, dumps, provenance):
    work = guarded(label_dir / "work" / run["id"])
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    targets_dir = guarded(label_dir / "targets")
    shots = run["shots"]
    for shot in shots:
        sid = shot["id"]
        for stale in [*label_dir.glob(f"{sid}.*"), *label_dir.glob(f"{sid}-*.*"), *targets_dir.glob(f"{sid}.*")]:
            guarded(stale, follow=False).unlink()
    if "viewer" not in run:
        def place(m):
            path = (label_dir / m.group(2)).as_posix()
            return path if m.group(1) == "burst" else path + ".png"

        guarded(work / "route.txt").write_text("\n".join(SHOT_TOKEN.sub(place, line) for line in route_lines(run, data)) + "\n", encoding="utf-8")
        settings = json.loads(json.dumps(data.get("defaults", {}).get("settings", {})))
        for section, values in run.get("settings", {}).items():
            settings.setdefault(section, {}).update(values)
        guarded(work / "pt.ini").write_text(settings_ini(settings), encoding="utf-8")
    cmd = build_command(run, data, exe, game, work, label_dir)
    # PT_HEADLESS_ONLY makes the game refuse any run that would open a window
    env = {**os.environ, **data.get("defaults", {}).get("env", {}), **run.get("env", {}), "PT_HEADLESS_ONLY": "1"}
    env.pop("PT_TARGET_DUMP", None)
    dumped = "viewer" not in run and (dumps == "all" or (dumps == "flagged" and bool(run.get("dump"))))
    if dumped:
        env["PT_TARGET_DUMP"] = "1"
    timeout = run.get("timeout", run_options(run, data).get("timeout", 1800))
    print(f"run {run['id']}: {len(shots)} shot(s){', target dumps' if dumped else ''} ...", flush=True)
    started, started_awake = time.time(), time.monotonic()
    with open(guarded(work / "stdout.txt"), "wb") as out:
        try:
            code = subprocess.run(cmd, cwd=work, stdout=out, stderr=subprocess.STDOUT, env=env, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = f"timeout after {timeout} s"
    elapsed = time.time() - started
    # the monotonic clock stops while the Mac sleeps: the difference is time the run spent frozen, not rendering (K6)
    slept = max(0.0, elapsed - (time.monotonic() - started_awake))
    if slept > 2:
        print(f"run {run['id']}: the computer slept {slept:.0f} s during this run; its timings are not the game's", flush=True)
    log_path = work / "pt.log"
    findings = log_findings(log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "")
    result = {"provenance": {**provenance, "run": run_hash(run, data), "dumps": dumped}, "command": cmd,
              "env": {k: v for k, v in env.items() if k.startswith(("PT_", "VK_", "MVK_"))}, "exit": code, "seconds": round(elapsed, 1), "slept": round(slept, 1),
              "log": log_path.relative_to(label_dir).as_posix(), "findings": findings, "shots": {}}
    problems = []
    if code != 0:
        problems.append(f"exit code {code}")
    options = run_options(run, data)
    for shot in shots:
        sid = shot["id"]
        problems += [f"shot {sid}: {p}" for p in normalize_burst(label_dir, sid)]
        image = label_dir / f"{sid}.png"
        if not image.exists():
            problems.append(f"shot {sid}: not written")
            continue
        entry = {"file": image.name, "sha256": sha256(image), "dumped": dumped}
        try:
            pixels = load_shot(image)
            entry["size"] = [pixels.shape[1], pixels.shape[0]]
            if entry["size"] != [options["width"], options["height"]]:
                problems.append(f"shot {sid}: {entry['size']} pixels, {options['width']}x{options['height']} asked for")
        except ShotError as error:
            problems.append(f"shot {sid}: {error}")
        for part in sorted(label_dir.glob(f"{sid}.*")):
            if part != image:
                part.rename(guarded(targets_dir / part.name, mkdir=True))
        index = targets_dir / f"{sid}.targets.txt"
        if dumped or index.exists():
            try:
                if not dumped:
                    raise TargetError("target dumps written by a run without PT_TARGET_DUMP")
                entry["targets"] = record_targets(targets_dir, sid)
            except TargetError as error:
                problems.append(f"shot {sid}: {error}")
        result["shots"][sid] = entry
    if findings["skipped"]:
        problems.append(f"{len(findings['skipped'])} route step(s) dropped, stuck or given up (findings.skipped)")
    if findings["expect_failed"]:
        problems.append(f"{len(findings['expect_failed'])} route expectation(s) failed (findings.expect_failed)")
    result["problems"] = problems
    print(f"run {run['id']}: {'ok' if not problems else 'FAILED: ' + '; '.join(problems)} ({elapsed:.0f} s, log {log_path})", flush=True)
    return result


def normalize_burst(label_dir, sid):
    """sburst 1 writes <id>-00.png and, with PT_TARGET_DUMP, <id>-00.targets.txt and <id>-00.<target>.bin; they all
    become <id>.*. Any other burst frame is a problem (the shot list allows sburst 1 only)."""
    problems = []
    for part in sorted(label_dir.glob(f"{sid}-00.*")):
        dest = guarded(label_dir / (sid + part.name[len(sid) + 3:]))
        if dest.exists():
            problems.append(f"both {part.name} and {dest.name} were written")
            continue
        part.rename(dest)
    extra = sorted(p.name for p in label_dir.glob(f"{sid}-*.*"))
    if extra:
        problems.append(f"burst frames left over: {', '.join(extra[:5])}")
    return problems


def provenance_now(exe, game, platform_data):
    stat = exe.stat()
    return {
        "captured": utc_now(),
        "exe": {"path": str(exe), "sha256": sha256(exe), "bytes": stat.st_size,
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "git": git_state(exe)},
        "game": {"path": str(game), "files": {p.name: p.stat().st_size for p in sorted(game.iterdir()) if p.is_file()}},
        "tools": {"golden.py": sha256(Path(__file__)), "golden_shots.json": sha256(SHOTS), "repo": git_state(REPO)},
        "platform": platform_data,
    }


def run_hash(run, data):
    """What decides a run's pictures besides the build: its own definition and the shot list's defaults."""
    text = json.dumps({"run": run, "defaults": data.get("defaults", {})}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def run_provenance(manifest, rid):
    """A run's provenance; a manifest of the first format kept one capture-wide record."""
    run = manifest.get("runs", {}).get(rid)
    if run is None:
        return None
    if "provenance" in run:
        return run["provenance"]
    return {"exe": manifest.get("exe", {}), "platform": manifest.get("platform", {}), "tools": manifest.get("tools", {}),
            "captured": manifest.get("updated"), "legacy": True}


def capture(args):
    data = load_shots()
    exe = Path(args.exe).expanduser().resolve()
    game = Path(args.game).expanduser().resolve()
    if not exe.is_file():
        sys.exit(f"no game executable at {exe} (--exe)")
    if not (game / "chunk1.psarc").exists():
        sys.exit(f"no game files at {game} (--game or PT_GAME_DIR)")
    label_dir = label_path(args.label)
    runs = data["runs"]
    if args.runs:
        wanted = set(args.runs.split(","))
        if unknown := wanted - {r["id"] for r in runs}:
            sys.exit(f"unknown run(s): {', '.join(sorted(unknown))}")
        runs = [r for r in runs if r["id"] in wanted]
    manifest_path = guarded(label_dir / "manifest.json")
    if manifest_path.exists() and not args.force and not args.runs:
        sys.exit(f"{label_dir} already holds a capture: --force replaces it, --runs retakes some runs")
    if args.force and not args.runs and label_dir.exists():
        shutil.rmtree(label_dir)
    label_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for rid in list(manifest.get("runs", {})):
        manifest["runs"][rid]["provenance"] = run_provenance(manifest, rid)
    manifest = {"format": 2, "label": args.label, "created": manifest.get("created", utc_now()), "updated": utc_now(),
                "runs": manifest.get("runs", {})}
    provenance = provenance_now(exe, game, platform_info())
    keep_awake()
    failed = False
    for run in runs:
        result = capture_run(run, data, exe, game, label_dir, args.dumps, provenance)
        manifest["runs"][run["id"]] = result
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if result["problems"]:
            failed = True
            if not args.keep_going:
                print("stopped at the first failed run (--keep-going runs the rest)")
                break
    shots = sum(len(r["shots"]) for r in manifest["runs"].values())
    builds = {(run_provenance(manifest, rid) or {}).get("exe", {}).get("sha256") for rid in manifest["runs"]}
    if len(builds) > 1:
        print(f"note: {args.label} now holds runs of {len(builds)} different executables (each run records its own)")
    print(f"{label_dir}: {shots} shot(s), manifest {manifest_path}")
    return 1 if failed else 0


# --- compare -----------------------------------------------------------------------------------------------------------


class ShotError(ValueError):
    pass


class TargetError(ValueError):
    pass


def load_shot(path):
    """The shot's RGB. The game writes opaque screenshots (Renderer::SaveScreenshot sets alpha to 255), so a shot with
    any other alpha is rejected rather than compared: alpha is checked, not measured."""
    try:
        with Image.open(path) as image:
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except (OSError, ValueError, SyntaxError) as error:
        raise ShotError(f"{path.name} is not a readable image ({error})") from None
    if (rgba[:, :, 3] != 255).any():
        raise ShotError(f"{path.name} has {np.count_nonzero(rgba[:, :, 3] != 255)} pixels that are not opaque")
    return rgba[:, :, :3]


def image_metrics(a, b, tolerance):
    """a and b are HxWx3 uint8; returns the metrics and the per pixel difference (largest channel)."""
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    pixel = diff.max(axis=2)
    mse = float(np.mean(diff.astype(np.float64) ** 2))
    h, w = pixel.shape
    hb, wb = -(-h // BLOCK), -(-w // BLOCK)
    sums = np.zeros((hb * BLOCK, wb * BLOCK))
    area = np.zeros_like(sums)
    sums[:h, :w] = diff.mean(axis=2)
    area[:h, :w] = 1.0
    blocks = sums.reshape(hb, BLOCK, wb, BLOCK).sum(axis=(1, 3)) / area.reshape(hb, BLOCK, wb, BLOCK).sum(axis=(1, 3))
    worst = np.unravel_index(int(np.argmax(blocks)), blocks.shape)
    return {
        "max_abs": int(pixel.max()),
        "mean_abs": float(diff.mean()),
        "changed_pct": 100.0 * np.count_nonzero(pixel) / pixel.size,
        "over_pct": 100.0 * np.count_nonzero(pixel > tolerance) / pixel.size,
        "worst_block": float(blocks.max()),
        "worst_block_at": [int(worst[1]) * BLOCK, int(worst[0]) * BLOCK],
        "psnr": math.inf if mse == 0.0 else 10.0 * math.log10(255.0 ** 2 / mse),
    }, pixel


def judge(metrics, profile):
    reasons = []
    if metrics["over_pct"] > profile["max_over_pct"]:
        reasons.append(f"{metrics['over_pct']:.4f} % of pixels more than {profile['tolerance']} steps off (limit {profile['max_over_pct']} %)")
    if metrics["worst_block"] > profile["max_block"]:
        reasons.append(f"the block at x,y {metrics['worst_block_at']} is {metrics['worst_block']:.2f} steps off (limit {profile['max_block']})")
    return reasons


def diff_image(b, pixel, tolerance, path):
    """b in dim grey; changed pixels red, brighter with the difference (x8); pixels over the tolerance yellow."""
    grey = (b.astype(np.float32).mean(axis=2) * 0.35).astype(np.uint8)
    out = np.stack([grey, grey, grey], axis=2)
    changed = pixel > 0
    out[changed] = 0
    out[changed, 0] = np.clip(96 + pixel[changed].astype(np.int32) * 8, 0, 255)
    if tolerance > 0:
        out[pixel > tolerance] = (255, 230, 0)
    Image.fromarray(out).save(guarded(path, mkdir=True))


def read_targets(folder, sid):
    """The targets of a shot from its <shot>.targets.txt (name width height VkFormat per line); None without an index.
    Every line must be well formed, with a known format, and every file must hold exactly width x height texels."""
    index = folder / f"{sid}.targets.txt"
    if not index.exists():
        return None
    targets = {}
    for number, line in enumerate(index.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        try:
            if len(parts) != 4:
                raise ValueError
            name, width, height, fmt = parts[0], int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            raise TargetError(f"{index.name} line {number} is not 'name width height format': {line!r}") from None
        if not re.fullmatch(r"[a-z_]+", name) or width <= 0 or height <= 0 or name in targets:
            raise TargetError(f"{index.name} line {number}: bad or repeated target {line!r}")
        if name == "mirror_square":
            # the mirror capture's square, written as 8-bit RGBA with gamma 2.2 to <shot>.mirror.raw (format field 0)
            if fmt != 0:
                raise TargetError(f"{index.name} line {number}: mirror_square with format {fmt}")
            targets[name] = (folder / f"{sid}.mirror.raw", width, height, fmt, (np.uint8, 4))
        elif fmt in DUMP_FORMATS:
            targets[name] = (folder / f"{sid}.{name}.bin", width, height, fmt, DUMP_FORMATS[fmt])
        else:
            raise TargetError(f"{index.name} line {number}: VkFormat {fmt} of {name} is not one golden.py can read")
    if not targets:
        raise TargetError(f"{index.name} lists no targets")
    for name, (path, width, height, _, (dtype, channels)) in targets.items():
        expected = width * height * channels * np.dtype(dtype).itemsize
        if not path.exists():
            raise TargetError(f"{path.name} (listed in {index.name}) is missing")
        if path.stat().st_size != expected:
            raise TargetError(f"{path.name}: {path.stat().st_size} bytes, {expected} expected for {width}x{height}")
    return targets


def record_targets(folder, sid):
    targets = read_targets(folder, sid)
    if targets is None:
        raise TargetError(f"no {sid}.targets.txt although the run dumped its targets")
    index = folder / f"{sid}.targets.txt"
    return {"index": {"file": index.name, "sha256": sha256(index)},
            "files": {name: {"file": t[0].name, "bytes": t[0].stat().st_size, "sha256": sha256(t[0])} for name, t in targets.items()}}


def load_target(entry):
    path, width, height, _, (dtype, channels) = entry
    raw = path.read_bytes()
    if len(raw) != width * height * channels * np.dtype(dtype).itemsize:
        raise TargetError(f"{path.name}: {len(raw)} bytes, {width * height * channels * np.dtype(dtype).itemsize} expected")
    values = np.frombuffer(raw, dtype=dtype).reshape(height, width, channels)
    return (values.astype(np.float32) / 255.0 if dtype == np.uint8 else values.astype(np.float32)), raw


# a value's kind for the non-finite policy: NaN (any payload or sign), +Inf and -Inf are three kinds, finite the fourth
KINDS = ("finite", "NaN", "+Inf", "-Inf")


def value_kinds(values):
    kinds = np.zeros(values.shape, dtype=np.uint8)
    kinds[np.isnan(values)] = 1
    kinds[np.isposinf(values)] = 2
    kinds[np.isneginf(values)] = 3
    return kinds


def target_metrics(a, b, tolerance):
    """Non-finite policy: two values of the same non-finite kind are equal (NaN payloads are not compared); a change of
    kind (finite to NaN, NaN to +Inf, +Inf to -Inf, ...) is a difference of its own, counted by kind and always over the
    tolerance. Finite values are over when they differ by more than tolerance relative to max(1, |a|)."""
    ka, kb = value_kinds(a), value_kinds(b)
    finite = (ka == 0) & (kb == 0)
    fa, fb = np.where(finite, a, 0.0), np.where(finite, b, 0.0)
    diff = np.abs(fa - fb)
    changed = ka != kb
    over = (diff > tolerance * np.maximum(1.0, np.abs(fa))) | changed
    texels = over.any(axis=2)
    kind_changes = {}
    if changed.any():
        pairs, counts = np.unique(np.stack([ka[changed], kb[changed]], axis=1), axis=0, return_counts=True)
        kind_changes = {f"{KINDS[x]}->{KINDS[y]}": int(n) for (x, y), n in zip(pairs, counts)}
    return {"max_abs": float(diff.max()), "mean_abs": float(diff.mean()), "over_pct": 100.0 * np.count_nonzero(texels) / texels.size,
            "kind_changes": kind_changes, "nonfinite": [int(np.count_nonzero(ka)), int(np.count_nonzero(kb))]}


def judge_target(m, profile, same_bytes):
    if same_bytes:
        return []
    reasons = ["bytes differ"] if profile["target_tolerance"] == 0.0 else []
    if m["over_pct"] > profile["max_target_over_pct"]:
        reasons.append(f"{m['over_pct']:.4f} % of texels over {profile['target_tolerance']} relative (limit {profile['max_target_over_pct']} %)")
    changes = sum(m["kind_changes"].values())
    if profile["max_kind_changes"] is not None and changes > profile["max_kind_changes"]:
        reasons.append(f"{changes} values changed kind ({', '.join(f'{k} {n}' for k, n in m['kind_changes'].items())})")
    return reasons


def compare_targets(dir_a, dir_b, sid, ea, eb, profile):
    """None when neither capture dumped the shot; else (name, metrics or None, reasons) rows. A shot whose manifest says
    it was dumped must have a valid index and files on that side; dumps on one side only fail."""
    rows = []
    sides = []
    for side, folder, entry in (("a", dir_a, ea), ("b", dir_b, eb)):
        try:
            targets = read_targets(folder / "targets", sid)
        except TargetError as error:
            rows.append(("index", None, [f"{side}: {error}"]))
            targets = {}
        expected = bool((entry or {}).get("dumped")) or bool((entry or {}).get("targets"))
        if targets is None and expected:
            rows.append(("index", None, [f"{side}: the manifest says the shot was dumped, {sid}.targets.txt is missing"]))
        recorded = (entry or {}).get("targets")
        index = folder / "targets" / f"{sid}.targets.txt"
        if isinstance(recorded, dict) and recorded.get("index") and index.exists() and sha256(index) != recorded["index"]["sha256"]:
            rows.append(("index", None, [f"{side}: {index.name} differs from its manifest record (changed after the capture)"]))
        # the first manifest format kept a plain list of file names, without hashes
        recorded = recorded.get("files", {}) if isinstance(recorded, dict) else {}
        for name, record in recorded.items():
            if targets and name in targets and targets[name][0].exists() and sha256(targets[name][0]) != record["sha256"]:
                rows.append((name, None, [f"{side}: {targets[name][0].name} differs from its manifest record (changed after the capture)"]))
            elif targets is not None and targets and name not in targets:
                rows.append((name, None, [f"{side}: listed in the manifest, not in the index"]))
        sides.append(targets)
    ta, tb = sides
    if ta is None and tb is None and not rows:
        return None
    if (ta is None) != (tb is None):
        rows.append(("index", None, [f"dumps only in {'a' if tb is None else 'b'}"]))
        return rows
    if any(row[0] == "index" for row in rows):
        return rows
    for name in sorted(set(ta or {}) | set(tb or {})):
        if name not in (ta or {}) or name not in (tb or {}):
            rows.append((name, None, [f"only in {'b' if name in (tb or {}) else 'a'}"]))
            continue
        if ta[name][1:4] != tb[name][1:4]:
            rows.append((name, None, [f"size and format {ta[name][1:4]} against {tb[name][1:4]}"]))
            continue
        try:
            a, raw_a = load_target(ta[name])
            b, raw_b = load_target(tb[name])
        except (OSError, TargetError) as error:
            rows.append((name, None, [str(error)]))
            continue
        m = target_metrics(a, b, profile["target_tolerance"])
        rows.append((name, m, judge_target(m, profile, raw_a == raw_b)))
    return rows


def provenance_notes(ma, mb, run_ids):
    if not ma or not mb:
        return ["a manifest is missing: nothing is known about the builds"]
    notes = []
    for label, manifest in (("a", ma), ("b", mb)):
        builds = {}
        for rid in run_ids:
            p = run_provenance(manifest, rid)
            if p:
                builds.setdefault(p.get("exe", {}).get("sha256", "?")[:12], []).append(rid)
        if len(builds) > 1:
            notes.append(f"{label} ({manifest.get('label')}) mixes executables: " + "; ".join(f"{k} for {', '.join(v)}" for k, v in builds.items()))
    same = True
    for rid in run_ids:
        pa, pb = run_provenance(ma, rid), run_provenance(mb, rid)
        if not pa or not pb:
            notes.append(f"run {rid}: not in {'a' if not pa else 'b'}")
            same = False
            continue
        exe_a, exe_b = pa.get("exe", {}).get("sha256", "?"), pb.get("exe", {}).get("sha256", "?")
        if exe_a != exe_b:
            same = False
            notes.append(f"run {rid}: executables {exe_a[:12]} and {exe_b[:12]}")
        if pa.get("run") and pb.get("run") and pa["run"] != pb["run"]:
            notes.append(f"run {rid}: the run's definition differs (route, options or defaults): its shots may not show the same thing")
        for key in ("macos", "machine", "gpu", "brew", "vulkaninfo"):
            va, vb = pa.get("platform", {}).get(key), pb.get("platform", {}).get(key)
            if va != vb:
                notes.append(f"run {rid}: platform {key} {va} against {vb}")
    if same:
        notes.append("same executable in every compared run")
    return notes


def compare_shot(dir_a, dir_b, sid, ea, eb, profile, with_targets):
    """One shot: (status, metrics or None, reasons, target rows, b's pixels and the difference map for a diff image)."""
    pa, pb = dir_a / f"{sid}.png", dir_b / f"{sid}.png"
    absent = [side for side, p in (("a", pa), ("b", pb)) if not p.exists()]
    if absent:
        return "MISSING", None, [f"no image in {' and '.join(absent)}"], None, None
    reasons = []
    images = []
    for side, path, entry in (("a", pa, ea), ("b", pb, eb)):
        if entry and entry.get("sha256") and sha256(path) != entry["sha256"]:
            reasons.append(f"{side}: {path.name} differs from its manifest record (changed after the capture)")
        try:
            images.append(load_shot(path))
        except ShotError as error:
            reasons.append(f"{side}: {error}")
    if len(images) < 2:
        return "FAIL", None, reasons, None, None
    a, b = images
    if a.shape != b.shape:
        return "FAIL", None, reasons + [f"size {a.shape[1]}x{a.shape[0]} against {b.shape[1]}x{b.shape[0]}"], None, None
    m, pixel = image_metrics(a, b, profile["tolerance"])
    reasons += judge(m, profile)
    targets = compare_targets(dir_a, dir_b, sid, ea, eb, profile) if with_targets else None
    status = "FAIL" if reasons or any(row[2] for row in targets or []) else "ok"
    return status, m, reasons, targets, (b, pixel)


def compare(args):
    profile = PROFILES[args.profile]
    dir_a, dir_b = label_path(args.a), label_path(args.b)
    for folder in (dir_a, dir_b):
        if not folder.is_dir():
            sys.exit(f"no capture at {folder}")

    def manifest(folder):
        path = folder / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    ma, mb = manifest(dir_a), manifest(dir_b)
    run_of = {s["id"]: r["id"] for r in load_shots()["runs"] for s in r["shots"]}
    ids = list(run_of)
    if args.shots:
        wanted = args.shots.split(",")
        if unknown := set(wanted) - set(ids):
            sys.exit(f"unknown shot(s): {', '.join(sorted(unknown))}")
        ids = [i for i in ids if i in wanted]
    lines = [f"golden compare {args.a} -> {args.b}, profile {args.profile}, {utc_now()}",
             f"limits: pixels more than {profile['tolerance']} steps off <= {profile['max_over_pct']} %, worst {BLOCK}x{BLOCK} block <= "
             f"{profile['max_block']} steps on average (PSNR for information); shots must be opaque"]
    if args.targets:
        kinds = "any" if profile["max_kind_changes"] is None else profile["max_kind_changes"]
        lines.append(f"targets: texels over {profile['target_tolerance']} relative <= {profile['max_target_over_pct']} %, "
                     f"values changing kind (finite, NaN, +Inf, -Inf) <= {kinds} and counted as over")
    lines += [f"note: {n}" for n in provenance_notes(ma, mb, sorted({run_of[i] for i in ids}))]
    lines += ["", f"{'shot':26} {'result':7} {'max':>4} {'changed%':>9} {'over%':>8} {'block':>6} {'PSNR':>7}"]
    counts = {"ok": 0, "FAIL": 0, "MISSING": 0}
    diff_dir = dir_b / f"diff-{args.a}"

    def shot_entry(m, sid):
        return m.get("runs", {}).get(run_of[sid], {}).get("shots", {}).get(sid)

    for sid in ids:
        status, m, reasons, targets, pixels = compare_shot(dir_a, dir_b, sid, shot_entry(ma, sid), shot_entry(mb, sid), profile, args.targets)
        counts[status] += 1
        if m is None:
            lines.append(f"{sid:26} {status:7}")
        else:
            psnr = "inf" if math.isinf(m["psnr"]) else f"{m['psnr']:.2f}"
            lines.append(f"{sid:26} {status:7} {m['max_abs']:4d} {m['changed_pct']:9.4f} {m['over_pct']:8.4f} {m['worst_block']:6.2f} {psnr:>7}")
        lines += [f"    {reason}" for reason in reasons]
        for name, tm, treasons in targets or []:
            if tm is None:
                lines.append(f"    target {name}: FAIL {'; '.join(treasons)}")
            elif treasons or args.verbose:
                kinds = ", ".join(f"{k} {n}" for k, n in tm["kind_changes"].items()) or "none"
                lines.append(f"    target {name}: {'FAIL' if treasons else 'ok'} max {tm['max_abs']:.4g}, {tm['over_pct']:.4f} % over, "
                             f"non-finite {tm['nonfinite'][0]}/{tm['nonfinite'][1]}, kind changes {kinds}"
                             f"{'; ' + '; '.join(treasons) if treasons else ''}")
        if args.diff and m is not None and m["changed_pct"] > 0:
            diff_image(*pixels, profile["tolerance"], diff_dir / f"{sid}.png")
    lines += ["", f"{counts['ok']} ok, {counts['FAIL']} failed, {counts['MISSING']} missing of {len(ids)}"]
    if args.diff:
        lines.append(f"diff images: {diff_dir}")
    report = "\n".join(lines) + "\n"
    report_path = guarded(dir_b / f"compare-{args.a}-{args.profile}.txt")
    report_path.write_text(report, encoding="utf-8")
    print(report + f"report: {report_path}")
    return 1 if counts["FAIL"] or counts["MISSING"] else 0


# --- list and selftest -------------------------------------------------------------------------------------------------


def list_shots(args):
    data = load_shots()
    by_effect = {e: [] for e in EFFECTS}
    for run in data["runs"]:
        options = run_options(run, data)
        kind = "viewer" if "viewer" in run else "game" + (f" from {run['start_floor']}" if run.get("start_floor") else "")
        extras = (["render-all"] if options.get("render_all") else []) + (["target dumps"] if run.get("dump") else [])
        extras += [f"{k}={v}" for k, v in run.get("env", {}).items()]
        print(f"{run['id']} ({', '.join([kind] + extras)})")
        for shot in run["shots"]:
            print(f"    {shot['id']:26} {', '.join(shot['effects'])}")
            for effect in shot["effects"]:
                by_effect[effect].append(shot["id"])
    print()
    for effect, ids in by_effect.items():
        print(f"{effect:15} {', '.join(ids)}")
    return 0


def selftest(args):
    rng = np.random.default_rng(1)
    base = rng.integers(0, 256, size=(144, 256, 3), dtype=np.uint8)
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    def fails(a, b, profile):
        return bool(judge(image_metrics(a, b, PROFILES[profile]["tolerance"])[0], PROFILES[profile]))

    m = image_metrics(base, base.copy(), 0)[0]
    check("identical images: max 0, PSNR inf, pass every profile",
          m["max_abs"] == 0 and math.isinf(m["psnr"]) and not any(fails(base, base.copy(), p) for p in PROFILES))
    one = base.copy()
    one[70, 100, 1] ^= 1
    check("one channel of one pixel one step off fails exact", fails(base, one, "exact"))
    check("... and passes refactor and backend", not fails(base, one, "refactor") and not fails(base, one, "backend"))
    noise1 = np.clip(base.astype(np.int16) + rng.integers(-1, 2, size=base.shape), 0, 255).astype(np.uint8)
    check("+-1 step everywhere passes refactor", not fails(base, noise1, "refactor"))
    noise3 = np.clip(base.astype(np.int16) + rng.integers(-3, 4, size=base.shape), 0, 255).astype(np.uint8)
    check("+-3 steps everywhere fails refactor", fails(base, noise3, "refactor"))
    check("+-3 steps everywhere passes backend", not fails(base, noise3, "backend"))
    edges = base.copy()
    edges[rng.integers(0, 144, 10), rng.integers(0, 256, 10)] ^= 0x80
    check("10 flipped edge pixels (0.03 %) pass refactor", not fails(base, edges, "refactor"))
    hole = base.copy()
    hole[40:72, 120:152] = 0
    m = image_metrics(base, hole, 8)[0]
    check("a black 32x32 patch fails backend and the block metric finds it",
          fails(base, hole, "backend") and m["worst_block"] > 50 and 112 <= m["worst_block_at"][0] <= 144 and 32 <= m["worst_block_at"][1] <= 64)
    flat = np.full((720, 1280, 3), 128, dtype=np.uint8)
    lamp = flat.copy()
    lamp[300:316, 600:616] = 160
    m = image_metrics(flat, lamp, 8)[0]
    check("a lost 16x16 detail in 720p passes the backend pixel limit, with a PSNR over 50 dB ...",
          m["over_pct"] <= PROFILES["backend"]["max_over_pct"] and m["psnr"] > 50)
    check("... and fails on the block limit", fails(flat, lamp, "backend"))
    check("a one pixel shift of a noisy image fails backend", fails(base, np.roll(base, 1, axis=1), "backend"))
    odd = rng.integers(0, 256, size=(37, 53, 3), dtype=np.uint8)
    odd2 = odd.copy()
    odd2[36, 52] = 255 - odd2[36, 52]
    m = image_metrics(odd, odd2, 0)[0]
    check("a size that is not a multiple of the block: the last partial block (5x5) is averaged over its own area",
          m["worst_block_at"] == [48, 32] and abs(m["worst_block"] - float(np.abs(odd[36, 52].astype(int) - odd2[36, 52]).mean()) / 25) < 1e-9)
    shift = np.clip(base.astype(np.int16) + 2, 0, 255).astype(np.uint8)
    check("the whole image 2 steps brighter fails refactor on the block limit only",
          fails(base, shift, "refactor") and image_metrics(base, shift, 2)[0]["over_pct"] == 0)
    a = rng.standard_normal((8, 8, 4)).astype(np.float32)
    check("a relative change of 1e-5 is under the refactor target tolerance", target_metrics(a, a * (1 + 1e-5), 1e-3)["over_pct"] == 0)
    b = a.copy()
    b[0, 0, 0] = np.nan
    t = target_metrics(a, b, 1e-3)
    check("finite to NaN is a kind change and an over texel", t["kind_changes"] == {"finite->NaN": 1} and t["over_pct"] > 0)
    inf = np.full((8, 8, 4), np.inf, dtype=np.float32)
    t = target_metrics(inf, np.full_like(inf, np.nan), 1e-2)
    check("a whole target going from +Inf to NaN: 100 % over, 256 kind changes, fails every profile",
          t["over_pct"] == 100.0 and t["kind_changes"] == {"+Inf->NaN": 256} and all(judge_target(t, p, False) for p in PROFILES.values()))
    t = target_metrics(inf, -inf, 1e-2)
    check("+Inf to -Inf is a kind change (sign)", t["kind_changes"] == {"+Inf->-Inf": 256})
    nans = a.copy()
    nans[1, 1, :2] = np.nan
    nans[2, 2, 3] = -np.inf
    t = target_metrics(nans, nans.copy(), 0.0)
    check("the same non-finite values on both sides are equal (as bath_baby.normal's 14)",
          t["over_pct"] == 0 and not t["kind_changes"] and t["nonfinite"] == [3, 3] and not judge_target(t, PROFILES["refactor"], False))
    one_nan = nans.copy()
    one_nan[5, 5, 0] = np.nan
    t = target_metrics(nans, one_nan, 1e-2)
    check("one new NaN fails refactor on its own and counts as an over texel in backend",
          any("changed kind" in r for r in judge_target(t, PROFILES["refactor"], False)) and t["over_pct"] == 100.0 / 64
          and not any("changed kind" in r for r in judge_target(t, PROFILES["backend"], False)))
    global GOLDEN
    saved_golden = GOLDEN
    with tempfile.TemporaryDirectory() as tmp:
        GOLDEN = Path(tmp) / "golden"
        image = rng.integers(0, 256, size=(32, 48, 3), dtype=np.uint8)
        depth = rng.random((32, 48)).astype(np.float32)
        hdr = rng.random((32, 48, 4)).astype(np.float16)

        def label(name, img=image, dumps=True, index="depth 48 32 126\nhdr 48 32 97\n", files=None, alpha=None):
            folder = GOLDEN / name
            (folder / "targets").mkdir(parents=True, exist_ok=True)
            rgba = np.dstack([img, np.full(img.shape[:2], 255, np.uint8) if alpha is None else alpha])
            Image.fromarray(rgba, "RGBA").save(folder / "s.png")
            entry = {"file": "s.png", "sha256": sha256(folder / "s.png"), "dumped": dumps}
            if dumps:
                if index is not None:
                    (folder / "targets" / "s.targets.txt").write_text(index, encoding="utf-8")
                for fname, data in (files if files is not None else {"s.depth.bin": depth.tobytes(), "s.hdr.bin": hdr.tobytes()}).items():
                    (folder / "targets" / fname).write_bytes(data)
            return folder, entry

        def result(a, b, profile="exact", ea=None, eb=None):
            return compare_shot(a[0], b[0], "s", a[1] if ea is None else ea, b[1] if eb is None else eb, PROFILES[profile], True)

        def fails_with(r, text):
            return r[0] == "FAIL" and any(text in reason for reason in r[2] + [x for row in r[3] or [] for x in row[2]])

        base_label = label("a")
        check("two identical labels pass exact with targets", result(base_label, label("b"))[0] == "ok")
        step = image.copy()
        step[3, 4, 0] ^= 1
        check("one step on one pixel fails exact", fails_with(result(base_label, label("c", step)), "pixels more than 0"))
        block = image.copy()
        block[0:16, 16:32] = 255 - block[0:16, 16:32]
        check("a changed 16x16 block fails backend on the block limit", fails_with(result(base_label, label("d", block), "backend"), "block at x,y [16, 0]"))
        alpha = np.full(image.shape[:2], 255, np.uint8)
        alpha[5, 5] = 254
        check("alpha only (one pixel not opaque) fails every profile",
              all(fails_with(result(base_label, label("e", alpha=alpha), p), "not opaque") for p in PROFILES))
        corrupt = label("f")
        (corrupt[0] / "s.png").write_bytes(b"\x89PNG\r\n\x1a\nbroken")
        check("a corrupt shot fails instead of crashing", fails_with(result(base_label, corrupt, eb={}), "not a readable image"))
        check("a shot changed after its capture fails on the manifest hash", fails_with(result(base_label, label("g", step), eb=base_label[1]), "manifest record"))
        extra = label("h", files={"s.depth.bin": depth.tobytes() + b"\0\0\0\0", "s.hdr.bin": hdr.tobytes()})
        check("extra target bytes fail", fails_with(result(base_label, extra, "backend"), "bytes, 6144 expected"))
        missing_file = label("i", files={"s.hdr.bin": hdr.tobytes()})
        check("a target file missing from its index fails", fails_with(result(base_label, missing_file, "backend"), "is missing"))
        no_index = label("j", index=None)
        check("a dumped shot without its index fails", fails_with(result(base_label, no_index, "backend"), "targets.txt is missing"))
        both_gone = (label("k", index=None), label("l", index=None))
        check("an index missing on both sides fails when the manifests say dumped", fails_with(result(*both_gone, "backend"), "targets.txt is missing"))
        check("dumps on one side only fail", fails_with(result(base_label, label("m", dumps=False), "backend"), "dumps only in a"))
        malformed = label("n", index="depth 48 thirty-two 126\n")
        check("a malformed index line fails", fails_with(result(base_label, malformed, "backend"), "is not 'name width height format'"))
        unknown = label("o", index="depth 48 32 126\nhdr 48 32 64\n")
        check("an unknown VkFormat fails instead of reading as RGBA8", fails_with(result(base_label, unknown, "backend"), "VkFormat 64"))
        flipped = depth.copy()
        flipped[:] = np.inf
        ia = label("p", files={"s.depth.bin": flipped.tobytes(), "s.hdr.bin": hdr.tobytes()})
        nan_depth = np.full_like(depth, np.nan)
        ib = label("q", files={"s.depth.bin": nan_depth.tobytes(), "s.hdr.bin": hdr.tobytes()})
        check("a whole target from +Inf to NaN fails backend", fails_with(result(ia, ib, "backend"), "texels over"))
        zeroed = label("r", files={"s.depth.bin": bytes(depth.nbytes), "s.hdr.bin": hdr.tobytes()})
        check("a zeroed depth dump fails exact with targets", fails_with(result(base_label, zeroed), "bytes differ"))

        def recorded(name, index=None):
            folder, entry = label(name)
            entry["targets"] = record_targets(folder / "targets", "s")
            if index is not None:
                (folder / "targets" / "s.targets.txt").write_text(index, encoding="utf-8")
            return folder, entry

        reshaped = "depth 32 48 126\nhdr 32 48 97\n"
        check("an index rewritten after the capture (48x32 to 32x48 on both sides, same bytes) fails on the manifest hash",
              result(recorded("s"), recorded("t"))[0] == "ok"
              and fails_with(result(recorded("u", reshaped), recorded("v", reshaped)), "targets.txt differs from its manifest record"))

        burst = GOLDEN / "burst"
        burst.mkdir()
        for name in ("m-00.png", "m-00.targets.txt", "m-00.depth.bin", "m-00.mirror.raw"):
            (burst / name).write_bytes(b"x")
        problems = normalize_burst(burst, "m")
        check("a burst shot's image, index and dumps are all renamed together",
              not problems and sorted(p.name for p in burst.iterdir()) == ["m.depth.bin", "m.mirror.raw", "m.png", "m.targets.txt"])
        (burst / "m-01.png").write_bytes(b"x")
        check("a second burst frame is a problem", any("left over" in p for p in normalize_burst(burst, "m")))

        outside = Path(tmp) / "elsewhere"
        outside.mkdir()
        (GOLDEN / "linked").symlink_to(outside)
        (GOLDEN / "a" / "work").symlink_to(REPO)

        def refused(call):
            try:
                call()
            except SystemExit:
                return True
            return False

        check("a label that is a symlink out of the golden folder is refused", refused(lambda: label_path("linked")))
        check("an artefact folder linked into the repository is refused", refused(lambda: guarded(GOLDEN / "a" / "work" / "hallway")))
        check("a plain artefact path is accepted", not refused(lambda: guarded(GOLDEN / "a" / "targets" / "x.bin")))

        def prov(exe, run="r1"):
            return {"exe": {"sha256": exe * 64}, "run": run, "platform": {"macos": "27.2"}}

        mixed = {"label": "x", "runs": {"r1": {"provenance": prov("1")}, "r2": {"provenance": prov("2")}}}
        clean = {"label": "y", "runs": {"r1": {"provenance": prov("1")}, "r2": {"provenance": prov("1")}}}
        notes = provenance_notes(clean, mixed, ["r1", "r2"])
        check("a label retaken in part with another build is reported per run, never as the same executable",
              any("mixes executables" in n for n in notes) and any("run r2: executables" in n for n in notes)
              and not any(n.startswith("same executable") for n in notes))
        check("matching runs report the same executable", any(n.startswith("same executable") for n in provenance_notes(clean, clean, ["r1", "r2"])))
        changed = {"label": "z", "runs": {"r1": {"provenance": prov("1", run="other")}, "r2": {"provenance": prov("1")}}}
        check("a changed run definition is reported", any("definition differs" in n for n in provenance_notes(clean, changed, ["r1"])))
        legacy = {"exe": {"sha256": "9" * 64}, "runs": {"r1": {"shots": {}}}}
        check("a first-format manifest still gives each run its capture-wide provenance",
              run_provenance(legacy, "r1")["exe"]["sha256"] == "9" * 64)
    GOLDEN = saved_golden
    check("the shot list follows its rules", not check_shots(json.loads(SHOTS.read_text(encoding="utf-8"))))
    broken = {"runs": [{"id": "r", "route": ["sshot {shot:a}", "sshot {shot:a}", "sburst 2 {burst:b}"],
                        "shots": [{"id": "a", "effects": ["vfx"], "look": "x", "ps4": "y"}, {"id": "b", "effects": [], "look": "x", "ps4": "y"}]}]}
    problems = check_shots(broken)
    check("the rules catch a shot taken twice, a burst of more than one frame and missing effects",
          any("taken 2 times" in p for p in problems) and any("needs 'sburst 1'" in p for p in problems)
          and any("no shot covers shadows" in p for p in problems))
    for name, ok in results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    failed = sum(1 for _, ok in results if not ok)
    print(f"{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("capture", help="run the shot list and write a reference set")
    p.add_argument("label")
    p.add_argument("--exe", default=str(DEFAULT_EXE), help=f"the game (default {DEFAULT_EXE})")
    p.add_argument("--game", default=str(DEFAULT_GAME), help="the CUSA01127 folder (default $PT_GAME_DIR or ~/personalDEV/pt-game/CUSA01127)")
    p.add_argument("--runs", help="comma separated run ids (default all); retakes them in an existing capture")
    p.add_argument("--dumps", choices=("flagged", "all", "none"), default="flagged",
                   help="render target dumps: of the runs marked dump (default), of every game run, or none")
    p.add_argument("--force", action="store_true", help="replace an existing capture of the label")
    p.add_argument("--keep-going", action="store_true", help="go on with the next runs after a failed one")
    p.set_defaults(func=capture)
    p = sub.add_parser("compare", help="compare capture b against capture a")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--profile", choices=tuple(PROFILES), default="exact")
    p.add_argument("--diff", action="store_true", help="write diff images of the changed shots to <b>/diff-<a>/")
    p.add_argument("--targets", action="store_true", help="compare the render target dumps too; they count toward the result")
    p.add_argument("--shots", help="comma separated shot ids (default all)")
    p.add_argument("--verbose", action="store_true", help="list every compared target, not only the failed ones")
    p.set_defaults(func=compare)
    sub.add_parser("list", help="runs, shots and effect coverage").set_defaults(func=list_shots)
    sub.add_parser("selftest", help="check the compare metrics on synthetic images").set_defaults(func=selftest)
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
