#!/usr/bin/env python3
"""THROWAWAY P4.2 risk spike: GLSL -> SPIR-V -> MSL -> AIR for every shader, with a result table.

Not product code; P4.2 replaces it with the real build step. See docs/macos/msl-spike.md.

  python3 tools/macos/spike/msl/msl_spike.py                 # every variant
  python3 tools/macos/spike/msl/msl_spike.py --variant map32 # one variant

Output goes to build/msl-spike/ (gitignored): spv/, <variant>/{*.metal,*.air,*.log,*.reflect.json}, <variant>/results.
{json,md}, <variant>/pipelines.log, summary.md and table.md. Builds two helpers there: msl_xlate (msl_xlate.cpp, the
SPIRV-Cross C++ API) and metal_load (metal_load.swift, pipeline creation on this GPU). ab_layout.swift is run by hand,
see the report.
"""

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
SHADERS = ROOT / "shaders"

# Test shaders are compiled by CMakeLists.txt with their own command lines.
TEST_SHADERS = {
    "tests/texture_descriptor.comp": [],
    "tests/reflection_mix_test.comp": ["-I", str(SHADERS)],
}

STAGE = {".vert": "vert", ".frag": "frag", ".comp": "comp"}

# MSL version -> metal -std
STD = {
    20400: "macos-metal2.4",
    30000: "metal3.0",
    30100: "metal3.1",
    30200: "metal3.2",
    40000: "metal4.0",
}

# Device-address-space argument buffers. SPIRV-Cross only accepts set 0's runtime descriptor arrays there (and the set is
# 129 KiB); sets 1 and 2 follow so that every set is bound the same way.
DEVICE_SETS = [0, 1, 2]

# ab: argument buffers for every set; hybrid: argument buffers, but set 0 (the bindless table) discrete;
# discrete: no argument buffers. tier2: --msl-argument-buffer-tier 1 (MTLArgumentBuffersTier2).
VARIANTS = {
    # spirv-cross CLI only: what the stock tool gives without a binding map.
    "cli-ab32": {"tool": "cli", "msl": 30200, "mode": "ab"},
    "cli-hybrid32": {"tool": "cli", "msl": 30200, "mode": "hybrid"},
    "cli-discrete32": {"tool": "cli", "msl": 30200, "mode": "discrete"},
    # msl_xlate (C++ API): the Vulkan layout counts as resource bindings, fixed [[id]]s per set, argument buffer N at
    # [[buffer(N)]], push constants at [[buffer(PUSH_BUFFER)]].
    "map32": {"tool": "map", "msl": 30200, "mode": "ab"},
    "map32-constexpr": {"tool": "map", "msl": 30200, "mode": "ab", "constexpr": True},
    # pad_argument_buffer_resources: every argument buffer struct has the full set layout, slot N at byte 8 * N.
    "map32-pad": {"tool": "map", "msl": 30200, "mode": "ab", "pad": True},
    "map31": {"tool": "map", "msl": 30100, "mode": "ab"},
    "map30": {"tool": "map", "msl": 30000, "mode": "ab"},
    "map24": {"tool": "map", "msl": 20400, "mode": "ab"},
    "map40": {"tool": "map", "msl": 40000, "mode": "ab"},
}

PUSH_BUFFER = 3

# Descriptor set layouts as the Vulkan renderer creates them: (binding, type, count).
TEXTURES = [
    (0, "combined", 8192),
    (1, "ssbo", 1),
    (2, "combined", 64),
]  # texture_manager.cpp:116, kMaxTextures/kMaxCubeTextures
FRAME = [
    (0, "ssbo", 1),
    (1, "ssbo", 1),
    (2, "image", 56),
    (3, "sampler", 5),
    (4, "image", 1),
    (5, "ssbo", 1),
]  # scene_renderer.cpp:222
RT = [(0, "as", 1), (1, "ssbo", 1), (2, "combined", 1)] + [
    (3 + i, "storage_image", 1) for i in range(5)
]  # raytracing.cpp:56
SET_LAYOUTS = {
    "scene": {0: TEXTURES, 1: FRAME},
    "rt": {0: TEXTURES, 1: FRAME, 2: RT},
    "subsurface": {
        0: TEXTURES,
        1: FRAME,
        2: [(0, "combined", 1), (1, "combined", 1)],
    },  # subsurface_pass.cpp:19
    "ui": {0: TEXTURES, 1: [(0, "ssbo", 1), (1, "combined", 1)]},  # ui_batch.cpp:58
    "vfx": {
        0: TEXTURES,
        1: [(0, "ssbo", 1), (1, "combined", 1), (2, "combined", 1), (3, "ubo", 1)],
    },  # vfx_pass.cpp:88
    "composite": {0: [(0, "combined", 1), (1, "combined", 1)]},  # renderer.cpp:203
    "test_texture": {
        0: TEXTURES,
        1: [(0, "ssbo", 1)],
    },  # tests/texture_descriptor_test.cpp:26
    "test_mix": {0: [(0, "ssbo", 1)]},  # tests/reflection_mix_test.cpp:15
}


