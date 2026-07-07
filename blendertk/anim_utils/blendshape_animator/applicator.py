# !/usr/bin/python
# coding=utf-8
"""Applies tween mesh edits back to the master shape key — mirror of mayatk's
``anim_utils.blendshape_animator.applicator.Applicator``.

**How Maya's blendShape in-between targets work**: a single blendShape target attribute can
carry several sculpted "in-between" shapes at different weights (0..1); Maya interpolates
*piecewise-linearly between consecutive in-between shapes* (not just linearly from base to
target), so scrubbing the one weight attribute follows a custom, hand-sculpted curve.

**Blender has no built-in equivalent** — a shape key's ``value`` blends linearly between its
own ``relative_key`` and itself; there is no per-key "multiple sculpted stops" mechanism. The
idiomatic Blender substitute (a well-known rigging technique, not invented for this port) is
**additive corrective shape keys driven by "tent" (triangular) drivers**: one extra, purely
additive shape key per tween, each driven by the *same* master value through a
``SINGLE_PROP`` scripted driver that peaks at the tween's own weight and decays linearly to
zero at its neighbours. This reproduces Maya's piecewise-linear in-between interpolation
exactly:

Let ``P(v) = Basis + v * (Target - Basis)`` be the plain two-point master-key mix, and for
each tween at weight ``w_i`` let its stored corrective delta be
``corrective_i = sculpted_i - P(w_i)`` (the amount the user's sculpt differs from the
uncorrected linear blend at that weight). Give ``corrective_i`` a driver
``tent_i(v)`` that is ``1`` at ``v = w_i``, ramps linearly to ``0`` at the neighbouring control
weights (using the virtual endpoints ``0`` / ``1`` — Basis / Target — when there is no closer
neighbour), and ``0`` outside that span. Then for any ``v`` between two adjacent control
points ``w_k`` and ``w_(k+1)``:

    ``P(v) + tent_k(v)*corrective_k + tent_(k+1)(v)*corrective_(k+1)``
    ``== lerp(sculpted_k, sculpted_(k+1), (v - w_k)/(w_(k+1) - w_k))``

— algebraically identical to Maya's piecewise-linear in-between blend (``P`` is itself affine
in ``v``, so interpolating its argument commutes with interpolating its value; expand and the
``P`` terms cancel to exactly the lerp of the two sculpted shapes). Verified in
``test/test_blendshape_animator.py``. Every corrective's tent must be rebuilt whenever the set
of applied weights changes (adding/removing a tween shifts its neighbours' spans) — see
:meth:`Applicator._rebuild_all_tents`, called once per :meth:`apply_tweens` batch.

Reuses ``blendertk.rig_utils.RigUtils.add_prop_var`` / ``refresh_drivers`` for driver
plumbing rather than re-deriving it (the "script-built driver needs a forced recompile"
gotcha documented there applies here too).

**Known Blender 5.1 limitation — multiple independently-keyed master keys sharing one mesh.**
Confirmed by direct investigation (see ``test/test_blendshape_animator.py``'s cross-session
section): once a SECOND master shape key gets its own keyframed ``value`` on the same base
mesh's ``Key`` ID (a normal multi-blend-shape rig — e.g. "Smile" + "Frown" on one face mesh),
Blender's dependency graph can silently stop re-evaluating EITHER key's own mix contribution
once that key has a driver-based corrective — even though every relevant RNA value
(``key_blocks[...].value``, a corrective's own driven ``value``) reads back correct at every
step. Only the FINAL baked mesh geometry (``Object.to_mesh()`` under a depsgraph) is affected,
and *which* key ends up wrong is not a stable "first" or "last" rule — it was observed to shift
between repro runs depending on apply order, sometimes affecting one key, sometimes both. This
reproduces regardless of scripted-driver vs. plain two-key (no driver, which is fine) setups, of
value-poke vs. real ``frame_set`` scrubbing, of key ordering/index/creation/apply order, and
survives every standard "settle the depsgraph" mitigation tried (``view_layer.update()`` incl.
repeated/staggered calls, forcing ``update_tag()``, moving the frame away and back, toggling
edit/object mode, fully removing+re-adding a driver, adding a redundant dummy driver variable,
rebuilding a sibling key's tents after the fact) — it is not something this module's Python-level
API usage can route around. Only the single-master-key-per-mesh case (by far the common
single-morph authoring flow, and the one the rest of this test suite exercises end-to-end,
including the full piecewise in-between playback proof above) is confirmed reliable.
:meth:`BlendshapeAnimator.create` logs a warning when binding a NEW master key onto a base mesh
that already carries another animated one, so this is surfaced to the artist rather than
silently producing a frozen shape in the viewport. A production mitigation (e.g. syncing
correctives via a ``depsgraph_update_post``/``frame_change_post`` handler instead of a native
scripted driver, sidestepping the depsgraph ordering entirely) is a viable follow-up but adds
handler-lifecycle scope (registration/undo/reload) beyond what could be verified here.
"""
from enum import Enum
from typing import List, Optional, Tuple

