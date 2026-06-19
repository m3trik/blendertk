# !/usr/bin/python
# coding=utf-8
"""Matrix utilities — the Blender counterpart of mayatk's ``xform_utils.matrices``
(``btk.Matrices`` ↔ ``mtk.Matrices``).

Mirrors the **portable pure-math + matrix-IO** surface of mayatk's ``Matrices`` (compose /
decompose / inverse / multiply / space conversion / object matrix get-set), adapted to
``mathutils.Matrix``. Two intentional divergences from mayatk (name + behavior mirror, not
internals):

* **Convention.** Maya is row-vector / left-multiply (``world = local * parent_world``); Blender
  is column-vector / right-multiply (``world = parent_world @ local``). The multiply ORDER flips,
  so :func:`world_to_local` / :func:`local_to_world` / :func:`mult` use ``@`` in Blender order —
  the *semantic* result matches mayatk.
* **Rigging node-graph builders are out of scope.** mayatk's ``Matrices`` also ships
  ``offsetParentMatrix`` / ``multMatrix`` / ``blendMatrix`` / ``aimMatrix`` / IK-FK / space-switch
  node builders. Blender rigging is constraint/driver-based, not matrix-node-graph based (see
  ``rig_utils`` absent-by-design in ``docs/STRUCTURE.md``), so those have no Blender analogue.

``import bpy`` is never needed here (objects are passed in); ``mathutils`` is imported lazily into
each call body so resolving the package surface never requires a running Blender.
"""

# How ``space`` names map onto Blender object matrix attributes.
_SPACE_ATTR = {
    "world": "matrix_world",
    "local": "matrix_local",
    "basis": "matrix_basis",
    "parent_inverse": "matrix_parent_inverse",
}


