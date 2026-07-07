# !/usr/bin/python
# coding=utf-8
"""Material utilities — mirror of mayatk's ``MatUtils`` public names where the concepts align:
Blender materials live on ``obj.material_slots`` / ``bpy.data.materials`` (no shading engines),
and textures are ``TEX_IMAGE`` nodes in the material's node tree (no ``file`` nodes).

Datablock-level (no viewport) → **headless-testable**. ``import bpy`` is deferred into the
call bodies (no import side effects). The material/texture *report* formatting is delegated to
``pythontk.MatReport`` (DCC-agnostic SSoT, shared with mayatk); texture metadata is read from the
Blender image datablock directly (Blender's bundled Python ships no PIL).
"""
import os
import random

import pythontk as ptk


def get_mats(objects):
    """Unique materials assigned to the given object(s), in slot order."""
    seen = []
    for o in ptk.make_iterable(objects):
        for slot in getattr(o, "material_slots", []):
            if slot.material is not None and slot.material not in seen:
                seen.append(slot.material)
    return seen


def create_mat(mat_type="standard", name=""):
    """Create a new material (mirror of ``mtk.MatUtils.create_mat``).

    ``mat_type='random'`` seeds a random base/viewport color (name defaults to the hex value).
    """
    import bpy

    if mat_type == "random":
        rgb = [random.uniform(0.1, 1.0) for _ in range(3)]
        name = name or "mat_" + "".join(f"{int(c * 255):02x}" for c in rgb)
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        mat.diffuse_color = (*rgb, 1.0)
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        return mat
    mat = bpy.data.materials.new(name or "material")
    mat.use_nodes = True
    return mat


def assign_mat(objects, material):
    """Assign ``material`` to the given object(s) — whole-object assignment (all slots).

    Mirrors Maya's object-level assign; per-face assignment is an edit-mode workflow and is
    intentionally out of scope here.
    """
    for o in ptk.make_iterable(objects):
        data = getattr(o, "data", None)
        if data is None or not hasattr(data, "materials"):
            continue
        data.materials.clear()
        data.materials.append(material)


def find_by_mat_id(material, objects=None):
    """Objects using ``material`` (mirror of ``mtk.find_by_mat_id`` at the object level).

    ``objects=None`` scans the whole scene; otherwise restricts to the given objects.
    """
    import bpy

    pool = ptk.make_iterable(objects) if objects is not None else bpy.data.objects
    return [
        o
        for o in pool
        if any(s.material is material for s in getattr(o, "material_slots", []))
    ]


def select_by_material(material, add=False):
    """Select every scene object using ``material`` (optionally adding to the selection)."""
    import bpy

    if not add:
        for o in bpy.data.objects:
            o.select_set(False)
    users = find_by_mat_id(material)
    for o in users:
        o.select_set(True)
    if users:
        bpy.context.view_layer.objects.active = users[0]
    return users


def reload_textures():
    """Reload every image datablock from disk (mirror of ``mtk.MatUtils.reload_textures``)."""
    import bpy

    for img in bpy.data.images:
        if img.source == "FILE":
            try:
                img.reload()
            except RuntimeError:
                pass


# ------------------------------------------------------------------ scene materials
def _is_gp_material(mat):
    """Grease-pencil materials (attribute renamed across Blender's GPv3 transition)."""
    return bool(getattr(mat, "is_grease_pencil", False))


def get_scene_mats(inc=None, exc=None, sort=False, as_dict=False, exclude_defaults=True, **filter_kwargs):
    """Scene materials with flexible name filtering — mirror of ``mtk.MatUtils.get_scene_mats``.

    Grease-pencil materials are always dropped. ``exclude_defaults`` is accepted for signature
    parity (Blender has no built-in default materials like Maya's ``lambert1``, so there is
    nothing to drop). ``inc`` / ``exc`` filter by name (``pythontk.filter_dict`` semantics).
    Returns a list (``{name: material}`` dict when ``as_dict``), sorted by name when ``sort``.
    """
    import bpy

    mats = [m for m in bpy.data.materials if not _is_gp_material(m)]
    d = {m.name: m for m in mats}
    filtered = ptk.filter_dict(d, keys=True, inc=inc, exc=exc, **filter_kwargs)
    result = list(filtered.values())
    if as_dict:
        dct = {m.name: m for m in result}
        return dict(sorted(dct.items())) if sort else dct
    return sorted(result, key=lambda m: m.name) if sort else result


def is_mat_assigned(mat):
    """True iff ``mat`` is assigned to at least one object (mirror of ``mtk.is_mat_assigned`` —
    the same condition Delete-Unused targets). Blender has no shading engines, so this is a
    direct object-user check."""
    return bool(find_by_mat_id(mat))


def get_mat_swatch_icon(mat, size=(20, 20), fallback_to_blank=True):
    """A ``QIcon`` filled with ``mat``'s viewport display color — mirror of
    ``mtk.MatUtils.get_mat_swatch_icon`` (Maya reads ``baseColor``/``color``; Blender uses
    ``material.diffuse_color``, its viewport swatch). Qt-only (deferred import) → returns
    ``None`` headless / when no Qt binding is present."""
    try:
        from qtpy.QtGui import QPixmap, QColor, QIcon
    except Exception:
        return None
    try:
        r, g, b = (int(max(0.0, min(1.0, c)) * 255) for c in mat.diffuse_color[:3])
        pixmap = QPixmap(size[0], size[1])
        pixmap.fill(QColor.fromRgb(r, g, b))
    except Exception:
        if not fallback_to_blank:
            raise
        pixmap = QPixmap(size[0], size[1])
        pixmap.fill(QColor(255, 255, 255, 0))
    return QIcon(pixmap)


# ------------------------------------------------------------------ textures (TEX_IMAGE nodes)
def _material_image_nodes(mat):
    """``(node, image)`` pairs for the image-texture nodes of ``mat``'s node tree."""
    nt = getattr(mat, "node_tree", None)
    if not nt:
        return []
    return [(n, n.image) for n in nt.nodes if n.type == "TEX_IMAGE" and n.image]


def _mat_surface_type(mat):
    """A human-readable material 'type' for the report — the surface shader node's label
    (e.g. ``Principled BSDF``), the Blender analogue of Maya's ``cmds.nodeType``. Falls back
    to ``"Material"``."""
    nt = getattr(mat, "node_tree", None)
    if nt:
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        surf = out.inputs.get("Surface") if out else None
        if surf is not None and surf.is_linked:
            return surf.links[0].from_node.bl_label
    return "Material"


def _abspath(img):
    """Absolute, normalized path of an image datablock's file (or '' when unset)."""
    import bpy

    fp = getattr(img, "filepath", "") or ""
    if not fp:
        return ""
    try:
        return os.path.normpath(bpy.path.abspath(fp))
    except Exception:
        return os.path.normpath(fp)


def _image_meta(img):
    """Texture metadata read from the Blender image datablock (no PIL): path / name / size /
    width / height / mode / format / bit_depth."""
    path = _abspath(img)
    size = os.path.getsize(path) if path and os.path.exists(path) else None
    w, h = (int(img.size[0]), int(img.size[1])) if len(img.size) >= 2 else (0, 0)
    mode = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}.get(img.channels, f"{img.channels}ch")
    return {
        "path": path,
        "name": os.path.basename(path) if path else img.name,
        "size": size,
        "width": w,
        "height": h,
        "mode": mode,
        "format": img.file_format or None,
        "bit_depth": f"{img.depth}bit" if img.depth else None,
    }


def get_texture_paths(objects=None, materials=None, absolute=True):
    """Unique texture file paths in scope — mirror of ``mtk.MatUtils.get_texture_paths``.

    Scope resolves to materials (from ``objects`` if given, else ``materials``, else the whole
    scene); reads the ``filepath`` of each material's image-texture nodes. ``absolute`` resolves
    against the .blend (Blender ``//`` relative paths)."""
    mats = _resolve_materials(objects, materials)
    paths = []
    for mat in mats:
        for _node, img in _material_image_nodes(mat):
            p = _abspath(img) if absolute else (getattr(img, "filepath", "") or "")
            if p:
                paths.append(p)
    return list(dict.fromkeys(paths))


