# !/usr/bin/python
# coding=utf-8
"""Batch object naming — Blender port of mayatk's ``edit_utils.naming.Naming``.

The pattern-matching / formatting logic is the **shared, DCC-agnostic** ``pythontk`` string layer
(``ptk.find_str_and_format`` / ``find_str`` / ``retain_suffix`` / ``format_suffix``), and every
operation applies its plan through :class:`pythontk.RenamePlan` exactly as mayatk does — so
``dry_run`` and the ``old → new`` report (one ``log_group`` record per operation, through
``cls.logger``) behave identically; only the node access differs (``cmds.rename`` → ``obj.name``).

Divergences (documented for parity — see ``tentacle/docs/PARITY_PORTING_PLAN.md``): Blender object
names are the leaf already (no ``|`` DAG path / ``:`` namespace to strip) and Blender auto-uniquifies
on collision (``.001``); ``suffix_by_type`` maps Blender object **types** (MESH→_GEO, CURVE→_CRV,
SURFACE→_SRF, CAMERA→_CAM, LIGHT→_LGT, ARMATURE→_JNT, LATTICE→_LAT, EMPTY→_GRP when it has children
else _LOC) plus Material / Image datablocks (→_MAT / _TEX) when handed them; the remaining mayatk
suffix keywords (IK handle, cluster, skin cluster, blend shape, constraint, shading group, display
layer, set) are accepted for signature parity but name no Blender object type. ``import bpy`` is
deferred into the call bodies.
"""

import re
import string
from typing import List, Tuple

import pythontk as ptk


