"""blendertk headless smoke test.

Verifies the bootstrap resolves the public surface and the bpy-backed helpers work under
a FRESH Blender (session-safe). Run:

    blender --background --factory-startup --python blendertk/test/blender_smoke_test.py
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)            # blendertk/
MONO = os.path.dirname(REPO)            # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, fn):
    try:
        lines.append(f"OK   {name}: {fn()}")
    except Exception as e:
        lines.append(f"FAIL {name}: {e!r}")
        lines.append(traceback.format_exc())

try:
    import blendertk as btk
    lines.append(f"OK   import blendertk: v{btk.__version__}")

    # Public surface resolves (mirrors mtk.undoable / mtk.get_env_info / mtk.CoreUtils)
    check("btk.CoreUtils", lambda: btk.CoreUtils.__name__)
    check("btk.undoable resolves", lambda: callable(btk.undoable))
    check("btk.get_env_info resolves", lambda: callable(btk.get_env_info))
    check("CoreUtils.undoable is btk.undoable",
          lambda: btk.CoreUtils.undoable is btk.undoable)

    # bpy-backed behavior
    check("get_env_info() dict", lambda: sorted(btk.get_env_info().keys()))
    check("get_env_info('blenderVersion')", lambda: btk.get_env_info("blenderVersion"))

    @btk.undoable
    def _touch(x):
        return x * 2
    check("undoable wraps + runs + pushes undo", lambda: _touch(21))
    check("undoable preserves __name__", lambda: _touch.__name__)
except Exception as e:
    lines.append(f"FAIL import/setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(not l.startswith("FAIL") for l in lines)
print("\n===BTK-SMOKE===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