def get_texture_info(objects=None, materials=None):
    """Image metadata for the textures in scope — mirror of ``mtk.MatUtils.get_texture_info``.
    Built from the Blender image datablocks (native size/format; no PIL)."""
    import bpy

    if objects is None and materials is None:  # whole scene → every loaded file image
        images = [i for i in bpy.data.images if i.source == "FILE"]
    else:
        images = [
            img for mat in _resolve_materials(objects, materials)
            for _n, img in _material_image_nodes(mat)
        ]
    seen, info = set(), []
    for img in images:
        key = _abspath(img) or img.name
        if key not in seen:
            seen.add(key)
            info.append(_image_meta(img))
    return info


def _resolve_materials(objects=None, materials=None):
    """Resolve a material scope: explicit ``materials`` win, else materials of ``objects``,
    else every scene material. An explicit empty iterable means 'no scope' → []."""
    import bpy

    if materials is not None:
        return [m for m in ptk.make_iterable(materials) if m]
    if objects is not None:
        return get_mats(objects)
    return [m for m in bpy.data.materials if not _is_gp_material(m)]


def get_mat_info(
    materials=None,
    objects=None,
    optimize_check=False,
    progress_callback=None,
    exclude_defaults=False,
    exclude_unassigned=False,
    include_textures=True,
    include_image_metadata=True,
    **optimize_kwargs,
):
    """Aggregate per-material info (name, type, textures + image metadata) — mirror of
    ``mtk.MatUtils.get_mat_info``; produces the same record schema consumed by
    ``pythontk.MatReport``. ``exclude_defaults`` is a no-op in Blender (no built-in defaults).
    ``optimize_check`` runs ``ptk.MapOptimizer.assess`` per texture (best-effort — degrades to an
    ``optimization`` error entry when the image stack is unavailable in Blender's Python)."""
    mats = _resolve_materials(objects, materials)
    if exclude_unassigned and mats:
        mats = [m for m in mats if is_mat_assigned(m)]

    need_image = include_image_metadata or optimize_check
    records, total = [], len(mats)
    for i, mat in enumerate(mats):
        if progress_callback:
            progress_callback(i, total, f"Reading material: {mat.name}")
        tex_entries = []
        if include_textures:
            for node, img in _material_image_nodes(mat):
                meta = _image_meta(img)
                entry = {"file_node": node.name, "path": meta["path"], "name": meta["name"],
                         "size": meta["size"]}
                if include_image_metadata:
                    entry.update({k: meta[k] for k in ("width", "height", "mode", "format", "bit_depth")})
                if optimize_check and meta["path"]:
                    try:
                        entry["optimization"] = ptk.MapOptimizer.assess(meta["path"], **optimize_kwargs)
                    except Exception as e:  # Blender Python often lacks PIL/cv2
                        entry["optimization"] = {"error": str(e)}
                tex_entries.append(entry)
        records.append({
            "material": mat.name,
            "type": _mat_surface_type(mat),
            "textures": tex_entries,
        })
    if progress_callback and total:
        progress_callback(total, total, "Done")
    return records


def format_mat_info_html(records):
    """Render :func:`get_mat_info` output as styled HTML (delegates to ``pythontk.MatReport``)."""
    return ptk.MatReport.format_mat_info_html(records)


def format_texture_info_html(info_list):
    """Render :func:`get_texture_info` output as styled HTML (delegates to ``pythontk.MatReport``)."""
    return ptk.MatReport.format_texture_info_html(info_list)


# ------------------------------------------------------------------ cleanup
def find_materials_with_duplicate_textures(materials=None):
    """Groups of materials that reference the *same* set of texture files — mirror of
    ``mtk.MatUtils.find_materials_with_duplicate_textures``. Materials with no textures are
    skipped. ``materials=None`` scans every scene material (the default); pass an explicit list
    to scope the scan (e.g. scene_exporter's export-object set via ``get_mats(objects)``).
    Returns a list of duplicate groups (each 2+ materials)."""
    groups = {}
    for mat in (materials if materials is not None else get_scene_mats()):
        sig = tuple(sorted({_abspath(img) for _n, img in _material_image_nodes(mat) if _abspath(img)}))
        if sig:
            groups.setdefault(sig, []).append(mat)
    return [g for g in groups.values() if len(g) > 1]


def reassign_duplicate_materials(duplicate_groups, delete=True):
    """Reassign every object using a duplicate to the group's first (canonical) material, then
    optionally delete the now-orphaned duplicates — mirror of
    ``mtk.MatUtils.reassign_duplicate_materials``. Returns the number of slots reassigned."""
    import bpy

    reassigned = 0
    for group in duplicate_groups:
        if len(group) < 2:
            continue
        keep, dups = group[0], group[1:]
        for dup in dups:
            for obj in find_by_mat_id(dup):
                for slot in obj.material_slots:
                    if slot.material is dup:
                        slot.material = keep
                        reassigned += 1
            if delete and not _is_gp_material(dup):
                try:
                    bpy.data.materials.remove(dup)
                except Exception:
                    pass
    return reassigned


def delete_unused_materials():
    """Delete materials assigned to no object — mirror of Maya's *Delete Unused Materials*.
    Respects Blender's fake-user convention (a material the user explicitly marked to keep is
    left alone) and skips grease-pencil materials. Returns the removed material names."""
    import bpy

    removed = []
    for mat in list(bpy.data.materials):
        if _is_gp_material(mat) or mat.use_fake_user:
            continue
        if not find_by_mat_id(mat):
            removed.append(mat.name)
            try:
                bpy.data.materials.remove(mat)
            except Exception:
                pass
    return removed


def graph_materials(materials, mode=None):
    """Open the Shader Editor focused on ``materials`` — the Blender analogue of Maya's
    ``graph_materials`` (Hypershade). Activates an object using the first material (with that
    material as the active slot) so the editor shows its node graph, then opens a Shader Editor
    window. GUI-only. ``mode`` is accepted for signature parity (no Blender analogue)."""
    import bpy

    from blendertk.ui_utils._ui_utils import open_editor

    mats = _resolve_materials(materials=materials)
    if not mats:
        return None
    mat = mats[0]
    users = find_by_mat_id(mat)
    if users:
        obj = users[0]
        bpy.context.view_layer.objects.active = obj
        for idx, slot in enumerate(obj.material_slots):
            if slot.material is mat:
                obj.active_material_index = idx
                break
    return open_editor("Shader Editor")


# ---------------------------------------------------------------------------------------------
# Texture path management (backs the Texture Path Editor panel) — mirror of mayatk's
# texture_path_editor. Blender image datablocks carry the path (``img.filepath``), so this is the
# datablock analogue of Maya's file-node re-pathing; all headless-testable (no bpy.ops).
# ---------------------------------------------------------------------------------------------
def get_image_records():
    """Every FILE-backed image datablock as a record for the Texture Path Editor:
    ``{name, image, filepath, abspath, exists, users}``. ``image`` is the live datablock
    (repath/select use it); the rest are display/decision fields."""
    import bpy

    records = []
    for img in bpy.data.images:
        if img.source != "FILE":
            continue
        ap = _abspath(img)
        records.append(
            {
                "name": img.name,
                "image": img,
                "filepath": getattr(img, "filepath", "") or "",
                "abspath": ap,
                "exists": bool(ap and os.path.exists(ap)),
                "users": img.users,
            }
        )
    return records


def repath_image(image, new_path, reload=True):
    """Point ``image`` (datablock or name) at ``new_path`` and reload it — mirror of the Texture
    Path Editor's per-row repath. Returns True on success."""
    import bpy

    img = bpy.data.images.get(image) if isinstance(image, str) else image
    if img is None:
        return False
    img.filepath = new_path
    if reload:
        try:
            img.reload()
        except RuntimeError:
            pass
    return True


