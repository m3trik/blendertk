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
  is by matching object name, else by order. A target JOINED from several
  sources (Object > Join, then re-unwrapped) reads them all: the sources join
  end to end in the order the join left them (:meth:`pair_sources`).
* **UV map -> UV map** on ONE mesh (``source=None``, ``source_uv_set=...``).

Outputs are written per target LAYOUT -- the targets' faces grouped by
overlap (:meth:`pythontk.UvTransfer.layout_jobs`), whatever their UV maps are
called or the targets wear -- one image per channel, sampled from whichever
source material each triangle wears; a source with no map for a channel
contributes its Principled BSDF constant. Every map is the same resample; a
normal map's XY also turn with any island the target layout rotates or
mirrors, read off the two layouts alone -- where a target stands never enters
it, and this is never a normal-map BAKE (mirror of mayatk).

A committed LIGHTMAP is not a material map and travels on its own pass,
:meth:`LightmapRecords.transfer_lightmaps` (built on this module's pairing):
rebound to the same map when the target's lightmap layout is the source's,
resampled into the target's layout otherwise, and committed on the target
either way.

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

    @staticmethod
    def _parts(mesh) -> list:
        """*mesh* as its parts: a tuple / list is several meshes that together
        form ONE mesh, in concatenation order (see :meth:`pair_sources`)."""
        return list(mesh) if isinstance(mesh, (list, tuple)) else [mesh]

    @classmethod
    def _topology(cls, mesh) -> Tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
        """``(counts, verts, world points)`` of *mesh* -- or of its parts joined
        end to end, the way Join joins them (vertex indices offset)."""
        counts, verts, points = [], [], []
        for part in cls._parts(mesh):
            o = cls._mesh(part)
            m = o.data
            c = np.empty(len(m.polygons), dtype=np.int32)
            m.polygons.foreach_get("loop_total", c)
            v = np.empty(len(m.loops), dtype=np.int32)
            m.loops.foreach_get("vertex_index", v)
            counts.append(c.astype(np.int64))
            verts.append(v.astype(np.int64) + sum(map(len, points)))
            points.append(cls._world_points(o))
        return np.concatenate(counts), np.concatenate(verts), np.concatenate(points)

    @classmethod
    def topology_matches(cls, a, b) -> Tuple[bool, str]:
        """``(ok, why)`` -- same polygon loop lists on both meshes.

        Either side may be a tuple of meshes: their parts joined in order.
        """
        ca, va, pa = cls._topology(a)
        cb, vb, pb = cls._topology(b)
        if len(ca) != len(cb) or len(pa) != len(pb):
            return False, (
                f"{len(ca)} faces / {len(pa)} verts vs {len(cb)} / {len(pb)}"
            )
        if len(va) != len(vb):
            return False, "face-corner counts differ"
        if not np.array_equal(ca, cb):
            return False, "per-face vertex counts differ"
        if not np.array_equal(va, vb):
            return False, "face vertex order differs"
        return True, ""

    @classmethod
    def positions_match(cls, a, b, tolerance: float = 1e-4) -> bool:
        """World-space vertices coincide (either side may be a tuple of parts)."""
        pa, pb = cls._topology(a)[2], cls._topology(b)[2]
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

        *source* may be a tuple of meshes the target was joined from, in join
        order (:meth:`pair_sources`): their face corners join end to end
        exactly as the target's do, each read through its own UV map.

        Returns:
            ``{"src_tris", "dst_tris", "faces", "dropped"}`` as the mayatk twin.
        """
        tgt = cls._mesh(target)
        tm = tgt.data
        srcs = (
            [cls._mesh(p).data for p in cls._parts(source)]
            if source is not None
            else [tm]
        )
        if source is not None:
            src_sets = [
                source_uv_set
                or (sm.uv_layers.active.name if sm.uv_layers.active else None)
                for sm in srcs
            ]
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
            src_sets = [src_set]
            if src_set == dst_set:
                raise ValueError(
                    "UV map -> UV map transfer needs two different maps "
                    f"(both are {dst_set!r})"
                )
        if not dst_set:
            raise ValueError(f"target {tgt.name!r} has no UV map")
        if not all(src_sets):
            raise ValueError("a source mesh has no UV map")
        if sum(len(sm.loops) for sm in srcs) != len(tm.loops):
            raise ValueError("source and target face-corner counts differ")

        tm.calc_loop_triangles()
        n_tri = len(tm.loop_triangles)
        loops = np.empty(n_tri * 3, dtype=np.int32)
        faces = np.empty(n_tri, dtype=np.int32)
        tm.loop_triangles.foreach_get("loops", loops)
        tm.loop_triangles.foreach_get("polygon_index", faces)
        loops = loops.reshape(-1, 3)

        d_uv = cls._uv_layer_vectors(tm, dst_set)
        s_uv = np.concatenate(
            [cls._uv_layer_vectors(sm, s) for sm, s in zip(srcs, src_sets)]
        )
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
        """``(materials, per-face index into materials)`` for *obj*.

        *obj* may be a tuple of parts (see :meth:`_parts`): their faces join
        end to end, and a material two parts share is listed once.
        """
        mats: List[Any] = []
        per_face = []
        for part in cls._parts(obj):
            o = cls._mesh(part)
            mesh = o.data
            idx = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("material_index", idx)
            slots = [s.material for s in o.material_slots]
            remap = np.full(max(len(slots), 1), -1, dtype=np.int64)
            for i, m in enumerate(slots):
                if m is None:
                    continue
                if m not in mats:
                    mats.append(m)
                remap[i] = mats.index(m)
            per_face.append(
                remap[np.clip(idx, 0, len(remap) - 1)]
                if slots
                else np.full(len(mesh.polygons), -1, dtype=np.int64)
            )
        return mats, np.concatenate(per_face)

    @staticmethod
    def material_maps(material) -> Dict[str, str]:
        """``{channel: absolute texture path}`` for the material's mapped slots."""
        from blendertk.mat_utils.mat_manifest import MatManifest

        return dict(MatManifest._process_material(material))

    @staticmethod
    def material_constant(material, channel: str) -> Optional[Tuple[float, ...]]:
        """The channel's Principled BSDF default value on *material*, or None.

        Emission is its colour times its unlinked ``Emission Strength``, which
        a fresh Principled BSDF holds at 0 behind a WHITE colour: read alone,
        every non-emissive source's share of a consolidated emission map came
        out white. Mirror of mayatk's ``ShaderAttributeMap.read_constant``.
        """
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal
        from blendertk.mat_utils.mat_manifest import _NORMAL_SLOTS, _SLOT_SOCKETS

        node = _MatUtilsInternal._principled_node(material)
        if node is None or channel in _NORMAL_SLOTS:
            return None
        # The manifest's own socket table, so a channel the maps carry is one
        # the constants carry too (the lobes, the specular level). A normal's
        # literal is no value.
        for name in _SLOT_SOCKETS.get(channel, ()):
            sock = node.inputs.get(name)
            if sock is None or sock.is_linked:
                continue
            value = sock.default_value
            try:
                seq = tuple(float(v) for v in value)
                values = seq[:3] if len(seq) >= 3 else seq
            except TypeError:
                values = (float(value),)
            strength = node.inputs.get("Emission Strength")
            if (
                channel == "emission"
                and strength is not None
                and not strength.is_linked
            ):
                values = tuple(v * float(strength.default_value) for v in values)
            return values
        return None

    # --------------------------------------------------------- ownership
    #: ID property naming the output a result material IS (mirror of mayatk's
    #: ``transferOutput`` attribute): what finds it again however it is called.
    OUTPUT_STAMP = "transferOutput"

    @classmethod
    def _holders(cls, name: str, material_name: str) -> list:
        """What holds output *name*: the material called *material_name* (the
        name its material takes), and every material stamped *name*, whatever
        it is called now (mirror of mayatk; stamps compare without case)."""
        import bpy

        return [
            m
            for m in bpy.data.materials
            if m.name == material_name
            or str(m.get(cls.OUTPUT_STAMP) or "").lower() == name.lower()
        ]

    @staticmethod
    def _worn_outside(material, targets) -> bool:
        """Whether an object other than *targets* wears *material* in a slot of
        its own. A slot on a target's mesh DATA is the target's whichever object
        shows it: a linked duplicate wears what its data's slots hold, and the
        run assigns through those slots."""
        import bpy

        names = {t.name for t in targets}
        data = {t.data for t in targets}
        return any(
            sl.material == material and not (sl.link == "DATA" and o.data in data)
            for o in bpy.data.objects
            if o.name not in names
            for sl in o.material_slots
        )

    @classmethod
    def _replaceable(cls, material, name: str, targets) -> bool:
        """Mirror of mayatk: *material* is output *name*'s own previous result
        -- stamped *name*, and worn by nothing outside *targets*. Anything else
        keeps the name: a material another object or a source of this run
        wears, the run's own original (a same-mesh run READS it), and every
        unstamped material, which may be anyone's."""
        stamp = str(material.get(cls.OUTPUT_STAMP) or "")
        return (
            bool(stamp)
            and stamp.lower() == name.lower()
            and not cls._worn_outside(material, targets)
        )

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

    @classmethod
    def pair_sources(cls, targets: Sequence, sources: Sequence) -> Dict[Any, Any]:
        """Target -> its source: one mesh, or the TUPLE it was joined from.

        Mirror of :meth:`mayatk.TextureTransfer.pair_sources`: one source feeds
        every target; otherwise one to one by :meth:`pair_by_name` unless there
        are more sources than targets; then
        each target takes the sources whose topologies, joined in some order,
        are exactly its own (Object > Join), as a tuple in that order.

        Raises:
            ValueError: A target no ordering of the remaining sources builds.
        """
        if len(sources) == 1:
            return {t: sources[0] for t in targets}
        if len(sources) <= len(targets):
            return cls.pair_by_name(targets, sources)
        topo = [cls._topology(s) for s in sources]
        free = list(range(len(sources)))
        pairs: Dict[Any, Any] = {}
        for t in targets:
            order = ptk.UvTransfer.concatenation_order(
                cls._topology(t), [topo[i] for i in free]
            )
            if order is None:
                raise ValueError(
                    f"{cls._obj(t).name}: no combination of the {len(free)} "
                    "source(s) has its topology -- a joined target must be its "
                    "sources joined, faces unedited"
                )
            picked = [free[i] for i in order]
            pairs[t] = (
                sources[picked[0]]
                if len(picked) == 1
                else tuple(sources[i] for i in picked)
            )
            free = [i for i in free if i not in picked]
        return pairs

    @classmethod
    def find_combined(cls, meshes: Sequence) -> Optional[Tuple[Any, Tuple]]:
        """The mesh among *meshes* joined from ALL the others, if any.

        Mirror of :meth:`mayatk.TextureTransfer.find_combined`: ``(target,
        sources in join order)``, or ``None`` (fewer than three meshes, or no
        mesh is exactly the others joined).
        """
        meshes = list(meshes)
        found = ptk.UvTransfer.find_combined(
            [len(cls._mesh(m).data.polygons) for m in meshes],
            lambda i: cls._topology(meshes[i]),
        )
        if found is None:
            return None
        i, order = found
        return meshes[i], tuple(meshes[j] for j in order)


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
        assign_prefix: str = "",
        assign_suffix: Optional[str] = None,
        assign_from: str = "target",
    ) -> Dict[str, Dict[str, str]]:
        """Transfer the source material(s)' maps onto the target UV layout.

        Same contract as :meth:`mayatk.TextureTransfer.transfer` -- *targets* /
        *source* are Blender objects (or names); *source_uv_set* /
        *target_uv_set* are UV map names. Returns ``{target material name:
        {channel: written path}}``. More sources than targets means targets
        joined from them (:meth:`pair_sources`).

        *output_name* names the whole result -- the assigned material AND every
        map wired to it (``<output_name>_<Channel>.png``) -- instead of deriving
        each from the target layout, and is what Auto *assign_suffix* reads (the
        user named the material, so nothing is appended). A run that keeps two
        layouts apart appends the layout label to each, since one name cannot
        cover both without their maps overwriting each other.

        *assign_prefix* / *assign_suffix* affix the assigned MATERIAL's name
        (``MAT_hero`` / ``hero_MAT``) -- the maps deliberately do not follow.
        Applied idempotently (``ptk.StrUtils.apply_affix``), so a re-run over a
        previous result does not stack a second copy. *assign_suffix* is Auto
        by default (``None``): ``_TRANSFER`` on the layout-derived name,
        nothing when *output_name* already names the material; ``""`` forces
        no suffix.

        *output_dir* is absolute (``//`` accepted) or relative to the .blend's
        ``textures`` folder -- see :meth:`resolve_output_dir`.

        *assign_from* picks what the assigned material is a copy of:
        ``"target"`` (default -- the target's own) or ``"source"``, the source
        material covering the most of each output layout, so the result keeps
        the look being transferred (see :meth:`assign_results`).
        """
        if np is None:
            raise RuntimeError("numpy is required")
        if assign_from not in ("target", "source"):
            raise ValueError(
                f"assign_from must be 'target' or 'source', not {assign_from!r}"
            )
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
            self.pair_sources(targets, sources)
            if sources
            else {t: None for t in targets}
        )
        read = {
            s.name
            for src in pairs.values()
            if src is not None
            for s in self._parts(src)
        }
        unused = sorted(s.name for s in sources if s.name not in read)
        if unused:
            self.logger.warning(
                f"{len(unused)} source(s) are part of no target and were not "
                "read: " + ", ".join(unused)
            )
        out_dir = self.resolve_output_dir(output_dir)

        # What each target contributes, per target material, grouped into
        # LAYOUTS by overlap (ptk.UvTransfer.layout_jobs). Mirror of mayatk,
        # where the target stands included: it never enters the transfer.
        parts: List[Dict[str, Any]] = []
        registry: List[Any] = []
        for tgt, src in pairs.items():
            if src is not None:
                ok, why = self.topology_matches(tgt, src)
                if not ok:
                    names = " + ".join(s.name for s in self._parts(src))
                    raise ValueError(f"{tgt.name} / {names}: topology differs ({why})")
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
            # -1: faces that wear nothing (an empty slot, or none) -- still
            # part of the layout, so transferred like any other. Mirror of mayatk.
            for ti in np.unique(tri_tgt).tolist():
                pick = (tri_tgt == ti) & (tri_src >= 0)
                if not pick.any():
                    continue
                t_mat = t_mats[ti] if ti >= 0 else None
                parts.append(
                    {
                        "material": t_mat.name if t_mat is not None else None,
                        "uv_set": corr["target_uv_set"],
                        "src": corr["src_tris"][pick],
                        "dst": corr["dst_tris"][pick],
                        "ids": tri_src[pick],
                        "members": [(tgt, t_mat)],
                    }
                )
        if not parts:
            raise ValueError(
                "nothing to transfer: no UV-mapped target face reads a shaded "
                "source face"
            )

        source_specs = [
            {
                "name": m.name,
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
        jobs = ptk.UvTransfer.layout_jobs(parts, source_specs, log=self.logger.info)
        # Mirror of mayatk: an explicit output name renames BOTH halves of
        # the result -- the maps and the material assigned from them.
        stem = (
            ptk.StrUtils.sanitize(output_name, preserve_case=True)
            if output_name
            else ""
        )
        # Mirror of mayatk: Auto (None) drops the `_TRANSFER` tag when
        # output_name already keeps the new material apart from the one it
        # was derived from; an affix the caller asked for is a naming
        # convention and applies either way.
        suffix = assign_suffix
        if suffix is None:
            suffix = "" if stem else "_TRANSFER"
        if stem and len(jobs) > 1:
            # Mirror of mayatk: a layout is labelled by its target material,
            # on a re-run this run's own previous result -- name by what it was
            # derived from, or every run stacks another `<stem>_`.
            relabel = ptk.UvTransfer.output_labels(
                list(jobs), stem, prefix=assign_prefix, suffix=suffix
            )
            jobs = {relabel[label]: job for label, job in jobs.items()}
        # Mirror of mayatk: every output's name -- its maps' stem AND its
        # material's core -- is decided before anything is written, and a
        # name held outside this run is named beside, never taken.
        if stem:
            name_format = "{material}_{channel}"
        names, replaced = self._output_names(
            jobs, stem, assign_prefix, suffix, out_dir, name_format, source_specs
        )
        named = {names[label]: job for label, job in jobs.items()}
        written = ptk.UvTransfer.transfer_materials(
            named,
            output_dir=out_dir,
            channels=channels,
            size=size,
            supersample=supersample,
            padding=padding,
            name_format=name_format,
            normal_convention=normal_convention,
            source_mask_from_uvs=source_mask_from_uvs,
            # Mirror of mayatk: a map a material this run keeps reads is never
            # written; a replaced previous result's are rewritten in place.
            avoid=[
                path
                for spec in source_specs
                if spec["name"] not in replaced
                for path in spec["maps"].values()
            ],
            log=self.logger.info,
        )
        if assign:
            self.assign_results(
                written,
                named,
                prefix=assign_prefix,
                suffix=suffix,
                assign_from=assign_from,
            )
        return {label: written[names[label]] for label in jobs}

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

    @staticmethod
    def _source_name(job: Dict[str, Any]) -> Optional[str]:
        """Name of the source material covering the most of *job*'s layout
        (:meth:`pythontk.UvTransfer.dominant_source`), or None."""
        idx = ptk.UvTransfer.dominant_source(job)
        return None if idx is None else job["sources"][idx].get("name")

    def _member_faces(self, members: Sequence[Tuple[Any, Any]]) -> List[Any]:
        """``[(object, face ids, vacated slot)]`` for *members*' faces.

        Each ``(object, target material)`` pair's faces wearing that material
        (``None``: wearing nothing), with the slot index it held -- a re-run
        refills that slot instead of appending an empty one per pass. Resolved
        before any material is freed (see :meth:`assign_results`).
        """
        out: List[Any] = []
        for obj, t_mat in dict.fromkeys(members):
            mats, per_face = self.face_materials(obj)
            if t_mat is not None and t_mat not in mats:
                continue
            index = mats.index(t_mat) if t_mat is not None else -1
            face_ids = np.nonzero(per_face == index)[0]
            if not len(face_ids):
                continue
            vacated = next(
                (i for i, sl in enumerate(obj.material_slots) if sl.material == t_mat),
                None,
            )
            out.append((obj, face_ids, vacated))
        return out

    def _output_names(
        self,
        jobs: Dict[str, Dict[str, Any]],
        stem: str,
        prefix: str,
        suffix: str,
        out_dir: str,
        name_format: str,
        source_specs: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, str], set]:
        """``({label: output name}, names of the previous results replaced)``.

        Mirror of :meth:`mayatk.TextureTransfer._output_names`: a layout is
        named *stem* (``<stem>_<label>`` when there are several), else after
        its label, or -- where that name is held -- the first free
        ``<name>_1``, ``<name>_2``, ... Held: taken by another output of this
        run, held by a material that is not this output's own previous result
        (:meth:`_replaceable`), or a file it would write is a map a material
        this run keeps reads.
        """
        targets = list(
            dict.fromkeys(
                obj for job in jobs.values() for obj, _m in job.get("members") or []
            )
        )

        def key(path: str) -> str:
            return os.path.normcase(os.path.abspath(path))

        def writes(name: str) -> set:
            return {
                key(os.path.join(out_dir, f"{file_stem}.png"))
                for token in ptk.UvTransfer.CHANNEL_TOKENS.values()
                for file_stem in [name_format.format(material=name, channel=token)]
            }

        steerable = writes("a") != writes("b")
        names: Dict[str, str] = {}
        replaced: set = set()
        taken: set = set()
        for label in jobs:
            if stem:
                base = stem if len(jobs) == 1 else f"{stem}_{label}"
            else:
                # Mirror of mayatk: a re-run's label is the result the last
                # run assigned (`wood_TRANSFER`); named by what it came from.
                base = (
                    ptk.StrUtils.strip_known_affix(
                        label, prefix=prefix, suffix=suffix
                    ).strip("_")
                    or label
                )
            name, k = base, 0
            while True:
                holders = self._holders(
                    name, ptk.StrUtils.apply_affix(name, prefix=prefix, suffix=suffix)
                )
                held = {m.name for m in holders}
                kept_reads = {
                    key(path)
                    for spec in source_specs
                    if spec["name"] not in held
                    for path in spec["maps"].values()
                }
                if (
                    name.lower() not in taken
                    and all(self._replaceable(m, name, targets) for m in holders)
                    and not (steerable and writes(name) & kept_reads)
                ):
                    break
                k += 1
                name = f"{base}_{k}"
            if k:
                self.logger.warning(
                    f"{base} is held outside this run -- a material another "
                    "object or this run's source wears, or a map one reads: "
                    f"named {name}."
                )
            taken.add(name.lower())
            names[label] = name
            replaced.update(m.name for m in holders)
        return names, replaced

    def assign_results(
        self,
        results: Dict[str, Dict[str, str]],
        jobs: Dict[str, Dict[str, Any]],
        suffix: str = "_TRANSFER",
        base_name: Optional[str] = None,
        prefix: str = "",
        assign_from: str = "target",
    ) -> Dict[str, str]:
        """One ``<prefix><layout><suffix>`` material per output, on its faces.

        *base_name* replaces the layout-derived name (see ``transfer``'s
        ``output_name``): the material becomes ``<base_name>``, or
        ``<base_name>_<layout>`` when the run produced more than one layout.
        The affixes are applied to the result idempotently
        (``ptk.StrUtils.apply_affix``).

        Mirror of mayatk: *jobs* carries each output's ``members`` --
        ``(object, target material)`` pairs, ``None`` for faces that wore
        nothing -- so every face transferred INTO the layout lands on the one
        new material, copied from the first member's material -- or, with
        *assign_from* ``"source"`` (or no member wearing one), from the source
        material covering the most of the layout -- and wired to the outputs.
        Originals are untouched.

        Mirror of mayatk: a material holding the result's name -- called it,
        or stamped as this output (:attr:`OUTPUT_STAMP`) whatever it is called
        now -- is replaced only when it is this output's own previous result
        (:meth:`_replaceable`); anything else keeps it, and the new one is
        named beside it. :meth:`transfer` names every output past such a holder
        before it writes, so only a direct call meets one here. Every result
        is stamped with its name.

        Returns ``{output label: new material name}``.
        """
        import bpy
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal
        from blendertk.mat_utils.mat_manifest import MatManifest

        targets = list(
            dict.fromkeys(
                obj for job in jobs.values() for obj, _m in job.get("members") or []
            )
        )
        # Mirror of mayatk: resolve EVERY output's faces and make every copy --
        # neither touches a slot -- before anything is freed. A replaced
        # material can be one another output's targets wear (or copy from), and
        # once it is removed their `t_mat` is a dangling StructRNA.
        planned: List[Any] = []
        for label, channels in results.items():
            members = jobs.get(label, {}).get("members") or []
            if not channels or not members:
                continue
            base_mat = next((m for _obj, m in members if m is not None), None)
            if assign_from == "source" or base_mat is None:
                named = self._source_name(jobs[label])
                base_mat = bpy.data.materials.get(named or "") or base_mat
            if base_name:
                core = base_name if len(jobs) == 1 else f"{base_name}_{label}"
            else:
                core = label
            new_name = ptk.StrUtils.apply_affix(core, prefix=prefix, suffix=suffix)
            per_object = self._member_faces(members)
            planned.append(
                (label, channels, core, new_name, per_object, base_mat.copy())
            )
        # A copy of a previous result carries its stamp until it is restamped.
        fresh = [new_mat for *_rest, new_mat in planned]
        created: Dict[str, str] = {}
        for label, channels, core, new_name, per_object, new_mat in planned:
            for old in self._holders(core, new_name):
                if any(old == m for m in fresh):  # another output's copy
                    continue
                if not self._replaceable(old, core, targets):
                    self.logger.warning(
                        f"{old.name} is not this output's previous result, so it "
                        "is kept; the result is named beside it."
                    )
                    continue
                bpy.data.materials.remove(old)
            new_mat.name = new_name
            new_mat[self.OUTPUT_STAMP] = core
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
