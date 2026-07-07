# !/usr/bin/python
# coding=utf-8
"""Tween mesh wrappers and registry — mirror of mayatk's
``anim_utils.blendshape_animator.target`` (``Target`` / ``Targets``).

Divergence from mayatk (by design): Maya tags a tween mesh with plain node attributes
(``addAttr``/``setAttr``); Blender custom properties (``obj["key"] = value``) are the direct
analogue for a plain mesh OBJECT. A Blender ``ShapeKey`` itself does **not** support custom
properties (id-properties are only valid on ID datablocks / objects), which is why tween
"mesh" metadata lives on the duplicated tween mesh object rather than the corrective shape key
that :mod:`applicator` later writes from it. ``Target.mesh`` mirrors mayatk's string-name
attribute (so status/log text reads identically); the live object is available via
``Target.obj``.
"""
from typing import Dict, List, Optional

import pythontk as ptk

from blendertk.anim_utils.blendshape_animator.weights import Weights

#: Name of the Empty that parents every tween mesh object (Blender's analogue of Maya's
#: ``_morphInbetweens_GRP`` — an Empty is the closest Blender equivalent of a Maya group
#: transform node, matching the pattern already used by ``env_utils.hierarchy_manager``'s
#: quarantine group).
TWEEN_GROUP_NAME = "_morphInbetweens"

_REQUIRED_PROPS = ("isInbetweenTarget", "inbetweenWeight", "keyBlockName", "baseMesh")


class Target:
    """Represents a single tween (in-between) target mesh object."""

    def __init__(self, obj):
        self.obj = obj
        self._validate_target_mesh()

    def _validate_target_mesh(self) -> None:
        """Validate this is a proper tagged tween mesh object."""
        for attr in _REQUIRED_PROPS:
            if attr not in self.obj.keys():
                raise ValueError(f"Object {self.obj.name} missing required property: {attr}")

    @property
    def mesh(self) -> str:
        """The tween object's name (string) — mirrors mayatk's ``Target.mesh`` string attribute."""
        return self.obj.name

    @property
    def weight(self) -> float:
        return Weights.round_weight(self.obj["inbetweenWeight"])

    @property
    def key_block_name(self) -> str:
        """The master shape key this tween belongs to (mirrors mayatk's ``blendshape_name``)."""
        return str(self.obj["keyBlockName"])

    @property
    def base_mesh_name(self) -> str:
        return str(self.obj["baseMesh"])

    @property
    def target_frame(self) -> Optional[int]:
        if "targetFrame" in self.obj.keys():
            return int(self.obj["targetFrame"])
        return None

    def update_references(self, new_key_block_name: str, new_base_mesh_name: str) -> None:
        """Update this tween's references to a new master shape key / base mesh."""
        self.obj["keyBlockName"] = str(new_key_block_name)
        self.obj["baseMesh"] = str(new_base_mesh_name)
        Targets.logger.info(f"  Updated {self.obj.name} references")


class Targets(ptk.LoggingMixin):
    """Manages collections of tween mesh objects."""

    GROUP_NAME = TWEEN_GROUP_NAME

    @classmethod
    def find_all_targets(
        cls,
        key_block_name: Optional[str] = None,
        base_mesh_name: Optional[str] = None,
    ) -> List[Target]:
        """Find tagged tween mesh objects in the scene (deduplicated).

        ``key_block_name``/``base_mesh_name`` scope the scan to tweens tagged for one
        specific master shape key / base mesh (via the ``keyBlockName``/``baseMesh``
        custom properties ``Creator.tag_tween_mesh`` writes). Pass both unset for the
        old, scene-wide behavior.

        Callers bound to a single setup (``Creator``, ``Applicator``,
        ``BlendshapeAnimator``) MUST pass both so two independent sessions authoring
        different shape keys on the same base mesh (or the same key name on two
        different base meshes) never see, apply, or delete each other's tweens —
        without this, ``get_existing_weights``/``apply_tweens(tweens=None)`` would
        silently cross-contaminate sibling sessions. Leave both unset only for
        deliberately scene-wide views (e.g. the panel's read-only diagnostics before a
        setup is bound).
        """
        import bpy

        seen = set()
        candidates = []

        group = bpy.data.objects.get(cls.GROUP_NAME)
        if group is not None:
            for child in group.children:
                if child.name not in seen:
                    seen.add(child.name)
                    candidates.append(child)

        # Loose tween meshes outside the group: scan once, skip anything already collected.
        for obj in bpy.data.objects:
            if obj.name in seen:
                continue
            if "isInbetweenTarget" in obj.keys():
                seen.add(obj.name)
                candidates.append(obj)

        tweens = []
        for obj in candidates:
            try:
                if not obj.get("isInbetweenTarget"):
                    continue
                if key_block_name is not None and obj.get("keyBlockName") != key_block_name:
                    continue
                if base_mesh_name is not None and obj.get("baseMesh") != base_mesh_name:
                    continue
                tweens.append(Target(obj))
            except ValueError:
                cls.logger.warning(f"Skipping invalid tween mesh: {obj.name}")
                continue

        return sorted(tweens, key=lambda t: t.weight)

    @classmethod
    def group_by_weight(cls, tweens: List[Target]) -> Dict[float, List[Target]]:
        """Group tweens by weight value, handling duplicates."""
        weight_groups: Dict[float, List[Target]] = {}
        for tween in tweens:
            weight_groups.setdefault(tween.weight, []).append(tween)
        return weight_groups

    @classmethod
    def update_all_references(
        cls,
        new_key_block_name: str,
        new_base_mesh_name: str,
        old_key_block_name: Optional[str] = None,
        old_base_mesh_name: Optional[str] = None,
    ) -> int:
        """Update tween mesh references to a new master shape key / base mesh.

        Pass ``old_key_block_name``/``old_base_mesh_name`` to scope the rename to only
        the tweens currently tagged with those identifiers — an unscoped call retags
        every tween in the scene, including unrelated sessions' (mirrors the same
        scene-wide-vs-scoped choice documented on ``find_all_targets``).
        """
        tweens = cls.find_all_targets(
            key_block_name=old_key_block_name, base_mesh_name=old_base_mesh_name
        )
        for tween in tweens:
            tween.update_references(new_key_block_name, new_base_mesh_name)
        cls.logger.info(f"Updated {len(tweens)} tween references")
        return len(tweens)


__all__ = ["Target", "Targets", "TWEEN_GROUP_NAME"]
