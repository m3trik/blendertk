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


class RenderOpacity(ptk.LoggingMixin):
    """Per-object opacity: keyable ``opacity`` prop + Principled-Alpha driver + visibility mirror."""

    ATTR_NAME = "opacity"
    # The render-visibility channel mirrored from opacity (Blender's m_Enabled analogue).
    VIS_PATH = "hide_render"

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _resolve(objects):
        import bpy

        if objects is None:
            return [o for o in (getattr(bpy.context, "selected_objects", None) or []) if o]
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
        from blendertk.anim_utils._anim_utils import get_fcurves

        for fc in get_fcurves([obj]):
            if fc.data_path == data_path and (index < 0 or fc.array_index == index):
                return fc
        return None

    @staticmethod
    def _remove_fc(obj, fc):
        """Remove fcurve *fc* from *obj*'s action (slot-aware, via the shared anim_utils helper)."""
        from blendertk.anim_utils._anim_utils import _remove_fcurve

        ad = getattr(obj, "animation_data", None)
        if ad and ad.action is not None and fc is not None:
            try:
                _remove_fcurve(ad.action, getattr(ad, "action_slot", None), fc)
            except (RuntimeError, ReferenceError, ValueError):
                pass

    @staticmethod
    def _refresh_drivers(node_trees):
        """Force-recompile the Alpha drivers (script-built-driver stale-compile gotcha: a freshly
        built driver first evaluates with an incomplete variable set → 0; settle the depsgraph, then
        re-assign each expression). Mirror of ``RigUtils.refresh_drivers`` for material node trees.

        No-op on an empty list — a spurious ``view_layer.update()`` rebuilds the depsgraph and can
        *re-stale* an already-compiled cross-datablock driver (material Alpha ← object prop) that we
        then never re-assign, so only update when there is something to refresh."""
        if not node_trees:
            return
        import bpy

        bpy.context.view_layer.update()
        for nt in node_trees:
            ad = getattr(nt, "animation_data", None)
            for d in (ad.drivers if ad else ()):
                d.driver.expression = d.driver.expression

    # ------------------------------------------------------------------ visibility-key queries
    @classmethod
    def objects_with_visibility_keys(cls, objects) -> list:
        """The subset of *objects* that already have keyframes on render visibility."""
        return [o for o in cls._resolve(objects) if cls._fcurve(o, cls.VIS_PATH) is not None]

    # ------------------------------------------------------------------ create / remove
    @classmethod
    def create(cls, objects=None, mode: str = "attribute", delete_visibility_keys: bool = False):
        """Add the ``opacity`` prop to *objects* and drive each material's Principled Alpha from it.

        ``mode`` is accepted for mayatk API parity ("attribute"/"material" behave identically in
        Blender; "remove" delegates to :meth:`remove`). Objects with existing visibility keys are
        skipped with a warning unless *delete_visibility_keys* is True (then their vis keys are cut).
        """
        import bpy

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

        cls.remove(objects)  # always clean prior state first
        if mode == "remove":
            return {}

        results = {}
        node_trees = []
        for obj in objects:
            cls._ensure_opacity_prop(obj, 1.0)
            node_trees.extend(cls._drive_material_alpha(obj))
            results[obj.name] = {"opacity": True}
        cls._refresh_drivers(node_trees)  # post-build recompile (script-built driver gotcha)
        return results

    @classmethod
    def _drive_material_alpha(cls, obj):
        """Single-user each Principled material on *obj* and drive its Alpha from ``opacity``.

        Returns the list of material node trees wired (for a post-build driver refresh). Objects
        with no Principled material get the prop but no viewport feedback (the prop still exports) —
        mirror of Maya's attribute-only objects.
        """
        from blendertk.mat_utils._mat_utils import _principled_node

        slots = getattr(obj.data, "materials", None)
        if not slots:
            return []
        wired = []
        for i, mat in enumerate(slots):
            if mat is None:
                continue
            node = _principled_node(mat)
            if node is None:
                continue
            if mat.users > 1:  # shared datablock -> per-object copy so opacity is per-object
                mat = mat.copy()
                obj.data.materials[i] = mat
                node = _principled_node(mat)
            mat.use_nodes = True
            cls._set_blend(mat)
            cls._alpha_driver(mat, node, obj)
            wired.append(mat.node_tree)
        return wired

    @staticmethod
    def _set_blend(mat):
        """Legacy-EEVEE alpha-blend knobs (EEVEE-Next drops them — alpha is socket-driven)."""
        for attr, val in (("blend_method", "BLEND"), ("shadow_method", "HASHED")):
            try:
                setattr(mat, attr, val)
            except (AttributeError, TypeError):
                pass

    @classmethod
    def _alpha_driver(cls, mat, node, obj):
        """Driver: material Alpha ← obj['opacity'] (re-entrant)."""
        nt = mat.node_tree
        path = node.inputs["Alpha"].path_from_id("default_value")
        try:
            nt.driver_remove(path)
        except (TypeError, RuntimeError):
            pass
        fc = nt.driver_add(path)
        drv = fc.driver
        drv.type = "SCRIPTED"
        var = drv.variables.new()
        var.name = "opacity"
        var.type = "SINGLE_PROP"
        var.targets[0].id = obj
        var.targets[0].data_path = f'["{cls.ATTR_NAME}"]'
        drv.expression = "opacity"
        return fc

    @classmethod
    def remove(cls, objects=None, mode=None):
        """Remove the opacity prop, its Alpha drivers, and its anim curves from *objects*."""
        import bpy
        from blendertk.mat_utils._mat_utils import _principled_node

        for obj in cls._resolve(objects):
            # Alpha drivers on this object's materials.
            for mat in (getattr(obj.data, "materials", None) or []):
                node = _principled_node(mat) if mat else None
                if node is None:
                    continue
                try:
                    mat.node_tree.driver_remove(node.inputs["Alpha"].path_from_id("default_value"))
                except (TypeError, RuntimeError):
                    pass
            # Opacity + mirrored visibility anim curves.
            for dp in (f'["{cls.ATTR_NAME}"]', cls.VIS_PATH):
                cls._remove_fc(obj, cls._fcurve(obj, dp))
            if cls.ATTR_NAME in obj:
                del obj[cls.ATTR_NAME]

    # ------------------------------------------------------------------ keying
    @staticmethod
    def _set_key(obj, data_path, frame, value, interp, index=-1):
        """Set *value* then insert a keyframe at *frame* with the given interpolation."""
        if data_path.startswith('['):  # custom prop
            obj[data_path[2:-2]] = value
        else:
            setattr(obj, data_path, value if data_path != "hide_render" else bool(value))
        obj.keyframe_insert(data_path=data_path, frame=frame, index=index)
        fc = RenderOpacity._fcurve(obj, data_path, index)
        if fc is not None:
            for kp in fc.keyframe_points:
                if round(kp.co[0]) == round(frame):
                    kp.interpolation = interp

    @classmethod
    def _resolve_auto_fade(cls, obj, reference_frame):
        """True → fade-in, False → fade-out, from the most recent opacity key ≤ *reference_frame*."""
        fc = cls._fcurve(obj, f'["{cls.ATTR_NAME}"]')
        prev = None
        for kp in sorted(getattr(fc, "keyframe_points", []), key=lambda k: k.co[0]):
            if kp.co[0] <= reference_frame:
                prev = kp.co[1]
            else:
                break
        return True if prev is None else prev < 0.5

    @classmethod
    def key_fade(cls, objects=None, start=0, end=15, direction="in", auto_create=True, tangent="LINEAR"):
        """Key an opacity fade (linear) and mirror it to render visibility (stepped).

        ``direction``: ``"in"`` (0→1), ``"out"`` (1→0), or ``"auto"`` (from the last opacity key).
        Returns ``[(object_name, "in"|"out")]``.
        """
        objects = cls._resolve(objects)
        if not objects:
            cls.logger.warning("No objects selected.")
            return []
        if auto_create:
            # Set up the prop + Alpha driver directly — NOT via create(), which guards on existing
            # visibility keys and would raise here (key_fade overwrites visibility anyway). Mirrors
            # Maya, whose key_fade calls the unguarded attribute-mode create.
            node_trees = []
            for o in objects:
                if cls.ATTR_NAME not in o:
                    cls._ensure_opacity_prop(o)
                    node_trees.extend(cls._drive_material_alpha(o))
            cls._refresh_drivers(node_trees)

        keyed = []
        for obj in objects:
            if cls.ATTR_NAME not in obj:
                continue
            fade_in = cls._resolve_auto_fade(obj, start) if direction == "auto" else direction == "in"
            start_val, end_val = (0.0, 1.0) if fade_in else (1.0, 0.0)

            cls._set_key(obj, f'["{cls.ATTR_NAME}"]', start, start_val, tangent)
            cls._set_key(obj, f'["{cls.ATTR_NAME}"]', end, end_val, tangent)
            # Visibility mirror: hidden (hide_render=1) when opacity ≤ 0, else visible; stepped.
            cls._set_key(obj, cls.VIS_PATH, start, 0.0 if start_val > 0 else 1.0, "CONSTANT")
            cls._set_key(obj, cls.VIS_PATH, end, 0.0 if end_val > 0 else 1.0, "CONSTANT")
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
                cls._set_key(obj, cls.VIS_PATH, kp.co[0], 0.0 if kp.co[1] > 0 else 1.0, "CONSTANT")

    @classmethod
    def ensure_connections(cls, objects=None) -> None:
        """Re-establish the Alpha driver on objects that have ``opacity`` but lost it (e.g. after a
        material was reassigned). Idempotent; safe to call before keying."""
        node_trees = []
        for obj in cls._resolve(objects):
            if cls.ATTR_NAME in obj:
                node_trees.extend(cls._drive_material_alpha(obj))
        cls._refresh_drivers(node_trees)

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
            cls.logger.info("prepare_for_export: synced visibility on %d object(s): %s",
                            len(synced), ", ".join(synced))
        return synced