class Naming(ptk.HelpMixin, ptk.LoggingMixin):
    """Batch find / rename / suffix scene objects (mirror of mayatk's ``Naming``)."""

    # Mirror of mayatk's ``SUFFIX_TYPES`` — same keywords, defaults and labels;
    # the type key is what :meth:`type_key` resolves an item to.
    SUFFIX_TYPES: Tuple[Tuple[str, str, str, str], ...] = (
        ("group_suffix", "_GRP", "Group", "group"),
        ("locator_suffix", "_LOC", "Locator", "locator"),
        ("joint_suffix", "_JNT", "Joint", "ARMATURE"),
        ("mesh_suffix", "_GEO", "Mesh", "MESH"),
        ("nurbs_curve_suffix", "_CRV", "Nurbs Curve", "CURVE"),
        ("camera_suffix", "_CAM", "Camera", "CAMERA"),
        ("light_suffix", "_LGT", "Light", "LIGHT"),
        ("display_layer_suffix", "_LYR", "Display Layer", "displayLayer"),
        ("ik_handle_suffix", "_IKH", "IK Handle", "ikHandle"),
        ("nurbs_surface_suffix", "_SRF", "Nurbs Surface", "SURFACE"),
        ("cluster_suffix", "_CLS", "Cluster", "cluster"),
        ("lattice_suffix", "_LAT", "Lattice", "LATTICE"),
        ("skin_cluster_suffix", "_SKN", "Skin Cluster", "skinCluster"),
        ("blend_shape_suffix", "_BS", "Blend Shape", "blendShape"),
        ("constraint_suffix", "_CON", "Constraint", "constraint"),
        ("material_suffix", "_MAT", "Material", "material"),
        ("shading_group_suffix", "_SG", "Shading Group", "shadingEngine"),
        ("texture_suffix", "_TEX", "Texture", "texture"),
        ("set_suffix", "_SET", "Set", "objectSet"),
    )

    @classmethod
    def scene_objects(cls) -> List:
        """Every object in the current scene — the naming tools' "Scene" scope."""
        import bpy

        return list(bpy.context.scene.objects)

    @classmethod
    def rename(
        cls,
        objects,
        to,
        fltr="",
        regex=False,
        ignore_case=False,
        retain_suffix=False,
        valid_suffixes=None,
        collapse_padding=True,
        dry_run=False,
    ):
        """Rename objects by pattern — Blender mirror of mayatk's ``Naming.rename``.

        ``to`` formatting tokens (via :func:`pythontk.find_str_and_format`) — the asterisk marks
        the part of the existing name that is *kept*: ``chars`` replace all, ``*chars*``
        replace-only, ``*chars`` replace-suffix, ``**chars`` append-suffix, ``chars*``
        replace-prefix, ``chars**`` append-prefix, ``""`` strip. Pipe-separated ``to`` terms pair
        positionally with ``fltr``'s (``*_L|*_R`` with ``*_lt|*_rt`` renames each side
        differently); a single term applies to every filter term. ``fltr`` filters which names
        match (wildcards or, with ``regex``, regex) and each of its terms supplies the "from" text
        for the names it matched; in ``regex`` mode the pattern also drives the substitution and
        its capture groups are available in ``to`` as ``\\1`` / ``\\g<name>``. ``retain_suffix``
        re-appends the object's existing type suffix (from ``valid_suffixes``).
        ``collapse_padding`` collapses the underscore residue strip/replace formatting leaves
        behind (skipped when ``to`` itself contains ``__``). ``dry_run`` plans and reports without
        renaming. Returns the new names parallel to ``objects`` (the planned names on a dry run).
        """
        objects = [o for o in ptk.make_iterable(objects) if o]
        name_to_obj = {
            o.name: o for o in objects
        }  # Blender object names are globally unique

        # An empty filter means "match all" → "*" (one batch call covers both cases).
        try:
            pairs = ptk.find_str_and_format(
                list(name_to_obj),
                to,
                fltr or "*",
                regex=regex,
                ignore_case=ignore_case,
                return_orig_strings=True,
            )
        except Exception as e:  # malformed pattern/filter — leave names unchanged
            cls.logger.error(f"Invalid pattern — filter '{fltr}', rename '{to}': {e}")
            return [o.name for o in objects]

        plan = []
        for old_name, new_name in pairs:
            if retain_suffix:
                new_name = ptk.retain_suffix(old_name, new_name, valid_suffixes)
            new_name = cls.strip_illegal_chars(new_name)
            # Collapse the separator residue that strip/replace formatting
            # leaves behind (removing a token from 'a__tok__tokB' yields
            # 'a____B'). An explicit '__' typed in the pattern is honored.
            # Mirrors mayatk's Naming.rename.
            if collapse_padding and "__" not in to:
                collapsed = ptk.collapse_delimiter_runs(new_name)
                if collapsed:
                    new_name = collapsed
            obj = name_to_obj.get(old_name)
            if obj is not None:
                plan.append((obj, old_name, new_name))

        if not plan and objects:
            cls.logger.warning(f"No objects matched '{fltr}'.")
            return [o.name for o in objects]

        title = f"Rename{f' — matching {fltr!r}' if fltr else ''}"
        finals = dict(
            zip((o for o, _old, _new in plan), cls._apply_plan(plan, title, dry_run))
        )
        return [finals.get(o, o.name) for o in objects]

    @classmethod
    def generate_unique_name(cls, base_name, suffix="_", padding=3):
        """A unique object name based on ``base_name`` (``Cube`` → ``Cube_001``) — mirror of
        mayatk's ``generate_unique_name``."""
        import bpy

        if base_name not in bpy.data.objects:
            return base_name
        counter = 1
        while True:
            candidate = cls.strip_illegal_chars(
                f"{base_name}{suffix}{str(counter).zfill(padding)}"
            )
            if candidate not in bpy.data.objects:
                return candidate
            counter += 1

    @staticmethod
    def strip_illegal_chars(input_data, replace_with="_"):
        """Replace characters outside ``[A-Za-z0-9_]`` (engine-export-safe naming). Accepts a string
        or a list of strings. Blender itself is permissive; this sanitizes for FBX/engine pipelines."""
        pattern = re.compile(r"[^a-zA-Z0-9_]")
        if isinstance(input_data, (list, tuple, set)):
            return [pattern.sub(replace_with, s) for s in input_data]
        if isinstance(input_data, str):
            return pattern.sub(replace_with, input_data)
        raise TypeError("Input data must be a string or a list/tuple/set of strings.")

    @classmethod
    def strip_chars(cls, objects, num_chars=1, trailing=False, dry_run=False):
        """Delete ``num_chars`` leading (or ``trailing``) characters from each object's name —
        mirror of mayatk's ``strip_chars``. Returns the new names (one per object renamed)."""
        plan = []
        for o in (o for o in ptk.make_iterable(objects) if o):
            s = o.name
            if num_chars >= len(s):
                cls.logger.warning(
                    f"Skipped '{s}': cannot remove {num_chars} characters from a "
                    f"{len(s)}-character name."
                )
                continue
            new_name = s[:-num_chars] if trailing else s[num_chars:]
            plan.append((o, s, cls.strip_illegal_chars(new_name)))
        return cls._apply_plan(plan, "Strip Chars", dry_run)

    @classmethod
    def set_case(cls, objects, case="capitalize", dry_run=False):
        """Rename objects by Python string case op — ``upper`` / ``lower`` / ``capitalize`` /
        ``swapcase`` / ``title``. Mirror of mayatk's ``set_case``. Returns the new names."""
        plan = [
            (o, o.name, ptk.set_case(o.name, case))
            for o in ptk.make_iterable(objects)
            if o
        ]
        return cls._apply_plan(plan, f"Convert Case ({case})", dry_run)

    @classmethod
    def type_key(cls, item) -> str:
        """Resolve an object (or Material / Image datablock) to its suffix-by-type key.

        An EMPTY is a ``group`` when it has children, else a ``locator``; a
        Material is ``material``, an Image / Texture is ``texture``; any other
        object returns its Blender ``type`` (``MESH``, ``CURVE``, ...), which is
        what ``SUFFIX_TYPES`` and a ``custom_suffixes`` mapping key on.
        """
        import bpy

        if isinstance(item, bpy.types.Material):
            return "material"
        if isinstance(item, (bpy.types.Image, bpy.types.Texture)):
            return "texture"
        if getattr(item, "type", None) == "EMPTY":
            return "group" if item.children else "locator"
        return getattr(item, "type", "") or type(item).__name__

    @classmethod
    def suffix_by_type(
        cls,
        objects,
        group_suffix="_GRP",
        locator_suffix="_LOC",
        joint_suffix="_JNT",
        mesh_suffix="_GEO",
        nurbs_curve_suffix="_CRV",
        camera_suffix="_CAM",
        light_suffix="_LGT",
        display_layer_suffix="_LYR",
        ik_handle_suffix="_IKH",
        nurbs_surface_suffix="_SRF",
        cluster_suffix="_CLS",
        lattice_suffix="_LAT",
        skin_cluster_suffix="_SKN",
        blend_shape_suffix="_BS",
        constraint_suffix="_CON",
        material_suffix="_MAT",
        shading_group_suffix="_SG",
        texture_suffix="_TEX",
        set_suffix="_SET",
        custom_suffixes=None,
        strip=None,
        strip_trailing_ints=False,
        strip_trailing_underscores=False,
        strip_trailing_padding=True,
        dry_run=False,
    ):
        """Append a conventional type suffix (stripping any existing known suffix) — mirror of
        mayatk's ``suffix_by_type``. Blender type map: MESH→mesh, CURVE→nurbs_curve,
        SURFACE→nurbs_surface, CAMERA→camera, LIGHT→light, ARMATURE→joint, LATTICE→lattice,
        EMPTY→group (has children) / locator; Material→material, Image/Texture→texture. The other
        keywords are accepted for parity and name no Blender object type (see module docstring).
        ``custom_suffixes`` maps further Blender object types (``"FONT"``, ``"GPENCIL"``...).
        ``strip`` lists extra suffixes to strip first. ``dry_run`` plans and reports only."""
        given = {
            "group_suffix": group_suffix,
            "locator_suffix": locator_suffix,
            "joint_suffix": joint_suffix,
            "mesh_suffix": mesh_suffix,
            "nurbs_curve_suffix": nurbs_curve_suffix,
            "camera_suffix": camera_suffix,
            "light_suffix": light_suffix,
            "display_layer_suffix": display_layer_suffix,
            "ik_handle_suffix": ik_handle_suffix,
            "nurbs_surface_suffix": nurbs_surface_suffix,
            "cluster_suffix": cluster_suffix,
            "lattice_suffix": lattice_suffix,
            "skin_cluster_suffix": skin_cluster_suffix,
            "blend_shape_suffix": blend_shape_suffix,
            "constraint_suffix": constraint_suffix,
            "material_suffix": material_suffix,
            "shading_group_suffix": shading_group_suffix,
            "texture_suffix": texture_suffix,
            "set_suffix": set_suffix,
        }
        smap = {key: given[kw] for kw, _d, _l, key in cls.SUFFIX_TYPES}
        if custom_suffixes:
            smap.update(custom_suffixes)
        all_suffixes = {s for s in smap.values() if s}
        if strip:
            all_suffixes.update(ptk.make_iterable(strip))
        all_suffixes = sorted(
            all_suffixes, key=len, reverse=True
        )  # '_LSG' before '_SG'

        plan = []
        for o in (o for o in ptk.make_iterable(objects) if o):
            target = smap.get(cls.type_key(o), "")
            base = o.name
            for wrong in (s for s in all_suffixes if s != target):
                if base.endswith(wrong):
                    base = base[: -len(wrong)]
                    break
            if strip_trailing_ints:
                base = ptk.format_suffix(
                    base,
                    suffix="",
                    strip_trailing_ints=True,
                    strip_trailing_alpha=False,
                )
            if strip_trailing_underscores:
                base = re.sub(r"_+$", "", base)
            if strip_trailing_padding:
                cleaned = re.sub(r"_+$", "", base)
                if (
                    cleaned != base
                ):  # underscores were at the end → also drop exposed trailing digits
                    cleaned = re.sub(r"_+$", "", re.sub(r"\d+$", "", cleaned))
                base = cleaned
            new_name = base + target if (target and not base.endswith(target)) else base
            plan.append((o, o.name, cls.strip_illegal_chars(new_name)))
        return cls._apply_plan(plan, "Suffix By Type", dry_run)

    @classmethod
    def append_location_based_suffix(
        cls,
        objects,
        first_obj_as_ref=False,
        alphabetical=False,
        strip_trailing_ints=True,
        strip_defined_suffixes=True,
        valid_suffixes=None,
        reverse=False,
        independent_groups=False,
        dry_run=False,
    ):
        """Suffix objects by their distance from a reference point (origin, or the first object's
        bbox center when ``first_obj_as_ref``) — ``_A``/``_B`` (``alphabetical``, ≤26) or ``_01``/
        ``_02``. Mirror of mayatk's ``append_location_based_suffix`` (uses ``order_by_distance``).
        ``dry_run`` plans and reports only. Returns the final names in distance order."""
        import bpy

        from blendertk.xform_utils._xform_utils import XformUtils

        objects = [o for o in ptk.make_iterable(objects) if o]
        if not objects:
            return []

        # order_by_distance / get_world_bbox read matrix_world — settle the depsgraph first so a
        # just-moved (or just-created) object's world position is current, not stale at the origin.
        bpy.context.view_layer.update()

        reference_point = (0.0, 0.0, 0.0)
        if first_obj_as_ref:
            mn, mx = XformUtils.get_world_bbox(objects[0])
            reference_point = tuple((mn[i] + mx[i]) / 2.0 for i in range(3))

        strip_for_grouping = strip_defined_suffixes and not independent_groups
        sorted_suffixes = sorted(valid_suffixes or [], key=len, reverse=True)

        def base_of(name):
            while True:
                before = name
                if strip_trailing_ints and name and name[-1].isdigit():
                    m = re.search(r"(_\d+|\d+)$", name)
                    if m:
                        name = name[: m.start()]
                if strip_for_grouping and sorted_suffixes:
                    for s in sorted_suffixes:
                        if name.endswith(s):
                            name = name[: -len(s)]
                            break
                if name == before:
                    return name

        def suffixes_for(n):
            if alphabetical and n <= 26:
                return list(string.ascii_uppercase)[:n]
            pad = max(2, len(str(n)))
            return [str(i + 1).zfill(pad) for i in range(n)]

        new_names = {}
        if independent_groups:
            groups = {}
            for o in objects:
                groups.setdefault(base_of(o.name), []).append(o)
            for base, members in groups.items():
                ordered = XformUtils.order_by_distance(
                    members, reference_point=reference_point, reverse=reverse
                )
                root, type_suffix = base, ""
                for s in sorted_suffixes:
                    if base.endswith(s):
                        root, type_suffix = base[: -len(s)], s
                        break
                if strip_defined_suffixes:
                    type_suffix = ""
                for i, (o, sfx) in enumerate(zip(ordered, suffixes_for(len(ordered)))):
                    new_names[o] = f"{root}_{sfx}{type_suffix}"
        else:
            ordered = XformUtils.order_by_distance(
                objects, reference_point=reference_point, reverse=reverse
            )
            for o, sfx in zip(ordered, suffixes_for(len(ordered))):
                new_names[o] = f"{base_of(o.name)}_{sfx}"

        plan = [
            (o, o.name, cls.strip_illegal_chars(name)) for o, name in new_names.items()
        ]
        if not dry_run:
            # Two-pass (placeholder then final) so a target name freed by a later rename doesn't
            # collide into a ``.001`` artifact. Unchanged entries are never renamed back by the
            # plan, so they must keep their name here.
            for o, old, new in plan:
                if new != old:
                    o.name = f"__naming_tmp_{id(o)}"
        return cls._apply_plan(plan, "Suffix By Location", dry_run)

    # ------------------------------------------------------------------
    # Plan execution — shared by every operation
    # ------------------------------------------------------------------

    @staticmethod
    def _rename_object(obj, new_name: str) -> str:
        """The :class:`pythontk.RenamePlan` strategy: assign the name, return what Blender kept."""
        obj.name = new_name
        return obj.name  # Blender may append .001 on collision

    @classmethod
    def _object_link(cls, obj, name: str) -> str:
        """Render a report item as a link that selects the object in the viewport."""
        return cls.log_link(name, "select", node=obj.name)

    @classmethod
    def _apply_plan(cls, plan, title: str, dry_run: bool) -> List[str]:
        """Apply ``(obj, old, new)`` entries and report; returns the resulting names."""
        results = ptk.RenamePlan.apply(
            plan,
            cls._rename_object,
            title=title,
            dry_run=dry_run,
            logger=cls.logger,
            link=cls._object_link,
            unit="object",
        )
        return [new for _old, new in results]


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
