# !/usr/bin/python
# coding=utf-8
"""Scene-level repairs -- mirror of mayatk's ``core_utils.diagnostics.scene_diag``.

Only what has a Blender meaning is mirrored: the color-space repair. Maya's
unknown-node, plugin and OCIO-preference repairs have no Blender counterpart (a .blend
holds no unknown nodes, and Blender's color management is built in). ``import bpy`` is
deferred into the call bodies (no import side effects).
"""

# Eager: ``CoreUtils.undoable`` decorates the repairs while the class body runs.
from blendertk.core_utils._core_utils import CoreUtils


class SceneDiagnostics(object):
    """Scene-level repairs (mirror of mayatk's ``SceneDiagnostics``).

    Static throughout, like the module's siblings: the ``btk.Diagnostics`` aggregate
    multi-inherits these, and a static method stays the same object there.
    """

    @staticmethod
    @CoreUtils.undoable
    def fix_missing_color_spaces(
        images=None, force_update=False, dry_run=False, verbose=True
    ):
        """Give each texture image its color space by map type (``MatUtils.
        fix_color_spaces``: data maps Non-Color, color maps sRGB), reported as
        mayatk's ``SceneDiagnostics.fix_missing_color_spaces`` reports -- one undo
        step.

        Parameters:
            images: Image datablocks (or names) to fix; None scans every file image.
            force_update (bool): mayatk's flag, for its signature: Maya re-assigns a
                space whose string matches but whose OCIO reference went stale.
                Blender keeps no such reference, so only a wrong space is re-assigned
                here either way (re-assigning a right one would only reload the image).
            dry_run (bool): Report what would change without writing.
            verbose (bool): Print a one-line summary.

        Returns:
            dict: ``{"fixed_count": int, "fixed_nodes": [image name, ...], "changes":
            [{"node", "from", "to", "source"}, ...], "color_space", "raw_space",
            "globals_fixed": False}`` -- mayatk's keys.
        """
        from blendertk.mat_utils._mat_utils import MatUtils

        changed = MatUtils.fix_color_spaces(images=images, dry_run=dry_run)
        changes = [
            {"node": name, "from": old, "to": new, "source": "map type"}
            for name, (old, new) in sorted(changed.items())
            if old != new
        ]
        if verbose:
            verb = "would update" if dry_run else "updated"
            print(f"[blendertk] fix_missing_color_spaces: {verb} {len(changes)} image(s)")
        return {
            "fixed_count": len(changes),
            "fixed_nodes": [change["node"] for change in changes],
            "changes": changes,
            "color_space": "sRGB",
            "raw_space": "Non-Color",
            "globals_fixed": False,
        }
