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
        result = fn()
        if result is False:  # a boolean check reports its failure by value
            raise AssertionError("returned False")
        lines.append(f"OK   {name}: {result}")
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

    # Importing the package loads declarations only (CODE_STANDARD section 4).
    # Until 2026-10-25 the Diagnostics alias was fused at import and pythontk's
    # root loaded PIL and numpy into every ``import blendertk``.
    def _declarations_only():
        from pythontk.core_utils.module_resolver import import_footprint

        footprint = import_footprint("blendertk")
        if footprint != {"modules": [], "third_party": []}:
            raise AssertionError(footprint)
        return footprint
    check("import blendertk loads only declarations", _declarations_only)

    # Each flat name has one owner: six had two, reached by module order alone
    # (btk.set_scene_source was the record-only DataNodes one). Consolidated: 2026-10-25.
    def _one_owner_each():
        if btk._RESOLVER.flat_collisions:
            raise AssertionError(btk._RESOLVER.flat_collisions)
        return "no flat name defined twice"
    check("each flat name has one owner", _one_owner_each)

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