import pythontk as ptk

from blendertk.anim_utils.blendshape_animator.keyframes import Keyframes, preserve_sibling_values
from blendertk.anim_utils.blendshape_animator.target import Target, Targets


class ApplyStatus(Enum):
    APPLIED = "applied"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    ERROR = "error"


class Applicator(ptk.LoggingMixin):
    """Applies tween mesh edits to the master shape key's corrective in-betweens."""

    #: Separator embedded in every corrective key's name so it can be found again / rebuilt.
    _CORRECTIVE_INFIX = "_tween_w"

    def __init__(self, keyframes: Keyframes):
        super().__init__()
        self.keyframes = keyframes

    def validate_topology(self, tweens: List[Target]) -> List[Target]:
        """Filter ``tweens`` to those matching the base mesh's vertex count."""
        self.logger.info("Validating tween mesh topology...")

        base_count = len(self.keyframes.base_obj.data.vertices)
        valid_tweens: List[Target] = []

        for tween in tweens:
            try:
                tween_count = len(tween.obj.data.vertices)
            except ReferenceError as e:
                self.logger.error(f"  {tween.mesh}: Error checking topology - {e}")
                continue
            if tween_count == base_count:
                valid_tweens.append(tween)
                self.logger.info(f"  {tween.mesh}: {tween_count} vertices (valid)")
            else:
                self.logger.error(
                    f"  {tween.mesh}: {tween_count} vs {base_count} vertices (topology mismatch)"
                )

        if len(valid_tweens) != len(tweens):
            self.logger.warning(
                f"Filtered {len(tweens) - len(valid_tweens)} tweens due to topology mismatch"
            )

        return valid_tweens

    def apply_tweens(
        self,
        tweens: Optional[List[Target]] = None,
        skip_duplicates: bool = True,
        validate_topology: bool = False,
    ) -> List[Tuple[Target, ApplyStatus]]:
        """Apply tween mesh edits to corrective in-between shape keys.

        ``skip_duplicates`` is accepted for interface parity with mayatk but
        ``ApplyStatus.SKIPPED_DUPLICATE`` cannot currently occur here: mayatk's variant guards
        against a Maya blendShape node rejecting a weight slot already consumed by *other*,
        untracked history ("Weights must be unique") — a corruption-recovery case tied to
        Maya's persistent deformer-node state. Corrective keys here are named deterministically
        by weight and owned entirely by this module (no hidden node-level state to collide
        with), so re-applying at an existing weight always just updates that same key in place.
        """
        if tweens is None:
            tweens = Targets.find_all_targets(
                key_block_name=self.keyframes.key_name,
                base_mesh_name=self.keyframes.base_obj.name,
            )

        if not tweens:
            self.logger.info("No tween meshes found to apply")
            return []

        if validate_topology:
            tweens = self.validate_topology(tweens)
            if not tweens:
                self.logger.warning("No valid tweens found after topology validation")
                return []

        weight_groups = Targets.group_by_weight(tweens)
        applied_results: List[Tuple[Target, ApplyStatus]] = []

        for weight, tween_group in sorted(weight_groups.items()):
            target_tween = tween_group[-1]

            if len(tween_group) > 1:
                self.logger.info(
                    f"  Found {len(tween_group)} tweens at weight {weight:.3f}, "
                    f"using: {target_tween.mesh}"
                )

            status = self._apply_single_tween(target_tween, skip_duplicates)
            applied_results.append((target_tween, status))

            if status is ApplyStatus.APPLIED:
                self.logger.info(f"Applied {target_tween.mesh} at weight {weight:.3f}")
            else:
                self.logger.error(f"Failed to apply {target_tween.mesh}")

        self._rebuild_all_tents()

        applied_count = sum(1 for _, s in applied_results if s is ApplyStatus.APPLIED)
        self.logger.info(f"Applied {applied_count}/{len(applied_results)} tween edits")
        return applied_results

    def _corrective_key_name(self, weight: float) -> str:
        return f"{self.keyframes.key_name}{self._CORRECTIVE_INFIX}{int(round(weight * 1000)):03d}"

    def _apply_single_tween(self, tween: Target, skip_duplicates: bool) -> ApplyStatus:
        """Write a single tween's sculpted delta into its corrective shape key."""
        try:
            self._write_corrective_key(tween)
            return ApplyStatus.APPLIED
        except (ValueError, ReferenceError) as e:
            self.logger.error(f"    Error applying {tween.mesh}: {e}")
            return ApplyStatus.ERROR

    def _write_corrective_key(self, tween: Target) -> None:
        base_obj = self.keyframes.base_obj
        weight = tween.weight
        corr_name = self._corrective_key_name(weight)

        shape_keys = base_obj.data.shape_keys
        basis = shape_keys.key_blocks["Basis"]
        key_target = shape_keys.key_blocks[self.keyframes.key_name]
        tween_obj = tween.obj

        n = len(basis.data)
        if len(tween_obj.data.vertices) != n:
            raise ValueError(
                f"topology mismatch ({len(tween_obj.data.vertices)} vs {n} vertices)"
            )

        corr = shape_keys.key_blocks.get(corr_name)
        if corr is None:
            corr = base_obj.shape_key_add(name=corr_name, from_mix=False)
            corr.relative_key = basis

        for i in range(n):
            b = basis.data[i].co
            t = key_target.data[i].co
            linear_mix = b + weight * (t - b)
            sculpted = tween_obj.data.vertices[i].co
            corr.data[i].co = b + (sculpted - linear_mix)

    # ------------------------------------------------------------------ tent drivers

    def _all_correctives(self):
        """Sorted ``(weight, key_block)`` pairs for every corrective belonging to this master key."""
        shape_keys = self.keyframes.key_id
        if shape_keys is None:
            return []
        prefix = f"{self.keyframes.key_name}{self._CORRECTIVE_INFIX}"
        out = []
        for kb in shape_keys.key_blocks:
            if kb.name.startswith(prefix):
                try:
                    w = int(kb.name[len(prefix):]) / 1000.0
                except ValueError:
                    continue
                out.append((w, kb))
        return sorted(out, key=lambda pair: pair[0])

    def _rebuild_all_tents(self) -> None:
        """Recompute every corrective's tent driver against the CURRENT full control-point set.

        Must run after any apply — adding/removing a control weight shifts its neighbours'
        spans, exactly like Maya recalculating a blendShape's piecewise curve whenever an
        in-between target is added or removed.
        """
        controls = self._all_correctives()
        n = len(controls)
        for idx, (w, kb) in enumerate(controls):
            w_prev = controls[idx - 1][0] if idx > 0 else 0.0
            w_next = controls[idx + 1][0] if idx < n - 1 else 1.0
            self._set_tent_driver(kb, w, w_prev, w_next)

        if controls:
            import blendertk as btk

            # refresh_drivers' view_layer.update() re-applies EVERY f-curve on this Key ID at
            # the current frame (not just the drivers it's meant to recompile) whenever a
            # keyframe was recently inserted anywhere on it -- guard sibling master keys' live
            # values (see preserve_sibling_values' docstring for the concrete two-morph repro).
            with preserve_sibling_values(self.keyframes.key_id):
                btk.RigUtils.refresh_drivers([self.keyframes.key_id])

    def _set_tent_driver(self, corr, w: float, w_prev: float, w_next: float) -> None:
        """Scripted driver: 1.0 at ``w``, linearly decaying to 0.0 at ``w_prev``/``w_next``."""
        import blendertk as btk

        try:
            corr.driver_remove("value")
        except Exception:
            pass
        fc = corr.driver_add("value")
        drv = fc.driver
        drv.type = "SCRIPTED"
        btk.RigUtils.add_prop_var(
            fc, "m", self.keyframes.key_id,
            f'key_blocks["{self.keyframes.key_name}"].value', id_type="KEY",
        )

        left = "1" if w == w_prev else f"(m - {w_prev!r}) / {(w - w_prev)!r}"
        right = "1" if w == w_next else f"({w_next!r} - m) / {(w_next - w)!r}"
        # Strict outer bounds: a control sitting exactly at a virtual endpoint (first
        # control at w==w_prev==0.0, or last control at w==w_next==1.0) has no decay on
        # that side at all, so m==w must reach the inner (peak-valued) branch rather than
        # the outer "outside the span" guard short-circuiting it to 0. Every non-degenerate
        # boundary (w_prev < w < w_next) still evaluates to 0 at m==w_prev/w_next via the
        # ramp formula itself (0/span), so this is purely a same-value refactor there.
        drv.expression = (
            f"0 if (m < {w_prev!r} or m > {w_next!r}) else "
            f"({left} if m <= {w!r} else {right})"
        )


__all__ = ["Applicator", "ApplyStatus"]