def ab_bindings(layout):
    """[[id(N)]] inside each set's argument buffer, in binding order; a combined image sampler takes count ids for
    the textures followed by count ids for the samplers, the way SPIRV-Cross lays out an unmapped set."""
    args = []
    for s, bindings in SET_LAYOUTS[layout].items():
        # Before the set's bindings: with pad_argument_buffer_resources, SPIRV-Cross keys its index -> binding lookup
        # on msl_buffer, so a later argument buffer binding would overwrite [[id(s)]] and the padding loop never ends.
        args += ["--ab-index", str(s), str(s)]
        i = 0
        for b, kind, count in bindings:
            buf = tex = smp = 0
            if kind in ("ssbo", "ubo", "as"):
                buf = i
            elif kind in ("image", "storage_image"):
                tex = i
            elif kind == "sampler":
                smp = i
            else:
                tex = i
                smp = i + count
                i += count
            i += count
            args += [
                "--bind",
                str(s),
                str(b),
                str(count),
                str(buf),
                str(tex),
                str(smp),
                kind,
            ]
        if s in DEVICE_SETS:
            args += ["--device-ab", str(s)]
    return args


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def first_error(text):
    lines = [l for l in text.splitlines() if l.strip()]
    for l in lines:
        if "error:" in l or "exception" in l.lower():
            return l.strip()
    return lines[0].strip() if lines else ""


def shader_list():
    items = []
    for p in sorted(SHADERS.iterdir()):
        if p.suffix in STAGE:
            items.append((p, []))
    for rel, extra in TEST_SHADERS.items():
        items.append((ROOT / rel, extra))
    return items


def entry_name(src):
    return src.name.replace(".", "_")


def compile_spv(src, extra, spv_dir):
    out = spv_dir / (src.name + ".spv")
    cmd = [
        "glslc",
        "--target-env=vulkan1.3",
        "-O",
        "-MD",
        "-MF",
        str(out) + ".d",
        *extra,
        str(src),
        "-o",
        str(out),
    ]
    rc, so, se = run(cmd)
    res = {"glslc": rc == 0, "glslc_err": first_error(se) if rc else ""}
    if rc == 0:
        rc, so, se = run(["spirv-val", "--target-env", "vulkan1.3", str(out)])
        res["spirv_val"] = rc == 0
        res["spirv_val_err"] = first_error(so + se) if rc else ""
        rc, so, se = run(["spirv-dis", str(out)])
        res["capabilities"] = sorted(set(re.findall(r"OpCapability (\w+)", so)))
        res["extensions"] = sorted(set(re.findall(r'OpExtension "(\w+)"', so)))
    return out, res


def build_driver(out_dir):
    exe = out_dir / "msl_xlate"
    src = HERE / "msl_xlate.cpp"
    if exe.exists() and exe.stat().st_mtime > src.stat().st_mtime:
        return exe
    prefix = Path(subprocess.check_output(["brew", "--prefix"], text=True).strip())
    libs = [
        str(prefix / "lib" / f"libspirv-cross-{n}.a")
        for n in ("msl", "glsl", "reflect", "util", "core")
    ]
    cmd = [
        "xcrun",
        "clang++",
        "-std=c++17",
        "-O1",
        "-I",
        str(prefix / "include"),
        str(src),
        *libs,
        "-o",
        str(exe),
    ]
    rc, so, se = run(cmd)
    if rc:
        sys.exit("msl_xlate build failed:\n" + se)
    return exe


