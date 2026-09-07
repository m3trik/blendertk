# !/usr/bin/python
# coding=utf-8
"""Render Opacity — Blender per-object opacity for engine-ready transparency (mirror of mayatk's
``mat_utils.render_opacity.RenderOpacity``: ``btk.RenderOpacity`` ↔ ``mtk.RenderOpacity``).

Adds a keyable ``opacity`` custom property (0-1) to objects — the export artifact Unity reads — and
drives the object's material **Principled BSDF Alpha** from it for live viewport feedback. The
``key_fade`` helper animates a fade **and mirrors it onto the object's render visibility** so the FBX
carries both channels (the Unity importer reconstructs per-object opacity from the *visibility*
m_Enabled curve, because Unity collapses same-named animated custom-property curves onto the root
with empty paths — see ``memory/reference_unitytk_opacity_from_visibility.md``). ``prepare_for_export``
is the safety net that dual-keys hand-authored opacity before export.

**Divergences from Maya (documented, not reductions):**
  - **No StingrayPBS / attribute-vs-material split.** Maya's "material" mode loads a transparency
    graph; in Blender the material wiring simply *is* the Alpha driver, so both Maya modes collapse
    onto one path here (``mode`` is accepted for API parity; "material"/"attribute" behave the same).
  - **No transform/shape split & no ``visibility`` attr.** The m_Enabled analogue is the object's
    ``hide_render`` (render visibility), keyed stepped (hidden when opacity ≤ 0).
  - **Shared material datablocks.** A driver on a shared material would read a single object, so
    ``create`` makes each object's material **single-user** before driving its Alpha — the Blender
    equivalent of Maya's per-object opacity proxy.
  - The FBX visibility-channel mapping is finalized with the SceneExporter / ``fbx_utils`` port; this
    engine produces the dual-keyed Blender data (opacity prop curve + ``hide_render`` curve).

``import bpy`` is deferred into the call bodies so the module resolves headless / under the .venv.
"""

import pythontk as ptk


