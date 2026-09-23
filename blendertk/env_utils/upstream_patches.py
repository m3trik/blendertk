# !/usr/bin/python
# coding=utf-8
"""Defects in Blender's own Python that blendertk corrects, each with the probe
that retires it.

ONE home, so "what do we patch, and does it still need patching?" is a file to
read rather than a search for ``setattr``. Every entry is a
:class:`pythontk.UpstreamPatch`: the target, the finding that justifies it, the
replacement, and a probe that reproduces the defect against the STOCK code.
``test_fbx_utils`` sweeps them and asserts each is still needed, so the release
that fixes one upstream makes that test fail and name the patch to delete.

Nothing here is applied at import. A patch is a ``with`` block at the one call
site that needs it (:meth:`blendertk.FbxUtils.import_fbx`), so a caller that
never asked for the correction never gets it, and a traceback through the
patched frame comes from a module whose source is right here.
"""

import pythontk as ptk

__all__ = ["SIBLING_ARMATURES"]


SIBLING_ARMATURES = ptk.UpstreamPatch(
    name="io_scene_fbx: sibling armatures skipped on import",
    target="io_scene_fbx.import_fbx:FbxImportHelperNode.collect_armature_meshes",
    reason=(
        "Its non-armature branch walks self.children LIVE while each armature it "
        "recurses into re-parents its skinned meshes out of that same list "
        "(mesh.parent = armature -> children.remove(mesh)). Every removal ahead of "
        "the cursor slides the next sibling into the slot just visited, so sibling "
        "armatures alternate bind/skip. find_armatures, in the same class, already "
        "snapshots with tuple(self.children); this method does not. Measured on a "
        "Maya payload, whose flatten leaves one armature per skin at the scene ROOT "
        "in name order: of 7 skinned meshes only 4 arrived with an ARMATURE "
        "modifier, the other 3 keeping their vertex groups and sitting in the bind "
        "pose while their skeleton animated. Blender's own round trip never hits it "
        "-- Blender parents a skinned mesh UNDER its armature, so no root list is "
        "mutated."
    ),
    tracker="https://projects.blender.org/blender/blender/issues (io_scene_fbx)",
)


@SIBLING_ARMATURES.replaces
def _collect_armature_meshes(original, self) -> None:
    """Visit a snapshot of the children, so a re-parent cannot skip a sibling.

    Only the non-armature branch is ours; the armature branch is delegated to
    Blender, so the matrix work stays upstream's and only the iteration changed.
    """
    if self.is_armature:
        return original(self)
    for child in tuple(self.children):
        child.collect_armature_meshes()


def _armature_tree(count: int = 7):
    """``(root, armatures)``: a root holding *count* meshes and then *count*
    single-bone armatures, one skin each.

    The shape a Maya payload produces, where the flatten leaves every skeleton
    group at the scene ROOT in name order, after the mesh it deforms. Shared by
    the probe below and by ``test_fbx_utils`` (internal: a fixture, not surface), which runs it under
    :meth:`~pythontk.UpstreamPatch.applied` to check the replacement binds all of
    them -- one fixture, so "the bug is still there" and "our fix still works"
    can never be measured against different trees.
    """
    from io_scene_fbx.import_fbx import FbxImportHelperNode as helper
    from mathutils import Matrix

    def node(name, is_bone=False, is_armature=False):
        made = helper(None, None, None, is_bone)
        made.fbx_name = name
        made.matrix = Matrix()
        made.is_armature = is_armature
        return made

    root = node("root")
    meshes = [node(f"mesh{i}") for i in range(count)]
    armatures = [node(f"arm{i}", is_armature=True) for i in range(count)]
    for child in meshes + armatures:
        child.parent = root
    for index, armature in enumerate(armatures):
        bone = node(f"bone{index}", is_bone=True)
        bone.parent = armature
        bone.armature = armature
        bone.clusters.append((None, {meshes[index]}))
        meshes[index].armature_setup[armature] = (Matrix(), Matrix())

    return root, armatures


@SIBLING_ARMATURES.detects
def _sibling_armatures_still_skipped() -> bool:
    """Whether the STOCK importer still skips half the sibling armatures.

    Stock, they alternate bind/skip; reversing the child order alone binds all of
    them, which is what identifies this as an iteration-order defect rather than
    a skinning one.
    """
    root, armatures = _armature_tree()
    root.collect_armature_meshes()
    return sum(1 for armature in armatures if armature.meshes) != len(armatures)