def translate(variant, spv, src, out_dir, driver):
    v = VARIANTS[variant]
    metal = out_dir / (src.name + ".metal")
    stage = STAGE[src.suffix]
    if v["tool"] == "cli":
        cmd = [
            "spirv-cross",
            str(spv),
            "--msl",
            "--msl-version",
            str(v["msl"]),
            "--output",
            str(metal),
            "--rename-entry-point",
            "main",
            entry_name(src),
            stage,
            "--msl-argument-buffer-tier",
            "1",
        ]
        if v["mode"] != "discrete":
            cmd += [
                "--msl-argument-buffers",
                "--msl-force-active-argument-buffer-resources",
            ]
            for s in DEVICE_SETS:
                cmd += ["--msl-device-argument-buffer", str(s)]
        if v["mode"] == "hybrid":
            cmd += ["--msl-discrete-descriptor-set", "0"]
    else:
        layout = layout_for(src)
        cmd = [
            str(driver),
            str(spv),
            str(metal),
            "--msl",
            str(v["msl"]),
            "--entry",
            entry_name(src),
            "--push-buffer",
            str(PUSH_BUFFER),
            "--reflect",
            str(out_dir / (src.name + ".reflect.json")),
        ]
        if v["mode"] == "ab":
            cmd += ["--argument-buffers", *ab_bindings(layout)]
        if v.get("pad"):
            cmd += ["--pad"]
        if v.get("constexpr") and SET_LAYOUTS[layout][0] is TEXTURES:
            # TextureManager's samplers: repeat + anisotropy for the 2D table, repeat without anisotropy for cubes
            cmd += [
                "--constexpr-sampler",
                "0",
                "0",
                "16",
                "repeat",
                "--constexpr-sampler",
                "0",
                "2",
                "1",
                "repeat",
            ]
    rc, so, se = run(cmd)
    (out_dir / (src.name + ".xlate.log")).write_text(" ".join(cmd) + "\n" + so + se)
    return metal, rc == 0, first_error(se) if rc else "", (so + se).strip()


def metal_compile(variant, metal, src, out_dir):
    air = out_dir / (src.name + ".air")
    std = STD[VARIANTS[variant]["msl"]]
    cmd = [
        "xcrun",
        "-sdk",
        "macosx",
        "metal",
        f"-std={std}",
        "-c",
        str(metal),
        "-o",
        str(air),
    ]
    rc, so, se = run(cmd)
    (out_dir / (src.name + ".metal.log")).write_text(" ".join(cmd) + "\n" + so + se)
    warnings = len(re.findall(r": warning:", se))
    return air, rc == 0, first_error(se) if rc else "", warnings


# The pipeline layout each shader is created with on the Vulkan side, see SET_LAYOUTS. Shaders not listed use the scene
# layout (scene_renderer.cpp:135).
LAYOUTS = {
    "composite.frag": "composite",
    "xr_copy.frag": "composite",
    "fullscreen.vert": "scene",
    "ui_sprite.vert": "ui",
    "ui_sprite.frag": "ui",
    "vfx_particle.vert": "vfx",
    "vfx_particle.frag": "vfx",
    "subsurface.frag": "subsurface",
    "texture_descriptor.comp": "test_texture",
    "reflection_mix_test.comp": "test_mix",
}
RT_SHADERS = {
    "light_rt.frag",
    "light_contact.frag",
    "reflect_make_rt.frag",
    "reflect_blend_rt.frag",
    "reflect_layer_rt.frag",
    "rt_ao.comp",
    "rt_ao_filter.comp",
    "rt_skin.comp",
    "probe_ao.frag",
}


def layout_for(src):
    if src.name in LAYOUTS:
        return LAYOUTS[src.name]
    return "rt" if src.name in RT_SHADERS else "scene"


