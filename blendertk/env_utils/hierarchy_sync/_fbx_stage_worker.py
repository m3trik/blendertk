# !/usr/bin/python
# coding=utf-8
"""Convert an FBX reference to a standalone ``.blend`` inside a FRESH headless Blender.

Invoked by :func:`_hierarchy_sync.stage_reference_blend` through
:class:`blendertk.env_utils.blender_connection.BlenderConnection` as a subprocess. Run as::

    blender --background --factory-startup --python _fbx_stage_worker.py -- <fbx> <out.blend> [<sys.path root> ...]

Isolation is the whole point. Blender object names are global to a ``.blend``, so importing an
FBX into the *user's live scene* (as the old in-process staging did) suffixes every imported
object ``.001`` whenever its name collides with an existing scene object — the normal case for a
Hierarchy Sync reference, which by design shares names with the current scene. That ``.001`` gets
baked into the staged ``.blend`` and then breaks the diff (every path mismatches → a fuzzy-match
explosion). Importing in a brand-new empty Blender guarantees CLEAN names and never touches the
user's scene. Exit code is non-zero on any failure so the caller can fall back / report cleanly.

The import is :meth:`FbxUtils.import_fbx`, so a file whose animation is split across takes (a
Shots Only export) stages ALL of it -- a Pull appends the staged object WITH its action. The
staged ``.blend`` is a throwaway, so it runs with ``stash=False``: nothing is parked in Key
Stash, and the warning naming the takes left out is printed as a ``STAGE_FBX_WARNING:`` line the
caller relays. Blender ignores ``PYTHONPATH``, so the caller passes the roots that make blendertk
and pythontk importable after the two paths; a child that cannot import them fails rather than
staging a scene that silently plays one take.

All work lives in :func:`main`, guarded by ``if __name__ == "__main__"``. This module is NOT
imported for an API, but it must still be **import-safe**: ``BlenderUiHandler``'s slot discovery
(``discover_slots=True, recursive=True``) walks the whole package at GUI startup and imports every
module in it, this one included. A bare top-level ``sys.exit`` would raise ``SystemExit`` on that
import — a ``BaseException`` that slips past the ``except Exception`` guards in ``tentacle_startup``
and tears the whole GUI Blender down at launch. Blender runs a ``--python`` script as ``__main__``,
so the subprocess call is unaffected. ``import bpy`` is likewise deferred into the body (the
package-wide "no ``bpy`` at import time" rule).
"""

import sys

#: The line prefix the caller relays as a warning.
WARNING_PREFIX = "STAGE_FBX_WARNING: "


def main() -> int:
    """Stage the FBX named on the command line; return the process exit code."""
    import bpy
    import logging

    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(argv) < 2:
        print("STAGE_FBX_ERROR: expected <fbx> <out.blend> [<sys.path root> ...]")
        return 2

    fbx_path, out_blend, roots = argv[0], argv[1], argv[2:]
    for entry in reversed(roots):
        if entry and entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        from blendertk.env_utils.fbx_utils import FbxUtils
    except Exception as e:  # noqa: BLE001 — no toolkit: fail, never stage one take
        print(f"STAGE_FBX_ERROR: blendertk is not importable here: {e}")
        return 1

    class _Relay(logging.Handler):
        def emit(self, record):
            print(WARNING_PREFIX + record.getMessage(), flush=True)

    relay = logging.getLogger("blendertk.fbx_stage_worker")
    relay.propagate = False
    relay.addHandler(_Relay(logging.WARNING))

    bpy.ops.wm.read_factory_settings(use_empty=True)

    before = set(bpy.data.objects)
    try:
        FbxUtils.import_fbx(fbx_path, stash=False, logger=relay)
    except Exception as e:  # noqa: BLE001 — surface any importer failure via exit code
        print(f"STAGE_FBX_ERROR: import failed: {e}")
        return 1

    if not (set(bpy.data.objects) - before):
        print("STAGE_FBX_ERROR: no objects imported")
        return 1

    bpy.ops.wm.save_as_mainfile(filepath=out_blend)
    print("STAGE_FBX_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
