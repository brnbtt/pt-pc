#!/usr/bin/env python3
"""Progress of the Apple Silicon / Metal port (docs/macos/PLAN.md).

Prints done/total per phase from the plan's checkboxes and the Phase 3 metric: Vulkan references left outside the
Vulkan backend. --files lists the files that still use Vulkan, --open lists the open tasks of the current phase.
"""

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "macos" / "PLAN.md"
SRC = ROOT / "src"

PHASE = re.compile(r"^## Phase (\d+): (.+)$")
TASK = re.compile(r"^\s*- \[( |x|X)\] (P\d+\.\d+[a-z]?) (.*)$")
VULKAN = re.compile(r"\bVk[A-Z]\w*|\bvk[A-Z]\w*\s*\(|\bVK_[A-Z0-9_]+")

# allowed to keep Vulkan after Phase 3: the backend itself, the Vulkan-only upscaler SDKs and OpenXR (PLAN.md P3.12)
BACKEND = ("engine/render/rhi/vulkan/",)
EXCEPTIONS = ("engine/render/upscale/", "engine/xr/", "game/vr_play.")


def read_plan():
    phases = []
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        if m := PHASE.match(line):
            phases.append({"number": int(m[1]), "name": m[2], "tasks": []})
        elif (m := TASK.match(line)) and phases:
            phases[-1]["tasks"].append({"id": m[2], "done": m[1] != " ", "text": m[3]})
    for phase in phases:
        ids = [t["id"] for t in phase["tasks"]]
        # a task with sub-tasks (P3.10 with P3.10a..g) is counted through its sub-tasks only
        phase["tasks"] = [
            t
            for t in phase["tasks"]
            if not any(re.fullmatch(re.escape(t["id"]) + "[a-z]", i) for i in ids)
        ]
    return phases


def vulkan_usage():
    counts = {}
    for path in SRC.rglob("*"):
        if path.suffix not in (".cpp", ".h", ".hpp", ".mm"):
            continue
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith(BACKEND):
            continue
        n = len(VULKAN.findall(path.read_text(encoding="utf-8", errors="replace")))
        if n:
            counts[rel] = n
    return counts


def bar(done, total, width=20):
    filled = round(width * done / total) if total else 0
    return "#" * filled + "." * (width - filled)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--files",
        action="store_true",
        help="list the files that still use Vulkan outside the backend",
    )
    parser.add_argument(
        "--open", action="store_true", help="list the open tasks of the current phase"
    )
    args = parser.parse_args()

    phases = read_plan()
    all_done = sum(t["done"] for p in phases for t in p["tasks"])
    all_total = sum(len(p["tasks"]) for p in phases)
    current = next((p for p in phases if not all(t["done"] for t in p["tasks"])), None)

    print(f"Apple Silicon port: {all_done}/{all_total} tasks\n")
    for p in phases:
        done = sum(t["done"] for t in p["tasks"])
        total = len(p["tasks"])
        mark = "  <- current" if p is current else ""
        print(
            f"  Phase {p['number']}  [{bar(done, total)}] {done:>2}/{total:<2}  {p['name']}{mark}"
        )

    counts = vulkan_usage()
    remaining = {f: n for f, n in counts.items() if not f.startswith(EXCEPTIONS)}
    allowed = sum(n for f, n in counts.items() if f.startswith(EXCEPTIONS))
    print(
        f"\nVulkan references outside the backend (Phase 3 target 0): {sum(remaining.values())} in {len(remaining)} files"
        f" (+{allowed} allowed in upscalers/OpenXR)"
    )

    if args.files:
        print()
        for f, n in sorted(remaining.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>5}  src/{f}")

    if args.open and current:
        print(f"\nOpen in Phase {current['number']}:")
        for t in current["tasks"]:
            if not t["done"]:
                print(f"  {t['id']:<7} {t['text']}")


if __name__ == "__main__":
    main()
