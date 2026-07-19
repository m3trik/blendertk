# !/usr/bin/python
# coding=utf-8
"""Batch object naming — Blender port of mayatk's ``edit_utils.naming.Naming``.

The pattern-matching / formatting logic is the **shared, DCC-agnostic** ``pythontk`` string layer
(``ptk.find_str_and_format`` / ``find_str`` / ``split_delimited_string`` / ``format_suffix``), so the
behavior mirrors mayatk's; only the node access differs (``cmds.rename`` → ``obj.name``).

Divergences (documented for parity — see ``tentacle/docs/PARITY_PORTING_PLAN.md``): Blender object
names are the leaf already (no ``|`` DAG path / ``:`` namespace to strip) and Blender auto-uniquifies
on collision (``.001``); ``suffix_by_type`` maps Blender object **types** (MESH→_GEO, CURVE→_CRV,
CAMERA→_CAM, LIGHT→_LGT, ARMATURE→_JNT, EMPTY→_GRP when it has children else _LOC); Maya's display
layers (_LYR) have no object-type analogue (collections), so that suffix is unused. ``import bpy`` is
deferred into the call bodies.
"""
import re
import string

import pythontk as ptk


class Naming(ptk.HelpMixin):
    """Batch find / rename / suffix scene objects (mirror of mayatk's ``Naming``)."""

    @classmethod
    def rename(cls, objects, to, fltr="", regex=False, ignore_case=False,
               retain_suffix=False, valid_suffixes=None):
        """Rename objects by pattern — Blender mirror of mayatk's ``Naming.rename``.

        ``to`` formatting tokens (via :func:`pythontk.find_str_and_format`): ``chars`` replace all,
        ``*chars*`` replace-only, ``*chars`` replace-suffix, ``**chars`` append-suffix, ``chars*``
        replace-prefix, ``chars**`` append-prefix. ``fltr`` filters which names match (wildcards or,
        with ``regex``, regex). ``retain_suffix`` re-appends the object's existing type suffix (from
        ``valid_suffixes``). Returns the new names parallel to ``objects``.
        """
        objects = [o for o in ptk.make_iterable(objects) if o]
        name_to_obj = {o.name: o for o in objects}  # Blender object names are globally unique

        # An empty filter means "match all" → "*" (one batch call covers both cases).
        try:
            pairs = ptk.find_str_and_format(
                list(name_to_obj), to, fltr or "*",
                regex=regex, ignore_case=ignore_case, return_orig_strings=True,
            )
        except Exception:  # malformed pattern/filter — leave names unchanged
            return [o.name for o in objects]

        results = {}
        for old_name, new_name in pairs:
            if retain_suffix:
                new_name = cls._retain_suffix(old_name, new_name, valid_suffixes)
            new_name = cls.strip_illegal_chars(new_name)
            obj = name_to_obj.get(old_name)
            if obj is not None:
                obj.name = new_name
                results[obj] = obj.name  # actual (Blender may append .001 on collision)
        return [results.get(o, o.name) for o in objects]

    @staticmethod
    def _retain_suffix(old_name, new_name, valid_suffixes):
        """Re-append ``old_name``'s trailing ``_TYPE`` suffix to ``new_name`` (stripping the digits,
        e.g. ``_GRP1`` → ``_GRP``), replacing ``new_name``'s own type suffix first. Honors
        ``valid_suffixes`` when provided. Mirror of mayatk's inline retain-suffix block."""
        if "_" not in old_name:
            return new_name
        old_suffix_base = old_name[old_name.rfind("_"):].rstrip("0123456789")
        if old_suffix_base == "_":  # trailing token was purely digits, not a type suffix
            return new_name
        if valid_suffixes is not None and old_suffix_base not in valid_suffixes:
            old_suffix_base = ""
        if old_suffix_base and not new_name.endswith(old_suffix_base):
            if "_" in new_name:
                new_suffix_base = new_name[new_name.rfind("_"):].rstrip("0123456789")
                if valid_suffixes is None or new_suffix_base in valid_suffixes:
                    new_name = new_name[: new_name.rfind("_")]
            new_name += old_suffix_base
        return new_name

    @classmethod
    def generate_unique_name(cls, base_name, suffix="_", padding=3):
        """A unique object name based on ``base_name`` (``Cube`` → ``Cube_001``) — mirror of
        mayatk's ``generate_unique_name``."""
        import bpy

        if base_name not in bpy.data.objects:
            return base_name
        counter = 1
        while True:
            candidate = cls.strip_illegal_chars(f"{base_name}{suffix}{str(counter).zfill(padding)}")
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
    def strip_chars(cls, objects, num_chars=1, trailing=False):
        """Delete ``num_chars`` leading (or ``trailing``) characters from each object's name —
        mirror of mayatk's ``strip_chars``. Returns the new names."""
        results = []
        for o in (o for o in ptk.make_iterable(objects) if o):
            s = o.name
            if num_chars >= len(s):
                continue
            new_name = s[:-num_chars] if trailing else s[num_chars:]
            if not new_name:
                continue
            o.name = cls.strip_illegal_chars(new_name)
            results.append(o.name)
        return results

    @staticmethod
    def set_case(objects, case="capitalize"):
        """Rename objects by Python string case op — ``upper`` / ``lower`` / ``capitalize`` /
        ``swapcase`` / ``title``. Mirror of mayatk's ``set_case``."""
        results = []
        for o in (o for o in ptk.make_iterable(objects) if o):
            o.name = getattr(o.name, case)()
            results.append(o.name)
        return results

    @classmethod
    def suffix_by_type(cls, objects, group_suffix="_GRP", locator_suffix="_LOC",
                       joint_suffix="_JNT", mesh_suffix="_GEO", nurbs_curve_suffix="_CRV",
                       camera_suffix="_CAM", light_suffix="_LGT", custom_suffixes=None,
                       strip_trailing_ints=False, strip_trailing_underscores=False,
                       strip_trailing_padding=True):
        """Append a conventional type suffix (stripping any existing known suffix) — mirror of
        mayatk's ``suffix_by_type``. Blender type map: MESH→mesh, CURVE/SURFACE→nurbs_curve,
        CAMERA→camera, LIGHT→light, ARMATURE→joint, EMPTY→group (has children) / locator."""
        smap = {
            "MESH": mesh_suffix, "CURVE": nurbs_curve_suffix, "SURFACE": nurbs_curve_suffix,
            "CAMERA": camera_suffix, "LIGHT": light_suffix, "ARMATURE": joint_suffix,
        }
        all_suffixes = {group_suffix, locator_suffix, joint_suffix, mesh_suffix,
                        nurbs_curve_suffix, camera_suffix, light_suffix}
        if custom_suffixes:
            smap.update(custom_suffixes)
            all_suffixes.update(custom_suffixes.values())

        results = []
        for o in (o for o in ptk.make_iterable(objects) if o):
            if o.type == "EMPTY":
                target = group_suffix if o.children else locator_suffix
            else:
                target = smap.get(o.type, "")
            base = o.name
            for wrong in (s for s in all_suffixes if s and s != target):
                if base.endswith(wrong):
                    base = base[: -len(wrong)]
                    break
            if strip_trailing_ints:
                base = ptk.format_suffix(base, suffix="", strip_trailing_ints=True,
                                         strip_trailing_alpha=False)
            if strip_trailing_underscores:
                base = re.sub(r"_+$", "", base)
            if strip_trailing_padding:
                cleaned = re.sub(r"_+$", "", base)
                if cleaned != base:  # underscores were at the end → also drop exposed trailing digits
                    cleaned = re.sub(r"_+$", "", re.sub(r"\d+$", "", cleaned))
                base = cleaned
            new_name = base + target if (target and not base.endswith(target)) else base
            o.name = cls.strip_illegal_chars(new_name)
            results.append(o.name)
        return results

    @classmethod
    def append_location_based_suffix(cls, objects, first_obj_as_ref=False, alphabetical=False,
                                     strip_trailing_ints=True, strip_defined_suffixes=True,
                                     valid_suffixes=None, reverse=False, independent_groups=False):
        """Suffix objects by their distance from a reference point (origin, or the first object's
        bbox center when ``first_obj_as_ref``) — ``_A``/``_B`` (``alphabetical``, ≤26) or ``_01``/
        ``_02``. Mirror of mayatk's ``append_location_based_suffix`` (uses ``order_by_distance``)."""
        import bpy

        from blendertk.xform_utils._xform_utils import order_by_distance, get_world_bbox

        objects = [o for o in ptk.make_iterable(objects) if o]
        if not objects:
            return []

        # order_by_distance / get_world_bbox read matrix_world — settle the depsgraph first so a
        # just-moved (or just-created) object's world position is current, not stale at the origin.
        bpy.context.view_layer.update()

        reference_point = (0.0, 0.0, 0.0)
        if first_obj_as_ref:
            mn, mx = get_world_bbox(objects[0])
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
                ordered = order_by_distance(members, reference_point=reference_point, reverse=reverse)
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
            ordered = order_by_distance(objects, reference_point=reference_point, reverse=reverse)
            for o, sfx in zip(ordered, suffixes_for(len(ordered))):
                new_names[o] = f"{base_of(o.name)}_{sfx}"

        # Two-pass (placeholder then final) so a target name freed by a later rename doesn't collide
        # into a ``.001`` artifact.
        for o in new_names:
            o.name = f"__naming_tmp_{id(o)}"
        return [cls._set_name(o, name) for o, name in new_names.items()]

    @staticmethod
    def _set_name(obj, name):
        obj.name = Naming.strip_illegal_chars(name)
        return obj.name


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
