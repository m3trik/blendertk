# !/usr/bin/python
# coding=utf-8
"""Creates in-between (tween) target meshes for sculpting a custom morph curve — mirror of
mayatk's ``anim_utils.blendshape_animator.creator.Creator``.

A tween is a plain, history-free duplicate mesh object frozen at the base+target mix for a
given weight (or frame) — the Blender analogue of Maya's
``cmds.duplicate(...); cmds.delete(dup, constructionHistory=True)``. It carries no shape keys
or modifiers of its own; the user hand-sculpts it, then :class:`~.applicator.Applicator` reads
its final vertex positions back into a driver-driven corrective shape key (see that module's
docstring for the interpolation math).
"""
from typing import List, Optional, Set

import pythontk as ptk

from blendertk.anim_utils.blendshape_animator.keyframes import Keyframes
from blendertk.anim_utils.blendshape_animator.target import Target, Targets
from blendertk.anim_utils.blendshape_animator.weights import Weights


class Creator(ptk.LoggingMixin):
    """Creates in-between target mesh objects for custom morph curves."""

    def __init__(self, keyframes: Keyframes):
        super().__init__()
        self.keyframes = keyframes

    def _duplicate_at_weight(self, name: str, weight: float):
        """A standalone, shape-key-free mesh object frozen at ``weight`` on the master key."""
        import bpy

        base_obj = self.keyframes.base_obj
        kb = self.keyframes.key_block
        original_value = kb.value
        kb.value = weight

        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        obj_eval = base_obj.evaluated_get(depsgraph)
        mesh_eval = obj_eval.to_mesh()
        try:
            coords = [v.co.copy() for v in mesh_eval.vertices]
            polys = [list(p.vertices) for p in mesh_eval.polygons]
        finally:
            obj_eval.to_mesh_clear()

        # Restore BEFORE creating/linking the new mesh+object on a base mesh that may carry a
        # SIBLING master key (another BlendshapeAnimator session sharing this base mesh): once
        # any driver later gets rebuilt (Applicator._rebuild_all_tents), Blender's depsgraph can
        # permanently mis-evaluate this key's own geometry contribution if new datablocks were
        # linked into the scene while its value was still parked at a stale, temporary weight —
        # restoring first keeps the depsgraph's authoritative state consistent with ``kb.value``
        # at every point a structural (new-datablock) change happens.
        kb.value = original_value

        new_mesh = bpy.data.meshes.new(name)
        new_mesh.from_pydata(coords, [], polys)
        new_mesh.update()

        dup = bpy.data.objects.new(name, new_mesh)
        dup.matrix_world = base_obj.matrix_world.copy()
        bpy.context.scene.collection.objects.link(dup)
        return dup

    def _ensure_group(self, group_name: str = Targets.GROUP_NAME):
        import bpy

        group = bpy.data.objects.get(group_name)
        if group is None:
            group = bpy.data.objects.new(group_name, None)
            bpy.context.scene.collection.objects.link(group)
        return group

    def create_weight_based_tweens(
        self,
        weights: List[float],
        group_name: str = Targets.GROUP_NAME,
        name_prefix: str = "morph_ib",
    ) -> List[Target]:
        """Create tween meshes at specific weight values.

        Skips or offsets weights that already exist (mirrors mayatk's duplicate-weight guard).
        """
        kb = self.keyframes.key_block
        original_value = kb.value
        created_tweens: List[Target] = []
        existing_weights = self.get_existing_weights()
        group = self._ensure_group(group_name)

        try:
            for raw_weight in weights:
                weight = Weights.round_weight(raw_weight)

                if weight in existing_weights:
                    offset = self.find_nearby_weight(weight, existing_weights)
                    if offset is None:
                        self.logger.warning(
                            f"Skipping weight {weight:.3f}: already exists, no nearby slot free"
                        )
                        continue
                    self.logger.info(
                        f"Weight {weight:.3f} exists, using nearby weight {offset:.3f}"
                    )
                    weight = offset

                tween_name = f"{name_prefix}_w{int(weight * 1000):03d}"
                dup = self._duplicate_at_weight(tween_name, weight)
                dup.parent = group

                self.tag_tween_mesh(dup, weight)
                created_tweens.append(Target(dup))
                existing_weights.add(weight)
        finally:
            kb.value = original_value

        self.logger.info(
            f"Created {len(created_tweens)} tween meshes at weights: "
            f"{[t.weight for t in created_tweens]}"
        )
        return created_tweens

    def create_frame_based_tween(self, target_frame: int) -> Optional[Target]:
        """Create a tween mesh at a specific animation frame."""
        import bpy

        try:
            start_frame, end_frame = self.keyframes.get_frame_range()
        except ValueError as e:
            self.logger.error(str(e))
            return None

        if not (start_frame < target_frame < end_frame):
            self.logger.error(
                f"Frame {target_frame} must be between {start_frame} and {end_frame}"
            )
            return None

        weight = Weights.frame_to_weight(target_frame, start_frame, end_frame)

        existing_weights = self.get_existing_weights()
        if weight in existing_weights:
            self.logger.warning(
                f"Weight {weight:.3f} already exists for frame {target_frame}"
            )
            self.logger.info(f"Existing in-between weights: {sorted(existing_weights)}")

            offset_weight = self.find_nearby_weight(weight, existing_weights)
            if offset_weight:
                self.logger.info(
                    f"Creating tween at nearby weight {offset_weight:.3f} instead"
                )
                weight = offset_weight
            else:
                self.logger.error("Cannot find suitable alternative weight")
                return None

        kb = self.keyframes.key_block
        original_value = kb.value
        original_frame = bpy.context.scene.frame_current

        try:
            bpy.context.scene.frame_set(target_frame)

            tween_name = f"tween_f{target_frame}_w{int(weight * 1000):03d}"
            dup = self._duplicate_at_weight(tween_name, weight)
            self.tag_tween_mesh(dup, weight, target_frame)
            dup.parent = self._ensure_group()

            self.logger.info(
                f"Created frame-based tween: {tween_name} "
                f"(frame {target_frame}, weight {weight:.3f})"
            )
            return Target(dup)
        finally:
            kb.value = original_value
            bpy.context.scene.frame_set(original_frame)

    def tag_tween_mesh(self, obj, weight: float, target_frame: Optional[int] = None) -> None:
        """Add metadata custom properties to ``obj``. Idempotent (safe to re-tag)."""
        obj["isInbetweenTarget"] = True
        obj["inbetweenWeight"] = float(weight)
        obj["keyBlockName"] = str(self.keyframes.key_name)
        obj["baseMesh"] = str(self.keyframes.base_obj.name)
        if target_frame is not None:
            obj["targetFrame"] = int(target_frame)

    def get_existing_weights(self) -> Set[float]:
        """All in-between weights known for the current master shape key.

        Sourced from tagged tween mesh objects (the SSoT — see ``tag_tween_mesh``),
        scoped to this key/base mesh so a sibling session authoring a different shape
        key on the same base mesh can't collide with (or get silently offset by)
        weights that aren't actually taken on THIS key.
        """
        return {
            tween.weight
            for tween in Targets.find_all_targets(
                key_block_name=self.keyframes.key_name,
                base_mesh_name=self.keyframes.base_obj.name,
            )
        }

    def find_nearby_weight(
        self,
        target_weight: float,
        existing_weights: Set[float],
        tolerance: float = 0.01,
    ) -> Optional[float]:
        """Find a nearby weight that doesn't conflict with existing weights."""
        for offset in (0.001, -0.001, 0.002, -0.002, 0.005, -0.005, 0.01, -0.01):
            candidate = Weights.round_weight(target_weight + offset)
            if 0.0 < candidate < 1.0 and candidate not in existing_weights:
                return candidate
        return None


__all__ = ["Creator"]
