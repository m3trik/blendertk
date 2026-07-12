# !/usr/bin/python
# coding=utf-8
"""Main workflow facade for shape-key morph creation, editing, and export — mirror of mayatk's
``anim_utils.blendshape_animator.BlendshapeAnimator``.

Ports Maya's blendShape-based "build a morph between two meshes, sculpt in-between shapes to
customize the curve, then bake for export" workflow onto Blender's native shape-key system.
The master shape key's ``value`` is itself directly keyable (no driven-attribute detour
needed, unlike Maya's blendShape weight); the harder-to-mirror piece — Maya's multi-target
"in-between" piecewise interpolation — is rebuilt with driver-driven corrective shape keys (see
``applicator.py``'s module docstring for the proof it's mathematically identical).

Divergence from mayatk (by design):
    * **Recovery of a "corrupted blendShape node"** (mayatk's ``Recovery``/``recover_setup``,
      which rebuilds a fresh blendShape node when Maya's deformer graph gets corrupted) has no
      Blender analogue: a shape key is data on the mesh, not a separate node that can end up in
      a broken position in a deformation stack. The one genuinely portable piece — recovering a
      LOST keyframe range — is kept as :meth:`recover_animation`.
    * ``target_obj``/the joined-in mesh is tracked via a custom property on the base object
      (``blendshape_animator_target``) rather than mayatk's brittle name-pattern heuristic
      (``from_existing``'s ``TWEEN_NAME_PATTERNS`` comment flags it as fragile) — Blender gives
      us a real, robust place to store this, so :meth:`from_existing` prefers it and only falls
      back to a heuristic scan for scenes set up before this convention existed.
"""
from typing import List, Optional, Tuple

import pythontk as ptk

from blendertk.anim_utils.blendshape_animator.applicator import Applicator, ApplyStatus
from blendertk.anim_utils.blendshape_animator.creator import Creator
from blendertk.anim_utils.blendshape_animator.keyframes import Keyframes
from blendertk.anim_utils.blendshape_animator.target import Target, Targets
from blendertk.anim_utils.blendshape_animator.validator import Validator
from pythontk import Weights

_TARGET_PROP = "blendshape_animator_target"
_KEY_PROP = "blendshape_animator_key"
#: Corrective-key naming infix (kept in lockstep with Applicator._CORRECTIVE_INFIX) so
#: ``from_existing`` can tell a master key apart from its own correctives.
_CORRECTIVE_INFIX = Applicator._CORRECTIVE_INFIX