class Matrices:
    """Matrix helpers over ``mathutils.Matrix`` (mirror of mayatk's ``Matrices`` pure-math API)."""

    # -- object matrix IO -----------------------------------------------------------------
    @staticmethod
    def get_matrix(obj, space="world"):
        """Return a COPY of *obj*'s matrix in ``space`` (``world`` / ``local`` / ``basis`` /
        ``parent_inverse``) — mirror of mayatk's ``get_matrix`` (which reads a node plug)."""
        try:
            attr = _SPACE_ATTR[space.lower()]
        except KeyError:
            raise ValueError(
                f"Unknown space {space!r}; expected one of {sorted(_SPACE_ATTR)}."
            )
        return getattr(obj, attr).copy()

    @staticmethod
    def set_matrix(obj, value, space="world"):
        """Set *obj*'s matrix in ``space`` (``world`` / ``local`` / ``basis``) from a
        ``mathutils.Matrix``, a 16-float flat sequence, or a 4×4 nested sequence — mirror of
        mayatk's ``set_matrix``."""
        m = Matrices.to_matrix(value)
        key = space.lower()
        if key not in ("world", "local", "basis"):
            raise ValueError(
                f"Cannot set space {space!r}; settable spaces are world / local / basis."
            )
        setattr(obj, _SPACE_ATTR[key], m)

    @staticmethod
    def local_matrix(obj):
        """*obj*'s local matrix (mirror of mayatk's ``local_matrix``)."""
        return Matrices.get_matrix(obj, "local")

    @staticmethod
    def to_matrix(matrix_like):
        """Coerce to a ``mathutils.Matrix`` — accepts a Matrix (copied), a bpy object (its
        ``matrix_world``), a 16-float flat sequence, or a 4×4 nested sequence. Mirror of mayatk's
        ``to_mmatrix`` (relaxed name: Blender has no ``MMatrix``)."""
        from mathutils import Matrix

        if isinstance(matrix_like, Matrix):
            return matrix_like.copy()
        if hasattr(matrix_like, "matrix_world"):  # a bpy object
            return matrix_like.matrix_world.copy()
        seq = list(matrix_like)
        if len(seq) == 16:
            return Matrix([seq[r * 4 : r * 4 + 4] for r in range(4)])
        if len(seq) == 4 and all(hasattr(r, "__len__") and len(r) == 4 for r in seq):
            return Matrix([list(r) for r in seq])
        raise TypeError(
            f"Cannot convert {type(matrix_like).__name__} to a 4×4 Matrix "
            "(expected Matrix, bpy object, 16-float, or 4×4 sequence)."
        )

    # -- compose / decompose --------------------------------------------------------------
    @staticmethod
    def identity():
        """A 4×4 identity matrix (mirror of mayatk's ``identity``)."""
        from mathutils import Matrix

        return Matrix.Identity(4)

    @staticmethod
    def from_srt(
        translate=(0.0, 0.0, 0.0),
        rotate_euler_deg=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        rotate_order="XYZ",
    ):
        """Compose a matrix from translation, Euler rotation (DEGREES), and scale — mirror of
        mayatk's ``from_srt``. ``rotate_order`` is a ``mathutils.Euler`` order (e.g. ``"XYZ"``)."""
        from math import radians
        from mathutils import Euler, Matrix, Vector

        eul = Euler([radians(a) for a in rotate_euler_deg], rotate_order.upper())
        return Matrix.LocRotScale(Vector(translate), eul.to_quaternion(), Vector(scale))

    @staticmethod
    def compose(translate=(0.0, 0.0, 0.0), rotation=None, scale=(1.0, 1.0, 1.0)):
        """Compose a matrix from translation, a ``Quaternion`` (or ``Euler`` / 3×3) rotation, and
        scale — the quaternion-native counterpart to :func:`from_srt` (Blender-idiomatic; avoids
        Euler-order ambiguity, matching how ``_xform_utils`` freezes/recomposes transforms)."""
        from mathutils import Matrix, Quaternion, Vector

        return Matrix.LocRotScale(
            Vector(translate), Quaternion() if rotation is None else rotation, Vector(scale)
        )

    @staticmethod
    def decompose(m, rotate_order="XYZ"):
        """Decompose *m* into ``(translation, rotation_degrees, scale)`` 3-tuples — mirror of
        mayatk's ``decompose`` (rotation returned in DEGREES in ``rotate_order``)."""
        from math import degrees

        loc, quat, scl = m.decompose()
        eul = quat.to_euler(rotate_order.upper())
        return (
            (loc.x, loc.y, loc.z),
            (degrees(eul.x), degrees(eul.y), degrees(eul.z)),
            (scl.x, scl.y, scl.z),
        )

    @staticmethod
    def extract_translation(m):
        """The translation component of *m* as an ``(x, y, z)`` tuple (mirror of mayatk)."""
        t = m.to_translation()
        return (t.x, t.y, t.z)

    # -- algebra / space conversion -------------------------------------------------------
    @staticmethod
    def inverse(m):
        """The inverse of *m* (``inverted_safe`` — returns a usable matrix for singular input,
        mirror of mayatk's ``inverse``)."""
        return m.inverted_safe()

    @staticmethod
    def mult(*mats):
        """Right-to-left matrix product: ``mult(A, B)`` returns ``A @ B`` (apply B, then A) —
        mirror of mayatk's ``mult`` semantics (``@`` for Blender's column-vector convention)."""
        from mathutils import Matrix

        if not mats:
            return Matrix.Identity(4)
        result = mats[0].copy()
        for m in mats[1:]:
            result = result @ m
        return result

    @staticmethod
    def world_to_local(world_matrix, parent_world_matrix):
        """World → local relative to a parent: ``local = parent_world⁻¹ @ world`` (Blender order;
        same semantic result as mayatk's ``world * parent_world.inverse()``)."""
        return parent_world_matrix.inverted_safe() @ world_matrix

    @staticmethod
    def local_to_world(local_matrix, parent_world_matrix):
        """Local → world: ``world = parent_world @ local`` (Blender order; same semantic result
        as mayatk's ``local * parent_world``)."""
        return parent_world_matrix @ local_matrix

    @staticmethod
    def is_identity(m, tolerance=1e-9):
        """True if *m* equals identity within ``tolerance`` per element (mirror of mayatk)."""
        from mathutils import Matrix

        ident = Matrix.Identity(4)
        return all(
            abs(m[r][c] - ident[r][c]) <= tolerance for r in range(4) for c in range(4)
        )
