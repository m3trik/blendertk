# !/usr/bin/python
# coding=utf-8
"""Render Effects — Blender per-object render-effect channels for engine-ready control (mirror of
mayatk's ``mat_utils.render_opacity.RenderEffects``: ``btk.RenderEffects`` ↔ ``mtk.RenderEffects``;
``RenderOpacity`` is the previous name, kept one release).

Adds a keyable custom property per channel to objects — ``opacity`` (0-1) and ``highlight`` with its
colour ramp — the channels the deliverables carry. ``key_fade`` animates a fade and, for opacity,
**mirrors it onto the object's render visibility** (stepped, hidden when opacity ≤ 0), as mayatk
mirrors it onto ``visibility``. The exports read the authored channels, not that mirror: for an FBX
write ``prepare_for_export`` stages one transient curve proxy per keyed channel (a child
``<object>__<channel>`` whose ``scale.x`` carries the curve; ``finish_export`` removes them), which
Unity's ``RenderEffectsImporter`` rebinds -- it reconstructs a fade from the visibility mirror only
for a file exported before that transport existed -- and the GLB derives presence from the authored
channels (``ptk.MeshConvert.apply_glb_visibility``). ``prepare_for_export`` writes no visibility mirror.

**Divergences from Maya (documented, not reductions):**
  - **No attribute-vs-material split.** Maya's "material" mode also binds the material for viewport
    lookdev; the material drivers that did that here were retired (2026-09-05), so ``"material"`` is
    the attribute mode with a warning for one release.
  - **No transform/shape split & no ``visibility`` attr.** The visibility analogue is the object's
    ``hide_render`` (render visibility), keyed stepped.

``import bpy`` is deferred into the call bodies so the module resolves headless / under the .venv.
"""

from typing import Optional

import pythontk as ptk