class RenderEffects(ptk.LoggingMixin):
    """Per-object render-effect channels: the keyable ``opacity`` prop (mirrored
    to render visibility) and the ``highlight`` prop with its colour."""

    ATTR_NAME = "opacity"
    #: The highlight channel: an additive emissive intensity (0-1) plus its
    #: colour, both custom properties on the object. Mirror of mayatk's
    #: ``channels.HIGHLIGHT`` row; the GLB half lives in pythontk's table.
    HIGHLIGHT_ATTR = "highlight"
    HIGHLIGHT_COLOR_ATTR = "highlightColor"
    CHANNELS = ("opacity", "highlight")
    #: The fcurve data paths of every render-effect property (the channels
    #: and the highlight colour) -- the mirror of mayatk's ``ChannelSpec.attrs``,
    #: what the shot system reads as content beside the transform channels.
    PROP_PATHS = tuple(f'["{n}"]' for n in CHANNELS + (HIGHLIGHT_COLOR_ATTR,))
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
    def _ensure_highlight_props(cls, obj, color=(0.2, 0.5, 1.0)):
        """Seed ``highlight`` (0-1, keyable) and ``highlightColor`` if absent."""
        if cls.HIGHLIGHT_ATTR not in obj:
            obj[cls.HIGHLIGHT_ATTR] = 0.0
        try:
            obj.id_properties_ui(cls.HIGHLIGHT_ATTR).update(
                min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, default=0.0
            )
        except (AttributeError, TypeError, KeyError):
            pass
        if cls.HIGHLIGHT_COLOR_ATTR not in obj:
            obj[cls.HIGHLIGHT_COLOR_ATTR] = [float(c) for c in color[:3]]
            try:
                obj.id_properties_ui(cls.HIGHLIGHT_COLOR_ATTR).update(
                    subtype="COLOR", min=0.0, max=1.0
                )
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
        start, end = cls._frames(whole_frames, start, end)
        if not objects or period <= 0 or end <= start:
            return []
        cls._ensure_channel(
            objects, cls.HIGHLIGHT_ATTR, auto_create, preview, delete_visibility_keys
        )
        ramp = max(0.0, min(0.5, ramp_fraction)) * period
        bright = max(0.0, min(1.0, bright_fraction)) * period
        bright_hold = max(0.0, bright - ramp)
        dim_hold = max(0.0, (period - bright) - ramp)
        cycle = [
            (0.0, 1.0),
            (bright_hold, 1.0),
            (bright_hold + ramp, 0.0),
            (bright_hold + ramp + dim_hold, 0.0),
        ]
        gap_min = cls.WHOLE_FRAME_GAP_MIN if whole_frames else cls.PULSE_GAP_MIN
        head, tail = cls._pulse_gaps(start, end, ramp, lead_in, lead_out, gap_min)
        train_start, train_end = cls._frames(
            whole_frames, float(start) + head, float(end) - tail
        )
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
            cls._set_key(obj, path, start, 0.0, "LINEAR")  # backward hold is dim
            t0 = train_start
            last = None
            while t0 < train_end:
                for offset, value in cycle:
                    # The cycle advances unrounded; only the key itself snaps,
                    # so a whole-frame train keeps the asked-for cadence
                    # instead of accumulating the rounding error.
                    (t,) = cls._frames(whole_frames, t0 + offset)
                    if t > train_end:
                        break
                    cls._set_key(obj, path, t, value, "LINEAR")
                    last = value
                t0 += period
            # The train's last value at the cut, so the trail-out falls over the
            # gap it was given rather than over the rest of the cycle it cut.
            if last is not None:
                cls._set_key(obj, path, train_end, last, "LINEAR")
            cls._set_key(obj, path, end, 0.0, "LINEAR")  # ...forward hold likewise
            if color is not None:
                obj[cls.HIGHLIGHT_COLOR_ATTR] = [float(c) for c in color[:3]]
            keyed.append(obj.name)
        return keyed

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
                for attr in (cls.HIGHLIGHT_ATTR, cls.HIGHLIGHT_COLOR_ATTR):
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
    #: The narrowest a pulse bracket may be: a gap of zero still needs its dim
    #: key strictly BEFORE the train's first bright one, or the two collide on
    #: one frame -- and Blender MERGES an inserted key into an existing one
    #: within 0.01 frames, so the floor has to clear that threshold, not sit
    #: on it. Mirror of mayatk's ``PULSE_GAP_MIN`` (and of ``_STEP_JUMP``).
    PULSE_GAP_MIN = 0.05

    #: The same floor for a whole-frame pulse (the default): a snapped bracket
    #: has to be a whole frame wide, or it rounds onto the train key it exists
    #: to stay clear of. Mirror of mayatk's twin.
    WHOLE_FRAME_GAP_MIN = 1.0

    @staticmethod
    def _frames(whole, *times):
        """*times* as floats, snapped to whole frames when *whole*.

        Half-up (``ptk.MathUtils.round_value``) rather than :func:`round`,
        whose banker's rounding would send two equal half-frames in one cycle
        to different frames. Mirror of mayatk's twin.
        """
        return tuple(
            float(ptk.MathUtils.round_value(t, mode="half_up")) if whole else float(t)
            for t in times
        )

    @classmethod
    def _pulse_gaps(cls, start, end, ramp, lead_in, lead_out, gap_min=None):
        """``(head, tail)`` frames for a pulse's dim brackets, fitted to the window.

        ``None`` takes the cycle's own *ramp*. The pair is held to half the
        window so there is always as much pulse as bracket, and each is floored
        at *gap_min* (:attr:`PULSE_GAP_MIN`). Mirror of mayatk's twin.
        """
        gap_min = cls.PULSE_GAP_MIN if gap_min is None else gap_min
        head = ramp if lead_in is None else max(0.0, float(lead_in))
        tail = ramp if lead_out is None else max(0.0, float(lead_out))
        budget = (float(end) - float(start)) / 2.0
        total = head + tail
        if total > budget and total > 0:
            scale = budget / total
            head, tail = head * scale, tail * scale
        return max(head, gap_min), max(tail, gap_min)

    @staticmethod
    def _set_key(obj, data_path, frame, value, interp, index=-1):
        """Set *value* then insert a keyframe at *frame* with the given interpolation."""
        if data_path.startswith("["):  # custom prop
            obj[data_path[2:-2]] = value
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
        start, end = cls._frames(whole_frames, start, end)
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
            start_val, end_val = (0.0, 1.0) if fade_in else (1.0, 0.0)

            cls._set_key(obj, path, start, start_val, tangent)
            cls._set_key(obj, path, end, end_val, tangent)
            if channel == cls.ATTR_NAME:
                # Visibility mirror: hidden (hide_render=1) when opacity ≤ 0, else visible; stepped.
                cls._set_key(
                    obj, cls.VIS_PATH, start, 0.0 if start_val > 0 else 1.0, "CONSTANT"
                )
                cls._set_key(
                    obj, cls.VIS_PATH, end, 0.0 if end_val > 0 else 1.0, "CONSTANT"
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
        """Dual-key safety net before FBX export: for every object with an animated ``opacity`` but
        missing / fewer visibility keys, mirror opacity → ``hide_render``. Returns the synced names.

        Scans the whole scene when *objects* is ``None``.
        """
        import bpy

        if objects is None:
            objects = [o for o in bpy.data.objects if cls.ATTR_NAME in o]
        else:
            objects = cls._resolve(objects)

        synced, needs = [], []
        for obj in objects:
            opa = cls._fcurve(obj, f'["{cls.ATTR_NAME}"]')
            if opa is None or not opa.keyframe_points:
                continue
            vis = cls._fcurve(obj, cls.VIS_PATH)
            if vis is None or len(vis.keyframe_points) < len(opa.keyframe_points):
                needs.append(obj)
                synced.append(obj.name)
        if needs:
            cls.sync_visibility_from_opacity(needs)
            cls.logger.info(
                "prepare_for_export: synced visibility on %d object(s): %s",
                len(synced),
                ", ".join(synced),
            )
        return synced

    # ------------------------------------------------------------------ in-band export metadata
    #: ``data_export`` channel read by ``ptk.MeshConvert.apply_glb_visibility``
    #: (mirror of mayatk's ``RenderOpacity.DATA_CHANNEL``).
    DATA_CHANNEL = ptk.MeshConvert.VISIBILITY_TRACKS_KEY
    SCHEMA_VERSION = ptk.MeshConvert.VISIBILITY_TRACKS_VERSION

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
                if attr == cls.HIGHLIGHT_ATTR and cls.HIGHLIGHT_COLOR_ATTR in obj:
                    try:
                        track["highlight_color"] = [
                            float(c) for c in list(obj[cls.HIGHLIGHT_COLOR_ATTR])[:3]
                        ]
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
    def refresh_export_metadata(cls):
        """Republish the ``visibility_tracks`` channel (``FbxUtils._KNOWN_PRODUCERS``).

        Mirror of mayatk's. glTF animates translation, rotation, scale and morph
        weights and nothing else, so keyed visibility does not survive the
        conversion from either DCC; ``MeshConvert.apply_glb_visibility`` rebuilds
        it from this channel as stepped scale. The authored *fade* rides along
        on the same channel and ``MeshConvert.apply_glb_fades`` writes it as
        ``KHR_animation_pointer`` alpha, which is why :meth:`_linear_ramp`
        matters: that consumer reads the ramp linearly.
        """
        import json

        from blendertk.node_utils.data_nodes import DataNodes

        # Bail BEFORE the span walk: that reads every fcurve in the file, and a
        # scene with no keyed visibility has nothing to spend it on.
        tracks = cls.visibility_tracks()
        if not tracks:
            DataNodes.set_export_string(cls.DATA_CHANNEL, "")
            return None

        metadata = cls._carrier_json("shot_metadata")
        from blendertk.env_utils.fbx_utils import FbxUtils

        # The scene's own rate when the shots producer published none (a
        # shot-less scene, or a hand-off that refreshes only this producer):
        # without a rate the GLB appliers cannot place the frames in time and
        # drop every track and ramp -- measured 2026-09-05 on the WebXR preview
        # ("carry no frame rate ... not applied"; mayatk has done this since
        # 2026-09-02).
        fps = (metadata or {}).get("fps") or cls._scene_fps()
        text = json.dumps(
            ptk.MeshConvert.build_visibility_tracks(
                tracks,
                fps=fps,
                clip_spans=ptk.MeshConvert.clip_spans(
                    cls._scene_key_frames(),
                    cls._carrier_json("fbx_takes") or [],
                    # The stack ships the range the write BAKES, and the
                    # converter rebases it onto its first key; the scene's
                    # own earliest key is not that range. Mirror of mayatk,
                    # which reads the same answer off its exporter state.
                    stack_range=FbxUtils.bake_range(),
                ),
            )
        )
        DataNodes.set_export_string(cls.DATA_CHANNEL, text)
        cls.logger.info(
            "Visibility: published %d keyed-visibility track(s) for the GLB "
            "route (glTF drops the FBX's own visibility curves).",
            len(tracks),
        )
        return text

    @staticmethod
    def _scene_fps() -> float:
        """The scene's playback rate (``fps / fps_base``)."""
        import bpy

        render = bpy.context.scene.render
        return float(render.fps) / float(render.fps_base or 1.0)

    @staticmethod
    def _carrier_json(attr):
        """One ``data_export`` channel, decoded, or ``None``."""
        import json

        from blendertk.node_utils.data_nodes import DataNodes

        try:
            raw = DataNodes.get_export_string(attr)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    @staticmethod
    def _scene_key_frames() -> list:
        """Every authored key time in the file, in frames.

        The scene-reaching half of ``ptk.MeshConvert.clip_spans``, which owns
        the rest (mirror of mayatk's ``_scene_key_frames``). Every animated
        channel counts, because the converter sizes a take from all of them
        while emitting a channel for only some.
        """
        from blendertk.anim_utils._anim_utils import AnimUtils

        import bpy

        return [
            float(k.co[0])
            for fc in AnimUtils.get_fcurves(list(bpy.data.objects))
            for k in fc.keyframe_points
        ]