def link_with_static_helpers(variant, results, vdir):
    """SPIRV-Cross emits some helpers (spvMakeIntersectionParams for ray queries) with external linkage, so two AIR
    files that both use one cannot go into one metallib. Retry with those helpers made static, in a scratch copy."""
    fix = vdir / "static-helpers"
    fix.mkdir(exist_ok=True)
    airs, fixed = [], []
    for r in results:
        if not r.get("air"):
            continue
        air = Path(r["air"])
        metal = air.with_suffix(".metal")
        text = metal.read_text()
        patched = re.sub(
            r"^(intersection_params spvMakeIntersectionParams)",
            r"static inline \1",
            text,
            flags=re.M,
        )
        if patched == text:
            airs.append(str(air))
            continue
        src = fix / metal.name
        src.write_text(patched)
        out, ok, err, _ = metal_compile(variant, src, Path(r["shader"]), fix)
        if not ok:
            return False, fixed
        airs.append(str(fix / (Path(r["shader"]).name + ".air")))
        fixed.append(r["shader"])
    rc, so, se = run(
        [
            "xcrun",
            "-sdk",
            "macosx",
            "metallib",
            *airs,
            "-o",
            str(vdir / f"pt-{variant}-static-helpers.metallib"),
        ]
    )
    return rc == 0, fixed


def load_pipelines(out, lib, vdir):
    """Build metal_load.swift once and create every compute and render pipeline from the metallib on this GPU."""
    exe = out / "metal_load"
    src = HERE / "metal_load.swift"
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        rc, so, se = run(["xcrun", "swiftc", "-O", str(src), "-o", str(exe)])
        if rc:
            return "metal_load build failed"
    rc, so, se = run([str(exe), str(lib), str(vdir)])
    (vdir / "pipelines.log").write_text(so + se)
    m = re.search(r"pipelines ok (\d+), failed (\d+)", so)
    return f"{m.group(1)} ok, {m.group(2)} failed" if m else first_error(so + se)