def to_project_relative(abspath, blenddir=None):
    """Convert an absolute path to a Blender ``//``-relative path when it falls under the saved
    .blend's own directory; otherwise return the normalized absolute path unchanged.

    The Blender analogue of mayatk's Texture Path Editor ``_project_relative_converter`` (which
    rewrites a path relative to *sourceimages* when it lands inside that folder). Shared by every
    workflow that (re)points an image at a resolved file — :func:`set_texture_directory`,
    :func:`find_and_copy_textures`, :func:`resolve_missing_textures`, and the ``"relative"`` mode
    of :func:`normalize_texture_paths` — so a repathed image always ends up relative when possible,
    the same way every one of mayatk's equivalent commands does.
    """
    if blenddir is None:
        import bpy

        blenddir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""
    ap = os.path.normpath(abspath)
    if not blenddir:
        return ap
    blenddir_norm = os.path.normpath(blenddir)
    try:
        if os.path.commonpath([ap, blenddir_norm]) != blenddir_norm:
            return ap
    except ValueError:  # different drive — can't be relative
        return ap
    return "//" + os.path.relpath(ap, blenddir_norm).replace("\\", "/")


def _map_type_and_base(orig_stem):
    """``(map_type, base_name_lower)`` for an ORIGINAL-CASE stem via the shared MapFactory.

    Resolution is done on the original case because short map aliases (``AO``, ``MS``, …) are
    case-sensitive — lowercasing first would silently drop them.
    """
    fname = orig_stem + ".png"  # extension is irrelevant to map-type / base resolution
    try:
        mt = ptk.MapFactory.resolve_map_type(fname, key=True)
    except Exception:
        mt = None
    try:
        base = ptk.MapFactory.get_base_texture_name(fname).lower()
    except Exception:
        base = orig_stem.lower()
    return mt, base


def _resolve_by_map_type(target_orig_stem, cand_meta, stem_index):
    """Map-type-aware fuzzy resolve (mayatk's 'Texture' strategy): restrict candidates to files of
    the SAME map type (AO/Normal/Roughness/…), then fuzzy-match the map-stripped base name — so an
    ``_AO`` is never repathed to a ``_Normal``. Returns a path or None.

    ``cand_meta`` is the pre-computed ``[(stem_key_lower, base_lower, map_type), …]`` for the
    search index (``stem_key_lower`` indexes ``stem_index``).
    """
    target_map, target_base = _map_type_and_base(target_orig_stem)
    if not target_map:
        return None
    same_keys = [k for k, _b, mt in cand_meta if mt == target_map]
    same_bases = [b for _k, b, mt in cand_meta if mt == target_map]
    if not same_bases:
        return None
    match, _score, status, _strat = ptk.FuzzyMatcher.find_with_fallbacks(
        target_base, same_bases, strategies=["substring", "ratio"], score_threshold=0.5,
    )
    if status != "unique":
        return None
    return stem_index.get(same_keys[same_bases.index(match)])


def resolve_missing_textures(
    search_dir, recursive=True, stem=False, texture=False, fuzzy=False, images=None
):
    """Repath missing FILE images within ``search_dir`` — the Blender analogue of Maya's Texture
    Path Editor 'Resolve Missing' and Blender's native Find Missing Files.

    A cascade of progressively looser strategies (safest first, shallowest match wins):
      * exact basename match (same name + extension) — always on;
      * ``stem``: same name, *any* extension (e.g. a missing ``.tga`` resolved by a ``.png``);
      * ``texture``: map-type-aware fuzzy — restrict candidates to the same map type (so an ``_AO``
        never repaths to a ``_Normal``), then fuzzy-match the base name (shared ``ptk.MapFactory``);
      * ``fuzzy``: loose stem match via ``ptk.FuzzyMatcher`` (substring/ratio).

    ``images=None`` scans every FILE image in the .blend (the default); pass an explicit list to
    restrict the scan (the Texture Path Editor's selection-aware scope — mirrors mayatk's
    ``file_nodes`` parameter on its Resolve Missing Textures command).

    Returns the count resolved.
    """
    if not (search_dir and os.path.isdir(search_dir)):
        return 0
    index = {}  # lowercase basename (with ext) -> first (shallowest) path found
    stem_index = {}  # lowercase stem (no ext) -> path (for the stem + fuzzy tiers)
    orig_stems = {}  # lowercase stem -> original-case stem (for case-sensitive map-type resolve)
    walker = os.walk(search_dir) if recursive else [
        (search_dir, [], [f for f in os.listdir(search_dir)
                          if os.path.isfile(os.path.join(search_dir, f))])
    ]
    for root, _dirs, files in walker:
        for f in files:
            index.setdefault(f.lower(), os.path.join(root, f))
            stem_orig = os.path.splitext(f)[0]
            key = stem_orig.lower()
            stem_index.setdefault(key, os.path.join(root, f))
            orig_stems.setdefault(key, stem_orig)
    stems = list(stem_index.keys())
    # Per-candidate (stem_key_lower, base_lower, map_type) — built once, only for the texture tier.
    cand_meta = None
    if texture and stems:
        cand_meta = []
        for key, orig in orig_stems.items():
            mt, base = _map_type_and_base(orig)
            cand_meta.append((key, base, mt))
    resolved = 0
    for img in _resolve_images(images):
        ap = _abspath(img)
        if ap and os.path.exists(ap):
            continue
        base = os.path.basename(getattr(img, "filepath", "") or "")
        if not base:
            continue
        target_orig_stem = os.path.splitext(base)[0]
        target_stem = target_orig_stem.lower()
        found = index.get(base.lower())  # exact name + extension
        if not found and stem:
            found = stem_index.get(target_stem)  # same name, any extension
        if not found and cand_meta:
            found = _resolve_by_map_type(target_orig_stem, cand_meta, stem_index)  # same map type
        if not found and fuzzy and stems:
            match, _score, status, _strat = ptk.FuzzyMatcher.find_with_fallbacks(
                target_stem, stems, strategies=["substring", "ratio"], score_threshold=0.6,
            )
            if status == "unique":
                found = stem_index.get(match)
        if found:
            img.filepath = to_project_relative(found)
            try:
                img.reload()
            except RuntimeError:
                pass
            resolved += 1
    return resolved


def normalize_texture_paths(mode="relative", project_dir=None, images=None):
    """Normalize FILE image paths — mirror of the Texture Path Editor's 'Normalize Paths'.

    ``mode``:
      * ``"relative"`` / ``"absolute"`` — rewrite each path relative to / absolute from the saved
        .blend (relative needs a saved file; no-op otherwise).
      * ``"copy"`` / ``"move"`` — bring *external* textures (outside ``project_dir``, default
        ``<blenddir>/textures``) into that folder and repath to them.

    ``images=None`` targets every FILE image in the .blend (the default); pass an explicit list to
    restrict the scope (the Texture Path Editor's selection-aware scope — mirrors mayatk's
    ``file_nodes`` parameter on its Normalize Paths command).

    Returns the number of images changed.
    """
    import bpy

    blenddir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""
    images = _resolve_images(images)
    changed = 0

    if mode in ("copy", "move"):
        if not project_dir:
            if not blenddir:
                return 0
            project_dir = os.path.join(blenddir, "textures")
        os.makedirs(project_dir, exist_ok=True)
        for img in images:
            ap = _abspath(img)
            if not (ap and os.path.exists(ap)):
                continue
            try:
                inside = os.path.commonpath([ap, project_dir]) == os.path.normpath(project_dir)
            except ValueError:  # different drive
                inside = False
            if inside:
                continue
            dst = os.path.join(project_dir, os.path.basename(ap))
            if _safe_relocate(ap, dst, mode) in ("skip", "error"):
                continue  # different-size collision — don't clobber / wrong-rebind
            img.filepath = to_project_relative(dst, blenddir)
            changed += 1
        return changed

    for img in images:  # relative / absolute
        ap = _abspath(img)
        if not ap:
            continue
        if mode == "absolute":
            new = ap
        else:  # relative
            if not blenddir:
                continue
            new = to_project_relative(ap, blenddir)
            if not new.startswith("//"):
                continue  # outside the .blend's folder — can't be made relative
        if new != (getattr(img, "filepath", "") or ""):
            img.filepath = new
            changed += 1
    return changed


def get_image_material_map():
    """``{image-name: [material names]}`` for every FILE image referenced by a material's shader
    graph — backs the Texture Path Editor's Material column + its selection helpers. Qt-free /
    bpy-only, so it is unit-testable headless."""
    import bpy

    mapping = {}
    for mat in bpy.data.materials:
        nt = getattr(mat, "node_tree", None)
        if not nt:
            continue
        for node in nt.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                mapping.setdefault(node.image.name, set()).add(mat.name)
    return {k: sorted(v) for k, v in mapping.items()}


