# !/usr/bin/python
# coding=utf-8
"""Scene auto-instancer: convert geometrically identical meshes to instances.

Blender port of mayatk's ``core_utils.auto_instancer``. All classes are
lazy-loaded via the blendertk root package — import from blendertk directly
(``btk.auto_instance``, ``btk.AutoInstancer``). The matching math
(``PointCloud.match_clouds`` / ``pca_basis``) and the assembly clustering
(``AssemblySorter``) are shared with mayatk via pythontk; the modules here
are the bpy adapters. A Blender "instance" is a linked duplicate — objects
sharing one mesh datablock.
"""
