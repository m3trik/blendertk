# !/usr/bin/python
# coding=utf-8
"""Transfer a mesh's textures from one UV layout to another -- no rays, no bake.

Blender twin of :mod:`mayatk.uv_utils.texture_transfer` (name + behaviour):
the adapter over :class:`pythontk.UvTransfer`. The engine does the texel
remap; this module supplies what only the host knows -- the triangle
correspondence between the two layouts (``Mesh.loop_triangles``, face-corner
UVs on both sides, so seams and concave faces are handled), which source
material each triangle wears, the maps (or constants) those materials carry,
and where the results go.

Two forms, one code path:

* **mesh -> mesh** -- a source mesh and a target mesh of identical topology
  (the same model re-unwrapped / re-packed, a material consolidation). Pairing
  is by matching object name, else by order.
* **UV map -> UV map** on ONE mesh (``source=None``, ``source_uv_set=...``).

Outputs are written per TARGET material -- one image per channel, sampled
from whichever source material each triangle wears; a source with no map for
a channel contributes its Principled BSDF constant. Normal maps are
re-encoded into the target island's tangent frame.

Deliberately NOT part of the Marmoset bridge (a high->low ray-cast bake); the
bridge only warns when its source and target are coincident, because that job
belongs here.
"""

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pythontk as ptk

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

# Logical channel -> Principled BSDF input names (first present wins). Mirrors
# ``mat_manifest._SLOT_SOCKETS`` for the constants; the maps themselves come
# from ``MatManifest._process_material`` so the two never drift.
_CONSTANT_SOCKETS: Dict[str, Tuple[str, ...]] = {
    "baseColor": ("Base Color",),
    "emission": ("Emission Color", "Emission"),
    "specular": ("Specular IOR Level", "Specular"),
    "roughness": ("Roughness",),
    "metallic": ("Metallic",),
    "opacity": ("Alpha",),
}