def materials_for_textures(paths):
    """Scene materials whose shader graph references an image at one of ``paths`` (matched by
    normalized absolute path) — backs the Material Updater's *Browse…* scope (Maya's
    ``_materials_from_texture_paths``). Qt-free / bpy-only, so it is unit-testable headless.

    Returns a list of material datablocks (de-duplicated, scene order)."""
    import bpy

    targets = {_norm(p) for p in ptk.make_iterable(paths) if p}
    if not targets:
        return []
    out = []
    for mat in bpy.data.materials:
        for _node, image in _material_image_nodes(mat):
            ap = _abspath(image)
            if ap and _norm(ap) in targets:
                out.append(mat)
                break
    return out


def _resolve_images(images):
    """Coerce ``images`` (datablocks, names, or None=all) to a list of FILE image datablocks."""
    import bpy

    if images is None:
        return [i for i in bpy.data.images if i.source == "FILE"]
    out = []
    for i in ptk.make_iterable(images):
        img = bpy.data.images.get(i) if isinstance(i, str) else i
        if img is not None:
            out.append(img)
    return out


# pythontk's working-space label ("Linear" = raw/data) → Blender's image color-space names.
_DATA_COLOR_SPACE = "Non-Color"
_SRGB_COLOR_SPACE = "sRGB"


def fix_color_spaces(images=None, force_update=False, dry_run=False):
    """Assign each texture image its correct color space by map type — the Blender counterpart of
    ``mtk.Diagnostics.fix_missing_color_spaces``.

    Blender defaults new image textures to *sRGB*, which is wrong for data maps (normal /
    roughness / metallic / height / AO …): they must be *Non-Color* or PBR shading is gamma-wrong.
    The intended space is resolved from each image's filename via ``pythontk.MapFactory`` (the
    shared map-registry SSoT — Base Color / Albedo / Emissive → sRGB, data maps → Non-Color), so
    detection matches Maya's. Images whose map type can't be resolved (HDRIs, UI art, unnamed
    maps) are left untouched.

    Args:
        images: image datablocks (or names) to fix; ``None`` scans every FILE image in the .blend.
        force_update (bool): reassign even when the current space already matches (clears a stale
            reference). Off by default so the result reports only genuine fixes.
        dry_run (bool): report what would change without writing.

    Returns:
        dict: ``{image_name: (old_space, new_space)}`` for each image changed (or, under
        ``dry_run``, that would change).
    """
    import bpy

    changed = {}
    for img in _resolve_images(images):
        if not getattr(img, "filepath", ""):
            continue
        # ``bpy.path.basename`` strips Blender's ``//`` relative prefix, which ``os.path``
        # (ntpath) misreads as a UNC root. "" sentinel → unresolved map type: leave the
        # user's choice intact.
        space = ptk.MapFactory.resolve_color_space(
            bpy.path.basename(img.filepath), default=""
        )
        if not space:
            continue
        target = _SRGB_COLOR_SPACE if space == "sRGB" else _DATA_COLOR_SPACE
        current = img.colorspace_settings.name
        if current == target and not force_update:
            continue
        if not dry_run:
            try:
                img.colorspace_settings.name = target
            except (TypeError, ValueError):
                continue  # target not defined in this OCIO config — skip
        changed[img.name] = (current, target)
    return changed


def _safe_relocate(src, dst, mode):
    """Relocate ``src`` → ``dst`` (``mode`` ``"copy"``/``"move"``) with a same-name collision guard
    — mirror of the Maya Texture Path Editor's policy (DRY'd here across the three relocate ops).

    Returns:
        ``"relocated"`` — the file was written to ``dst``.
        ``"rebind"``    — ``dst`` already holds an identical-size file (reuse it without
                          overwriting; for ``"move"`` the redundant ``src`` is removed), or
                          ``src`` is already at ``dst``.
        ``"skip"``      — ``dst`` holds a DIFFERENT-size file: refuse to overwrite, so we never
                          rebind to the wrong texture (and never destroy the external).
        ``"error"``     — the disk op failed.
    """
    import logging
    import shutil

    if os.path.normpath(src) == os.path.normpath(dst):
        return "rebind"  # already in place
    try:
        if os.path.exists(dst):
            try:
                same = os.path.getsize(src) == os.path.getsize(dst)
            except OSError:
                same = False
            if not same:
                # Mirrors mayatk's ``cmds.warning`` on the same collision — never silently
                # rebind to a wrong texture, never destroy the external file.
                logging.getLogger(__name__).warning(
                    f"'{os.path.basename(dst)}' already exists at destination with a "
                    f"different size; skipping to avoid a wrong-file rebind: {dst}"
                )
                return "skip"  # different file, same name — don't clobber / wrong-rebind
            if mode == "move":
                try:
                    os.remove(src)  # dst is equivalent; the source copy is redundant
                except OSError:
                    pass
            return "rebind"
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(src, dst) if mode == "move" else shutil.copy2(src, dst)
        return "relocated"
    except (OSError, shutil.Error):
        return "error"


def set_texture_directory(images=None, target_dir=None, mode="rewrite"):
    """Repath each image so its file lives directly under ``target_dir`` — mirror of the Texture
    Path Editor's *Set Directory*.

    ``mode``: ``"rewrite"`` (path only), ``"copy"`` / ``"move"`` (relocate the file on disk first,
    then repath). ``images=None`` targets every FILE image. A copy/move whose destination already
    holds a different-size file is skipped (no overwrite, no wrong-file rebind). Returns the number
    repathed.
    """
    if not target_dir:
        return 0
    count = 0
    for img in _resolve_images(images):
        ap = _abspath(img)
        old = getattr(img, "filepath", "") or ""
        if not old:
            continue
        dst = os.path.join(target_dir, os.path.basename(ap or old))
        if mode in ("copy", "move") and ap and os.path.exists(ap):
            if _safe_relocate(ap, dst, mode) in ("skip", "error"):
                continue  # leave the image on its current (valid) path
        img.filepath = to_project_relative(dst)
        try:
            img.reload()
        except RuntimeError:
            pass
        count += 1
    return count


def find_and_copy_textures(images=None, search_dir=None, dest_dir=None, mode="copy"):
    """Search ``search_dir`` recursively for the textures used by ``images`` (matched by basename),
    relocate the matches into ``dest_dir`` (``mode`` copy/move), and repath — mirror of the Texture
    Path Editor's *Find & Copy Textures*. A match whose destination already holds a different-size
    file is skipped (no overwrite, no wrong-file rebind). Returns the number of images repathed."""
    if not (search_dir and os.path.isdir(search_dir) and dest_dir):
        return 0
    wanted = {}  # basename -> image datablock
    for img in _resolve_images(images):
        base = os.path.basename(getattr(img, "filepath", "") or "").lower()
        if base:
            wanted.setdefault(base, img)
    if not wanted:
        return 0
    found = {}  # basename -> source path (shallowest wins)
    for root, _dirs, files in os.walk(search_dir):
        for f in files:
            key = f.lower()
            if key in wanted:
                found.setdefault(key, os.path.join(root, f))
    if not found:
        return 0
    os.makedirs(dest_dir, exist_ok=True)
    count = 0
    for key, src in found.items():
        dst = os.path.join(dest_dir, os.path.basename(src))
        if _safe_relocate(src, dst, mode) in ("skip", "error"):
            continue  # different-size collision — don't clobber / wrong-rebind
        wanted[key].filepath = to_project_relative(dst)
        try:
            wanted[key].reload()
        except RuntimeError:
            pass
        count += 1
    return count


def format_texture_paths_html(records=None):
    """Render :func:`get_image_records` as an HTML table for the panel/report (missing flagged)."""
    if records is None:
        records = get_image_records()
    if not records:
        return "<h3>Texture Paths</h3><p>No file textures in the scene.</p>"
    rows = "".join(
        "<tr>"
        f"<td>{'⚠ ' if not r['exists'] else ''}<b>{_html_escape(r['name'])}</b></td>"
        f"<td>{_html_escape(r['filepath'])}</td>"
        f"<td align='right'>{r['users']}</td>"
        "</tr>"
        for r in records
    )
    missing = sum(1 for r in records if not r["exists"])
    head = f"<h3>Texture Paths — {len(records)} image(s), {missing} missing</h3>"
    return (
        head + "<table cellspacing='6'>"
        "<tr><th align='left'>Image</th><th align='left'>Path</th><th>Users</th></tr>"
        f"{rows}</table>"
    )


