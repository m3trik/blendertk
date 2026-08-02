# !/usr/bin/python
# coding=utf-8
"""Telescope Rig — engine + Switchboard slot wiring for the co-located ``telescope_rig.ui``.

Blender port of mayatk's ``rig_utils.telescope_rig`` (``btk.TelescopeRig`` ↔ ``mtk.TelescopeRig``):
build a chain of segments that telescopes — extends and retracts — between a base and an end handle,
the middle segments distributed evenly along the line and scaling with the base→end distance.

Maya wires this with a ``distanceBetween`` node + aim/point/parent constraints + driven keys; the
Blender analogue is idiomatic constraints + a single clamped linear driver (the "relax the mirror
where concepts diverge" rule). Each handle ``Damped Track``s the other; the two END segments ride
their handle with a ``Child Of`` (Maya's ``parentConstraint(maintainOffset=True)``, scale channels
off); each *middle* segment is a base→end lerp built from a pair of ``Copy Location`` constraints
whose offset is solved so the build pose is preserved exactly (Maya's ``pointConstraint(mo=True)``),
aimed along the chain; and each middle segment's along-axis scale is driven by
``s0 * max(dist, collapsed) / initial`` — the continuous, cycle-free equivalent of Maya's two-key
driven curve *including* its constant pre-infinity clamp.

Two segments is a first-class build: the halves ride their handles and slide, so no driver is
created — there is no interior to stretch. Either handle may be omitted and is then created at the
outer end of the strut (measured along the aim axis). Every build returns a ``TelescopeRigBundle``
that is also stamped onto the base handle as JSON, so ``teardown`` works in a later session.

The ``.ui`` is a byte-identical copy of mayatk's (``header`` / ``grp_options.cmb_axis`` +
``spin_collapsed`` / ``grp_finalize.btn_build`` + ``btn_remove`` / ``output_grp.txt003``) and
``TelescopeRigSlots`` mirrors mayatk's slot 1:1 — same widget names, same log-link wiring (clickable
``action://`` links in the log panel), same ``header_init`` shape. Maya's strict click-order
selection has no Blender analogue (selection order isn't reliably preserved), so ``build_rig`` uses
Blender's own idiom instead: Empties are handles, everything else is a segment, and the segments are
ordered by distance from the **active** object.

``import bpy`` is deferred into the call bodies and the Qt-only ``uitk`` helper into its method, so
importing the module / resolving the package surface never needs a running Blender or Qt.
"""

import json
from dataclasses import dataclass, field, fields, asdict
from typing import Dict, List, Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.rig_utils._rig_utils import RigUtils
from blendertk.xform_utils._xform_utils import XformUtils


