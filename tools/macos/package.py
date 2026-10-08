#!/usr/bin/env python3
"""Builds P.T.app and a .zip of it (docs/macos.md; docs/macos/PLAN.md Phase 2).

The app is the `macos` preset build of pt (RelWithDebInfo, the build the reference screenshots come from), with what
tools/package.py puts next to pt.exe on Windows, laid out the way a bundle needs it:

  Contents/MacOS/pt                     the game
  Contents/Frameworks/                  Homebrew's Vulkan loader and MoltenVK (D5), found through the rpath
  Contents/Resources/                   shaders, fonts, voice/, licenses/, the loop browser previews, the icon, and
                                        vulkan/icd.d/MoltenVK_icd.json, the driver the game points the loader at

The game finds its files in SDL_GetBasePath(), which is Contents/Resources in a bundle. Every load command is rewritten
to the system or the bundle, the result is checked, and the app is signed ad hoc (no hardened runtime: it would need a
Developer ID to load the bundled libraries, and notarisation is out of scope).
"""

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGING = REPO / "packaging" / "macos"
# voice/ as the recognizer loads it (formats/voice.md), as in tools/package.py; the ggml CPU variants are the Apple ones
VOICE_MODELS = ("ggml-base.en-q5_1.bin", "ggml-silero-v6.2.0.bin")
VOICE_LIBRARIES = ("libwhisper.dylib", "libggml.dylib", "libggml-base.dylib")
# the enhanced-texture runtime is not shipped on the Mac yet (P1.13), and neither are its notices
LEFT_OUT_LICENSES = ("enhanced-textures", "Real-ESRGAN-model.txt", "Real-ESRGAN-ncnn-vulkan.txt")
SYSTEM_LIBRARIES = ("/usr/lib/", "/System/Library/")
BUNDLE_RPATHS = ("@loader_path", "@executable_path")


def run(cmd, **kwargs):
    return subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def output(cmd):
    return run(cmd, capture_output=True, text=True).stdout


def version():
    try:
        rev = output(["git", "-C", REPO, "rev-parse", "--short", "HEAD"]).strip()
    except (OSError, subprocess.CalledProcessError):
        rev = "local"
    return f"{datetime.date.today():%Y%m%d}-{rev}"


def cache_value(build, name):
    match = re.search(rf"^{name}:[^=]*=(.*)$", (build / "CMakeCache.txt").read_text(), re.M)
    return match.group(1) if match else ""


def game_version(build):
    return re.search(r'#define PT_VERSION "([^"]+)"', (build / "generated" / "pt_version.h").read_text()).group(1)


def homebrew(formula):
    try:
        return Path(output(["brew", "--prefix", formula]).strip()).resolve()
    except (OSError, subprocess.CalledProcessError):
        sys.exit(f"Homebrew's {formula} not found; install the tools first (tools/macos/setup.sh)")


def is_macho(path):
    with open(path, "rb") as f:
        return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def load_commands(path):
    """The libraries a Mach-O file loads (without its own install name), its rpaths and the macOS it is built for."""
    libraries = [line.split(" (compatibility")[0].strip() for line in output(["otool", "-L", path]).splitlines()[1:]]
    install_name = output(["otool", "-D", path]).splitlines()[1:]
    if install_name and libraries and libraries[0] == install_name[0]:
        libraries = libraries[1:]
    text = output(["otool", "-l", path])
    rpaths = re.findall(r"cmd LC_RPATH\n\s+cmdsize \d+\n\s+path (.*) \(offset \d+\)", text)
    minimum = re.findall(r"cmd LC_BUILD_VERSION\n\s+cmdsize \d+\n\s+platform \d+\n\s+minos ([\d.]+)", text)
    return libraries, rpaths, minimum


def build_pt(build):
    # configured every time, so an older build folder takes the deployment target (CMakeLists.txt)
    run(["cmake", "--preset", "macos", "-B", build], cwd=REPO)
    run(["cmake", "--build", build, "--target", "pt"], cwd=REPO)


def copy_voice(source, target):
    variants = sorted(source.glob("libggml-cpu-*.dylib"))
    missing = [n for n in VOICE_MODELS + VOICE_LIBRARIES if not (source / n).is_file()] + ([] if variants else ["libggml-cpu-*.dylib"])
    if missing or not (source / "licenses").is_dir():
        sys.exit(f"{source} lacks {', '.join(missing or ['licenses'])}; build pt first")
    target.mkdir(parents=True)
    for name in VOICE_MODELS + VOICE_LIBRARIES:
        shutil.copy2(source / name, target / name)
    for library in variants:
        shutil.copy2(library, target / library.name)
    shutil.copytree(source / "licenses", target / "licenses")


def make_icon(resources, work):
    iconset = work / "pt.iconset"
    run(["xcrun", "swift", PACKAGING / "make_icon.swift", iconset])
    run(["iconutil", "-c", "icns", iconset, "-o", resources / "pt.icns"])
    shutil.rmtree(iconset)