def _html_escape(s):
    import html

    return html.escape(str(s))


# ---------------------------------------------------------------------------------------------
# Shader templates (backs the Shader Templates panel) — the Blender analogue of Maya's shader
# templates: quick-create / apply a Principled-BSDF preset. Input labels are matched leniently
# (Blender 4.x renamed several) and unknown inputs are skipped — version-tolerant.
# ---------------------------------------------------------------------------------------------
SHADER_TEMPLATES = {
    "Metal": {"Metallic": 1.0, "Roughness": 0.25},
    "Brushed Metal": {"Metallic": 1.0, "Roughness": 0.5},
    "Rough Metal": {"Metallic": 1.0, "Roughness": 0.8},
    "Plastic": {"Metallic": 0.0, "Roughness": 0.35},
    "Rubber": {"Metallic": 0.0, "Roughness": 0.7},
    "Glass": {"Metallic": 0.0, "Roughness": 0.0, "IOR": 1.45, "Transmission Weight": 1.0},
    "Clearcoat": {"Metallic": 0.0, "Roughness": 0.3, "Coat Weight": 1.0},
    "Emission": {"Emission Strength": 2.0},
    "Skin (SSS)": {"Roughness": 0.4, "Subsurface Weight": 0.2},
}


def get_shader_templates():
    """The available Principled-BSDF template names (mirror of Maya's Shader Templates list)."""
    return list(SHADER_TEMPLATES)