class RenderEffects(ptk.LoggingMixin):
    """Per-object render-effect channels: the keyable ``opacity`` prop (mirrored
    to render visibility) and the ``highlight`` prop with its colour."""

    ATTR_NAME = "opacity"
    #: The highlight channel: an additive emissive intensity (0-1) plus its
    #: colour, both custom properties on the object. Mirror of mayatk's
    #: ``channels.HIGHLIGHT`` row; the GLB half lives in pythontk's table.
    HIGHLIGHT_ATTR = "highlight"
    #: The two ends of the highlight's colour ramp: the property read at
    #: intensity 1 and the one read at 0. Mirror of mayatk's
    #: ``HIGHLIGHT.color_stops``; the published keys the GLB carrier expects
    #: are pythontk's, and :data:`HIGHLIGHT_TRACK_STOPS` states them so the two
    #: spellings of one concept sit side by side rather than in two files.
    HIGHLIGHT_COLOR_STOPS = ptk.ColorStops("highlightColor", "highlightColorDim")
    HIGHLIGHT_TRACK_STOPS = ptk.ColorStops("highlight_color", "highlight_color_dim")
    #: Deprecated read-through of the BRIGHT end, kept for one release.
    HIGHLIGHT_COLOR_ATTR = HIGHLIGHT_COLOR_STOPS.hi
    CHANNELS = ("opacity", "highlight")
    #: The fcurve data paths of every render-effect property (the channels
    #: and both ends of the highlight colour) -- the mirror of mayatk's
    #: ``ChannelSpec.attrs``, what the shot system reads as content beside the
    #: transform channels.
    PROP_PATHS = tuple(f'["{n}"]' for n in CHANNELS + HIGHLIGHT_COLOR_STOPS.keys)
    #: Custom property stamping a transient curve-proxy Empty
    #: (``ptk.MeshConvert.CURVE_PROXY_MARKER``): the GLB conversion strips
    #: nodes carrying it and the Unity importer rebinds and deletes them.
    PROXY_MARKER = ptk.MeshConvert.CURVE_PROXY_MARKER
    PROXY_SEPARATOR = "__"
    # The render-visibility channel mirrored from opacity (Blender's m_Enabled analogue).
    VIS_PATH = "hide_render"

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _resolve(objects):
        import bpy

        if objects is None:
            return [
                o for o in (getattr(bpy.context, "selected_objects", None) or []) if o
            ]
        out = []
        for o in objects:
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            if obj is not None:
                out.append(obj)
        return out

    @classmethod
    def _ensure_opacity_prop(cls, obj, value=1.0):
        """Seed the keyable ``opacity`` custom property (0-1) with UI limits if absent."""
        if cls.ATTR_NAME not in obj:
            obj[cls.ATTR_NAME] = float(value)
        try:
            obj.id_properties_ui(cls.ATTR_NAME).update(
                min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, default=1.0
            )
        except (AttributeError, TypeError, KeyError):
            pass

    @staticmethod
    def _fcurve(obj, data_path, index=-1):
        """*obj*'s fcurve for *data_path* (slot-aware via the shared anim_utils helper — Blender
        4.4+/5.x drop the legacy flat ``action.fcurves``)."""
        from blendertk.anim_utils._anim_utils import AnimUtils

        for fc in AnimUtils.get_fcurves([obj]):
            if fc.data_path == data_path and (index < 0 or fc.array_index == index):
                return fc
        return None

    @staticmethod
    def _remove_fc(obj, fc):
        """Remove fcurve *fc* from *obj*'s action (slot-aware, via the shared anim_utils helper)."""
        from blendertk.anim_utils._anim_utils import AnimUtils

        ad = getattr(obj, "animation_data", None)
        if ad and ad.action is not None and fc is not None:
            try:
                AnimUtils._remove_fcurve(
                    ad.action, getattr(ad, "action_slot", None), fc
                )
            except (RuntimeError, ReferenceError, ValueError):
                pass

    # ------------------------------------------------------------ hand-off transfer
    # Mirror of mayatk's ``channel_records`` / ``apply_channel_records``: neither
    # carrier animates a custom property, so the shot store's transfer carries
    # these records (``ptk.ShotTransfer``'s ``channels`` payload). Labels are
    # mayatk's attribute spelling -- a vector property's components as leaves
    # (``highlightColorR``) -- so one vocabulary serves both directions.

    #: Blender interpolation -> the transfer's; anything else is ``"smooth"``.
    _INTERP_FROM_BLENDER = {"CONSTANT": "step", "LINEAR": "linear"}
    _BLENDER_FROM_INTERP = {"step": "CONSTANT", "linear": "LINEAR", "smooth": "BEZIER"}

    @classmethod
    def _axes_for(cls, obj, prop: str) -> str:
        """The leaf letters a vector property's components travel under."""
        if prop in cls.HIGHLIGHT_COLOR_STOPS.keys:
            return "RGB"
        try:
            subtype = obj.id_properties_ui(prop).as_dict().get("subtype")
        except (AttributeError, TypeError, KeyError):
            subtype = None
        return "RGB" if subtype in ("COLOR", "COLOR_GAMMA") else "XYZ"

    @classmethod
    def _fc_keys(cls, fc) -> list:
        """``[[frame, value, interpolation], ...]`` for *fc* (``[]`` for none)."""
        if fc is None:
            return []
        return [
            [
                float(k.co[0]),
                float(k.co[1]),
                cls._INTERP_FROM_BLENDER.get(getattr(k, "interpolation", ""), "smooth"),
            ]
            for k in fc.keyframe_points
        ]

    @classmethod
    def channel_records(cls, objects=None) -> dict:
        """``{object name: {label: {"value", "keys"}}}`` -- every render-effect
        property *objects* carry (keyed or not) and every other keyed numeric
        custom property, a key being ``[frame, value, interpolation]``.

        Parameters:
            objects: The objects to read (``None`` = every object).
        """
        import bpy
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        declared = tuple(cls.CHANNELS) + tuple(cls.HIGHLIGHT_COLOR_STOPS.keys)
        objs = cls._resolve(objects) if objects is not None else list(bpy.data.objects)
        out: dict = {}
        for obj in objs:
            if obj.get(cls.PROXY_MARKER):
                continue
            fcurves: dict = {}
            if getattr(obj, "animation_data", None):
                for fc in BlenderShotStore.iter_action_fcurves(obj):
                    path = fc.data_path
                    if path.startswith('["') and path.endswith('"]'):
                        fcurves.setdefault(path[2:-2], {})[fc.array_index] = fc
            records: dict = {}
            for prop in dict.fromkeys(list(declared) + list(fcurves)):
                if prop not in obj.keys() or prop == cls.PROXY_MARKER:
                    continue
                value = obj[prop]
                curves = fcurves.get(prop, {})
                if not curves and prop not in declared:
                    continue
                if isinstance(value, (int, float)):
                    fc = curves.get(0, curves.get(-1))
                    records[prop] = {"value": float(value), "keys": cls._fc_keys(fc)}
                    continue
                try:
                    components = [float(c) for c in value]
                except (TypeError, ValueError):
                    continue  # a string or a nested property: not a channel
                axes = cls._axes_for(obj, prop)
                for index, component in enumerate(components[: len(axes)]):
                    records[f"{prop}{axes[index]}"] = {
                        "value": component,
                        "keys": cls._fc_keys(curves.get(index)),
                    }
            if records:
                out[obj.name] = records
        return out

    @classmethod
    def _channel_slot(cls, obj, label: str) -> tuple:
        """``(property, array index)`` a transfer *label* lands on for *obj*."""
        if label in cls.CHANNELS:
            return label, -1
        stem, suffix = label[:-1], label[-1:]
        if stem in cls.HIGHLIGHT_COLOR_STOPS.keys and suffix in "RGB":
            return stem, "RGB".index(suffix)
        if stem in obj.keys() and not isinstance(obj[stem], (int, float, str)):
            axes = cls._axes_for(obj, stem)
            if suffix in axes:
                return stem, axes.index(suffix)
        return label, -1

    @staticmethod
    def _set_prop(obj, prop: str, index: int, value: float) -> None:
        """Set a custom property, or one component of a vector one."""
        if index < 0:
            obj[prop] = value
            return
        current = obj.get(prop)
        try:
            components = [float(c) for c in current]
        except TypeError:
            components = []
        components += [0.0] * (index + 1 - len(components))
        components[index] = float(value)
        obj[prop] = components

    @classmethod
    def apply_channel_records(cls, obj_name, records: dict) -> int:
        """Land :meth:`channel_records` records on the object; returns the
        properties written (mirror of mayatk's).

        A declared channel is seeded through its own ensurer (limits and the
        colour ramp are the channel's); any other label lands as a float, or
        as one component of an existing vector property. Frames must already
        be on the scene's clock.
        """
        import bpy

        obj = bpy.data.objects.get(obj_name) if isinstance(obj_name, str) else obj_name
        if obj is None:
            return 0
        written = 0
        for label, rec in (records or {}).items():
            prop, index = cls._channel_slot(obj, label)
            if prop in cls.CHANNELS or prop in cls.HIGHLIGHT_COLOR_STOPS.keys:
                channel = cls.ATTR_NAME if prop == cls.ATTR_NAME else cls.HIGHLIGHT_ATTR
                cls._ensure_channel([obj], channel, True, None, False)
            elif prop not in obj.keys():
                obj[prop] = 0.0 if index < 0 else [0.0, 0.0, 0.0]
            value = rec.get("value")
            if value is not None:
                cls._set_prop(obj, prop, index, float(value))
            data_path = f'["{prop}"]'
            for key in rec.get("keys") or []:
                try:
                    frame, val = float(key[0]), float(key[1])
                except (TypeError, ValueError, IndexError):
                    continue
                interp = cls._BLENDER_FROM_INTERP.get(
                    key[2] if len(key) > 2 else "smooth", "BEZIER"
                )
                cls._set_key(obj, data_path, frame, val, interp, index=index)
            written += 1
        return written

    @classmethod
    def objects_with_visibility_keys(cls, objects) -> list:
        """The subset of *objects* that already have keyframes on render visibility."""
        return [
            o for o in cls._resolve(objects) if cls._fcurve(o, cls.VIS_PATH) is not None
        ]

    # ------------------------------------------------------------------ create / remove
    @classmethod
    def create(
        cls,
        objects=None,
        mode: str = "attribute",
        delete_visibility_keys: bool = False,
        channel: str = "opacity",
    ):
        """Add the channel's prop to *objects* (or remove it).

        ``mode`` mirrors mayatk: ``"attribute"`` adds the prop, ``"remove"``
        delegates to :meth:`remove`, and ``"material"`` is DEPRECATED (2026-09-05,
        one release) -- the material drivers that showed the channel in the
        viewport copied a shared material per object and were retired; it is
        the attribute mode with a warning. Objects with existing visibility keys
        are skipped with a warning unless *delete_visibility_keys* is True.
        """

        objects = cls._resolve(objects)
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        vis_keyed = cls.objects_with_visibility_keys(objects)
        if vis_keyed:
            names = [o.name for o in vis_keyed]
            if delete_visibility_keys:
                for o in vis_keyed:
                    cls._remove_fc(o, cls._fcurve(o, cls.VIS_PATH))
                    o.hide_render = False
                cls.logger.info("Deleted visibility keys on: %s", ", ".join(names))
            else:
                raise RuntimeError(
                    f"Visibility keys found on: {', '.join(names)}. Enable 'Delete Visibility "
                    "Keys' or remove them manually before applying opacity."
                )

        if mode == "material":
            cls._warn_preview_retired()
        cls.remove(objects)  # always clean prior state first (legacy drivers too)
        if mode == "remove":
            return {}

        results = {}
        for obj in objects:
            if channel == cls.HIGHLIGHT_ATTR:
                cls._ensure_highlight_props(obj)
            else:
                cls._ensure_opacity_prop(obj, 1.0)
            results[obj.name] = {channel: True}
        return results

    @classmethod
    def _warn_preview_retired(cls):
        """One line, once per session: the in-scene preview is gone, and why."""
        if getattr(cls, "_preview_warned", False):
            return
        cls._preview_warned = True
        cls.logger.warning(
            "The viewport material preview was retired (2026-09-05): it copied "
            "the authored material per object and cost every export a restore "
            "step. Keys are written as before; preview the deliverable with the "
            "WebXR push."
        )

    @classmethod
    def _ensure_highlight_props(
        cls, obj, color=(0.0, 0.088656, 0.723055), dim_color=(0.0, 0.0, 0.0)
    ):
        """Seed ``highlight`` (0-1, keyable) and both ends of its colour ramp.

        The bright end is LINEAR light: the linear value OF #0054DD, which
        is what the 8-bit colour editor hands back for it, matching mayatk's
        ``highlight`` attribute preset and the panel's seed.

        The dim end seeds BLACK on purpose: the published ramp rides between
        the two ends, so a black low end collapses it to the one-colour shape
        this channel had before the end existed.
        """
        if cls.HIGHLIGHT_ATTR not in obj:
            obj[cls.HIGHLIGHT_ATTR] = 0.0
        try:
            obj.id_properties_ui(cls.HIGHLIGHT_ATTR).update(
                min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, default=0.0
            )
        except (AttributeError, TypeError, KeyError):
            pass
        for prop, value in zip(cls.HIGHLIGHT_COLOR_STOPS.keys, (color, dim_color)):
            if prop in obj:
                continue
            obj[prop] = [float(c) for c in value[:3]]
            try:
                obj.id_properties_ui(prop).update(subtype="COLOR", min=0.0, max=1.0)
            except (AttributeError, TypeError, KeyError):
                pass

    @classmethod
    def _remove_emission_drivers(cls, obj):
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        for mat in getattr(obj.data, "materials", None) or []:
            node = _MatUtilsInternal._principled_node(mat) if mat else None
            if node is None:
                continue
            for name, indices in (
                ("Emission Strength", (-1,)),
                ("Emission Color", (0, 1, 2)),
            ):
                socket = node.inputs.get(name)
                if socket is None:
                    continue
                path = socket.path_from_id("default_value")
                for index in indices:
                    try:
                        if index >= 0:
                            mat.node_tree.driver_remove(path, index)
                        else:
                            mat.node_tree.driver_remove(path)
                    except (TypeError, RuntimeError):
                        pass

    @classmethod
    def key_pulse(
        cls,
        objects=None,
        start=0,
        end=100,
        period=86,
        bright_fraction=0.59,
        ramp_fraction=0.25,
        lead_in=None,
        lead_out=None,
        color=None,
        dim_color=None,
        auto_create=True,
        channel="highlight",
        preview=None,
        delete_visibility_keys=False,
        whole_frames=True,
    ):
        """Key a repeating bright/dim pulse on the highlight prop over ``start..end``.

        Mirror of mayatk's ``RenderEffects.key_pulse``: four LINEAR keys per
        cycle (bright hold, ramp down, dim hold, ramp up), because the published
        ramp is read linearly. The defaults are the cadence measured on the
        WebXR reference at 30 fps. Channel creation is owned here too (see
        :meth:`_ensure_channel`). *preview* is DEPRECATED and ignored (one
        release): the driver preview was retired 2026-09-05.

        The train is bracketed by dim keys at *start* and *end*, because an
        F-Curve holds its first key value backwards and its last forwards: a
        pulse that merely BEGAN bright glowed for the whole timeline before it.
        *lead_in* / *lead_out* are the frames each bracket ramp takes; None
        takes the cycle's own ramp, so the ends match every interior
        transition, and 0 cuts as hard as the floor allows (one frame
        under *whole_frames*).

        *whole_frames* (the default) snaps every key to a whole frame and
        widens the brackets to :attr:`WHOLE_FRAME_GAP_MIN`; the cycle itself
        still advances by the exact *period*, so the cadence is kept. Mirror of
        mayatk's twin.

        Returns the keyed objects' names.
        """
        objects = cls._resolve(objects)
        # Planned once, host-free (``ptk.RampKeys.pulse``) -- the plan mayatk's
        # writer keys and the WebXR preview publishes without keying.
        plan = ptk.RampKeys.pulse(
            start,
            end,
            period,
            bright_fraction=bright_fraction,
            ramp_fraction=ramp_fraction,
            lead_in=lead_in,
            lead_out=lead_out,
            whole_frames=whole_frames,
        )
        if not objects or not plan:
            return []
        cls._ensure_channel(
            objects, cls.HIGHLIGHT_ATTR, auto_create, preview, delete_visibility_keys
        )
        start, end = plan[0][0], plan[-1][0]
        path = f'["{cls.HIGHLIGHT_ATTR}"]'
        keyed = []
        for obj in objects:
            if cls.HIGHLIGHT_ATTR not in obj:
                continue
            fc = cls._fcurve(obj, path)
            if fc is not None:
                # Highest index first, re-fetched each time: a removal shifts the
                # array, so a held KeyframePoint reference goes stale (RuntimeError
                # 'Keyframe not in F-Curve' when re-keying over an existing pulse).
                hits = [
                    i
                    for i, k in enumerate(fc.keyframe_points)
                    if start <= k.co[0] <= end
                ]
                for i in reversed(hits):
                    fc.keyframe_points.remove(fc.keyframe_points[i])
            for frame, value in plan:
                cls._set_key(obj, path, frame, value, "LINEAR")
            for stop, value in (("hi", color), ("lo", dim_color)):
                if value is None:
                    continue
                obj[cls._color_attr(channel, stop)] = [float(c) for c in value[:3]]
            keyed.append(obj.name)
        return keyed

    @classmethod
    def preview_channels(
        cls, objects, channel="highlight", keys=(), colors=None, fps=None
    ) -> dict:
        """The WebXR-push overlay that previews one effect on *objects* at *keys*.

        Mirror of mayatk's ``RenderEffects.preview_channels``: nothing is
        created, keyed or coloured. The objects' names go into
        ``ptk.MeshConvert.effect_preview_channels``, and the result goes to
        ``WebXrPreview.push(data_export=...)``, which builds the GLB as if these
        keys had been authored -- with whatever the objects already carry left
        out, so the page shows this effect alone.

        Parameters:
            objects: Objects or their names.
            channel: ``"opacity"`` or ``"highlight"``.
            keys: ``[(frame, value), ...]`` -- a ``ptk.RampKeys`` plan.
            colors: ``(bright, dim)`` for the highlight; ``None`` in either
                place leaves that end to the reader's default.
            fps: The rate *keys* are quoted in; the scene's when ``None``.

        Raises:
            KeyError: For a channel that is not a render-effect channel.
            ValueError: No object resolved, or fewer than two keys.
        """
        if channel not in cls.CHANNELS:
            raise KeyError(
                f"Unknown render-effect channel {channel!r}. "
                f"Known: {', '.join(cls.CHANNELS)}."
            )
        return ptk.MeshConvert.effect_preview_channels(
            [obj.name for obj in cls._resolve(list(objects or []))],
            channel,
            keys,
            colors=colors,
            fps=fps or cls._scene_fps() or 30.0,
        )

    # ------------------------------------------------------------------ colour

    @classmethod
    def _color_attr(cls, channel, stop: str = "hi") -> str:
        """The property holding one end of *channel*'s colour ramp.

        Parameters:
            channel: The channel name; ``"highlight"``.
            stop: ``"hi"`` (the colour at intensity 1) or ``"lo"`` (at 0).

        Raises:
            KeyError: For a channel that is not a render-effect channel.
            ValueError: For one that owns no colour, or an unknown *stop*.
        """
        if stop not in ("hi", "lo"):
            raise ValueError(f"Unknown colour stop {stop!r}; expected 'hi' or 'lo'.")
        if channel not in cls.CHANNELS:
            raise KeyError(
                f"Unknown render-effect channel {channel!r}. "
                f"Known: {', '.join(cls.CHANNELS)}."
            )
        if channel != cls.HIGHLIGHT_ATTR:
            raise ValueError(f"Channel {channel!r} has no colour attribute.")
        return (
            cls.HIGHLIGHT_COLOR_STOPS.hi
            if stop == "hi"
            else cls.HIGHLIGHT_COLOR_STOPS.lo
        )

    @classmethod
    def objects_with_channel(cls, channel="highlight") -> list:
        """Every object carrying the channel's property.

        Parameters:
            channel: The channel name; ``"highlight"``.

        Returns:
            Blender objects, in data order.
        """
        import bpy

        if channel not in cls.CHANNELS:
            raise KeyError(
                f"Unknown render-effect channel {channel!r}. "
                f"Known: {', '.join(cls.CHANNELS)}."
            )
        return [o for o in bpy.data.objects if channel in o]

    @classmethod
    def channel_colors(
        cls, objects=None, channel="highlight", stop: str = "hi"
    ) -> dict:
        """What each object's channel colour is authored as right now.

        The read half of :meth:`set_channel_color` -- what a revision starts
        from, and what proves one landed.

        Parameters:
            objects: Objects to read. ``None`` reads every object carrying the
                channel.
            channel: The channel name; ``"highlight"``.
            stop: Which end of the ramp to read -- ``"hi"`` (the default) or
                ``"lo"``.

        Returns:
            ``{object name: (r, g, b)}``, skipping objects without the colour.
        """
        attr = cls._color_attr(channel, stop)
        pool = (
            cls.objects_with_channel(channel)
            if objects is None
            else cls._resolve(objects)
        )
        colors = {}
        for obj in pool:
            if attr in obj:
                colors[obj.name] = tuple(float(c) for c in list(obj[attr])[:3])
        return colors

    @classmethod
    def channel_color_stops(cls, objects=None, channel="highlight") -> dict:
        """Both ends of each object's colour ramp, high first.

        Mirror of mayatk's ``RenderEffects.channel_color_stops``. What an
        editor showing the two ends side by side seeds from: reading the pair
        in ONE pass is what lets it tell a mixed selection from an agreeing one
        without walking the scene twice. An end the object does not carry reads
        ``None``.

        Returns:
            ``{object name: ((r, g, b) | None, ...)}``, skipping objects
            carrying neither end.
        """
        props = cls.HIGHLIGHT_COLOR_STOPS.keys if channel == cls.HIGHLIGHT_ATTR else ()
        if not props:
            return {}
        pool = (
            cls.objects_with_channel(channel)
            if objects is None
            else cls._resolve(objects)
        )
        out = {}
        for obj in pool:
            stops = tuple(
                tuple(float(c) for c in list(obj[p])[:3]) if p in obj else None
                for p in props
            )
            if any(c is not None for c in stops):
                out[obj.name] = stops
        return out

    @classmethod
    def set_channel_color(
        cls, objects=None, color=None, channel="highlight", stop: str = "hi"
    ) -> list:
        """Restate an already-authored channel colour, leaving its keys alone.

        The revision path for a look signed off after the pulses were keyed.
        The colour is its own property rather than part of the curve, so it can
        be rewritten at any time and the animation is untouched -- which is what
        makes a scene-wide recolour a one-liner instead of a re-key.

        Parameters:
            objects: Objects to write. ``None`` takes the selection, and falls
                back to every object carrying the channel when nothing is
                selected -- the scene-wide revision this exists for.
            color: ``(r, g, b)``, linear 0-1. Required.
            channel: The channel name; ``"highlight"``.
            stop: Which end of the ramp to write -- ``"hi"`` (the default) or
                ``"lo"``.

        Returns:
            The names of the objects written.

        Raises:
            ValueError: When *color* is missing or is not three components, or
                the channel owns no colour.
        """
        attr = cls._color_attr(channel, stop)
        if color is None:
            raise ValueError("A colour is required.")
        rgb = [float(c) for c in tuple(color)[:3]]
        if len(rgb) != 3:
            raise ValueError(f"Expected an (r, g, b) colour, got {color!r}.")

        pool = cls._resolve(objects)
        if objects is None and not pool:
            pool = cls.objects_with_channel(channel)
        if not pool:
            cls.logger.warning(f"No objects carry the {channel} channel.")
            return []

        written = []
        for obj in pool:
            if channel not in obj:
                cls.logger.warning(f"No {channel} channel on {obj.name}; skipped.")
                continue
            obj[attr] = rgb
            try:
                obj.id_properties_ui(attr).update(subtype="COLOR", min=0.0, max=1.0)
            except (AttributeError, TypeError, KeyError):
                pass
            written.append(obj.name)
        cls.logger.info(
            "Set %s colour to (%s) on %d object(s).",
            channel,
            ", ".join(f"{c:.3f}" for c in rgb),
            len(written),
        )
        return written

    @classmethod
    def preview(cls, objects=None, channel="highlight", enabled=True):
        """DEPRECATED (one release). ``enabled=False`` removes the material drivers
        a scene saved with the old preview still carries; ``True`` warns and does
        nothing -- the prop and its keys are the whole authoring now."""
        objects = cls._resolve(objects)
        if not objects:
            return {}
        if enabled:
            cls._warn_preview_retired()
            return {}
        for o in objects:
            cls._remove_legacy_drivers(o, channel)
        return {}

    @classmethod
    def _remove_legacy_drivers(cls, obj, channel):
        """Strip the retired preview's drivers for *channel* off *obj*'s materials."""
        if channel == cls.HIGHLIGHT_ATTR:
            cls._remove_emission_drivers(obj)
            return
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        for mat in getattr(obj.data, "materials", None) or []:
            node = _MatUtilsInternal._principled_node(mat) if mat else None
            if node is None:
                continue
            try:
                mat.node_tree.driver_remove(
                    node.inputs["Alpha"].path_from_id("default_value")
                )
            except (TypeError, RuntimeError):
                pass

    @classmethod
    def stage_export_proxies(cls):
        """Stage one transient Empty per keyed channel per object, for the FBX write.

        Blender's FBX exporter cannot ship custom-property animation, so each
        keyed channel gets a child Empty named ``<object>__<channel>`` whose
        ``scale.x`` carries the curve -- the same idiom EmissiveGroups uses for
        its weights and the transport mayatk stages for the same channels, so
        Unity's ``RenderEffectsImporter`` rebinds both DCCs' files identically.
        Marked with :attr:`PROXY_MARKER`; :meth:`remove_export_proxies` deletes
        them (the Scene Exporter stages the deferred restore). Stale proxies from
        an interrupted export are pre-cleaned here.

        Returns:
            The created proxy objects (empty when nothing is keyed).
        """
        import bpy
        from blendertk.env_utils.fbx_utils import FbxUtils

        cls.remove_export_proxies()
        proxies = []
        for obj in list(bpy.data.objects):
            if not getattr(obj, "animation_data", None) or obj.get(cls.PROXY_MARKER):
                continue
            for attr in cls.CHANNELS:
                fc = cls._fcurve(obj, f'["{attr}"]')
                if fc is None or not len(fc.keyframe_points):
                    continue
                name = f"{obj.name}{cls.PROXY_SEPARATOR}{attr}"
                proxy = FbxUtils.stage_curve_proxy(name, obj, fc)
                if proxy is None:
                    cls.logger.warning(
                        "Curve-proxy name %r is taken by an existing object -- %s's %s "
                        "animation will not ship this export.",
                        name,
                        obj.name,
                        attr,
                    )
                    continue
                proxies.append(proxy)
        if proxies:
            cls.logger.info(
                "Staged %d render-effect curve prox%s: %s",
                len(proxies),
                "y" if len(proxies) == 1 else "ies",
                ", ".join(p.name for p in proxies),
            )
        return proxies

    @classmethod
    def remove_export_proxies(cls):
        """Delete every staged render-effect curve proxy and its action. Idempotent."""
        import bpy

        removed = []
        for obj in [
            o
            for o in bpy.data.objects
            if o.get(cls.PROXY_MARKER) and cls.PROXY_SEPARATOR in o.name
        ]:
            ad = getattr(obj, "animation_data", None)
            action = ad.action if ad is not None else None
            removed.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)
            if action is not None and action.users == 0:
                bpy.data.actions.remove(action)
        return removed

    @classmethod
    def finish_export(cls):
        """Undo :meth:`prepare_for_export`'s staging (mirror of mayatk's)."""
        cls.remove_export_proxies()

    @classmethod
    def remove(cls, objects=None, mode=None, channel=None):
        """Remove a channel's prop and anim curves from *objects*, and heal the
        retired preview's material drivers.

        *channel* ``None`` removes every channel; ``mode`` is accepted for mayatk
        API parity.
        """
        channels = cls.CHANNELS if channel is None else (channel,)
        if cls.HIGHLIGHT_ATTR in channels:
            for obj in cls._resolve(objects):
                cls._remove_legacy_drivers(obj, cls.HIGHLIGHT_ATTR)
                cls._remove_fc(obj, cls._fcurve(obj, f'["{cls.HIGHLIGHT_ATTR}"]'))
                for attr in (cls.HIGHLIGHT_ATTR,) + cls.HIGHLIGHT_COLOR_STOPS.keys:
                    if attr in obj:
                        del obj[attr]
        if cls.ATTR_NAME not in channels:
            return
        for obj in cls._resolve(objects):
            cls._remove_legacy_drivers(obj, cls.ATTR_NAME)
            # Opacity + mirrored visibility anim curves.
            for dp in (f'["{cls.ATTR_NAME}"]', cls.VIS_PATH):
                cls._remove_fc(obj, cls._fcurve(obj, dp))
            if cls.ATTR_NAME in obj:
                del obj[cls.ATTR_NAME]

    # ------------------------------------------------------------------ keying
    #: The pulse brackets' floors, owned by the planner (``ptk.RampKeys``).
    #: Mirror of mayatk's names for them.
    PULSE_GAP_MIN = ptk.RampKeys.PULSE_GAP_MIN
    WHOLE_FRAME_GAP_MIN = ptk.RampKeys.WHOLE_FRAME_GAP_MIN

    @staticmethod
    def _set_key(obj, data_path, frame, value, interp, index=-1):
        """Set *value* then insert a keyframe at *frame* with the given interpolation."""
        if data_path.startswith("["):  # custom prop, or one component of a vector one
            RenderEffects._set_prop(obj, data_path[2:-2], index, value)
        else:
            setattr(
                obj, data_path, value if data_path != "hide_render" else bool(value)
            )
        obj.keyframe_insert(data_path=data_path, frame=frame, index=index)
        fc = RenderEffects._fcurve(obj, data_path, index)
        if fc is not None:
            for kp in fc.keyframe_points:
                if round(kp.co[0]) == round(frame):
                    kp.interpolation = interp

    @classmethod
    def _resolve_auto_fade(cls, obj, reference_frame, channel=None):
        """True → fade-in, False → fade-out, from the most recent key of *channel* ≤ *reference_frame*."""
        fc = cls._fcurve(obj, f'["{channel or cls.ATTR_NAME}"]')
        prev = None
        for kp in sorted(getattr(fc, "keyframe_points", []), key=lambda k: k.co[0]):
            if kp.co[0] <= reference_frame:
                prev = kp.co[1]
            else:
                break
        return True if prev is None else prev < 0.5

    @classmethod
    def _ensure_channel(
        cls, objects, channel, auto_create, preview, delete_visibility_keys
    ):
        """Give *objects* the channel's prop (and its material drivers) before keying.

        Mirror of mayatk's ``RenderEffects._ensure_channel``. Objects lacking the
        prop get it; with *delete_visibility_keys* the opacity channel's create
        path clears their render-visibility keys first, otherwise the keying
        mirror writes over whatever is there -- NOT via :meth:`create`, whose
        guard would raise. *preview* is the retired driver preview's kwarg:
        honoured as a warning, nothing more.
        """
        if preview:
            cls._warn_preview_retired()
        ensure = (
            cls._ensure_highlight_props
            if channel == cls.HIGHLIGHT_ATTR
            else cls._ensure_opacity_prop
        )
        if auto_create:
            for o in objects:
                if channel in o:
                    continue
                if delete_visibility_keys and channel == cls.ATTR_NAME:
                    cls._remove_fc(o, cls._fcurve(o, cls.VIS_PATH))
                    o.hide_render = False
                ensure(o)

    @classmethod
    def key_fade(
        cls,
        objects=None,
        start=0,
        end=15,
        direction="in",
        auto_create=True,
        tangent="LINEAR",
        preview=None,
        delete_visibility_keys=False,
        channel="opacity",
        whole_frames=True,
    ):
        """Key an opacity fade (linear) and mirror it to render visibility (stepped).

        ``direction``: ``"in"`` (0→1), ``"out"`` (1→0), or ``"auto"`` (from the last key).
        Channel creation is owned here (see :meth:`_ensure_channel`). ``channel`` picks the prop
        (mirror of mayatk's); only the opacity channel mirrors to render visibility.
        *whole_frames* (the default) snaps the window to whole frames.
        Returns ``[(object_name, "in"|"out")]``.
        """
        objects = cls._resolve(objects)
        if not objects:
            cls.logger.warning("No objects selected.")
            return []
        start, end = ptk.RampKeys.frames(whole_frames, start, end)
        cls._ensure_channel(
            objects, channel, auto_create, preview, delete_visibility_keys
        )

        path = f'["{channel}"]'
        keyed = []
        for obj in objects:
            if channel not in obj:
                continue
            fade_in = (
                cls._resolve_auto_fade(obj, start, channel)
                if direction == "auto"
                else direction == "in"
            )
            # Already snapped above, where ``auto`` read its reference frame.
            plan = ptk.RampKeys.fade(
                start, end, "in" if fade_in else "out", whole_frames=False
            )
            for frame, value in plan:
                cls._set_key(obj, path, frame, value, tangent)
            if channel == cls.ATTR_NAME:
                # Visibility mirror: hidden (hide_render=1) when opacity ≤ 0, else visible; stepped.
                for frame, value in plan:
                    cls._set_key(
                        obj, cls.VIS_PATH, frame, 0.0 if value > 0 else 1.0, "CONSTANT"
                    )
            keyed.append((obj.name, "in" if fade_in else "out"))
        return keyed

    @classmethod
    def sync_visibility_from_opacity(cls, objects=None) -> None:
        """Rebuild the ``hide_render`` curve from the ``opacity`` curve (stepped, hidden when ≤ 0).

        Clears existing visibility keys first so repeated calls don't accumulate.
        """
        for obj in cls._resolve(objects):
            fc = cls._fcurve(obj, f'["{cls.ATTR_NAME}"]')
            if fc is None or not fc.keyframe_points:
                continue
            cls._remove_fc(obj, cls._fcurve(obj, cls.VIS_PATH))
            for kp in fc.keyframe_points:
                cls._set_key(
                    obj,
                    cls.VIS_PATH,
                    kp.co[0],
                    0.0 if kp.co[1] > 0 else 1.0,
                    "CONSTANT",
                )

    @classmethod
    def ensure_connections(cls, objects=None) -> None:
        """Kept for mayatk API parity; there is no wiring to re-establish.

        The prop IS the channel in Blender, and the material drivers that used
        to shadow it were the retired preview. A no-op.
        """
        return None

    @classmethod
    def prepare_for_export(cls, objects=None) -> list:
        """Stage the curve-proxy transport for an FBX write; write nothing else.

        Mirror of mayatk's: one transient child per keyed channel carries the
        per-object curve (:meth:`stage_export_proxies`), and :meth:`finish_export`
        removes them after the write. No ``hide_render`` mirror is written for an
        opacity keyed by hand -- the GLB derives presence from the authored
        channels itself (``ptk.MeshConvert.apply_glb_visibility``).

        *objects* is ignored and the return is always empty, both kept for API
        compatibility for one release (it named the objects whose visibility was
        re-synced).
        """
        cls.stage_export_proxies()
        return []

    # ------------------------------------------------------------------ in-band export metadata
    #: ``data_export`` channel read by ``ptk.MeshConvert.apply_glb_visibility``
    #: -- the key of the ``ptk.SceneRecords.VISIBILITY`` record (mirror of
    #: mayatk's ``RenderEffects.DATA_CHANNEL``).
    DATA_CHANNEL = ptk.SceneRecords.VISIBILITY.key
    #: Schema this producer writes (stamped by the declaration).
    SCHEMA_VERSION = ptk.SceneRecords.VISIBILITY.version

    @classmethod
    def visibility_tracks(cls) -> list:
        """Every visibility-keyed object in the file, as stepped on/off tracks.

        Mirror of mayatk's ``RenderOpacity.visibility_tracks``, and the values
        are INVERTED on the way out: Blender's channel is ``hide_render``, so a
        keyframe value of 1 means *hidden* where the published contract — which
        is glTF's, not either DCC's — means *visible*. Doing that flip here is
        the point of the split; a consumer must not have to know which DCC
        wrote the file.
        """
        import bpy

        tracks = []
        for obj in bpy.data.objects:
            # ``_fcurve`` resolves an action slot per call, so it is not free;
            # an un-animated object cannot carry a visibility curve and most of
            # a scene's objects are un-animated.
            if not getattr(obj, "animation_data", None):
                continue
            if obj.get(cls.PROXY_MARKER):
                continue  # a transport node, never a track of its own
            vis = cls._fcurve(obj, cls.VIS_PATH)
            track = {"node": obj.name}
            if vis is not None and vis.keyframe_points:
                keys = [
                    [float(k.co[0]), 0.0 if k.co[1] >= 0.5 else 1.0]
                    for k in vis.keyframe_points
                ]
                track["visibility"] = sorted(keys)
            for attr in cls.CHANNELS:
                fc = cls._fcurve(obj, f'["{attr}"]')
                if fc is None or not fc.keyframe_points:
                    continue
                track[attr] = cls._linear_ramp(fc)
                if attr != cls.HIGHLIGHT_ATTR:
                    continue
                # One published key per end the object actually carries. An
                # object stating no dim end states nothing for it, so the
                # reader falls back to THAT end's default (black) rather than
                # to the bright colour.
                for prop, key in zip(
                    cls.HIGHLIGHT_COLOR_STOPS.keys, cls.HIGHLIGHT_TRACK_STOPS.keys
                ):
                    if prop not in obj:
                        continue
                    try:
                        track[key] = [float(c) for c in list(obj[prop])[:3]]
                    except (TypeError, ValueError):
                        pass
            if len(track) > 1:
                tracks.append(track)
        return tracks

    #: How much of a frame a CONSTANT key's jump is given when it is
    #: linearized. Mirror of mayatk's ``RenderOpacity._STEP_JUMP``.
    _STEP_JUMP = 0.01

    @classmethod
    def _linear_ramp(cls, fcurve) -> list:
        """*fcurve*'s keys, shaped so a LINEAR consumer reproduces it exactly.

        Mirror of mayatk's ``RenderOpacity._linear_ramp``, for the same reason
        and against the same contract: the ramp is published as
        ``[frame, alpha]`` pairs and every consumer interpolates them linearly,
        which is only faithful while the DCC's own curve does too. Blender's
        ``CONSTANT`` interpolation holds a key's value to the next one and then
        jumps -- so publishing the keys alone invents a ramp across a segment
        the artist authored as a cut. (Measured on the Maya side, where the
        equivalent tangent made a hold read as a fifteen-frame fade-out.)

        ``BEZIER`` is left as-is: it is Blender's default and it is a CURVE, so
        no finite set of endpoints reproduces it -- publishing its keys is the
        same approximation every consumer has always made, and eased alpha is
        visually close to linear. Only the case that is plainly WRONG is fixed.
        """
        # Sorted ONCE, carrying each key's interpolation with it, so a frame is
        # never used as a lookup key: two keys can share a frame, and a dict
        # would silently drop one of them along with its interpolation.
        keys = sorted(
            (float(k.co[0]), float(k.co[1]), getattr(k, "interpolation", "BEZIER"))
            for k in fcurve.keyframe_points
        )
        out: list = []
        for index, (frame, value, interpolation) in enumerate(keys):
            out.append([frame, value])
            if index + 1 >= len(keys):
                continue
            gap = keys[index + 1][0] - frame
            if interpolation == "CONSTANT" and gap > cls._STEP_JUMP:
                out.append([frame + gap - cls._STEP_JUMP, value])
        return out

    @classmethod
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """The ``visibility_tracks`` record for this file, or ``None`` when it
        has no keyed visibility -- the ``ptk.SceneRecords.VISIBILITY`` producer
        (``FbxUtils.PRODUCERS``, mirror of mayatk's).  Pure: it reads the
        curves and the shot record and never writes.

        glTF animates translation, rotation, scale and morph weights and
        nothing else, so keyed visibility does not survive the conversion from
        either DCC; ``MeshConvert.apply_glb_visibility`` rebuilds it from this
        channel as stepped scale.  The authored *fade* rides along on the same
        channel and ``MeshConvert.apply_glb_fades`` writes it as
        ``KHR_animation_pointer`` alpha, which is why :meth:`_linear_ramp`
        matters: that consumer reads the ramp linearly.

        Also publishes ``clip_span`` -- per take, the first and last authored
        frame inside its window (the take's own zero: the converter rebases a
        clip onto its first authored key).  The whole-timeline entry is the
        exporter's ``ctx.clip_span`` when the pipeline measured one (the first
        and last frame the stack CARRIES), else the bake range as a seed.

        Parameters:
            ctx: The export's decisions (``clip_span``) and the records
                produced before this one -- the shot record's ``fps`` and its
                clips' ranges (the takes); the stored records are read when
                this assembly did not produce them (an authoring-time
                republish).

        Returns:
            The record, or ``None`` when there is no keyed visibility (the
            publisher then clears the channel).
        """
        from blendertk.env_utils.fbx_utils import FbxUtils
        from blendertk.node_utils.data_nodes import DataNodes

        # Bail BEFORE the span walk: that reads every fcurve in the file, and a
        # scene with no keyed visibility has nothing to spend it on.
        tracks = cls.visibility_tracks()
        if not tracks:
            return None

        # The shot record the shots producer has just built (it runs first:
        # the record declares ``after=("shot_metadata",)``), else the stored
        # one -- it keeps the frame rate defined in ONE place for the export.
        metadata = ctx.record(ptk.SceneRecords.SHOTS, DataNodes)
        if not isinstance(metadata, dict):
            metadata = {}
        # The scene's own rate when the shots producer published none (a
        # shot-less scene, or a hand-off that refreshes only this producer):
        # without a rate the GLB appliers cannot place the frames in time and
        # drop every track and ramp -- measured 2026-09-05 on the WebXR preview
        # ("carry no frame rate ... not applied"; mayatk has done this since
        # 2026-09-02).
        fps = metadata.get("fps") or cls._scene_fps()
        takes = ptk.SceneRecords.declared_takes(lambda key: ctx.record(key, DataNodes))
        # The stack's origin: measured by the pipeline (``ctx.clip_span``)
        # once it has seen the final curves; until then the range the write
        # BAKES is a seed, since the converter rebases every stack onto its
        # first key and the scene's own earliest key is not that range.
        # Mirror of mayatk, which reads the same answer off its exporter state.
        # Widened by THIS assembly's takes: the carrier still holds the last
        # export's until the snapshot commits.
        stack_range = ctx.clip_span or FbxUtils.bake_range(takes)
        payload = ptk.MeshConvert.build_visibility_tracks(
            tracks,
            fps=fps,
            clip_spans=ptk.MeshConvert.clip_spans(
                cls._scene_key_frames(), takes, stack_range=stack_range
            ),
        )
        if payload is None:
            return None
        return ptk.SceneRecords.VISIBILITY.make(payload)

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Republish the ``visibility_tracks`` channel on the ``data_export`` carrier.

        The authoring-time publish of :meth:`export_record`, committed through
        ``FbxUtils.publish_authored`` (an export pipeline runs the producer
        itself: ``FbxUtils.PRODUCERS``).  Clears the channel when the file has
        no keyed visibility, leaving no empty carrier behind.

        Returns:
            The published JSON string, or ``None`` when cleared.
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        record = cls.export_record(ptk.ExportContext(mode=ptk.ExportContext.AUTHORING))
        FbxUtils.publish_authored({ptk.SceneRecords.VISIBILITY: record})
        if record is None:
            return None
        cls.logger.info(
            "Visibility: published %d keyed-visibility track(s) for the GLB "
            "route (glTF drops the FBX's own visibility curves).",
            len(record.payload.get("tracks") or []),
        )
        return record.text

    @staticmethod
    def _scene_fps() -> float:
        """The scene's playback rate (``fps / fps_base``)."""
        import bpy

        render = bpy.context.scene.render
        return float(render.fps) / float(render.fps_base or 1.0)

    @staticmethod
    def _scene_key_frames() -> list:
        """Every authored key time in the file, in frames.

        The scene-reaching half of ``ptk.MeshConvert.clip_spans``, which owns
        the rest (mayatk answers the same through ``key_spans``, per window
        off its curves, because a Maya bake is millions of keys). Every
        animated channel counts, because the converter sizes a take from all
        of them while emitting a channel for only some -- and only the
        objects' own fcurves are channels, so an action assigned to nothing
        does not count.
        """
        from blendertk.anim_utils._anim_utils import AnimUtils

        import bpy

        return [
            float(k.co[0])
            for fc in AnimUtils.get_fcurves(list(bpy.data.objects))
            for k in fc.keyframe_points
        ]