def write_table(out):
    """Per-shader table for docs/macos/msl-spike.md: the stock CLI against the recommended option set (map32-pad)."""
    try:
        cli = json.loads((out / "cli-ab32" / "results.json").read_text())["results"]
        rec = json.loads((out / "map32-pad" / "results.json").read_text())["results"]
        log = (out / "map32-pad" / "pipelines.log").read_text()
    except FileNotFoundError:
        return
    features = {
        "RuntimeDescriptorArray": "bindless",
        "RayQueryKHR": "ray query",
        "PhysicalStorageBufferAddresses": "buffer_reference",
        "DemoteToHelperInvocation": "discard→demote",
        "ClipDistance": "clip distance",
        "DerivativeControl": "dFdxFine",
    }
    lines = [
        "| shader | SPIR-V | MSL (cli-ab32) | MSL (map32-pad) | metal (map32-pad) | pipeline (M4 Pro) | push bytes | uses |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c, r in zip(cli, rec):
        name = Path(r["shader"]).name
        entry = entry_name(Path(r["shader"]))
        m = re.search(rf"^(ok|FAILED)\s+{entry} (.*)$", log, re.M)
        if name.endswith(".vert"):
            pipe = "via its fragment" if r["metal"] else "-"
        else:
            pipe = ("ok" if m.group(1) == "ok" else "FAIL") if m else "-"
            if m and "render pipeline with" in m.group(2):
                pipe += (
                    " ("
                    + m.group(2).split("render pipeline with ")[1].split(",")[0]
                    + ")"
                )
        try:
            refl = json.loads(
                (out / "map32-pad" / (name + ".reflect.json")).read_text()
            )
        except FileNotFoundError:
            refl = {"resources": [], "push_constant_bytes": 0}
        uses = [v for k, v in features.items() if k in r["capabilities"]]
        if any(x["kind"] == "storage_image" for x in refl["resources"]):
            uses.append("storage image")
        lines.append(
            f"| `{r['shader']}` | {'ok' if r['glslc'] and r.get('spirv_val') else 'FAIL'} | "
            f"{'ok' if c['msl'] else 'FAIL'} | {'ok' if r['msl'] else 'FAIL'} | {'ok' if r['metal'] else 'FAIL'} | "
            f"{pipe} | {refl['push_constant_bytes'] or ''} | {', '.join(uses)} |"
        )
    (out / "table.md").write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", action="append", choices=sorted(VARIANTS))
    ap.add_argument("--out", default=str(ROOT / "build" / "msl-spike"))
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()
    variants = args.variant or list(VARIANTS)
    out = Path(args.out)
    spv_dir = out / "spv"
    spv_dir.mkdir(parents=True, exist_ok=True)
    shaders = shader_list()
    driver = (
        build_driver(out)
        if any(VARIANTS[v]["tool"] == "map" for v in variants)
        else None
    )

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
        spv = list(ex.map(lambda s: compile_spv(s[0], s[1], spv_dir), shaders))

    summary = []
    for variant in variants:
        vdir = out / variant
        if vdir.exists():
            shutil.rmtree(vdir)
        vdir.mkdir(parents=True)

        def one(i):
            src = shaders[i][0]
            path, base = spv[i]
            r = {
                "shader": str(src.relative_to(ROOT)),
                "layout": layout_for(src),
                **base,
            }
            if not base["glslc"] or not base.get("spirv_val"):
                r.update(msl=False, metal=False)
                return r
            metal, ok, err, log = translate(variant, path, src, vdir, driver)
            r.update(msl=ok, msl_err=err)
            if not ok:
                r["metal"] = False
                return r
            air, ok, err, warns = metal_compile(variant, metal, src, vdir)
            r.update(
                metal=ok,
                metal_err=err,
                metal_warnings=warns,
                air=str(air) if ok else "",
            )
            return r

        with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
            results = list(ex.map(one, range(len(shaders))))

        airs = [r["air"] for r in results if r.get("air")]
        lib = vdir / f"pt-{variant}.metallib"
        rc, so, se = (
            run(["xcrun", "-sdk", "macosx", "metallib", *airs, "-o", str(lib)])
            if airs
            else (1, "", "no air")
        )
        link = {
            "metallib": rc == 0,
            "metallib_err": first_error(se) if rc else "",
            "airs": len(airs),
        }
        if rc and "multiple symbols" in se:
            link["metallib_fixed"], link["fixed_shaders"] = link_with_static_helpers(
                variant, results, vdir
            )

        (vdir / "results.json").write_text(
            json.dumps(
                {
                    "variant": variant,
                    "options": VARIANTS[variant],
                    "link": link,
                    "results": results,
                },
                indent=1,
            )
        )
        n = len(results)
        counts = (
            sum(r["glslc"] for r in results),
            sum(r["msl"] for r in results),
            sum(r["metal"] for r in results),
        )
        lines = [
            f"# {variant}",
            "",
            f"options: `{VARIANTS[variant]}`",
            "",
            f"SPIR-V {counts[0]}/{n}, MSL {counts[1]}/{n}, metal {counts[2]}/{n}, "
            f"metallib link of {link['airs']} AIR files: {'ok' if link['metallib'] else 'FAILED ' + link['metallib_err']}"
            + (
                f"; after making the helpers static: {'ok' if link['metallib_fixed'] else 'FAILED'}"
                if "metallib_fixed" in link
                else ""
            ),
            "",
            "| shader | layout | SPIR-V | MSL | metal | warnings | first error |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in results:
            err = (
                r.get("glslc_err")
                or r.get("spirv_val_err")
                or r.get("msl_err")
                or r.get("metal_err")
                or ""
            )
            lines.append(
                f"| {r['shader']} | {r['layout']} | {'ok' if r['glslc'] and r.get('spirv_val') else 'FAIL'} | "
                f"{'ok' if r['msl'] else 'FAIL'} | {'ok' if r['metal'] else 'FAIL'} | {r.get('metal_warnings', '')} | "
                f"{err.replace('|', '/')[:160]} |"
            )
        (vdir / "results.md").write_text("\n".join(lines) + "\n")
        fixed = link.get("metallib_fixed")
        lib = vdir / f"pt-{variant}-static-helpers.metallib"
        if not fixed:
            lib = vdir / f"pt-{variant}.metallib"
        pipelines = load_pipelines(out, lib, vdir) if lib.exists() else "no metallib"
        summary.append(
            f"| {variant} | {counts[0]}/{n} | {counts[1]}/{n} | {counts[2]}/{n} | "
            f"{'ok' if link['metallib'] else 'FAIL'} ({link['airs']}) | "
            f"{'' if fixed is None else 'ok' if fixed else 'FAIL'} | {pipelines} |"
        )
        print(summary[-1], flush=True)

    (out / "summary.md").write_text(
        "| variant | SPIR-V | MSL | metal | metallib (AIR files) | metallib, static helpers | pipelines on this GPU |\n"
        "|---|---|---|---|---|---|---|\n" + "\n".join(summary) + "\n"
    )
    write_table(out)


if __name__ == "__main__":
    main()