def _principled_node(mat):
    """The material's Principled BSDF node, or None."""
    if not (mat and getattr(mat, "use_nodes", False)):
        return None
    return next(
        (n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None
    )


def _set_principled_inputs(node, params):
    """Set named Principled-BSDF inputs from a ``{label: value}`` dict (skipping inputs absent in
    this Blender version). Returns the labels actually set. Shared by apply_shader_template +
    restore_material's parameter-preset path."""
    applied = []
    for label, value in (params or {}).items():
        inp = node.inputs.get(label) if node is not None else None
        if inp is not None:
            try:
                inp.default_value = value
                applied.append(label)
            except (TypeError, ValueError):
                pass
    return applied


def apply_shader_template(material, template):
    """Apply a Principled-BSDF template preset to ``material``'s shader. Unknown inputs (across
    Blender versions) are skipped. Returns the list of inputs actually set."""
    params = SHADER_TEMPLATES.get(template)
    node = _principled_node(material)
    if params is None or node is None:
        return []
    applied = _set_principled_inputs(node, params)
    if template == "Emission":
        ec = node.inputs.get("Emission Color")
        if ec is not None:
            ec.default_value = (1.0, 1.0, 1.0, 1.0)
    return applied


def create_shader_template(template, name=None):
    """Create a new node-based material configured from a Principled-BSDF ``template`` — mirror of
    Maya's Shader Templates 'create new'. Returns the material."""
    mat = create_mat("standard", name=name or template)
    apply_shader_template(mat, template)
    return mat


# Curated, JSON-safe node properties captured by serialize_material (only those a node actually has
# are stored). Enum/bool/int — all round-trip cleanly; left unset, the node keeps its created default.
_GRAPH_NODE_PROPS = (
    "blend_type", "operation", "data_type", "space", "invert", "mode", "vector_type",
    "projection", "extension", "interpolation", "use_clamp", "clamp", "distribution",
)


def _json_socket_value(value):
    """Coerce a socket ``default_value`` (float / int / bool / Color / Vector) to a JSON-safe form."""
    if hasattr(value, "__len__") and not isinstance(value, str):
        return [float(x) for x in value]  # Color / Vector → list
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return float(value)
    return value  # str / enum


def serialize_material(material):
    """Capture a material's shader node graph as a portable, JSON-safe dict — the Blender analogue of
    mayatk's ``GraphSaver``. Records each node's type, location, curated props and *unlinked* input
    values, plus the links by node-name + socket index. Image nodes store their resolved **map type**
    (via the shared ``ptk.MapFactory``) rather than the absolute path, so :func:`restore_material` can
    rebind **fresh** textures by map type. Returns ``{"nodes": [...], "links": [...]}`` (empty when the
    material has no node tree).

    Socket links use positional indices (stable within a Blender version) — see
    :func:`restore_material` for the round-trip.
    """
    nt = getattr(material, "node_tree", None)
    if not (material and getattr(material, "use_nodes", False) and nt):
        return {"nodes": [], "links": []}

    nodes = []
    for n in nt.nodes:
        entry = {
            "name": n.name,
            "bl_idname": n.bl_idname,
            "location": [round(n.location.x, 1), round(n.location.y, 1)],
            "inputs": {},
            "props": {},
        }
        for idx, sock in enumerate(n.inputs):
            if sock.is_linked or not hasattr(sock, "default_value"):
                continue
            entry["inputs"][str(idx)] = _json_socket_value(sock.default_value)
        for prop in _GRAPH_NODE_PROPS:
            if hasattr(n, prop):
                entry["props"][prop] = getattr(n, prop)
        if n.bl_idname == "ShaderNodeTexImage" and getattr(n, "image", None):
            entry["map_type"] = ptk.MapFactory.resolve_map_type(n.image.name, key=True)
            entry["colorspace"] = n.image.colorspace_settings.name
        nodes.append(entry)

    links = []
    for lk in nt.links:
        try:
            fi = list(lk.from_node.outputs).index(lk.from_socket)
            ti = list(lk.to_node.inputs).index(lk.to_socket)
        except ValueError:
            continue
        links.append({
            "from_node": lk.from_node.name, "from_socket": fi,
            "to_node": lk.to_node.name, "to_socket": ti,
        })
    return {"nodes": nodes, "links": links}


def restore_material(data, name=None, textures=None):
    """Rebuild a material from a :func:`serialize_material` dict — the Blender analogue of mayatk's
    ``GraphRestorer``. Recreates the nodes (type, location, props, unlinked input values) and links,
    and **rebinds fresh textures by map type**: each ``ShaderNodeTexImage`` is matched against
    ``textures`` (first file per map type wins, via the shared ``ptk.MapFactory``) and loaded with its
    saved color space; an image node with no matching texture is left unbound (mirrors Maya's "missing
    texture for X").

    A parameter-preset shorthand ``{"params": {input: value, …}}`` (no ``"nodes"``) builds a plain
    Principled material with those inputs set — so the fixed built-in presets and full saved graphs
    share one restore path.

    Args:
        data: a ``serialize_material`` dict, or a ``{"params": {...}}`` shorthand.
        name: material name (defaults to ``"ShaderTemplate"``).
        textures: texture file paths to rebind by map type (graph templates only).

    Returns:
        the created material.
    """
    import bpy

    nodes_data = data.get("nodes") or []
    if not nodes_data and isinstance(data.get("params"), dict):
        mat = create_mat("standard", name=name or "ShaderTemplate")
        _set_principled_inputs(_principled_node(mat), data["params"])
        return mat

    mat = bpy.data.materials.new(name or "ShaderTemplate")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()  # drop the default Principled + Output; the snapshot is authoritative

    by_type = {}  # map type -> texture path (first wins) for image rebinding
    for tex in (textures or []):
        if tex and os.path.isfile(tex):
            mt = ptk.MapFactory.resolve_map_type(tex, key=True)
            if mt:
                by_type.setdefault(mt, tex)

    name_map = {}  # serialized node name -> created node (Blender may rename on collision)
    for nd in nodes_data:
        try:
            node = nt.nodes.new(nd["bl_idname"])
        except RuntimeError:
            continue  # node type unavailable in this Blender — skip (links to it drop)
        node.location = nd.get("location", (0, 0))
        for prop, val in (nd.get("props") or {}).items():
            if hasattr(node, prop):
                try:
                    setattr(node, prop, val)
                except (TypeError, ValueError, AttributeError):
                    pass
        if nd["bl_idname"] == "ShaderNodeTexImage":
            tex = by_type.get(nd.get("map_type")) if nd.get("map_type") else None
            if tex:
                node.image = bpy.data.images.load(tex, check_existing=True)
                cs = nd.get("colorspace")
                if cs:
                    try:
                        node.image.colorspace_settings.name = cs
                    except (TypeError, ValueError):
                        pass
        for idx_str, val in (nd.get("inputs") or {}).items():
            idx = int(idx_str)
            if idx < len(node.inputs) and hasattr(node.inputs[idx], "default_value"):
                try:
                    node.inputs[idx].default_value = val
                except (TypeError, ValueError, AttributeError):
                    pass
        name_map[nd["name"]] = node

    for lk in (data.get("links") or []):
        a, b = name_map.get(lk["from_node"]), name_map.get(lk["to_node"])
        if not (a and b):
            continue
        fi, ti = lk["from_socket"], lk["to_socket"]
        if fi < len(a.outputs) and ti < len(b.inputs):
            try:
                nt.links.new(a.outputs[fi], b.inputs[ti])
            except (RuntimeError, IndexError):
                pass
    return mat


def _norm(p):
    """Case/sep-insensitive key for filesystem path comparison."""
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


# Map-type key (``ptk.MapFactory.resolve_map_type``) → (Principled input, is_color) for the
# single-channel maps that wire straight into one Principled BSDF input. (Opacity is handled in
# the dedicated alpha section so an Albedo+Transparency map's alpha can take precedence.)
_PBR_DIRECT = {
    "Metallic": ("Metallic", False),
    "Roughness": ("Roughness", False),
    "Specular": ("Specular IOR Level", False),
    "Subsurface_Scattering": ("Subsurface Weight", False),
}


def create_pbr_material(textures, name=None, normal_direction="OpenGL"):
    """Build a Principled-BSDF material from a set of PBR texture files — Blender mirror of mayatk's
    ``GameShader.create_network`` (the auto-wire-a-shader-from-textures tool, distinct from the
    Shader Templates *parameter* presets).

    Each texture is classified by map type via the SHARED ``ptk.MapFactory.resolve_map_type`` (the
    same SSoT the Material Updater uses), loaded with the correct color space (sRGB for color maps,
    Non-Color for data), and wired into the right Principled input with the needed conversion nodes:
    Normal Map (with a green-flip for DirectX normals), glossiness/smoothness → invert → roughness,
    Ambient Occlusion multiplied into Base Color, Bump/Height → Bump, Displacement on the material
    output, and the **combined game-engine maps** the Maya tool also handles —
    ``Albedo_Transparency`` (RGB→Base Color, A→Alpha), ``Metallic_Smoothness`` (RGB→Metallic,
    A→invert→Roughness), the Unity HDRP mask ``MSAO`` (R→Metallic, G→AO, A→invert→Roughness), and the
    packed ``ORM`` split (R→AO, G→Roughness, B→Metallic). Needs **no image library** (node setup
    only — Blender loads the images), so it runs in Blender's Python as-is.

    For a folder that holds **several** texture sets, use :func:`create_pbr_materials` (the batch
    orchestrator); this builds a single material (first file per map type wins).

    Args:
        textures: texture file paths (a folder is the caller's to expand). First file per map type
            wins; unclassifiable files are skipped.
        name: material name. Defaults to the texture set's base name.
        normal_direction: ``"OpenGL"`` (default) wires the normal directly; ``"DirectX"`` flips the
            green channel first.

    Returns:
        the created material, or None if no texture classified.
    """
    import bpy

    by_type = {}
    for tex in ptk.make_iterable(textures):
        if not (tex and os.path.isfile(tex)):
            continue
        mt = ptk.MapFactory.resolve_map_type(tex, key=True)
        if mt:
            by_type.setdefault(mt, tex)  # first file per map type wins
    if not by_type:
        return None

    # Packed-map priority (mirror mayatk's _create_single_network): a fuller combined map
    # supersedes the narrower ones so they don't fight over Metallic/Roughness/AO.
    if "ORM" in by_type:
        by_type.pop("MSAO", None)
        by_type.pop("Metallic_Smoothness", None)
    elif "MSAO" in by_type:
        by_type.pop("Metallic_Smoothness", None)

    if not name:
        sets = ptk.MapFactory.group_textures_by_set(list(by_type.values()))
        name = next(iter(sets), None) or os.path.splitext(
            os.path.basename(next(iter(by_type.values())))
        )[0]

    mat = create_mat("standard", name=name)
    nt = mat.node_tree
    bsdf = _principled_node(mat)
    output = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)

    state = {"y": 600}

    def _img(path, non_color):
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(path, check_existing=True)
        node.image.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
        node.location = (-900, state["y"])
        state["y"] -= 300
        return node

    def _set_input(input_name, socket):
        target = bsdf.inputs.get(input_name)
        if target is not None and socket is not None:
            nt.links.new(socket, target)

    def _multiply_into_base(ao_socket):
        """AO has no Principled input — multiply it into Base Color (named-socket MixRGB)."""
        mix = nt.nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MULTIPLY"
        mix.inputs["Fac"].default_value = 1.0
        mix.location = (-500, 580)
        base_in = bsdf.inputs.get("Base Color")
        existing = base_in.links[0].from_socket if (base_in and base_in.is_linked) else None
        if existing is not None:
            nt.links.new(existing, mix.inputs["Color1"])
        else:
            mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
        nt.links.new(ao_socket, mix.inputs["Color2"])
        _set_input("Base Color", mix.outputs["Color"])

    def _enable_alpha_blend():
        """Alpha textures need a non-opaque blend mode to show through in EEVEE."""
        try:
            mat.blend_method = "HASHED"
        except (AttributeError, TypeError):
            pass  # EEVEE-Next / future Blender dropped blend_method

    # --- Base Color / Diffuse / Albedo+Transparency --------------------------
    # Albedo_Transparency is a color map whose alpha carries opacity; it is the lowest-priority
    # base-color source but the highest-priority alpha source.
    base_key = next(
        (k for k in ("Base_Color", "Diffuse", "Albedo_Transparency") if k in by_type), None
    )
    base_node = None
    if base_key:
        base_node = _img(by_type[base_key], non_color=False)
        _set_input("Base Color", base_node.outputs["Color"])

    # --- Alpha: Albedo+Transparency alpha channel wins, else a standalone Opacity map --------
    if base_key == "Albedo_Transparency":
        _set_input("Alpha", base_node.outputs["Alpha"])
        _enable_alpha_blend()
    elif "Opacity" in by_type:
        _set_input("Alpha", _img(by_type["Opacity"], non_color=True).outputs["Color"])
        _enable_alpha_blend()

    # --- Direct single-channel maps ------------------------------------------
    for map_type, (input_name, is_color) in _PBR_DIRECT.items():
        if map_type in by_type:
            _set_input(
                input_name, _img(by_type[map_type], non_color=not is_color).outputs["Color"]
            )

    # --- Glossiness / Smoothness → invert → Roughness ------------------------
    if "Roughness" not in by_type:
        gloss_key = "Glossiness" if "Glossiness" in by_type else (
            "Smoothness" if "Smoothness" in by_type else None
        )
        if gloss_key:
            invert = nt.nodes.new("ShaderNodeInvert")
            invert.location = (-500, 200)
            nt.links.new(_img(by_type[gloss_key], non_color=True).outputs["Color"], invert.inputs["Color"])
            _set_input("Roughness", invert.outputs["Color"])

    # --- Normal (OpenGL direct / DirectX green-flip) -------------------------
    normal_key = next((k for k in ("Normal", "Normal_OpenGL", "Normal_DirectX") if k in by_type), None)
    has_normal = normal_key is not None
    if has_normal:
        normal_color = _img(by_type[normal_key], non_color=True).outputs["Color"]
        is_directx = normal_key == "Normal_DirectX" or normal_direction.lower() == "directx"
        if is_directx:
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            sep.location = (-650, -100)
            nt.links.new(normal_color, sep.inputs["Color"])
            flip = nt.nodes.new("ShaderNodeInvert")
            flip.location = (-500, -150)
            nt.links.new(sep.outputs["Green"], flip.inputs["Color"])
            comb = nt.nodes.new("ShaderNodeCombineColor")
            comb.location = (-350, -100)
            nt.links.new(sep.outputs["Red"], comb.inputs["Red"])
            nt.links.new(flip.outputs["Color"], comb.inputs["Green"])
            nt.links.new(sep.outputs["Blue"], comb.inputs["Blue"])
            normal_color = comb.outputs["Color"]
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.location = (-200, -100)
        nt.links.new(normal_color, nmap.inputs["Color"])
        _set_input("Normal", nmap.outputs["Normal"])

    # --- Bump / Height → Bump (only when there's no normal map to own Normal) -
    bump_key = "Bump" if "Bump" in by_type else ("Height" if "Height" in by_type else None)
    if bump_key and not has_normal:
        bump = nt.nodes.new("ShaderNodeBump")
        bump.location = (-200, -350)
        nt.links.new(_img(by_type[bump_key], non_color=True).outputs["Color"], bump.inputs["Height"])
        _set_input("Normal", bump.outputs["Normal"])

    # --- Emissive ------------------------------------------------------------
    if "Emissive" in by_type:
        _set_input("Emission Color", _img(by_type["Emissive"], non_color=False).outputs["Color"])
        strength = bsdf.inputs.get("Emission Strength")
        if strength is not None:
            strength.default_value = 1.0

    # --- Ambient Occlusion (standalone) → multiply Base Color ----------------
    if "Ambient_Occlusion" in by_type:
        _multiply_into_base(_img(by_type["Ambient_Occlusion"], non_color=True).outputs["Color"])

    # A packed map only drives Roughness when no dedicated roughness/glossiness/smoothness map
    # already owns it (the glossiness→roughness section above runs first).
    has_roughness_src = bool({"Roughness", "Glossiness", "Smoothness"} & by_type.keys())

    # --- Metallic_Smoothness (RGB=Metallic, A=Smoothness→invert→Roughness) ---
    # Unity's metallic-smoothness packing; one image node feeds both inputs.
    if "Metallic_Smoothness" in by_type:
        ms = _img(by_type["Metallic_Smoothness"], non_color=True)
        if "Metallic" not in by_type:
            _set_input("Metallic", ms.outputs["Color"])
        if not has_roughness_src:
            inv = nt.nodes.new("ShaderNodeInvert")
            inv.location = (-500, 60)
            nt.links.new(ms.outputs["Alpha"], inv.inputs["Color"])
            _set_input("Roughness", inv.outputs["Color"])

    # --- MSAO / Unity HDRP mask (R=Metallic, G=AO, A=Smoothness→invert→Rough) -
    if "MSAO" in by_type:
        msao = _img(by_type["MSAO"], non_color=True)
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = (-650, -400)
        nt.links.new(msao.outputs["Color"], sep.inputs["Color"])
        if "Metallic" not in by_type:
            _set_input("Metallic", sep.outputs["Red"])
        if not has_roughness_src:
            inv = nt.nodes.new("ShaderNodeInvert")
            inv.location = (-350, -300)
            nt.links.new(msao.outputs["Alpha"], inv.inputs["Color"])
            _set_input("Roughness", inv.outputs["Color"])
        if "Ambient_Occlusion" not in by_type:
            _multiply_into_base(sep.outputs["Green"])

    # --- Packed ORM (R=AO, G=Roughness, B=Metallic) → Separate Color ---------
    if "ORM" in by_type:
        orm = nt.nodes.new("ShaderNodeSeparateColor")
        orm.location = (-500, -550)
        nt.links.new(_img(by_type["ORM"], non_color=True).outputs["Color"], orm.inputs["Color"])
        if not has_roughness_src:
            _set_input("Roughness", orm.outputs["Green"])
        if "Metallic" not in by_type:
            _set_input("Metallic", orm.outputs["Blue"])
        if "Ambient_Occlusion" not in by_type:
            _multiply_into_base(orm.outputs["Red"])

    # --- Displacement → material output --------------------------------------
    if "Displacement" in by_type and output is not None:
        disp = nt.nodes.new("ShaderNodeDisplacement")
        disp.location = (-200, -600)
        nt.links.new(_img(by_type["Displacement"], non_color=True).outputs["Color"], disp.inputs["Height"])
        nt.links.new(disp.outputs["Displacement"], output.inputs["Displacement"])

    return mat


