# !/usr/bin/python
# coding=utf-8
"""What the Shot Manifest panel needs from Blender: its store, engine and
detection classes, the playhead, the Outliner, object names and icons.

The panel's other modules are one text in mayatk and blendertk
(``m3trik/scripts/check_dcc_twins.py`` holds them identical); every call that
names the host is answered here, as mayatk's ``manifest_host.py`` answers them
for Maya.  ``bpy`` is imported in the calls that need it (headless
``blender --background`` and the docs tooling import this module without it).
"""

from typing import List, Optional

from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils.shots.shot_manifest._shot_manifest import (
    BlenderShotManifest,
)


class ManifestHost:
    """Blender's answers to the Shot Manifest panel's host calls."""

    #: The host's name, as the panel's messages say it.
    APP = "Blender"
    #: Where the panel's colour presets are kept (``ColorMappingDialog``).
    COLOR_PRESET_DIR = "blendertk/shot_manifest_colors"

    @staticmethod
    def store_cls():
        """The host's store class (``BlenderShotStore``)."""
        return BlenderShotStore

    @staticmethod
    def manifest_cls():
        """The host's manifest engine class (``BlenderShotManifest``)."""
        return BlenderShotManifest

    @staticmethod
    def detection():
        """The host's animation-region detection (``Detection``)."""
        from blendertk.anim_utils.shots._detection import Detection

        return Detection

    @staticmethod
    def available() -> bool:
        """Whether the scene can be reached (``bpy`` imports)."""
        try:
            import bpy  # noqa: F401 -- availability check
        except ImportError:
            return False
        return True

    @staticmethod
    def current_frame() -> Optional[float]:
        """The playhead's frame, or ``None`` without a scene."""
        try:
            import bpy
        except ImportError:
            return None
        # _final: the playhead with its sub-frame (``frame_current`` is the
        # int), as Maya's currentTime keeps the fraction.
        return float(bpy.context.scene.frame_current_final)

    @staticmethod
    def find_nodes(name: str, store) -> List[str]:
        """The scene objects doc object *name* answers to: the store's
        members, else the object of that name."""
        if store is not None:
            return store.member_nodes(name)
        import bpy

        return [name] if name in bpy.data.objects else []

    @staticmethod
    def reveal(nodes: List[str]) -> List[str]:
        """Select *nodes* so Blender's Outliner reveals them (it tracks the
        active object).  Returns a note naming those outside the active view
        layer: selection is a view-layer concept, and the ``bpy.data.objects``
        lookup is scene-wide (an excluded collection, another scene)."""
        from blendertk.core_utils._core_utils import CoreUtils
        from blendertk.ui_utils._ui_utils import UiUtils

        layer = CoreUtils._active_view_layer()
        shown = [n for n in nodes if layer is not None and n in layer.objects]
        outside = [f"'{n}'" for n in nodes if n not in shown]
        if shown:
            UiUtils.reveal_in_outliner(shown)
        if outside:
            return [f"{', '.join(outside)} outside the active view layer."]
        return []

    @staticmethod
    def leaf_name(name: str) -> str:
        """*name* as shown: Blender object names are flat, so effectively
        itself -- a Maya-style ``|`` path handed in is stripped."""
        return name.rsplit("|", 1)[-1] if "|" in name else name

    @staticmethod
    def object_icon(obj, name: str):
        """The icon of a table row's object, or ``None``: *obj* is its
        ``BuilderObject`` (``None`` for a bare scene name).  An audio object
        takes the speaker icon directly -- a VSE strip is no
        ``bpy.data.objects`` entry -- and anything else its object type's
        (``NodeIcons``)."""
        try:
            from blendertk.ui_utils.node_icons import NodeIcons
            import bpy  # noqa: F401 -- availability check
        except ImportError:
            return None
        if getattr(obj, "kind", None) == "audio":
            from uitk.managers.icon_manager import IconManager
            from blendertk.ui_utils.node_icons import ICON_COLOR

            icon_name = NodeIcons.icon_name_for_type("SPEAKER")
            icon = (
                IconManager.get(icon_name, size=(16, 16), color=ICON_COLOR)
                if icon_name
                else None
            )
            return icon if (icon is not None and not icon.isNull()) else None
        return NodeIcons.get_icon(name)
