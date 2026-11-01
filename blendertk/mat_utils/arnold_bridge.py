# !/usr/bin/python
# coding=utf-8
"""Arnold render-bridge management -- Blender port of mayatk's ``mat_utils.arnold_bridge``.

mayatk's Arnold bridge attaches a parallel ``aiStandardSurface`` shading-engine network to a
Maya material so the same asset previews correctly under Arnold (MtoA) *inside Maya* while the
exported Stingray / Standard Surface material is untouched -- Maya's two renderers read two
separate shader graphs off the same shading engine.

Blender has no such split: Cycles and EEVEE both read the *same* Principled BSDF node graph, and
this codebase ships no Arnold-for-Blender integration at all (no MtoA-equivalent addon, no
``aiStandardSurface`` node type). There is nothing to attach and nothing to preview under, so
the Arnold Preview Shader panel (tentacle's ``slots/tools/arnold_bridge.py``) is Maya's alone:
Blender never offers it (``tentacle.slots.tools.HOSTS``).

``ArnoldBridge`` has no scene-mutating implementation for the same reason: there is no
Blender-side operation for it to perform. It exists for API parity only.
"""

import contextlib
from typing import Iterator, List, Optional, Union

import pythontk as ptk


_NOT_AVAILABLE = (
    "Not available in Blender: this codebase ships no Arnold-for-Blender integration "
    "(no MtoA-equivalent addon, no aiStandardSurface node type). Cycles and EEVEE both "
    "read the same Principled BSDF graph, so there is no parallel preview network to "
    "attach the way mayatk's Arnold bridge does in Maya."
)


class ArnoldBridge(ptk.LoggingMixin):
    """Structural mirror of mayatk's ``ArnoldBridge`` -- no-op on Blender (see module docstring).

    Kept importable (rather than omitted) so code that duck-types across ``mtk.ArnoldBridge`` /
    ``btk.ArnoldBridge`` doesn't need a branch just to detect blendertk's absence of the concept.
    """

    #: Cycles and EEVEE render every material graph they hold, so none needs a bridge.
    UNTRANSLATABLE_TYPES = frozenset()

    def __init__(self, ambient_occlusion: bool = True):
        """
        Parameters:
            ambient_occlusion: mayatk's switch for multiplying an AO map into the
                bridge's base colour. Kept so the same call runs here; there is
                no bridge to apply it to.
        """
        super().__init__()
        self.ambient_occlusion = bool(ambient_occlusion)

    @classmethod
    def unrenderable_materials(cls) -> List[str]:
        """No material needs a bridge before a Blender render: always ``[]``."""
        return []

    def add(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
        force: bool = False,
    ) -> List[str]:
        self.logger.warning(_NOT_AVAILABLE)
        return []

    def remove(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
    ) -> List[str]:
        self.logger.warning(_NOT_AVAILABLE)
        return []

    def rebuild(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
    ) -> List[str]:
        self.logger.warning(_NOT_AVAILABLE)
        return []

    @contextlib.contextmanager
    def temporary(self, materials) -> Iterator[List[str]]:
        """Bridge nothing for the block: there is no Arnold graph to add (see
        the module docstring). Yields no shading groups."""
        yield []

    def get_bridge(self, material) -> Optional[str]:
        return None

    def has_bridge(self, material) -> bool:
        return False

    @staticmethod
    def authored_plug(plug: str) -> str:
        """*plug* itself: no bridge follows a material here (see the module docstring)."""
        return plug

    @staticmethod
    def base_materials() -> List[str]:
        """No material is a bridge's base here: always ``[]``."""
        return []

    def select_bridged(self) -> List[str]:
        """Nothing is bridged here: selects nothing and returns ``[]``."""
        return []