def create_pbr_materials(textures, name=None, normal_direction="OpenGL", prefix="", suffix=""):
    """Batch builder — Blender mirror of mayatk's ``GameShader.create_network`` batch path.

    Groups ``textures`` into sets by base name (the SHARED
    ``ptk.MapFactory.group_textures_by_set``) and builds one Principled-BSDF material per set via
    :func:`create_pbr_material`. This is the right entry point for "build from a folder", where the
    folder may hold several texture sets (``brick_*``, ``wood_*``, …) — each becomes its own
    material instead of one garbled merge.

    An explicit ``name`` (or a folder holding a single set) forces a single material from all the
    files — mirroring mayatk, where ``group_by_set = not bool(name)``.

    Args:
        textures: texture file paths (a folder is the caller's to expand).
        name: when given, build ONE material with this name from all files (no set grouping).
        normal_direction: ``"OpenGL"`` (default) or ``"DirectX"`` (green-flip) — see
            :func:`create_pbr_material`.
        prefix/suffix: stripped from set keys so an affixed filename (``Mat_brick_Albedo``)
            still groups with its set.

    Returns:
        dict ``{set_name: material | None}`` (``None`` for a set with no classifiable map);
        ``{}`` when nothing on disk classified.
    """
    paths = [t for t in ptk.make_iterable(textures) if t and os.path.isfile(t)]
    if not paths:
        return {}

    def _final(base):  # idempotent prefix/suffix (mayatk's _create_single_network does the same)
        return ptk.StrUtils.apply_affix(base, prefix=prefix, suffix=suffix)

    if name:  # explicit name -> single material from all files (mayatk: group_by_set off)
        final = _final(name)
        return {final: create_pbr_material(paths, name=final, normal_direction=normal_direction)}
    sets = ptk.MapFactory.group_textures_by_set(paths, prefix=prefix, suffix=suffix)
    return {
        _final(set_name): create_pbr_material(files, name=_final(set_name), normal_direction=normal_direction)
        for set_name, files in sets.items()
    }


