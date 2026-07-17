"""Regression: the FBX-staging worker must be import-safe (no side effects at module import).

``BlenderUiHandler`` is built with ``discover_slots=True, recursive=True``, so at GUI startup it
recursively imports EVERY module under the ``blendertk`` package to find ``*Slots`` classes.
``env_utils/hierarchy_sync/_fbx_stage_worker.py`` is a standalone headless worker (run as
``blender --python _fbx_stage_worker.py -- <fbx> <out.blend>``), not a slot — but it lives inside
the scanned package, so discovery imports it too. It used to run its body at module top level,
ending in ``sys.exit(2)`` when it found no CLI args. ``SystemExit`` is a ``BaseException`` (not
``Exception``), so it sailed past the ``except Exception`` guards in ``tentacle_startup`` and quit
GUI Blender at launch (a clean exit, no crash log). The fix moved all work into ``main()`` under
``if __name__ == "__main__"``.

This guards both the specific module and the whole class of bug (any future in-package script with
a top-level ``sys.exit``). Pure Python — the worker now defers ``import bpy`` into ``main``, so this
runs under the workspace ``.venv`` OR the Blender harness.

Run: blender --background --factory-startup --python blendertk/test/test_fbx_stage_worker.py
  or: python blendertk/test/test_fbx_stage_worker.py   (from the workspace .venv)
"""
import sys, os, importlib, pkgutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


WORKER = "blendertk.env_utils.hierarchy_sync._fbx_stage_worker"

# (1) Importing the worker must NOT raise SystemExit, and must expose a callable main().
try:
    mod = importlib.import_module(WORKER)
    check("importing the FBX-stage worker is side-effect-free (no SystemExit)", True)
    check("worker exposes a callable main()", callable(getattr(mod, "main", None)))
except SystemExit as e:
    check("importing the FBX-stage worker is side-effect-free (no SystemExit)", False,
          f"SystemExit(code={e.code}) on import — the launch-killer regression")
except Exception as e:  # noqa: BLE001
    check("importing the FBX-stage worker imports cleanly", False, repr(e))

# (2) Class-of-bug guard: the recursive slot-discovery walk over the whole blendertk package
#     (discover_slots=True, recursive=True) must never hit a module that SystemExits on import.
#     Ordinary ImportErrors (Qt/bpy absent under --factory-startup or the .venv) are tolerated —
#     they don't kill startup; only a BaseException that isn't an Exception does.
try:
    import blendertk

    offenders = []
    walked = 0
    for m in pkgutil.walk_packages(blendertk.__path__, prefix="blendertk.", onerror=lambda n: None):
        walked += 1
        try:
            importlib.import_module(m.name)
        except SystemExit as e:  # the fatal class: BaseException, escapes `except Exception` at startup
            offenders.append(f"{m.name}: SystemExit({e.code})")
        except Exception:  # noqa: BLE001 - tolerated (Qt/bpy/etc. not importable here) — never kills startup
            pass
    check("no blendertk module SystemExits on import (recursive discovery is safe)",
          not offenders, detail=("; ".join(offenders) if offenders else f"{walked} modules walked"))
except BaseException as e:  # noqa: BLE001 - the walk itself exited before finishing
    check("recursive discovery walk completes without a fatal exit", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