class BlendshapeAnimator(ptk.LoggingMixin):
    """Main workflow facade for shape-key morph animation.

    Holds references to the three sub-components:
      * ``keyframes`` (:class:`Keyframes`)   — keyframe authoring on the master shape key's value
      * ``tween_creator`` (:class:`Creator`) — duplicate-mesh in-between creation
      * ``tween_applicator`` (:class:`Applicator`) — apply tween edits back as correctives
    """

    DEFAULT_START_FRAME = 1
    DEFAULT_END_FRAME = 100

    def __init__(self):
        super().__init__()
        self.base_obj = None
        self.target_obj = None
        self.key_name: Optional[str] = None
        self.keyframes: Optional[Keyframes] = None
        self.tween_creator: Optional[Creator] = None
        self.tween_applicator: Optional[Applicator] = None

    # =============================================================================
    # CREATE
    # =============================================================================

    def create(
        self,
        base_obj=None,
        target_obj=None,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None,
        name: str = "morph",
        test_setup: bool = True,
    ) -> bool:
        """Set up a basic morph animation between two mesh objects."""
        import bpy

        self.logger.info("=== CREATE PHASE: Setting up morph animation ===")

        if start_frame is None:
            start_frame = self.DEFAULT_START_FRAME
        if end_frame is None:
            end_frame = self.DEFAULT_END_FRAME

        if base_obj is None or target_obj is None:
            from blendertk.core_utils._core_utils import selected_objects

            sel = selected_objects()
            if len(sel) != 2:
                self.logger.error(
                    "Please select exactly 2 mesh objects (the contributing shape, then the "
                    "receiving mesh last/active)"
                )
                return False
            # Active object receives the new shape key (mirrors bpy.ops.object.join_shapes'
            # own "active = destination" selection convention) -- the Blender analogue of
            # Maya's base_mesh (the one that gets the blendShape node).
            active = bpy.context.view_layer.objects.active
            if active in sel:
                base_obj = active
                target_obj = next(o for o in sel if o is not active)
            else:
                base_obj, target_obj = sel[0], sel[1]

        if not Validator.validate_meshes(base_obj, target_obj):
            return False

        self.base_obj = base_obj
        self.target_obj = target_obj

        try:
            shape_keys = base_obj.data.shape_keys
            existing_key = shape_keys.key_blocks.get(name) if shape_keys else None

            if existing_key is not None:
                self.key_name = name
                self.logger.info(f"Found existing shape key: {self.key_name}")
            else:
                if shape_keys is None or "Basis" not in shape_keys.key_blocks:
                    base_obj.shape_key_add(name="Basis", from_mix=False)

                bpy.ops.object.select_all(action="DESELECT")
                target_obj.select_set(True)
                base_obj.select_set(True)
                bpy.context.view_layer.objects.active = base_obj
                bpy.ops.object.join_shapes()

                new_kb = base_obj.data.shape_keys.key_blocks[-1]
                new_kb.name = name
                self.key_name = new_kb.name  # Blender may have suffixed .001 on a name clash
                self.logger.info(f"Created shape key: {self.key_name}")

            kb = base_obj.data.shape_keys.key_blocks[self.key_name]
            kb.slider_min, kb.slider_max = 0.0, 1.0

            self._warn_if_sibling_master_key_exists(base_obj.data.shape_keys, self.key_name)

            base_obj[_KEY_PROP] = self.key_name
            base_obj[_TARGET_PROP] = target_obj.name

            self.keyframes = Keyframes(self.base_obj, self.target_obj, self.key_name)
            self.tween_creator = Creator(self.keyframes)
            self.tween_applicator = Applicator(self.keyframes)

            if not self.keyframes.create_keyframes(start_frame, end_frame):
                self._clear_setup_state()
                return False

            if test_setup:
                self.logger.info("Testing shape key setup...")
                self.keyframes.test_morph()

            self.logger.info(f"CREATE phase complete: {base_obj.name} -> {target_obj.name}")
            self.logger.info(f"Animation range: {start_frame} to {end_frame}")
            return True

        except RuntimeError as e:
            self.logger.error(f"in CREATE phase: {e}")
            self._clear_setup_state()
            return False

    def _warn_if_sibling_master_key_exists(self, shape_keys, key_name: str) -> None:
        """Warn once when binding onto a base mesh that already carries ANOTHER animated
        master key (a normal multi-blend-shape rig — e.g. "Smile" + "Frown" sharing one face
        mesh) — see :mod:`applicator`'s module docstring for the confirmed Blender 5.1
        depsgraph limitation this can trigger once a driver-based corrective exists on either
        key: baked mesh geometry (viewport/export) for one (not reliably predictable which)
        can silently stop reflecting its own mix contribution, even though every relevant RNA
        value still reads back correct."""
        import blendertk as btk

        animated = {
            fc.data_path[len('key_blocks["') : -len('"].value')]
            for fc in btk.get_fcurves([shape_keys])
            if fc.data_path.startswith('key_blocks["') and fc.data_path.endswith('"].value')
        }
        animated.discard(key_name)
        animated = {n for n in animated if _CORRECTIVE_INFIX not in n}
        if animated:
            self.logger.warning(
                f"Base mesh already has another animated master key {sorted(animated)} — "
                "Blender's depsgraph can stop reflecting one key's own mix contribution in "
                "the baked mesh once a driver-based corrective exists on this mesh (confirmed "
                "Blender 5.1 limitation, not something this module can route around; see "
                "applicator.py's module docstring). Re-check the baked mesh for each key after "
                "applying tweens."
            )

    def _clear_setup_state(self) -> None:
        """Reset the bound setup after a failed create so the animator (and any UI gating on
        it) doesn't report a half-initialized setup."""
        self.base_obj = None
        self.target_obj = None
        self.key_name = None
        self.keyframes = None
        self.tween_creator = None
        self.tween_applicator = None

    # =============================================================================
    # EDIT — three explicit methods (no string dispatch)
    # =============================================================================

    def edit_weight_based(
        self,
        weights: Optional[List[float]] = None,
        count: int = 3,
        weight_range: Tuple[float, float] = (0.0, 1.0),
    ) -> List[Target]:
        """Create tweens at specific weights or evenly spaced."""
        if not self._validate_setup():
            return []
        self.logger.info("=== EDIT PHASE: Creating weight-based tweens ===")

        if weights is None:
            weights = Weights.generate_weights(count, weight_range)
        else:
            weights = [Weights.round_weight(w) for w in weights]

        tweens = self.tween_creator.create_weight_based_tweens(weights)

        if tweens:
            self.logger.info(
                f"Edit these {len(tweens)} meshes to customize the morph curve"
            )
            self.logger.info("When done editing, call: edit_apply_tweens()")

        return tweens

    def edit_frame_based(
        self,
        frames: Optional[List[int]] = None,
        target_frame: Optional[int] = None,
    ) -> List[Target]:
        """Create tweens at specific animation frames."""
        if not self._validate_setup():
            return []
        self.logger.info("=== EDIT PHASE: Creating frame-based tweens ===")

        created_tweens: List[Target] = []

        if target_frame is not None:
            tween = self.tween_creator.create_frame_based_tween(target_frame)
            if tween:
                created_tweens.append(tween)

        if frames:
            for frame in frames:
                tween = self.tween_creator.create_frame_based_tween(frame)
                if tween:
                    created_tweens.append(tween)

        if created_tweens:
            self.logger.info(
                f"Edit these {len(created_tweens)} meshes to customize specific frames"
            )
            self.logger.info("When done editing, call: edit_apply_tweens()")

        return created_tweens

    def edit_apply_tweens(self, tweens: Optional[List[Target]] = None) -> List[Target]:
        """Apply tween mesh edits back to the master shape key's correctives."""
        if not self._validate_setup():
            return []
        self.logger.info("=== EDIT PHASE: Applying tween edits ===")

        results = self.tween_applicator.apply_tweens(tweens)
        applied = [t for t, s in results if s is ApplyStatus.APPLIED]

        if applied:
            self.logger.info("Tween edits applied! Scrub timeline to see custom curve")

        return applied

    # =============================================================================
    # INTERNAL
    # =============================================================================

    def _validate_setup(self) -> bool:
        """True if base object + master shape key + keyframes engine are bound and live."""
        if not all([self.base_obj, self.key_name, self.keyframes]):
            self.logger.error("Setup not complete. Run create() first.")
            return False
        try:
            self.base_obj.name  # touch to detect a removed/invalidated reference
        except ReferenceError:
            self.logger.error("Base mesh no longer exists.")
            return False
        if self.keyframes.key_block is None:
            self.logger.error(f"Shape key '{self.key_name}' no longer exists.")
            return False
        return True

    def _process_existing_inbetweens(self, inbetween_objs: List) -> None:
        """Add pre-existing in-between mesh objects as tweens, then apply them."""
        if not self._validate_setup():
            return

        self.logger.info(
            f"Processing {len(inbetween_objs)} existing in-between meshes..."
        )

        count = len(inbetween_objs)
        weights = Weights.generate_weights(count, (0.0, 1.0))

        for obj, weight in zip(inbetween_objs, weights):
            try:
                self.tween_creator.tag_tween_mesh(obj, weight)
                self.tween_applicator.apply_tweens([Target(obj)])
                self.logger.info(f"  Added {obj.name} as in-between at weight {weight:.3f}")
            except (RuntimeError, ValueError) as e:
                self.logger.error(f"  Failed to add {obj.name}: {e}")

        self.logger.info("Existing in-between meshes processed.")

    # =============================================================================
    # WORKFLOW CONVENIENCE
    # =============================================================================

    @classmethod
    def basic_workflow(
        cls,
        base_obj=None,
        target_obj=None,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None,
        name: str = "morph",
    ) -> Optional["BlendshapeAnimator"]:
        """Complete basic workflow: create setup with tweens ready for editing."""
        cls.logger.info("=== BASIC WORKFLOW ===")

        animator = cls()
        success = animator.create(
            base_obj=base_obj,
            target_obj=target_obj,
            start_frame=start_frame,
            end_frame=end_frame,
            name=name,
            test_setup=True,
        )
        if not success:
            cls.logger.error("Setup failed. Check your mesh objects or selection.")
            return None

        cls.logger.info("Creating tween meshes for custom animation curve...")
        targets = animator.edit_weight_based(count=3)
        if targets:
            cls.logger.info(f"Created {len(targets)} tween meshes")
            cls.logger.info("Now edit these meshes in Blender to customize your curve")
            cls.logger.info("When done editing, call: animator.apply_all_edits()")

        return animator

    def apply_all_edits(self) -> bool:
        """Apply all tween edits to the current setup."""
        self.logger.info("=== APPLYING ALL TWEEN EDITS ===")

        if not self._validate_setup():
            return False

        applied = self.edit_apply_tweens()

        if applied:
            self.logger.info(f"Applied {len(applied)} tween edits")
            self.logger.info("Scrub the timeline - animation should now show custom curve")
            return True
        self.logger.warning("No tween edits found to apply")
        return False

    def finalize_for_export(
        self,
        cleanup_scene: bool = True,
        delete_construction_history: bool = True,
        hide_target_mesh: bool = True,
        delete_inbetween_meshes: bool = True,
    ) -> bool:
        """Finalize the morph animation and clean up the scene for export."""
        import bpy

        self.logger.info("=== FINALIZING FOR EXPORT ===")

        if not self._validate_setup():
            return False

        self.logger.info("Step 1: Applying all in-between edits...")
        applied = self.edit_apply_tweens()
        if not applied:
            self.logger.info("No edits to apply - continuing with cleanup...")
        else:
            self.logger.info(f"Applied {len(applied)} in-between edits")

        if cleanup_scene:
            self.logger.info("Step 2: Cleaning up scene...")

            if hide_target_mesh and self.target_obj is not None:
                try:
                    self.target_obj.hide_viewport = True
                    self.target_obj.hide_render = True
                    self.logger.info(f"  Hidden target mesh: {self.target_obj.name}")
                except ReferenceError as e:
                    self.logger.warning(f"  Could not hide target mesh: {e}")

            if delete_inbetween_meshes:
                tweens = Targets.find_all_targets(
                    key_block_name=self.key_name, base_mesh_name=self.base_obj.name
                )
                deleted_count = 0
                for tween in tweens:
                    try:
                        bpy.data.objects.remove(tween.obj, do_unlink=True)
                        deleted_count += 1
                    except ReferenceError:
                        pass
                if deleted_count:
                    self.logger.info(f"  Deleted {deleted_count} in-between mesh objects")

                group = bpy.data.objects.get(Targets.GROUP_NAME)
                if group is not None and not group.children:
                    bpy.data.objects.remove(group, do_unlink=True)
                    self.logger.info(f"  Deleted empty group: {Targets.GROUP_NAME}")
            else:
                group = bpy.data.objects.get(Targets.GROUP_NAME)
                if group is not None:
                    group.hide_viewport = True
                    self.logger.info(f"  Hidden group: {group.name}")

        if delete_construction_history:
            self.logger.info(
                "Step 3: no-op — Blender shape keys carry no construction-history stack "
                "to bake away (there is no separate deformer-node graph to flatten, unlike "
                "Maya's blendShape); kept only for interface parity with mayatk."
            )

        self.logger.info("Step 4: Final validation...")
        try:
            kb = self.keyframes.key_block
            original_value = kb.value
            kb.value = 0.5
            bpy.context.view_layer.update()
            kb.value = original_value
            self.logger.info("  Shape key validation passed")
        except RuntimeError as e:
            self.logger.warning(f"  Shape key validation warning: {e}")

        self.logger.info("=== EXPORT READY ===")
        self.logger.info(f"Base mesh: {self.base_obj.name}")
        self.logger.info(f"Shape key: {self.key_name}")
        try:
            fc = self.keyframes._value_fcurve()
            n_keys = len(fc.keyframe_points) if fc is not None else 0
        except RuntimeError:
            n_keys = 0
        self.logger.info(f"Animation keyframes: {n_keys} keys")
        self.logger.info("Scene cleaned and ready for baking/export")
        return True

    @classmethod
    def from_existing(cls, base_obj=None) -> Optional["BlendshapeAnimator"]:
        """Create an animator bound to an existing shape-key setup on ``base_obj``."""
        cls.logger.info("=== LOADING EXISTING SETUP ===")

        if base_obj is None:
            from blendertk.core_utils._core_utils import selected_objects

            sel = selected_objects()
            if sel:
                base_obj = sel[0]
            else:
                cls.logger.error("No base mesh provided and nothing selected.")
                return None

        shape_keys = getattr(base_obj.data, "shape_keys", None)
        if shape_keys is None or len(shape_keys.key_blocks) < 2:
            cls.logger.error(f"No shape-key setup found on {base_obj.name}")
            return None

        key_name = base_obj.get(_KEY_PROP)
        if not key_name or key_name not in shape_keys.key_blocks:
            # Fallback heuristic for scenes created before the custom-property convention
            # existed: the first non-Basis, non-corrective key.
            key_name = next(
                (
                    kb.name
                    for kb in shape_keys.key_blocks
                    if kb.name != "Basis" and _CORRECTIVE_INFIX not in kb.name
                ),
                None,
            )
        if key_name is None:
            cls.logger.error(f"No usable master shape key found on {base_obj.name}")
            return None

        target_name = base_obj.get(_TARGET_PROP)
        import bpy

        target_obj = bpy.data.objects.get(target_name) if target_name else None

        animator = cls()
        animator.base_obj = base_obj
        animator.target_obj = target_obj
        animator.key_name = key_name
        animator.keyframes = Keyframes(base_obj, target_obj, key_name)
        animator.tween_creator = Creator(animator.keyframes)
        animator.tween_applicator = Applicator(animator.keyframes)

        try:
            start, end = animator.keyframes.get_frame_range()
            cls.logger.info(f"Found keyframe range: {start} to {end}")
        except ValueError:
            cls.logger.warning("No animation keyframes found")

        cls.logger.info(f"Loaded existing setup: {base_obj.name} (key '{key_name}')")
        return animator

    def recover_animation(self) -> bool:
        """Recover lost animation keyframes on the master shape key's value."""
        self.logger.info("=== RECOVERING ANIMATION ===")

        if not self._validate_setup():
            return False

        try:
            current_start, current_end = self.keyframes.get_frame_range()
            self.logger.info(
                f"Animation already exists ({current_start} to {current_end})"
            )
            return True
        except ValueError:
            pass

        self.logger.warning("No animation keyframes found - attempting recovery...")

        tweens = Targets.find_all_targets(
            key_block_name=self.key_name, base_mesh_name=self.base_obj.name
        )
        frames = [t.target_frame for t in tweens if t.target_frame is not None]

        if len(frames) >= 2:
            start_frame, end_frame = min(frames), max(frames)
            self.logger.info(
                f"Recovered frame range from tweens: {start_frame} to {end_frame}"
            )
            if self.keyframes.create_keyframes(start_frame, end_frame):
                self.logger.info("Animation keyframes recovered")
                return True

        self.logger.warning(
            "Could not recover original range - creating default animation "
            f"(frames {self.DEFAULT_START_FRAME}-{self.DEFAULT_END_FRAME})"
        )
        if self.keyframes.create_keyframes(self.DEFAULT_START_FRAME, self.DEFAULT_END_FRAME):
            self.logger.info("Default animation created")
            return True

        self.logger.error("Failed to recover animation")
        return False

    def diagnose_topology_issues(self) -> bool:
        """Diagnose topology mismatches between the base mesh and in-between meshes."""
        self.logger.info("=== TOPOLOGY DIAGNOSIS ===")

        if not self._validate_setup():
            return False

        base_verts = len(self.base_obj.data.vertices)
        base_faces = len(self.base_obj.data.polygons)
        self.logger.info(
            f"Base mesh '{self.base_obj.name}': {base_verts} vertices, {base_faces} faces"
        )

        if self.target_obj is not None:
            try:
                target_verts = len(self.target_obj.data.vertices)
                target_faces = len(self.target_obj.data.polygons)
                self.logger.info(
                    f"Target mesh '{self.target_obj.name}': {target_verts} vertices, "
                    f"{target_faces} faces"
                )
                if target_verts != base_verts:
                    self.logger.warning("Target mesh topology mismatch!")
            except ReferenceError as e:
                self.logger.error(f"Cannot read target mesh topology ({e})")

        tweens = Targets.find_all_targets(
            key_block_name=self.key_name, base_mesh_name=self.base_obj.name
        )
        if not tweens:
            self.logger.info("No in-between meshes found")
            return True

        self.logger.info(f"Checking {len(tweens)} in-between meshes:")

        mismatched_count = 0
        for tween in tweens:
            try:
                tween_verts = len(tween.obj.data.vertices)
                tween_faces = len(tween.obj.data.polygons)
            except ReferenceError as e:
                self.logger.error(f"  {tween.mesh}: Error - {e}")
                mismatched_count += 1
                continue

            if tween_verts == base_verts and tween_faces == base_faces:
                self.logger.info(f"  {tween.mesh}: {tween_verts}v, {tween_faces}f (MATCH)")
            else:
                self.logger.error(
                    f"  {tween.mesh}: {tween_verts}v, {tween_faces}f (MISMATCH)"
                )
                mismatched_count += 1

        if mismatched_count > 0:
            self.logger.warning(f"{mismatched_count} meshes have topology mismatches")
            self.logger.info(
                "Possible solutions: delete + recreate / manually fix vertex counts / "
                "start over with matching topology"
            )
            return False

        self.logger.info("All meshes have matching topology")
        return True

    def cleanup_topology_mismatches(
        self,
        delete_mismatched: bool = True,
        apply_valid_only: bool = True,
    ) -> bool:
        """Clean up topology mismatches by deleting bad meshes and applying good ones."""
        import bpy

        self.logger.info("=== CLEANING UP TOPOLOGY MISMATCHES ===")

        if not self._validate_setup():
            return False

        base_verts = len(self.base_obj.data.vertices)
        target_topology_mismatch = False

        if self.target_obj is not None:
            try:
                target_verts = len(self.target_obj.data.vertices)
                if target_verts != base_verts:
                    self.logger.warning(
                        f"Target mesh topology mismatch: {target_verts}v vs {base_verts}v"
                    )
                    target_topology_mismatch = True
                else:
                    self.logger.info(f"Target mesh topology OK: {target_verts}v")
            except ReferenceError as e:
                self.logger.warning(f"Cannot validate target mesh topology ({e})")
                target_topology_mismatch = True

        all_tweens = Targets.find_all_targets(
            key_block_name=self.key_name, base_mesh_name=self.base_obj.name
        )
        if not all_tweens:
            self.logger.info("No in-between meshes found")
            if target_topology_mismatch and delete_mismatched:
                self._cleanup_target_mesh()
            return True

        valid_tweens = self.tween_applicator.validate_topology(all_tweens)
        invalid_tweens = [t for t in all_tweens if t not in valid_tweens]

        self.logger.info(
            f"Found {len(valid_tweens)} valid and {len(invalid_tweens)} invalid in-between meshes"
        )

        if apply_valid_only and valid_tweens:
            self.logger.info(f"Applying {len(valid_tweens)} valid meshes...")
            results = self.tween_applicator.apply_tweens(valid_tweens, validate_topology=False)
            applied_count = sum(1 for _, status in results if status is ApplyStatus.APPLIED)
            self.logger.info(f"Successfully applied {applied_count} valid meshes")

        if delete_mismatched and invalid_tweens:
            self.logger.info(f"Deleting {len(invalid_tweens)} mismatched meshes...")
            deleted_count = 0
            for tween in invalid_tweens:
                try:
                    name = tween.mesh
                    bpy.data.objects.remove(tween.obj, do_unlink=True)
                    self.logger.info(f"  Deleted: {name}")
                    deleted_count += 1
                except ReferenceError as e:
                    self.logger.error(f"  Failed to delete {tween.mesh}: {e}")

            group = bpy.data.objects.get(Targets.GROUP_NAME)
            if group is not None and not group.children:
                bpy.data.objects.remove(group, do_unlink=True)
                self.logger.info(f"  Deleted empty group: {Targets.GROUP_NAME}")

            self.logger.info(f"Deleted {deleted_count} mismatched meshes")

        if target_topology_mismatch and delete_mismatched:
            self._cleanup_target_mesh()

        remaining_tweens = Targets.find_all_targets(
            key_block_name=self.key_name, base_mesh_name=self.base_obj.name
        )
        self.logger.info("Cleanup complete")
        self.logger.info(f"  Remaining in-between meshes: {len(remaining_tweens)}")
        self.logger.info(
            f"  Applied valid meshes: {len(valid_tweens) if apply_valid_only else 0}"
        )
        if target_topology_mismatch and delete_mismatched:
            self.logger.info("  Target mesh: Updated/cleaned")

        return True

    def _cleanup_target_mesh(self) -> None:
        """Hide the problematic target mesh and clear the local reference."""
        try:
            old_target_name = self.target_obj.name
            self.target_obj.hide_viewport = True
            self.target_obj.hide_render = True
            self.logger.info(f"  Hidden problematic target mesh: {old_target_name}")
            self.target_obj = None
            self.logger.info("  Updated target reference to None")
        except (ReferenceError, AttributeError) as e:
            self.logger.warning(f"  Could not clean up target mesh: {e}")

    def remove_target_for_export(self) -> bool:
        """Remove the target mesh object for a clean export."""
        import bpy

        self.logger.info("=== REMOVING TARGET MESH FOR EXPORT ===")

        if not self._validate_setup():
            return False

        if self.target_obj is not None:
            try:
                name = self.target_obj.name
                bpy.data.objects.remove(self.target_obj, do_unlink=True)
                self.logger.info(f"Removed target mesh: {name}")
                self.target_obj = None

                if self.keyframes.key_block is not None:
                    self.logger.info(f"Shape key '{self.key_name}' preserved - animation intact")
                else:
                    self.logger.warning("Shape key not found - animation may be lost")

                self.logger.info(
                    "Export cleanup complete - scene contains only base mesh with animation"
                )
                return True
            except ReferenceError as e:
                self.logger.error(f"Failed to remove target mesh: {e}")
                return False

        self.logger.info("No target mesh to remove - scene already clean for export")
        return True


__all__ = ["BlendshapeAnimator"]