class _TextureTransferInternal:
    """Host-side helpers: correspondence, material lookup."""

    # ------------------------------------------------------------ meshes
    @staticmethod
    def _obj(o):
        import bpy

        if isinstance(o, str):
            found = bpy.data.objects.get(o)
            if found is None:
                raise ValueError(f"no object named {o!r}")
            return found
        return o

    @classmethod
    def _mesh(cls, o):
        o = cls._obj(o)
        if getattr(o, "type", None) != "MESH":
            raise ValueError(f"{getattr(o, 'name', o)!r} is not a mesh")
        return o

    @staticmethod
    def _uv_layer_vectors(mesh, name: str) -> "np.ndarray":
        """``(loops, 2)`` float array of the named UV map, per face corner."""
        layer = mesh.uv_layers.get(name)
        if layer is None:
            raise ValueError(f"mesh {mesh.name!r} has no UV map {name!r}")
        n = len(mesh.loops)
        buf = np.empty(n * 2, dtype=np.float32)
        try:  # Blender 3.5+: the UV attribute
            layer.uv.foreach_get("vector", buf)
        except (AttributeError, TypeError):  # older: MeshUVLoop.uv
            layer.data.foreach_get("uv", buf)
        return buf.reshape(-1, 2).astype(float)

    @classmethod
    def topology_matches(cls, a, b) -> Tuple[bool, str]:
        """``(ok, why)`` -- same polygon loop lists on both meshes."""
        ma, mb = cls._mesh(a).data, cls._mesh(b).data
        if len(ma.polygons) != len(mb.polygons) or len(ma.vertices) != len(mb.vertices):
            return False, (
                f"{len(ma.polygons)} faces / {len(ma.vertices)} verts vs "
                f"{len(mb.polygons)} / {len(mb.vertices)}"
            )
        if len(ma.loops) != len(mb.loops):
            return False, "face-corner counts differ"
        ta = np.empty(len(ma.polygons), dtype=np.int32)
        tb = np.empty(len(mb.polygons), dtype=np.int32)
        ma.polygons.foreach_get("loop_total", ta)
        mb.polygons.foreach_get("loop_total", tb)
        if not np.array_equal(ta, tb):
            return False, "per-face vertex counts differ"
        va = np.empty(len(ma.loops), dtype=np.int32)
        vb = np.empty(len(mb.loops), dtype=np.int32)
        ma.loops.foreach_get("vertex_index", va)
        mb.loops.foreach_get("vertex_index", vb)
        if not np.array_equal(va, vb):
            return False, "face vertex order differs"
        return True, ""

    @classmethod
    def positions_match(cls, a, b, tolerance: float = 1e-4) -> bool:
        oa, ob = cls._mesh(a), cls._mesh(b)
        pa = cls._world_points(oa)
        pb = cls._world_points(ob)
        if pa.shape != pb.shape:
            return False
        return float(np.abs(pa - pb).max()) <= tolerance

    @staticmethod
    def _world_points(o) -> "np.ndarray":
        n = len(o.data.vertices)
        buf = np.empty(n * 3, dtype=np.float32)
        o.data.vertices.foreach_get("co", buf)
        pts = buf.reshape(-1, 3).astype(float)
        m = np.array(o.matrix_world, dtype=float)
        return pts @ m[:3, :3].T + m[:3, 3]

    @classmethod
    def auto_source_uv_set(cls, obj) -> str:
        """The UV map *obj*'s materials actually sample their textures through.

        An Image Texture node reads the UV Map node wired into its Vector input
        when there is one, else the mesh's *active render* map -- that binding
        is the ground truth for "which layout were these maps painted for", so
        Auto reads it (mirror of mayatk's ``uvLink`` lookup). Falls back to the
        active map.
        """
        o = cls._mesh(obj)
        mesh = o.data
        names = [uv.name for uv in mesh.uv_layers]
        if not names:
            raise ValueError(f"{o.name} has no UV maps")
        render = next((uv.name for uv in mesh.uv_layers if uv.active_render), None)
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.type != "TEX_IMAGE" or node.image is None:
                    continue
                vec = node.inputs.get("Vector")
                if vec is not None and vec.is_linked:
                    up = vec.links[0].from_node
                    if up.type == "UVMAP" and up.uv_map in names:
                        return up.uv_map
                    continue  # some other mapping: no UV-map claim
                if render:
                    return render
        return (mesh.uv_layers.active.name if mesh.uv_layers.active else None) or names[
            0
        ]

    @classmethod
    def correspondence(
        cls,
        target,
        source=None,
        *,
        source_uv_set: Optional[str] = None,
        target_uv_set: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Per-triangle ``(src_uv, dst_uv, face)`` for *target* vs *source*.

        Triangulates the TARGET once (``Mesh.loop_triangles`` -- Blender's own
        triangulation, indexed by face corner, purely topological) and reads
        both layouts through it, so seams are honoured and the two arrays
        correspond row for row.

        Returns:
            ``{"src_tris", "dst_tris", "faces", "dropped"}`` as the mayatk twin.
        """
        tgt = cls._mesh(target)
        src = cls._mesh(source) if source is not None else tgt
        tm, sm = tgt.data, src.data
        if source is not None:
            src_set = source_uv_set or (
                sm.uv_layers.active.name if sm.uv_layers.active else None
            )
            dst_set = target_uv_set or (
                tm.uv_layers.active.name if tm.uv_layers.active else None
            )
        else:
            # Same mesh: the SOURCE is whichever map the textures are sampled
            # through (where the maps were painted), the target the other one.
            src_set = source_uv_set or cls.auto_source_uv_set(tgt)
            dst_set = target_uv_set or next(
                (uv.name for uv in tm.uv_layers if uv.name != src_set), src_set
            )
        if not dst_set or not src_set:
            raise ValueError(f"target {tgt.name!r} has no UV map")
        if source is None and src_set == dst_set:
            raise ValueError(
                f"UV map -> UV map transfer needs two different maps (both are {dst_set!r})"
            )
        if len(sm.loops) != len(tm.loops):
            raise ValueError("source and target face-corner counts differ")

        tm.calc_loop_triangles()
        n_tri = len(tm.loop_triangles)
        loops = np.empty(n_tri * 3, dtype=np.int32)
        faces = np.empty(n_tri, dtype=np.int32)
        tm.loop_triangles.foreach_get("loops", loops)
        tm.loop_triangles.foreach_get("polygon_index", faces)
        loops = loops.reshape(-1, 3)

        d_uv = cls._uv_layer_vectors(tm, dst_set)
        s_uv = cls._uv_layer_vectors(sm, src_set)
        return {
            "src_tris": s_uv[loops],
            "dst_tris": d_uv[loops],
            "faces": faces.astype(np.int64),
            "dropped": 0,  # Blender UV maps are total over the mesh's corners
            "target_uv_set": dst_set,
        }

    # --------------------------------------------------------- materials
    @classmethod
    def face_materials(cls, obj) -> Tuple[List[Any], "np.ndarray"]:
        """``(materials, per-face index into materials)`` for *obj*."""
        o = cls._mesh(obj)
        mesh = o.data
        idx = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("material_index", idx)
        slots = [s.material for s in o.material_slots]
        mats: List[Any] = []
        remap = np.full(max(len(slots), 1), -1, dtype=np.int64)
        for i, m in enumerate(slots):
            if m is None:
                continue
            if m not in mats:
                mats.append(m)
            remap[i] = mats.index(m)
        per_face = (
            remap[np.clip(idx, 0, len(remap) - 1)]
            if slots
            else np.full(len(mesh.polygons), -1, dtype=np.int64)
        )
        return mats, per_face

    @staticmethod
    def material_maps(material) -> Dict[str, str]:
        """``{channel: absolute texture path}`` for the material's mapped slots."""
        from blendertk.mat_utils.mat_manifest import MatManifest

        return dict(MatManifest._process_material(material))

    @staticmethod
    def material_constant(material, channel: str) -> Optional[Tuple[float, ...]]:
        """The channel's Principled BSDF default value on *material*, or None."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        node = _MatUtilsInternal._principled_node(material)
        if node is None:
            return None
        for name in _CONSTANT_SOCKETS.get(channel, ()):
            sock = node.inputs.get(name)
            if sock is None or sock.is_linked:
                continue
            value = sock.default_value
            try:
                seq = tuple(float(v) for v in value)
                return seq[:3] if len(seq) >= 3 else seq
            except TypeError:
                return (float(value),)
        return None

    @staticmethod
    def pair_by_name(targets: Sequence, sources: Sequence) -> Dict[Any, Any]:
        """Target -> source, by matching object name; leftovers by order."""
        by_name = {s.name: s for s in sources}
        pairs: Dict[Any, Any] = {}
        rest_t: List[Any] = []
        used = set()
        for t in targets:
            s = by_name.get(t.name)
            if s is not None and s.name not in used and s is not t:
                pairs[t] = s
                used.add(s.name)
            else:
                rest_t.append(t)
        rest_s = [s for s in sources if s.name not in used]
        if len(rest_t) != len(rest_s):
            raise ValueError(
                f"cannot pair {len(rest_t)} target(s) with {len(rest_s)} "
                "source(s): give them matching names or equal counts"
            )
        pairs.update(zip(rest_t, rest_s))
        return pairs


class TextureTransfer(ptk.LoggingMixin, _TextureTransferInternal):
    """Move textures between UV layouts of the same mesh(es) -- see module doc."""

    def __init__(self, log_level="INFO"):
        super().__init__()
        self.logger.setLevel(log_level)

    def transfer(
        self,
        targets,
        source=None,
        *,
        source_uv_set: Optional[str] = None,
        target_uv_set: Optional[str] = None,
        channels: Optional[Sequence[str]] = None,
        size: Optional[int] = None,
        supersample: int = 2,
        padding: int = -1,
        output_dir: Optional[str] = None,
        name_format: str = "{material}_{channel}",
        output_name: Optional[str] = None,
        normal_convention: Optional[str] = None,
        source_mask_from_uvs: bool = True,
        assign: bool = False,
        assign_suffix: str = "_TRANSFER",
    ) -> Dict[str, Dict[str, str]]:
        """Transfer the source material(s)' maps onto the target UV layout.

        Same contract as :meth:`mayatk.TextureTransfer.transfer` -- *targets* /
        *source* are Blender objects (or names); *source_uv_set* /
        *target_uv_set* are UV map names. Returns ``{target material name:
        {channel: written path}}``.

        *output_name* names the whole result -- the assigned material AND every
        map wired to it (``<output_name>_<Channel>.png``) -- instead of deriving
        each from the target layout, and suppresses *assign_suffix* (the user
        named the material). A run that keeps two layouts apart appends the
        layout label to each, since one name cannot cover both without their
        maps overwriting each other.

        *output_dir* is absolute (``//`` accepted) or relative to the .blend's
        ``textures`` folder -- see :meth:`resolve_output_dir`.
        """
        if np is None:
            raise RuntimeError("numpy is required")
        # Blender bundles numpy but not Pillow, which the pythontk map IO needs;
        # provision it the way every other image tool here does (idempotent).
        from blendertk.core_utils._core_utils import CoreUtils

        CoreUtils.ensure_image_deps()
        targets = [self._mesh(t) for t in ptk.make_iterable(targets)]
        if not targets:
            raise ValueError("no target meshes")
        sources = (
            [self._mesh(s) for s in ptk.make_iterable(source)]
            if source is not None
            else []
        )
        pairs = (
            self.pair_by_name(targets, sources)
            if sources
            else {t: None for t in targets}
        )
        out_dir = self.resolve_output_dir(output_dir)

        # Bucketed by target UV map then material: the unit of a transfer is
        # a LAYOUT (see ptk.UvTransfer.merge_layouts). Mirror of mayatk.
        by_set: Dict[str, Dict[str, Dict[str, Any]]] = {}
        registry: List[Any] = []
        for tgt, src in pairs.items():
            if src is not None:
                ok, why = self.topology_matches(tgt, src)
                if not ok:
                    raise ValueError(
                        f"{tgt.name} / {src.name}: topology differs ({why})"
                    )
                if not self.positions_match(tgt, src):
                    self.logger.warning(
                        f"{tgt.name}: source and target vertex positions differ; "
                        "colour maps transfer fine, but the normal-map tangent "
                        "frames are only exact for coincident geometry."
                    )
            corr = self.correspondence(
                tgt, src, source_uv_set=source_uv_set, target_uv_set=target_uv_set
            )
            t_mats, t_face = self.face_materials(tgt)
            s_mats, s_face = self.face_materials(src if src is not None else tgt)
            faces = corr["faces"]
            for m in s_mats:
                if m not in registry:
                    registry.append(m)
            s_ids = np.array([registry.index(m) for m in s_mats], dtype=np.int64)
            tri_src = (
                np.where(s_face[faces] >= 0, s_ids[np.maximum(s_face[faces], 0)], -1)
                if len(s_ids)
                else np.full(len(faces), -1, dtype=np.int64)
            )
            tri_tgt = t_face[faces]
            for ti, t_mat in enumerate(t_mats):
                pick = (tri_tgt == ti) & (tri_src >= 0)
                if not pick.any():
                    continue
                bucket = by_set.setdefault(corr["target_uv_set"], {}).setdefault(
                    t_mat.name, {"src": [], "dst": [], "ids": [], "members": []}
                )
                bucket["src"].append(corr["src_tris"][pick])
                bucket["dst"].append(corr["dst_tris"][pick])
                bucket["ids"].append(tri_src[pick])
                bucket["members"].append((tgt, t_mat))
        if not by_set:
            raise ValueError("nothing to transfer: no shaded, UV-mapped faces found")

        source_specs = [
            {
                "maps": self.material_maps(m),
                "constants": {
                    ch: const
                    for ch in ptk.UvTransfer.CHANNEL_TOKENS
                    for const in [self.material_constant(m, ch)]
                    if const is not None
                },
            }
            for m in registry
        ]
        if not any(spec["maps"] for spec in source_specs):
            raise ValueError("no source material carries a texture map to transfer")
        jobs: Dict[str, Dict[str, Any]] = {}
        for uv_set, per_mat in by_set.items():
            per_mat_jobs = {
                name: {
                    "src": np.concatenate(b["src"]),
                    "dst": np.concatenate(b["dst"]),
                    "ids": np.concatenate(b["ids"]).astype(np.int32),
                    "sources": source_specs,
                    "members": list(b["members"]),
                }
                for name, b in per_mat.items()
            }
            merged = ptk.UvTransfer.merge_layouts(per_mat_jobs, uv_set)
            if len(per_mat) > 1:
                self.logger.info(
                    f"UV map {uv_set!r}: {len(per_mat)} target material(s) -> "
                    + (
                        f"one layout ({uv_set})"
                        if len(merged) == 1
                        else f"{len(merged)} overlapping layouts, kept apart"
                    )
                )
            for key, job in merged.items():
                label = key if key not in jobs else f"{uv_set}_{key}"
                jobs[label] = job
        # Mirror of mayatk: an explicit output name renames BOTH halves of
        # the result -- the maps and the material assigned from them.
        stem = (
            ptk.StrUtils.sanitize(output_name, preserve_case=True)
            if output_name
            else ""
        )
        if stem:
            name_format = (
                f"{stem}_{{channel}}"
                if len(jobs) == 1
                else f"{stem}_{{material}}_{{channel}}"
            )
        results = ptk.UvTransfer.transfer_materials(
            jobs,
            output_dir=out_dir,
            channels=channels,
            size=size,
            supersample=supersample,
            padding=padding,
            name_format=name_format,
            normal_convention=normal_convention,
            source_mask_from_uvs=source_mask_from_uvs,
            log=self.logger.info,
        )
        if assign:
            self.assign_results(
                results,
                jobs,
                suffix="" if stem else assign_suffix,
                base_name=stem or None,
            )
        return results

    # ----------------------------------------------------------- helpers
    @classmethod
    def default_output_dir(cls) -> str:
        """Where the maps go when the caller names no directory."""
        base = cls.output_base_dir()
        if base:
            return os.path.join(base, "uv_transfer").replace("\\", "/")
        return ptk.TempArtifacts("uv_transfer", policy="detached").dir_path()

    @staticmethod
    def output_base_dir() -> Optional[str]:
        """The directory a RELATIVE output entry is resolved against.

        The .blend's ``textures`` folder -- the base that makes a stored
        setting portable (it survives the file being moved or copied). None
        for an unsaved file. Twin of mayatk's, which uses ``sourceimages``.
        """
        import bpy

        if not bpy.data.filepath:
            return None
        return os.path.join(
            os.path.dirname(bpy.path.abspath(bpy.data.filepath)), "textures"
        ).replace("\\", "/")

    @classmethod
    def resolve_output_dir(cls, entry: Optional[str] = None) -> str:
        """The absolute output directory for a user-typed *entry*.

        Blank -> :meth:`default_output_dir`. Blender's ``//`` prefix is
        expanded first (it means "beside the .blend", and is rooted as far as
        the user is concerned); a rooted path then wins outright, and anything
        else is a subdirectory of :meth:`output_base_dir` -- the portable
        spelling a UI should store (inverse:
        ``ptk.FileUtils.relativize_output_dir``). Falls back to the default
        when a relative entry has no saved file to resolve against.
        """
        import bpy

        text = (entry or "").strip()
        if not text:
            return cls.default_output_dir()
        if text.startswith("//"):
            text = bpy.path.abspath(text)
        resolved = ptk.FileUtils.resolve_output_dir(text, cls.output_base_dir())
        return resolved or cls.default_output_dir()

    def assign_results(
        self,
        results: Dict[str, Dict[str, str]],
        jobs: Dict[str, Dict[str, Any]],
        suffix: str = "_TRANSFER",
        base_name: Optional[str] = None,
    ) -> Dict[str, str]:
        """One ``<layout><suffix>`` material per output, assigned to its faces.

        *base_name* replaces the layout-derived name (see ``transfer``'s
        ``output_name``): the material becomes ``<base_name>``, or
        ``<base_name>_<layout>`` when the run produced more than one layout.

        Mirror of mayatk: *jobs* carries each output's ``members`` --
        ``(object, target material)`` pairs -- so every face transferred INTO
        the layout lands on the one new material, copied from the first
        member's material and wired to the outputs. Originals are untouched.

        Returns ``{output label: new material name}``.
        """
        import bpy
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal
        from blendertk.mat_utils.mat_manifest import MatManifest

        created: Dict[str, str] = {}
        for label, channels in results.items():
            members = jobs.get(label, {}).get("members") or []
            if not channels or not members:
                continue
            base_mat = members[0][1]
            if base_name:
                new_name = base_name if len(jobs) == 1 else f"{base_name}_{label}"
            else:
                new_name = f"{label}{suffix}"
            # Resolve which faces each member contributes BEFORE the datablock
            # below is freed. With an explicit output_name a second run's target
            # material IS the one the previous run assigned, so `remove` frees
            # the very Material these members hold: touching `t_mat` afterwards
            # is a dangling StructRNA, and re-querying finds nothing to match.
            # The vacated slot index rides along so the re-run reuses it instead
            # of appending an empty slot per pass. Mirror of mayatk's ordering.
            per_object: List[Any] = []
            for obj, t_mat in dict.fromkeys(members):
                mats, per_face = self.face_materials(obj)
                if t_mat not in mats:
                    continue
                face_ids = np.nonzero(per_face == mats.index(t_mat))[0]
                if not len(face_ids):
                    continue
                vacated = next(
                    (
                        i
                        for i, sl in enumerate(obj.material_slots)
                        if sl.material == t_mat
                    ),
                    None,
                )
                per_object.append((obj, face_ids, vacated))
            # Copy BEFORE removing, for the same reason: removing by name first
            # frees the very datablock being copied.
            new_mat = base_mat.copy()
            old = bpy.data.materials.get(new_name)
            if old is not None and old is not new_mat:
                bpy.data.materials.remove(old)
            new_mat.name = new_name
            # Drop the copied Principled input links so the restore wires only
            # the outputs (a transferred channel must not keep the source's
            # image behind it).
            node = _MatUtilsInternal._principled_node(new_mat)
            if node is not None:
                nt = new_mat.node_tree
                for sock in node.inputs:
                    for link in list(sock.links):
                        nt.links.remove(link)
            MatManifest.restore(new_mat.name, {"materials": {new_mat.name: channels}})
            for obj, face_ids, vacated in per_object:
                slot_index = next(
                    (
                        i
                        for i, sl in enumerate(obj.material_slots)
                        if sl.material == new_mat
                    ),
                    None,
                )
                if slot_index is None:
                    # The replaced material left its slot empty; refill that one
                    # rather than appending beside it, or a re-run leaves one
                    # dead slot per pass on every target mesh.
                    slots = obj.material_slots
                    if vacated is not None and (
                        vacated < len(slots) and slots[vacated].material is None
                    ):
                        slots[vacated].material = new_mat
                        slot_index = vacated
                    else:
                        obj.data.materials.append(new_mat)
                        slot_index = len(obj.material_slots) - 1
                for f in face_ids:
                    obj.data.polygons[int(f)].material_index = slot_index
            created[label] = new_mat.name
            self.logger.info(f"Assigned {new_mat.name} ({len(channels)} map(s)).")
        return created