def relink(contents, deployment_target):
    """Points every load command at the system or the bundle, checks them all, and returns the highest macOS any binary
    needs."""
    for library in (contents / "Frameworks").glob("*.dylib"):
        if not library.is_symlink():
            run(["install_name_tool", "-id", f"@rpath/{library.name}", library], stderr=subprocess.DEVNULL)
    exe = contents / "MacOS" / "pt"
    for rpath in load_commands(exe)[1]:
        run(["install_name_tool", "-delete_rpath", rpath, exe])
    run(["install_name_tool", "-add_rpath", "@executable_path/../Frameworks", exe])
    # whisper and ggml find each other through @loader_path; the build folder's absolute rpath goes
    for library in (contents / "Resources" / "voice").glob("*.dylib"):
        for rpath in load_commands(library)[1]:
            if not rpath.startswith(BUNDLE_RPATHS):
                run(["install_name_tool", "-delete_rpath", rpath, library], stderr=subprocess.DEVNULL)
    return check(contents, deployment_target)


def expand(path, image, exe):
    """A load command path with @executable_path and @loader_path replaced, normalized, symlinks followed."""
    for prefix, folder in (("@executable_path", exe.parent), ("@loader_path", image.parent)):
        if path == prefix or path.startswith(prefix + "/"):
            path = str(folder) + path[len(prefix):]
    return Path(os.path.realpath(path))


def check(contents, deployment_target):
    """Every library a binary loads is part of macOS or a file inside the app, every rpath and symlink stays inside the
    app; returns the highest macOS any binary needs."""
    bundle = Path(os.path.realpath(contents.parent))
    exe = Path(os.path.realpath(contents / "MacOS" / "pt"))
    exe_rpaths = [expand(rp, exe, exe) for rp in load_commands(exe)[1]]
    problems = []
    minimum = deployment_target
    for path in sorted(p for p in contents.rglob("*") if p.is_symlink()):
        if not Path(os.path.realpath(path)).is_relative_to(bundle):
            problems.append(f"{path.relative_to(contents)} links to {os.path.realpath(path)}")
    for path in sorted(p for p in contents.rglob("*") if p.is_file() and not p.is_symlink() and is_macho(p)):
        image = Path(os.path.realpath(path))
        libraries, rpaths, minos = load_commands(image)
        name = path.relative_to(contents)
        own_rpaths = []
        for rpath in rpaths:
            folder = expand(rpath, image, exe)
            if not rpath.startswith(BUNDLE_RPATHS) or not folder.is_relative_to(bundle):
                problems.append(f"{name} has rpath {rpath}")
            own_rpaths.append(folder)
        for library in libraries:
            if library.startswith("/"):
                if not os.path.normpath(library).startswith(SYSTEM_LIBRARIES):
                    problems.append(f"{name} loads {library}")
                continue
            if library.startswith("@rpath/"):
                # dyld tries the image's own rpaths, then those of the executable that loads it
                candidates = [folder / library[len("@rpath/"):] for folder in own_rpaths + exe_rpaths]
            else:
                candidates = [expand(library, image, exe)]
            found = [Path(os.path.realpath(c)) for c in candidates if c.is_file()]
            if not found or not found[0].is_relative_to(bundle):
                problems.append(f"{name} loads {library}, " + (f"found at {found[0]}" if found else "found nowhere in the app"))
        for value in minos:
            if tuple(map(int, value.split("."))) > tuple(map(int, minimum.split("."))):
                print(f"{name} needs macOS {value}, newer than the deployment target {deployment_target}")
                minimum = value
    if problems:
        sys.exit("load commands outside the system and the app:\n  " + "\n  ".join(problems))
    return minimum


def sign(app):
    contents = app / "Contents"
    # inside out: every library first, then the app, which signs the executable and seals the resources
    libraries = sorted(p for p in contents.rglob("*.dylib") if not p.is_symlink())
    run(["codesign", "--force", "--sign", "-", "--timestamp=none", *libraries])
    run(["codesign", "--force", "--sign", "-", "--timestamp=none", app])
    run(["codesign", "--verify", "--deep", "--strict", app])