class MatUpdater(ptk.LoggingMixin):
    """Batch texture reprocessor for scene materials — Blender mirror of mayatk's ``MatUpdater``.

    The reprocessing engine is the SHARED pythontk factory (``ptk.MapRegistry.resolve_config`` +
    ``ptk.MapFactory.prepare_maps``), so format-conversion / max-size / optimize behavior matches
    Maya bit-for-bit; only the *collect* and *reconnect* glue is Blender-idiomatic (image
    datablocks, not Maya ``file`` nodes). An image library is required — Pillow is provisioned on
    demand by :func:`blendertk.ensure_image_deps` (Blender bundles numpy but not PIL).

    **Blender divergence (intentional).** This repaths each material's existing per-channel image
    nodes to their processed equivalents (matched by texture-set + map-type). It does NOT pack or
    rewire ORM / MSAO into the shader graph the way the Maya/StingrayPBS updater does — Blender's
    Principled BSDF consumes *separate* Roughness / Metallic / AO inputs (there is no single packed
    slot), so packing into the live shader would be wrong. Any packed maps the factory writes still
    land on disk (via the Output Folder) for downstream game-engine export.
    """

    @classmethod
    def update_materials(cls, materials=None, config=None, verbose=False, progress_callback=None):
        """Reprocess the textures of ``materials`` and repath their image nodes to the results.

        Args:
            materials: materials (datablocks or names) to update; ``None`` = every scene material.
            config: preset name (str) or dict (may carry a ``preset`` key to inherit) — see
                ``ptk.MapRegistry().get_workflow_presets()``. A truthy ``discover_sourceimages``
                (mirrors mayatk) gap-fills each texture set from the .blend's own folder — the
                nearest Blender analogue of Maya's project ``sourceimages`` folder — unless an
                explicit ``discover_dir`` is already given.
            verbose: INFO-level logging (else WARNING).
            progress_callback: ``cb(current, total, message)`` invoked per material in the loop.

        Returns:
            dict: ``{material_name: {"updated": int, "skipped": int, "files": [str, ...]}}``.
        """
        import logging
        import bpy

        from blendertk.core_utils._core_utils import ensure_image_deps, get_env_info

        cls.set_log_level(logging.INFO if verbose else logging.WARNING)

        cfg = ptk.MapRegistry().resolve_config(config)
        move_to = cfg.get("move_to_folder")
        if move_to and not os.path.isabs(move_to):
            workspace = get_env_info("workspace")
            if workspace:
                move_to = os.path.join(workspace, move_to)
                cfg["move_to_folder"] = move_to

        # Resolve opt-in sibling discovery into a concrete directory for the factory. Mirrors
        # mayatk's ``discover_sourceimages`` -> ``discover_dir`` resolution; Blender has no
        # ``sourceimages`` project-folder convention, so the nearest analogue is the .blend's own
        # directory (the same "workspace" concept already used above for move_to_folder).
        if cfg.pop("discover_sourceimages", False) and not cfg.get("discover_dir"):
            workspace = get_env_info("workspace")
            if workspace:
                cfg["discover_dir"] = workspace
            else:
                cls.logger.warning(
                    "Discovery enabled but the .blend file hasn't been saved (no workspace folder)."
                )

        if materials is None:
            materials = get_scene_mats(sort=True)
        pool = []
        for m in ptk.make_iterable(materials):
            mat = bpy.data.materials.get(m) if isinstance(m, str) else m
            if mat is not None:
                pool.append(mat)
        if not pool:
            cls.logger.info("No materials to update.")
            return {}

        dry_run = bool(cfg.get("dry_run"))

        # 1. Collect each material's on-disk image textures (deduped per material).
        mat_images = {}
        all_files = set()
        for mat in pool:
            recs, seen = [], set()
            for _node, image in _material_image_nodes(mat):
                ap = _abspath(image)
                if ap and os.path.isfile(ap) and ap not in seen:
                    seen.add(ap)
                    recs.append((image, ap))
                    all_files.add(ap)
            if recs:
                mat_images[mat] = recs
        if not all_files:
            cls.logger.info("No file textures found on the given materials.")
            return {}

        # 2. Dry run reports the plan without touching disk or the datablocks.
        if dry_run:
            cls.logger.info(
                f"[dry run] Would reprocess {len(all_files)} texture(s) across "
                f"{len(mat_images)} material(s); no files written."
            )
            return {
                mat.name: {"updated": 0, "skipped": len(recs), "files": []}
                for mat, recs in mat_images.items()
            }

        # 3. Provision the image library on demand (only now that there's real work). Without it
        #    the shared factory can only no-op, so fail clearly instead of silently doing nothing.
        if "PIL" not in ensure_image_deps():
            cls.logger.error(
                "Image library (Pillow) is unavailable and could not be installed into Blender's "
                "Python — cannot reprocess textures."
            )
            return {}

        # 4. Batch reprocess via the shared pythontk factory (the SSoT — same as Maya).
        batch = dict(cfg)
        for k in ("max_workers", "move_to_folder", "dry_run"):
            batch.pop(k, None)
        try:
            processed = ptk.MapFactory.prepare_maps(
                sorted(all_files),
                output_dir=move_to,
                group_by_set=True,
                max_workers=cfg.get("max_workers", 1) or 1,
                **batch,
            )
        except Exception as error:
            cls.logger.error(f"Texture reprocessing failed: {error}")
            return {}

        # 5. Normalize the factory result to ``{set_name: [files]}`` keyed the same way
        #    ``group_textures_by_set`` keys the originals (a bare list == one set).
        orig_sets = ptk.MapFactory.group_textures_by_set(sorted(all_files))
        if isinstance(processed, list):
            set_name = next(iter(orig_sets), "__single__")
            processed = {set_name: processed}
        file_to_set = {
            _norm(f): set_name for set_name, files in orig_sets.items() for f in files
        }
        out_by_set_type, out_by_type = {}, {}
        for set_name, files in processed.items():
            for f in files:
                mt = ptk.MapFactory.resolve_map_type(f, key=True)
                if mt is None:
                    continue
                out_by_set_type[(set_name, mt)] = f
                out_by_type.setdefault(mt, f)
        single_set = len(processed) == 1

        # An input map can be *renamed* on output via the registry's input fallbacks (e.g. a
        # ``Diffuse`` input becomes a ``Base_Color`` output). Invert the fallback table so each
        # original node can find the output map it turned into — matched by exact type first, then
        # by any output type the input is a recognized source for.
        in_to_outs = {}
        for out_type, sources in ptk.MapRegistry().get_fallbacks().items():
            for src in sources:
                in_to_outs.setdefault(src, []).append(out_type)

        def _matched_output(orig, set_name):
            it = ptk.MapFactory.resolve_map_type(orig, key=True)
            for ot in [it] + in_to_outs.get(it, []):
                hit = out_by_set_type.get((set_name, ot))
                if hit is None and single_set:  # fall back to map-type when the set name drifts
                    hit = out_by_type.get(ot)
                if hit and os.path.isfile(hit):
                    return hit
            return None

        # 6. Repath each material's image nodes to the matched processed output.
        results = {}
        total = len(mat_images)
        for i, (mat, recs) in enumerate(mat_images.items()):
            if progress_callback:
                progress_callback(i, total, f"Updating: {mat.name}")
            updated = skipped = 0
            out_files = []
            for image, orig in recs:
                new = _matched_output(orig, file_to_set.get(_norm(orig)))
                if new:
                    repath_image(image, new)
                    out_files.append(new)
                    updated += 1
                else:
                    skipped += 1
            results[mat.name] = {"updated": updated, "skipped": skipped, "files": out_files}
            cls.logger.info(f"{mat.name}: updated {updated}, skipped {skipped}")
        return results


def update_materials(materials=None, config=None, verbose=False, progress_callback=None):
    """Module-level alias for :meth:`MatUpdater.update_materials` (``btk.update_materials``)."""
    return MatUpdater.update_materials(
        materials=materials, config=config, verbose=verbose, progress_callback=progress_callback
    )


class MatUtils:
    """Namespace mirror of mayatk's ``MatUtils`` (helpers also exposed module-level)."""

    get_mats = staticmethod(get_mats)
    create_mat = staticmethod(create_mat)
    assign_mat = staticmethod(assign_mat)
    find_by_mat_id = staticmethod(find_by_mat_id)
    select_by_material = staticmethod(select_by_material)
    reload_textures = staticmethod(reload_textures)
    get_scene_mats = staticmethod(get_scene_mats)
    is_mat_assigned = staticmethod(is_mat_assigned)
    get_mat_swatch_icon = staticmethod(get_mat_swatch_icon)
    get_texture_paths = staticmethod(get_texture_paths)
    get_texture_info = staticmethod(get_texture_info)
    get_mat_info = staticmethod(get_mat_info)
    format_mat_info_html = staticmethod(format_mat_info_html)
    format_texture_info_html = staticmethod(format_texture_info_html)
    find_materials_with_duplicate_textures = staticmethod(find_materials_with_duplicate_textures)
    reassign_duplicate_materials = staticmethod(reassign_duplicate_materials)
    delete_unused_materials = staticmethod(delete_unused_materials)
    graph_materials = staticmethod(graph_materials)
    get_image_records = staticmethod(get_image_records)
    get_image_material_map = staticmethod(get_image_material_map)
    materials_for_textures = staticmethod(materials_for_textures)
    repath_image = staticmethod(repath_image)
    to_project_relative = staticmethod(to_project_relative)
    resolve_missing_textures = staticmethod(resolve_missing_textures)
    normalize_texture_paths = staticmethod(normalize_texture_paths)
    set_texture_directory = staticmethod(set_texture_directory)
    find_and_copy_textures = staticmethod(find_and_copy_textures)
    format_texture_paths_html = staticmethod(format_texture_paths_html)
    get_shader_templates = staticmethod(get_shader_templates)
    apply_shader_template = staticmethod(apply_shader_template)
    create_shader_template = staticmethod(create_shader_template)
    serialize_material = staticmethod(serialize_material)
    restore_material = staticmethod(restore_material)
    create_pbr_material = staticmethod(create_pbr_material)
    create_pbr_materials = staticmethod(create_pbr_materials)
    update_materials = staticmethod(update_materials)