@dataclass
class TelescopeRigBundle:
    """Record of everything one ``setup_telescope_rig`` build created — mirror of mayatk's
    ``TelescopeRigBundle``.

    Objects are recorded by NAME (a JSON-able handle that survives the round trip onto the base
    handle's custom property), and every piece of state the build overwrote — locks, the segments'
    own location/scale — is recorded alongside so ``teardown`` restores exactly what it changed.
    """

    name: str
    base_locator: str
    end_locator: str
    segments: List[str]
    scale_index: int
    initial_distance: float
    collapsed_distance: float
    constraints: List[List[str]] = field(default_factory=list)  # [object, constraint]
    drivers: List[List] = field(default_factory=list)  # [object, data_path, index]
    created_locators: List[str] = field(default_factory=list)
    original_locations: Dict[str, List[float]] = field(default_factory=dict)
    original_scales: Dict[str, float] = field(default_factory=dict)
    prior_locks: Dict[str, List[List[bool]]] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, payload: str) -> "TelescopeRigBundle":
        """Rebuild a bundle from :meth:`to_json` output, ignoring unknown keys (a scene stamped by
        an older/newer build still reads back)."""
        data = json.loads(payload)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class TelescopeRig(ptk.LoggingMixin):
    """Constraint + driver telescoping-segment rig (mirror of mayatk's ``TelescopeRig``)."""

    _AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
    _DATA_KEY = "telescope_rig_data"

    def __init__(self, log_level="WARNING"):
        """Initialize telescope rig with logging."""
        super().__init__()
        self.set_log_level(log_level)
        self.bundle: Optional[TelescopeRigBundle] = None

    # ------------------------------------------------------------------ resolution
    @classmethod
    def _resolve_axis(cls, aim_axis):
        """Resolve an axis token ("y", "-z", …) to Damped-Track enums, the driven scale index,
        the per-channel scale-lock tuple, and the signed unit axis — mirror of mayatk's
        ``_resolve_axis`` (Maya resolves aim/up vectors + scale attr names; Blender's Damped Track
        owns the up handling, so the track enums stand in for the vectors).

        Returns:
            (track, reverse_track, scale_index, lock_scale, axis_vector) — ``track`` aims a segment
            at the end handle along the signed axis, ``reverse_track`` aims the end back at the
            base, ``scale_index`` is the along-strut scale channel the driver animates,
            ``lock_scale`` locks the two off-axis channels so the stack can't shear, and
            ``axis_vector`` is the segments' modeled long axis (used to place auto handles).
        """
        token = str(aim_axis).strip().lower()
        sign = -1 if token.startswith("-") else 1
        letter = token.lstrip("+-")
        if letter not in cls._AXES:
            raise ValueError(
                f"aim_axis must be one of x, y, z (optionally signed); got {aim_axis!r}."
            )
        pos, neg = f"TRACK_{letter.upper()}", f"TRACK_NEGATIVE_{letter.upper()}"
        track, reverse_track = (pos, neg) if sign > 0 else (neg, pos)
        scale_index = "xyz".index(letter)
        lock_scale = tuple(i != scale_index for i in range(3))
        axis_vector = tuple(sign * c for c in cls._AXES[letter])
        return track, reverse_track, scale_index, lock_scale, axis_vector

    # ------------------------------------------- geometry probes (auto handles / collapse)
    @staticmethod
    def _project_size(size, direction):
        """Support width of an axis-aligned box of *size* along *direction*."""
        return (
            abs(direction.x) * size[0]
            + abs(direction.y) * size[1]
            + abs(direction.z) * size[2]
        )

    @classmethod
    def _axis_extent(cls, obj, direction):
        """Width of *obj*'s world bounding box measured along *direction*.

        A world AABB — the same measure mayatk takes from ``exactWorldBoundingBox``, so both
        twins derive the same auto handles and collapse distance. Exact for a segment modeled
        on axis; an overestimate by roughly its cross-section for one modeled at an angle to
        it. A shapeless object (an Empty) collapses to a zero-size box at its own position,
        so it needs no special case.
        """
        bbox = XformUtils.get_bounding_box(obj, world_space=True)
        return cls._project_size(bbox["size"], direction) if bbox else 0.0

    @classmethod
    def _support_point(cls, obj, direction, sign):
        """The point on *obj*'s world bbox furthest along ``sign * direction``.

        Taken through the box CENTER (not a corner) so an auto handle lands on the strut's
        centerline rather than off on an edge.
        """
        from mathutils import Vector

        bbox = XformUtils.get_bounding_box(obj, world_space=True)
        if not bbox:
            return obj.matrix_world.translation.copy()
        half = 0.5 * cls._project_size(bbox["size"], direction)
        return Vector(bbox["center"]) + direction * (sign * half)

    @classmethod
    def _chain_direction(cls, segments, axis_vector):
        """Unit world direction pointing base-segment → end-segment.

        Reads the base segment's LOCAL long axis rather than a pivot-to-pivot vector: a fully
        nested strut has every segment sitting on top of the others, so the modeled axis is the
        only reliable read of which way it points.
        """
        from mathutils import Vector

        direction = segments[0].matrix_world.to_3x3() @ Vector(axis_vector)
        if direction.length < 1e-9:  # degenerate (zero-scaled) transform
            direction = Vector(axis_vector)
        direction.normalize()
        chain = (
            segments[-1].matrix_world.translation - segments[0].matrix_world.translation
        )
        # Selection order is the authority on which end is which; only trust it when the
        # segments are actually spread out (a nested strut is not).
        if chain.length > 1e-6 and chain.dot(direction) < 0.0:
            direction = -direction
        return direction

    @classmethod
    def _auto_collapsed_distance(cls, segments, direction, initial_distance):
        """Collapse distance inferred from the segments themselves.

        Fully nested, a telescope is as long as its longest tube — so the longest segment's extent
        along the strut axis is the base-to-end distance at full retraction. Falls back to an even
        split of the build pose when the segments carry no geometry to measure.
        """
        longest = max((cls._axis_extent(s, direction) for s in segments), default=0.0)
        if longest <= 1e-6:
            longest = initial_distance / len(segments)
        return min(max(longest, initial_distance * 1e-3), initial_distance * 0.999)

    @staticmethod
    def _set_preconstraint_position(obj, world_position):
        """Place *obj*'s pre-constraint (basis) transform at *world_position*.

        ``Copy Location(use_offset=True)`` adds the owner's pre-constraint location, so that is
        where a maintained offset has to be written. Routed through the parent's matrix so a
        parented segment lands in the right place too.
        """
        if obj.parent is None:
            obj.location = world_position
        else:
            parent_matrix = obj.parent.matrix_world @ obj.matrix_parent_inverse
            obj.location = parent_matrix.inverted() @ world_position

    # ------------------------------------------------------------------ build
    @CoreUtils.undo_checkpoint
    def setup_telescope_rig(
        self,
        base_locator=None,
        end_locator=None,
        segments=None,
        collapsed_distance=None,
        aim_axis="y",
        lock_attributes=True,
        name="telescope",
    ):
        """Wire a telescoping rig between two handles.

        Parameters:
            base_locator (str/object/None): The base handle (an Empty or any object). ``None``
                creates one at the outer end of the first segment.
            end_locator (str/object/None): The end handle. ``None`` creates one at the outer end
                of the last segment.
            segments (list): Ordered segment objects (>= 2): the tube nearest the base first,
                the tube nearest the end last. Exactly two builds a sliding strut (no scaling —
                there is nothing between them).
            collapsed_distance (float/None): The base→end distance at which the segments are fully
                retracted; the driver clamps at or below it. ``None`` (the default) derives it from
                the longest segment's length along the aim axis — fully nested, the assembly is as
                long as its longest tube. Ignored for two-segment builds.
            aim_axis (str): The segments' long axis — "x", "y", or "z", optionally signed
                ("-y" …). The rig aims every segment along it, drives that axis' scale, locks the
                other two scale channels, and places auto handles along it (mirror of mayatk's
                ``aim_axis``).
            lock_attributes (bool): Lock the constraint-driven channels on every segment. The
                previous lock state is recorded so ``teardown`` restores it.
            name (str): Prefix for the nodes this build creates.

        Returns:
            TelescopeRigBundle: Names of everything created (also stored on ``self.bundle`` for
            ``teardown``, and stamped onto the base handle so a later session can recover it).

        Raises:
            ValueError: If the base/end handles are invalid, coincident, duplicated, fewer than
                two segments are provided, any of them already carries a telescope rig,
                ``aim_axis`` is not a signed x/y/z token, or an explicit ``collapsed_distance``
                falls outside the build pose.
        """
        import bpy

        self.logger.info("Setting up Telescope Rig...", preset="header")

        segs = [
            s
            for s in (RigUtils.resolve_object(o) for o in ptk.make_iterable(segments))
            if s is not None
        ]
        if len(segs) < 2:
            self.logger.error("At least two segments must be provided.")
            raise ValueError("At least two segments must be provided.")
        if len({s.name for s in segs}) != len(segs):
            raise ValueError("Duplicate segments provided.")

        track, reverse_track, scale_index, lock_scale, axis_vector = self._resolve_axis(
            aim_axis
        )

        base = RigUtils.resolve_object(base_locator) if base_locator is not None else None
        end = RigUtils.resolve_object(end_locator) if end_locator is not None else None
        if base_locator is not None and base is None:
            self.logger.error("A valid base handle must be provided.")
            raise ValueError("A valid base handle must be provided.")
        if end_locator is not None and end is None:
            self.logger.error("A valid end handle must be provided.")
            raise ValueError("A valid end handle must be provided.")
        if base is not None and base is end:
            raise ValueError("Base and end handles must be different objects.")
        if any(h is not None and h in segs for h in (base, end)):
            raise ValueError("Base/end handles cannot also be segments.")

        # Refuse to re-rig objects that already carry one. Unlike Maya there is no
        # "channel is already connected" pre-flight to fall back on here — constraints
        # and drivers just STACK — so without this a second Build silently double-rigs
        # and orphans the first bundle's record.
        existing = self.find_bundles([h for h in (base, end) if h is not None] + segs)
        if existing:
            names = ", ".join(sorted({b.name for b in existing}))
            msg = (
                f"These objects already carry a telescope rig ({names}); "
                f"remove it before building a new one."
            )
            self.logger.error(msg)
            raise ValueError(msg)

        bpy.context.view_layer.update()
        direction = self._chain_direction(segs, axis_vector)
        base_pos = (
            base.matrix_world.translation.copy()
            if base is not None
            else self._support_point(segs[0], direction, -1.0)
        )
        end_pos = (
            end.matrix_world.translation.copy()
            if end is not None
            else self._support_point(segs[-1], direction, 1.0)
        )
        initial_distance = (end_pos - base_pos).length
        if initial_distance < 1e-6:
            raise ValueError("The base and end handles must not be coincident.")

        # Two segments slide against each other — no interior to stretch, so the collapse
        # distance never enters the build.
        has_interiors = len(segs) > 2
        if not has_interiors:
            collapsed_distance = 0.0
        elif collapsed_distance is None:
            collapsed_distance = self._auto_collapsed_distance(
                segs, direction, initial_distance
            )
            self.logger.info(
                f"Collapsed distance (auto): <hl>{collapsed_distance:.4f}</hl>"
            )
        if has_interiors and not 0.0 < collapsed_distance < initial_distance:
            raise ValueError(
                f"collapsed_distance must be between 0 and the current base-to-end "
                f"distance ({initial_distance:.4f}); got {collapsed_distance}."
            )

        bundle = TelescopeRigBundle(
            name=str(name) or "telescope",
            base_locator=base.name if base is not None else "",
            end_locator=end.name if end is not None else "",
            segments=[s.name for s in segs],
            scale_index=scale_index,
            initial_distance=initial_distance,
            collapsed_distance=collapsed_distance,
        )

        try:
            handle_size = max(initial_distance * 0.05, 1e-3)
            if base is None:
                base = self._create_handle(
                    f"{bundle.name}_base_LOC", base_pos, handle_size, bundle
                )
                bundle.base_locator = base.name
            if end is None:
                end = self._create_handle(
                    f"{bundle.name}_end_LOC", end_pos, handle_size, bundle
                )
                bundle.end_locator = end.name
            self._build(
                bundle,
                base,
                end,
                segs,
                base_pos,
                end_pos,
                track,
                reverse_track,
                lock_scale,
                lock_attributes,
                has_interiors,
            )
        except Exception:
            # A validated build can still die on exotic scene state — never leave a half-wired
            # rig behind.
            self.logger.error("Build failed — rolling back partially created rig nodes.")
            self._delete_bundle_nodes(bundle, restore=True)
            raise

        self._stamp(bundle)
        self.bundle = bundle
        self.logger.success("Telescope Rig setup complete.")
        return bundle

    def _create_handle(self, name, position, size, bundle):
        """Create one auto handle (Empty), recording it on *bundle* for teardown."""
        locator = RigUtils.create_locator(name, location=position, size=size)
        bundle.created_locators.append(locator.name)
        self.logger.info(f"Created handle: <hl>{locator.name}</hl>")
        return locator

    def _build(
        self,
        bundle,
        base,
        end,
        segs,
        base_pos,
        end_pos,
        track,
        reverse_track,
        lock_scale,
        lock_attributes,
        has_interiors,
    ):
        """Create the constraints/drivers, recording each into *bundle* as it appears."""
        import bpy

        def record(obj, constraint):
            bundle.constraints.append([obj.name, constraint.name])
            return constraint

        # Handles aim at each other so the chain keeps a consistent up-axis.
        record(base, RigUtils.damped_track(base, end, track))
        record(end, RigUtils.damped_track(end, base, reverse_track))
        self.logger.info("Locators constrained.")

        # Build-pose world positions must be read BEFORE any segment constraint exists —
        # afterwards matrix_world reports the constrained result.
        bpy.context.view_layer.update()
        poses = [s.matrix_world.translation.copy() for s in segs]

        last = len(segs) - 1
        for k, seg in enumerate(segs):
            frac = k / last
            bundle.original_locations[seg.name] = list(seg.location)
            if frac == 0.0 or frac == 1.0:
                # End segments ride their handle exactly, offset preserved by the Child Of
                # inverse — Maya's parentConstraint(mo=True). Scale channels off: a Maya parent
                # constraint drives translate/rotate only.
                handle = base if frac == 0.0 else end
                record(
                    seg,
                    RigUtils.child_of(
                        seg,
                        handle,
                        use_scale_x=False,
                        use_scale_y=False,
                        use_scale_z=False,
                    ),
                )
                continue

            # Interior = lerp(base, end, frac) plus the constant offset that keeps the build
            # pose. Copy Location(use_offset) adds the owner's PRE-constraint location, and the
            # second constraint blends from the first's result, so the offset that satisfies
            # (1-frac)*(base + o) + frac*end == pose is o = (pose - frac*end)/(1-frac) - base.
            # It is ~0 for a segment already near its ideal position; teardown restores the
            # object's own location either way.
            offset = (poses[k] - end_pos * frac) / (1.0 - frac) - base_pos
            self._set_preconstraint_position(seg, offset)
            record(seg, RigUtils.copy_location(seg, base, 1.0, use_offset=True))
            record(seg, RigUtils.copy_location(seg, end, frac))
            record(seg, RigUtils.damped_track(seg, end, track))
        self.logger.info("Segments constrained.")

        # Middle segments telescope: the aim axis' scale tracks the live base->end distance,
        # clamped at the collapsed distance (Blender's continuous-driver analogue of Maya's
        # two-key driven curve + constant pre-infinity).
        if has_interiors:
            index = bundle.scale_index
            for k, seg in enumerate(segs):
                if not 0 < k < last:
                    continue
                build_scale = seg.scale[index]
                bundle.original_scales[seg.name] = build_scale
                RigUtils.add_distance_driver(
                    seg,
                    "scale",
                    index,
                    base,
                    end,
                    expression=(
                        f"{build_scale!r} * max(dist, {bundle.collapsed_distance!r})"
                        f" / {bundle.initial_distance!r}"
                    ),
                )
                bundle.drivers.append([seg.name, "scale", index])
            self.logger.info("Driven keys set.")
        else:
            self.logger.info("Two segments — sliding strut (no interior to scale).")

        # Location & rotation are constraint-driven; only the aim axis' scale telescopes.
        if lock_attributes:
            for seg in segs:
                bundle.prior_locks[seg.name] = [
                    list(seg.lock_location),
                    list(seg.lock_rotation),
                    list(seg.lock_scale),
                ]
                RigUtils.lock_channels(
                    seg,
                    location=(True, True, True),
                    rotation=(True, True, True),
                    scale=lock_scale,
                )

        RigUtils.refresh_drivers(segs)  # post-build recompile (script-built driver gotcha)

    # ------------------------------- scene persistence (recover a bundle later)
    def _stamp(self, bundle):
        """Record *bundle* as JSON on its base handle.

        Without this the build record only lives on the Python instance, so reopening the panel
        (or the .blend) makes ``teardown`` unreachable and the rig has to be picked apart by hand.
        """
        base = RigUtils.resolve_object(bundle.base_locator)
        if base is None:
            return
        base[self._DATA_KEY] = bundle.to_json()

    @classmethod
    def scene_bundles(cls):
        """Every telescope-rig bundle stamped into the current .blend."""
        import bpy

        found = []
        for obj in bpy.data.objects:
            payload = obj.get(cls._DATA_KEY)
            if not payload:
                continue
            try:
                found.append(TelescopeRigBundle.from_json(payload))
            except (ValueError, TypeError):
                continue
        return found

    @classmethod
    def find_bundles(cls, objects):
        """Bundles whose handles or segments intersect *objects*."""
        wanted = {
            o.name
            for o in (RigUtils.resolve_object(x) for x in ptk.make_iterable(objects))
            if o is not None
        }
        if not wanted:
            return []
        return [
            b
            for b in cls.scene_bundles()
            if wanted & {b.base_locator, b.end_locator, *b.segments}
        ]

    # ------------------------------------------------------------------ teardown
    def _delete_bundle_nodes(self, bundle, restore=True):
        """Remove every constraint/driver *bundle* records; optionally restore the state it
        overwrote (locks, the segments' own location/scale)."""
        import bpy

        for obj_name, constraint_name in bundle.constraints:
            obj = bpy.data.objects.get(obj_name)
            constraint = obj and obj.constraints.get(constraint_name)
            if constraint is not None:
                obj.constraints.remove(constraint)
        for obj_name, data_path, index in bundle.drivers:
            obj = bpy.data.objects.get(obj_name)
            if obj is not None:
                RigUtils.remove_driver(obj, data_path, index)

        if restore:
            for obj_name, locks in bundle.prior_locks.items():
                obj = bpy.data.objects.get(obj_name)
                if obj is not None:
                    RigUtils.lock_channels(obj, *[tuple(v) for v in locks])
            for obj_name, location in bundle.original_locations.items():
                obj = bpy.data.objects.get(obj_name)
                if obj is not None:
                    obj.location = location
            for obj_name, value in bundle.original_scales.items():
                obj = bpy.data.objects.get(obj_name)
                if obj is not None:
                    obj.scale[bundle.scale_index] = value

        # Drop the stamp before the handle goes, so a surviving user handle isn't left
        # advertising a rig that no longer exists.
        base = bpy.data.objects.get(bundle.base_locator)
        if base is not None and self._DATA_KEY in base:
            del base[self._DATA_KEY]

        # Handles the BUILD created are rig nodes; user handles are not.
        for name in bundle.created_locators:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)

    @CoreUtils.undo_checkpoint
    def teardown(self, bundle=None):
        """Remove a telescope rig built by this class.

        Deletes the constraints, scale drivers, and any handles the build itself created; restores
        the channel locks it changed and the segments' build-pose location/scale. User-supplied
        handles and the segment objects are left in place.

        Parameters:
            bundle (TelescopeRigBundle): The build record to remove. Defaults to the most recent
                build on this instance.

        Returns:
            bool: True when a bundle was torn down, False when there was nothing to do.
        """
        bundle = bundle or self.bundle
        if bundle is None:
            self.logger.warning("No telescope rig bundle to tear down.")
            return False
        self.logger.info("Removing Telescope Rig...", preset="header")
        self._delete_bundle_nodes(bundle, restore=True)
        if bundle is self.bundle:
            self.bundle = None
        self.logger.success("Telescope Rig removed.")
        return True


class TelescopeRigSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Telescope Rig panel.

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on
    tentacle; the Qt-only ``uitk`` helper is deferred into ``header_init``.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.telescope_rig
        self.bundle = None  # most recent build, for a selection-less Remove

        # Setup Logging Redirect. Best-effort: a mock/headless switchboard may not carry
        # ``registered_widgets.TextEditLogHandler`` — the panel still works without it.
        self._mirror_engine_log(self)
        self.logger.info("Telescope Rig Tool initialized.", preset="italic")

        # Connect clickable log links (action:// URIs in QTextBrowser)
        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

        # Connect Signals
        self.ui.btn_build.clicked.connect(self.build_rig)
        self.ui.btn_remove.clicked.connect(self.remove_rig)

        self._init_tooltips()

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def _mirror_engine_log(self, owner) -> None:
        """Stream *owner*'s log into this panel's browser (no-op on a stub switchboard)."""
        try:
            owner.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            owner.logger.setup_logging_redirect(self.ui.txt003)
        except (AttributeError, TypeError):
            pass

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Telescope Rig",
                body="Build a telescoping segment chain where segments extend "
                "and retract between a base and end handle, driven by their "
                "distance.",
                steps=[
                    "Select the segments — <b>at least 2</b>. Empties in the "
                    "selection are treated as handles, everything else as a "
                    "segment.",
                    "Make the <b>base</b> end of the strut <b>active</b> — "
                    "Blender doesn't preserve click-order, so the segments are "
                    "ordered by distance from the active object.",
                    "Leave <b>Collapsed Dist</b> on <i>Auto</i> to measure the "
                    "longest segment, or enter the base→end distance at full "
                    "retraction.",
                    "Press <b>Build</b> to wire the constraints + scale driver "
                    "on each segment.",
                ],
                notes=[
                    "Two segments builds a sliding strut — the halves ride "
                    "their handles and never stretch.",
                    "Handles you don't supply are created for you at the outer "
                    "ends of the strut.",
                    "<b>Remove</b> tears down the rig on the selection (or the "
                    "last one built), including any handles it created.",
                    "Build results stream to the log panel; handle names are "
                    "rendered as clickable <i>action://</i> links that select "
                    "the object in the viewport.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and action."""
        try:
            fmt = self.sb.tooltip.fmt
            ui = self.ui
        except AttributeError:  # stub/headless switchboard
            return

        ui.cmb_axis.setToolTip(
            fmt(
                title="Aim Axis",
                body="The segments' long axis — the local axis that points "
                "from the base toward the end handle. The rig aims every "
                "segment along it and drives that axis' scale.",
                notes=[
                    "The other two scale channels are locked at build time "
                    "so the stack can't shear.",
                    "Auto-created handles are placed along this axis, at the "
                    "outer ends of the first and last segment.",
                ],
            )
        )
        ui.spin_collapsed.setToolTip(
            fmt(
                title="Collapsed Distance",
                body="Base-to-end distance at which the segments are fully "
                "retracted (nested). As the end handle pulls farther than "
                "this, the segments slide apart to bridge the gap.",
                notes=[
                    "<b>Auto</b> (0) measures the longest segment along the "
                    "aim axis — fully nested, the strut is as long as its "
                    "longest tube.",
                    "Pushing closer than this distance clamps the segments "
                    "at their fully-nested size.",
                    "Ignored for a two-segment strut — there is no interior "
                    "segment to scale.",
                ],
            )
        )
        ui.btn_build.setToolTip(
            fmt(
                title="Build Telescope Rig",
                body="Wires constraints and a scale driver onto each segment "
                "so they extend and retract as the gap between the base and "
                "end handles changes.",
                steps=[
                    "Select the <b>segments</b> <i>(min 2)</i>, with the base "
                    "end <b>active</b>.",
                    "Optionally include Empties as the <b>base</b>/<b>end</b> "
                    "handles — either one you omit is created for you.",
                    "Press <b>Build Telescope Rig</b>.",
                ],
                notes=[
                    "Needs at least 2 objects: two segments is a sliding "
                    "strut, three or more telescope.",
                ],
            )
        )
        ui.btn_remove.setToolTip(
            fmt(
                title="Remove Telescope Rig",
                body="Deletes the constraints and scale drivers the build "
                "created, restores the channel locks it changed, and puts the "
                "segments back on their build-pose location and scale.",
                steps=[
                    "Select any part of the rig — a handle or a segment.",
                    "Press <b>Remove Telescope Rig</b>.",
                ],
                notes=[
                    "Handles the build created are deleted; handles you "
                    "supplied are kept.",
                    "The build record is stamped on the base handle, so a rig "
                    "from an earlier session still tears down cleanly.",
                ],
            )
        )

    def _partition_selection(self, sel, active):
        """Split a Blender selection into (base_handle, segments, end_handle).

        Blender has no reliable click-order, so roles come from type + position instead of
        selection index: Empties are handles, everything else is a segment, segments are ordered
        by distance from the *active* object (the base end of the strut), and a lone handle is
        assigned to whichever end of that chain it sits nearer.
        """
        handles = [o for o in sel if o.type == "EMPTY"]
        segments = [o for o in sel if o.type != "EMPTY"]
        if len(segments) < 2:
            return None, segments, None

        anchor = (active or sel[0]).matrix_world.translation
        # Ties (a nested strut, where every segment shares an origin) fall back to the longest
        # tube first — the outer sleeve is always the base of a telescope.
        segments.sort(
            key=lambda o: (
                round((o.matrix_world.translation - anchor).length, 4),
                -max(o.dimensions),
            )
        )

        base = end = None
        if len(handles) >= 2:
            first = segments[0].matrix_world.translation
            handles.sort(key=lambda o: (o.matrix_world.translation - first).length)
            base, end = handles[0], handles[-1]
            if active in handles and active is not base:  # explicit beats inferred
                base, end = end, base
        elif len(handles) == 1:
            handle = handles[0]
            position = handle.matrix_world.translation
            to_first = (position - segments[0].matrix_world.translation).length
            to_last = (position - segments[-1].matrix_world.translation).length
            if handle is active or to_first <= to_last:
                base = handle
            else:
                end = handle
        return base, segments, end

    def build_rig(self):
        self.logger.log_divider()

        import bpy

        sel = CoreUtils.selected_objects()
        bpy.context.view_layer.update()
        active = bpy.context.view_layer.objects.active
        base, segments, end = (
            self._partition_selection(sel, active if active in sel else None)
            if sel
            else (None, [], None)
        )
        if len(segments) < 2:
            self.logger.error("Insufficient selection.")
            self.sb.message_box(
                "Selection Error:\n"
                "Select at least 2 segments, with the BASE end ACTIVE:\n"
                "1. Segments (min 2) — ordered by distance from the active object\n"
                "2. Base/End handles (optional Empties — created if omitted)"
            )
            return

        collapsed_dist = self.ui.spin_collapsed.value() or None
        aim_axis = ("x", "y", "z")[self.ui.cmb_axis.currentIndex()]

        try:
            rig = TelescopeRig()
            # ``logger`` is a ClassProperty (no setter) — configure it, never reassign it.
            self._mirror_engine_log(rig)

            for role, obj in (("Base", base), ("End", end)):
                if obj is None:
                    self.logger.info(f"{role} detected: <hl>auto</hl>")
                else:
                    link = self.logger.log_link(obj.name, "select", node=obj.name)
                    self.logger.info(f"{role} detected: {link}")
            self.logger.info(
                f"Segments detected: <hl>{len(segments)}</hl> "
                f"(aim axis: <hl>{aim_axis.upper()}</hl>)"
            )

            self.bundle = rig.setup_telescope_rig(
                base_locator=base,
                end_locator=end,
                segments=segments,
                collapsed_distance=collapsed_dist,
                aim_axis=aim_axis,
            )
        except Exception as e:
            self.logger.error(f"Error setting up rig: {str(e)}")
            self.sb.message_box(f"Error setting up rig: {str(e)}")

    def remove_rig(self):
        self.logger.log_divider()

        sel = CoreUtils.selected_objects()
        if sel:
            bundles = TelescopeRig.find_bundles(sel)
            empty_msg = "No telescope rig found on the selected objects."
        else:
            bundles = [self.bundle] if self.bundle else []
            empty_msg = (
                "Nothing selected and no rig built this session.\n"
                "Select a rig handle or segment and try again."
            )
        if not bundles:
            self.logger.error(empty_msg.splitlines()[0])
            self.sb.message_box(empty_msg)
            return

        try:
            rig = TelescopeRig()
            self._mirror_engine_log(rig)
            for bundle in bundles:
                rig.teardown(bundle)
                if bundle is self.bundle:
                    self.bundle = None
        except Exception as e:
            self.logger.error(f"Error removing rig: {str(e)}")
            self.sb.message_box(f"Error removing rig: {str(e)}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("telescope_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