def main():
    parser = argparse.ArgumentParser(description="Build pt with the macos preset and package it as P.T.app and a .zip")
    parser.add_argument("--build", default=str(REPO / "build" / "macos"))
    parser.add_argument("--out", default=str(REPO / "dist"))
    parser.add_argument("--no-build", action="store_true", help="package the build folder as it is")
    parser.add_argument("--bundle-id", default="org.pt-port.pt")
    parser.add_argument("--game", default=str(REPO / "game" / "CUSA01127"),
                        help="the game files, for the loop browser previews (shot by the packaged game, --make-loop-previews)")
    args = parser.parse_args()
    if sys.platform != "darwin":
        sys.exit("package.py makes the macOS app; tools/package.py packages Windows and Linux builds")
    build = Path(args.build).resolve()
    game = Path(args.game).expanduser().resolve()
    if not args.no_build:
        build_pt(build)
    exe = build / "pt"
    if not exe.is_file():
        sys.exit(f"{exe} not found; build first (cmake --preset macos && cmake --build --preset macos --target pt)")
    name = f"pt-port-{version()}-macos"
    root = Path(args.out).resolve() / name
    archive = root.with_suffix(".zip")
    if root.exists() or archive.exists():
        sys.exit(f"Package output already exists; choose another --out folder: {root}")
    app = root / "P.T.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    frameworks = contents / "Frameworks"
    for folder in (contents / "MacOS", frameworks, resources / "shaders", resources / "vulkan" / "icd.d"):
        folder.mkdir(parents=True)
    shutil.copy2(exe, contents / "MacOS" / "pt")
    # the game starts itself from SDL_GetBasePath() to shoot the loop browser and Museum previews
    (resources / "pt").symlink_to("../MacOS/pt")
    for spv in sorted((build / "shaders").glob("*.spv")):
        shutil.copy2(spv, resources / "shaders" / spv.name)
    copy_voice(build / "voice", resources / "voice")
    shutil.copytree(build / "fonts", resources / "fonts")
    shutil.copytree(build / "licenses", resources / "licenses", ignore=lambda folder, names: [n for n in names if n in LEFT_OUT_LICENSES])

    # D5: Homebrew's loader and MoltenVK. volk opens libvulkan.dylib and then libvulkan.1.dylib by name, which the
    # rpath finds here before any copy elsewhere on the Mac
    loader, moltenvk = homebrew("vulkan-loader"), homebrew("molten-vk")
    shutil.copy2((loader / "lib" / "libvulkan.1.dylib").resolve(), frameworks / "libvulkan.1.dylib")
    (frameworks / "libvulkan.dylib").symlink_to("libvulkan.1.dylib")
    shutil.copy2((moltenvk / "lib" / "libMoltenVK.dylib").resolve(), frameworks / "libMoltenVK.dylib")
    icd = (moltenvk / "etc" / "vulkan" / "icd.d" / "MoltenVK_icd.json").read_text()
    icd = re.sub(r'"library_path"\s*:\s*"[^"]*"', '"library_path": "../../../Frameworks/libMoltenVK.dylib"', icd)
    (resources / "vulkan" / "icd.d" / "MoltenVK_icd.json").write_text(icd)
    shutil.copy2(loader / "LICENSE.txt", resources / "licenses" / "Vulkan-Loader.txt")
    shutil.copy2(moltenvk / "LICENSE", resources / "licenses" / "MoltenVK.txt")

    deployment_target = cache_value(build, "CMAKE_OSX_DEPLOYMENT_TARGET")
    if not deployment_target:
        sys.exit(f"{build} has no CMAKE_OSX_DEPLOYMENT_TARGET; configure it again: cmake --preset macos -B {build}")
    minimum = relink(contents, deployment_target)
    plist = (PACKAGING / "Info.plist.in").read_text()
    # CFBundleVersion takes numbers only: a version like 1.0.1-rc1 goes in as 1.0.1
    values = {"PT_BUNDLE_ID": args.bundle_id, "PT_VERSION": re.sub(r"-.*$", "", game_version(build)), "PT_MINIMUM_MACOS": minimum}
    for key, value in values.items():
        plist = plist.replace(f"@{key}@", value)
    (contents / "Info.plist").write_text(plist)
    run(["plutil", "-lint", "-s", contents / "Info.plist"])
    make_icon(resources, root)
    shutil.copy2(REPO / "README.md", resources / "README.md")
    # unsigned code does not run on Apple Silicon: sign before the packaged game shoots the previews
    sign(app)

    # the loop browser's previews, shot by the packaged game itself as tools/package.py does; without the game files the
    # game shoots them on the player's Mac at the browser's first use
    if game.is_dir():
        previews = resources / "loop-previews"
        run([contents / "MacOS" / "pt", "--make-loop-previews", previews, "--game", game], cwd=resources, timeout=1800)
        shots = sorted(previews.glob("loop-*.png"))
        print(f"loop browser previews: {len(shots)}")
        if len(shots) != 18 or not (previews / "version.txt").is_file():
            sys.exit(f"loop browser previews incomplete: {len(shots)} of 18, see {previews / 'capture.log'}")
        for extra in ("capture.txt", "capture.log", "preview.ini"):
            (previews / extra).unlink(missing_ok=True)
        sign(app)
    else:
        print(f"loop browser previews skipped: no game files at {game}")

    # ditto keeps the symlinks and the signature, which zipfile would not
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, archive])
    size = archive.stat().st_size / (1024 * 1024)
    print(f"{app}\n{archive} ({size:.1f} MB), macOS {minimum} or newer")


if __name__ == "__main__":
    main()
