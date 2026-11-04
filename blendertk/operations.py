# !/usr/bin/python
# coding=utf-8
"""blendertk's operations: how an agent reaches Blender.

Two, the ones only this host can declare: a console that runs code in a
background Blender, and an image of its scene. Every other blendertk function
is reached through them (``api.search`` finds it, ``blender.python.exec`` calls
it), so nothing here wraps a library function one by one.
``ptk.OperationProvider`` turns these declarations into the committed
``operations.json`` beside this file, which ``python -m pythontk ops`` (a fresh
Blender per call) and the MCP server (the one Blender a session keeps) read;
after changing one, rewrite it: ``python -m pythontk ops apply
operations.catalog module=blendertk.operations``. Mirror of mayatk's
(``maya.*``).
"""

from pythontk import Operation

OPERATIONS = (
    Operation(
        "blender.python.exec",
        "pythontk:PythonConsole.run",
        effect="code",
        title="Run Python in Blender",
        host="blender",
    ),
    Operation(
        "blender.scene.view",
        "blendertk:CamUtils.capture_view",
        title="An image of the scene",
        host="blender",
    ),
)
