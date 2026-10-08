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

# target_tolerance: the render target dumps, relative to max(1, |value|) per channel (they hold floats and depth)
PROFILES = {
    "exact": {"tolerance": 0, "max_over_pct": 0.0, "max_block": 0.0, "target_tolerance": 0.0, "max_target_over_pct": 0.0},
    "refactor": {"tolerance": 2, "max_over_pct": 0.05, "max_block": 1.0, "target_tolerance": 1e-3, "max_target_over_pct": 0.05},
    "backend": {"tolerance": 8, "max_over_pct": 0.5, "max_block": 4.0, "target_tolerance": 1e-2, "max_target_over_pct": 0.5},
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


def label_path(label):
    if not label or "/" in label or label.startswith("."):
        sys.exit(f"label {label!r}: a plain folder name under {GOLDEN}")
    root = GOLDEN.resolve()
    if root == REPO or REPO in root.parents:
        sys.exit(f"{root} is inside the repository; reference images are game content and stay outside it (PT_GOLDEN_DIR)")
    return root / label


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


def capture_run(run, data, exe, game, label_dir, dumps):
    work = label_dir / "work" / run["id"]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    shots = run["shots"]
    for shot in shots:
        sid = shot["id"]
        for stale in [*label_dir.glob(f"{sid}.*"), *label_dir.glob(f"{sid}-*.png"), *(label_dir / "targets").glob(f"{sid}.*")]:
            stale.unlink()
    if "viewer" not in run:
        def place(m):
            path = (label_dir / m.group(2)).as_posix()
            return path if m.group(1) == "burst" else path + ".png"

        (work / "route.txt").write_text("\n".join(SHOT_TOKEN.sub(place, line) for line in route_lines(run, data)) + "\n", encoding="utf-8")
        settings = json.loads(json.dumps(data.get("defaults", {}).get("settings", {})))
        for section, values in run.get("settings", {}).items():
            settings.setdefault(section, {}).update(values)
        (work / "pt.ini").write_text(settings_ini(settings), encoding="utf-8")
    cmd = build_command(run, data, exe, game, work, label_dir)
    # PT_HEADLESS_ONLY makes the game refuse any run that would open a window
    env = {**os.environ, **data.get("defaults", {}).get("env", {}), **run.get("env", {}), "PT_HEADLESS_ONLY": "1"}
    env.pop("PT_TARGET_DUMP", None)
    dumped = "viewer" not in run and (dumps == "all" or (dumps == "flagged" and bool(run.get("dump"))))
    if dumped:
        env["PT_TARGET_DUMP"] = "1"
    timeout = run.get("timeout", run_options(run, data).get("timeout", 1800))
    print(f"run {run['id']}: {len(shots)} shot(s){', target dumps' if dumped else ''} ...", flush=True)
    started = time.time()
    with open(work / "stdout.txt", "wb") as out:
        try:
            code = subprocess.run(cmd, cwd=work, stdout=out, stderr=subprocess.STDOUT, env=env, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = f"timeout after {timeout} s"
    elapsed = time.time() - started
    log_path = work / "pt.log"
    findings = log_findings(log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "")
    result = {"command": cmd, "env": {k: v for k, v in env.items() if k.startswith(("PT_", "VK_", "MVK_"))}, "exit": code,
              "seconds": round(elapsed, 1), "log": log_path.relative_to(label_dir).as_posix(), "findings": findings, "shots": {}}
    problems = []
    if code != 0:
        problems.append(f"exit code {code}")
    for shot in shots:
        sid = shot["id"]
        image = label_dir / f"{sid}.png"
        if not image.exists() and (label_dir / f"{sid}-00.png").exists():
            (label_dir / f"{sid}-00.png").rename(image)
        if not image.exists():
            problems.append(f"shot {sid} not written")
            continue
        with Image.open(image) as opened:
            entry = {"file": image.name, "sha256": sha256(image), "size": list(opened.size)}
        moved = []
        for part in sorted(label_dir.glob(f"{sid}.*")):
            if part != image:
                (label_dir / "targets").mkdir(exist_ok=True)
                part.rename(label_dir / "targets" / part.name)
                moved.append(part.name)
        if moved:
            entry["targets"] = moved
        result["shots"][sid] = entry
    if findings["skipped"]:
        problems.append(f"{len(findings['skipped'])} route step(s) dropped, stuck or given up (findings.skipped)")
    if findings["expect_failed"]:
        problems.append(f"{len(findings['expect_failed'])} route expectation(s) failed (findings.expect_failed)")
    result["problems"] = problems
    print(f"run {run['id']}: {'ok' if not problems else 'FAILED: ' + '; '.join(problems)} ({elapsed:.0f} s, log {log_path})", flush=True)
    return result


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
    manifest_path = label_dir / "manifest.json"
    if manifest_path.exists() and not args.force and not args.runs:
        sys.exit(f"{label_dir} already holds a capture: --force replaces it, --runs retakes some runs")
    if args.force and not args.runs and label_dir.exists():
        shutil.rmtree(label_dir)
    label_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    stat = exe.stat()
    manifest.update({
        "label": args.label,
        "created": manifest.get("created", utc_now()),
        "updated": utc_now(),
        "exe": {"path": str(exe), "sha256": sha256(exe), "bytes": stat.st_size,
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "git": git_state(exe)},
        "game": {"path": str(game), "files": {p.name: p.stat().st_size for p in sorted(game.iterdir()) if p.is_file()}},
        "tools": {"golden.py": sha256(Path(__file__)), "golden_shots.json": sha256(SHOTS), "repo": git_state(REPO)},
        "defaults": data.get("defaults", {}),
        "dumps": args.dumps,
        "platform": platform_info(),
    })
    manifest.setdefault("runs", {})
    failed = False
    for run in runs:
        result = capture_run(run, data, exe, game, label_dir, args.dumps)
        manifest["runs"][run["id"]] = result
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if result["problems"]:
            failed = True
            if not args.keep_going:
                print("stopped at the first failed run (--keep-going runs the rest)")
                break
    shots = sum(len(r["shots"]) for r in manifest["runs"].values())
    print(f"{label_dir}: {shots} shot(s), manifest {manifest_path}")
    return 1 if failed else 0


# --- compare -----------------------------------------------------------------------------------------------------------


def load_rgb(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(path)


def read_targets(folder, sid):
    """The targets of a shot from its <shot>.targets.txt (name width height VkFormat per line)."""
    index = folder / f"{sid}.targets.txt"
    if not index.exists():
        return {}
    targets = {}
    for line in index.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        name, width, height, fmt = parts[0], int(parts[1]), int(parts[2]), int(parts[3])
        if name == "mirror_square":
            # the mirror capture's square, written as 8-bit RGBA with gamma 2.2 to <shot>.mirror.raw
            targets[name] = (folder / f"{sid}.mirror.raw", width, height, fmt, (np.uint8, 4))
        else:
            targets[name] = (folder / f"{sid}.{name}.bin", width, height, fmt, DUMP_FORMATS.get(fmt, (np.uint8, 4)))
    return targets


def load_target(entry):
    path, width, height, _, (dtype, channels) = entry
    raw = path.read_bytes()
    values = np.frombuffer(raw, dtype=dtype)
    if values.size < width * height * channels:
        raise ValueError(f"{path.name}: {values.size} values, {width * height * channels} expected")
    values = values[:width * height * channels].reshape(height, width, channels)
    return (values.astype(np.float32) / 255.0 if dtype == np.uint8 else values.astype(np.float32)), raw


def target_metrics(a, b, tolerance):
    """Float targets: a texel is over when a channel differs by more than tolerance relative to max(1, |a|), or when
    only one of the two is finite."""
    finite_a, finite_b = np.isfinite(a), np.isfinite(b)
    both = finite_a & finite_b
    diff = np.abs(np.where(both, a, 0.0) - np.where(both, b, 0.0))
    over = (diff > tolerance * np.maximum(1.0, np.abs(np.where(both, a, 0.0)))) | (finite_a != finite_b)
    texels = over.any(axis=2)
    return {"max_abs": float(diff.max()), "mean_abs": float(diff.mean()), "over_pct": 100.0 * np.count_nonzero(texels) / texels.size,
            "nonfinite_mismatch": int(np.count_nonzero(finite_a != finite_b))}


def compare_targets(dir_a, dir_b, sid, profile):
    ta, tb = read_targets(dir_a / "targets", sid), read_targets(dir_b / "targets", sid)
    if not ta and not tb:
        return None
    rows = []
    for name in sorted(set(ta) | set(tb)):
        if name not in ta or name not in tb:
            rows.append((name, None, [f"only in {'b' if name in tb else 'a'}"]))
            continue
        if ta[name][1:4] != tb[name][1:4]:
            rows.append((name, None, [f"size and format {ta[name][1:4]} against {tb[name][1:4]}"]))
            continue
        try:
            a, raw_a = load_target(ta[name])
            b, raw_b = load_target(tb[name])
        except (OSError, ValueError) as error:
            rows.append((name, None, [str(error)]))
            continue
        if raw_a == raw_b:
            rows.append((name, {"max_abs": 0.0, "mean_abs": 0.0, "over_pct": 0.0, "nonfinite_mismatch": 0}, []))
            continue
        m = target_metrics(a, b, profile["target_tolerance"])
        reasons = ["bytes differ"] if profile["target_tolerance"] == 0.0 else []
        if m["over_pct"] > profile["max_target_over_pct"]:
            reasons.append(f"{m['over_pct']:.4f} % of texels over {profile['target_tolerance']} relative (limit {profile['max_target_over_pct']} %)")
        rows.append((name, m, reasons))
    return rows


def manifest_notes(ma, mb):
    if not ma or not mb:
        return ["a manifest is missing"]
    notes = []
    if ma.get("tools", {}).get("golden_shots.json") != mb.get("tools", {}).get("golden_shots.json"):
        notes.append("the shot lists differ (golden_shots.json): a shot id may not show the same thing in both")
    if ma.get("defaults") != mb.get("defaults"):
        notes.append("the default options or settings differ")
    notes.append("same executable" if ma.get("exe", {}).get("sha256") == mb.get("exe", {}).get("sha256") else
                 f"executables {ma.get('exe', {}).get('sha256', '?')[:12]} and {mb.get('exe', {}).get('sha256', '?')[:12]}")
    for key in ("macos", "machine", "gpu", "brew", "vulkaninfo"):
        if ma.get("platform", {}).get(key) != mb.get("platform", {}).get(key):
            notes.append(f"platform {key}: {ma.get('platform', {}).get(key)} against {mb.get('platform', {}).get(key)}")
    return notes


def compare(args):
    profile = PROFILES[args.profile]
    dir_a, dir_b = label_path(args.a), label_path(args.b)
    for folder in (dir_a, dir_b):
        if not folder.is_dir():
            sys.exit(f"no capture at {folder}")

    def manifest(folder):
        path = folder / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    ids = [s["id"] for r in load_shots()["runs"] for s in r["shots"]]
    if args.shots:
        wanted = args.shots.split(",")
        if unknown := set(wanted) - set(ids):
            sys.exit(f"unknown shot(s): {', '.join(sorted(unknown))}")
        ids = [i for i in ids if i in wanted]
    lines = [f"golden compare {args.a} -> {args.b}, profile {args.profile}, {utc_now()}",
             f"limits: pixels more than {profile['tolerance']} steps off <= {profile['max_over_pct']} %, worst {BLOCK}x{BLOCK} block <= "
             f"{profile['max_block']} steps on average (PSNR for information)"]
    lines += [f"note: {n}" for n in manifest_notes(manifest(dir_a), manifest(dir_b))]
    lines += ["", f"{'shot':26} {'result':7} {'max':>4} {'changed%':>9} {'over%':>8} {'block':>6} {'PSNR':>7}"]
    failed = missing = 0
    diff_dir = dir_b / f"diff-{args.a}"
    for sid in ids:
        pa, pb = dir_a / f"{sid}.png", dir_b / f"{sid}.png"
        if not pa.exists() or not pb.exists():
            missing += 1
            lines.append(f"{sid:26} MISSING in {' and '.join(label for label, p in ((args.a, pa), (args.b, pb)) if not p.exists())}")
            continue
        a, b = load_rgb(pa), load_rgb(pb)
        if a.shape != b.shape:
            failed += 1
            lines.append(f"{sid:26} FAIL    size {a.shape[1]}x{a.shape[0]} against {b.shape[1]}x{b.shape[0]}")
            continue
        m, pixel = image_metrics(a, b, profile["tolerance"])
        reasons = judge(m, profile)
        targets = compare_targets(dir_a, dir_b, sid, profile) if args.targets else None
        status = "FAIL" if reasons or any(row[2] for row in targets or []) else "ok"
        failed += status == "FAIL"
        psnr = "inf" if math.isinf(m["psnr"]) else f"{m['psnr']:.2f}"
        lines.append(f"{sid:26} {status:7} {m['max_abs']:4d} {m['changed_pct']:9.4f} {m['over_pct']:8.4f} {m['worst_block']:6.2f} {psnr:>7}")
        lines += [f"    {reason}" for reason in reasons]
        for name, tm, treasons in targets or []:
            if tm is None:
                lines.append(f"    target {name}: FAIL {'; '.join(treasons)}")
            elif treasons or args.verbose:
                lines.append(f"    target {name}: {'FAIL' if treasons else 'ok'} max {tm['max_abs']:.4g}, {tm['over_pct']:.4f} % over, "
                             f"{tm['nonfinite_mismatch']} non-finite mismatches{'; ' + '; '.join(treasons) if treasons else ''}")
        if args.diff and m["changed_pct"] > 0:
            diff_image(b, pixel, profile["tolerance"], diff_dir / f"{sid}.png")
    lines += ["", f"{len(ids) - failed - missing} ok, {failed} failed, {missing} missing of {len(ids)}"]
    if args.diff:
        lines.append(f"diff images: {diff_dir}")
    report = "\n".join(lines) + "\n"
    report_path = dir_b / f"compare-{args.a}-{args.profile}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(report + f"report: {report_path}")
    return 1 if failed or missing else 0


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
    b = a.copy()
    b[0, 0, 0] = np.nan
    t = target_metrics(a, b, 1e-3)
    check("a NaN in one target dump is a mismatch", t["nonfinite_mismatch"] == 1 and t["over_pct"] > 0)
    check("a relative change of 1e-5 is under the refactor target tolerance", target_metrics(a, a * (1 + 1e-5), 1e-3)["over_pct"] == 0)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        depth = rng.random((4, 6)).astype(np.float32)
        hdr = rng.random((4, 6, 4)).astype(np.float16)
        mirror = rng.integers(0, 256, size=(3, 3, 4), dtype=np.uint8)
        (folder / "s.depth.bin").write_bytes(depth.tobytes())
        (folder / "s.hdr.bin").write_bytes(hdr.tobytes())
        (folder / "s.mirror.raw").write_bytes(mirror.tobytes())
        (folder / "s.targets.txt").write_text("depth 6 4 126\nhdr 6 4 97\nmirror_square 3 3 0\n", encoding="utf-8")
        targets = read_targets(folder, "s")
        check("dumps decode: D32_SFLOAT, R16G16B16A16_SFLOAT and the mirror square",
              np.array_equal(load_target(targets["depth"])[0][:, :, 0], depth) and np.array_equal(load_target(targets["hdr"])[0], hdr.astype(np.float32))
              and np.array_equal(load_target(targets["mirror_square"])[1], mirror.tobytes()))
    check("the shot list follows its rules", not check_shots(json.loads(SHOTS.read_text(encoding="utf-8"))))
    broken = {"runs": [{"id": "r", "route": ["sshot {shot:a}", "sshot {shot:a}"], "shots": [{"id": "a", "effects": ["vfx"], "look": "x", "ps4": "y"}]}]}
    problems = check_shots(broken)
    check("the rules catch a shot taken twice and missing effects",
          any("taken 2 times" in p for p in problems) and any("no shot covers shadows" in p for p in problems))
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
