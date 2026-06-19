# !/usr/bin/python
# coding=utf-8
"""Rigging utilities — Blender port of mayatk's ``rig_utils``.

Mirrors mayatk's procedural-rig tools (telescope / wheel / tube / shadow). Each rig is a
self-contained module (engine + co-located ``<rig>.ui`` + ``<Rig>Slots``) discovered by
``BlenderUiHandler`` and launched from the tentacle rigging menu via
``marking_menu.show("<rig>")`` — the same mayatk / MayaUiHandler split. Maya's joint/skinCluster
machinery has no faithful Blender analogue (Armature + vertex groups, handled in the rigging slot),
so only the DCC-agnostic procedural rigs live here.

Public engines are registered via the root ``DEFAULT_INCLUDE``; this file is a docstring-only
package marker (no import side effects).
"""
