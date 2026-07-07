"""Dump blendertk's live HelpMixin surface for the runtime-vs-static drift gate.

Runs under a FRESH headless Blender (session-safe) - never an attached session.
This is the Blender half of the drift gate: the static registry walker cannot
import bpy, so the live surface (which sees dynamically-composed members) is
dumped here and diffed against the committed registry from a normal shell.

    blender --background --factory-startup --python blendertk/test/dump_runtime_surface.py
    python m3trik/scripts/verify_runtime_surface.py verify blendertk --runtime blendertk/API_RUNTIME.json

Writes ``blendertk/API_RUNTIME.json`` (gitignored build artifact - never committed).
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # blendertk/
MONO = os.path.dirname(REPO)  # _scripts/
# Full ecosystem on path so blendertk's uitk-backed Slots classes can resolve
# (those needing Qt are skipped gracefully under --background, which has none).
for _p in (
    REPO,
    os.path.join(MONO, "pythontk"),
    os.path.join(MONO, "uitk"),
    os.path.join(MONO, "tentacle"),
    os.path.join(MONO, "unitytk"),
    os.path.join(MONO, "m3trik", "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Blender does not reliably propagate a script's exit code, so signal via a
# sentinel line the orchestrator/log can read.
try:
    import verify_runtime_surface as v

    v.main(["dump", "blendertk"])
    print("===DUMP OK===")
except Exception:
    traceback.print_exc()
    print("===DUMP FAIL===")
