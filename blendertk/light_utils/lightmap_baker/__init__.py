# !/usr/bin/python
# coding=utf-8
"""Subpackage — see root ``blendertk.__init__`` for the public API.

The lightmap baker lives in its own subpackage because it ships bundled data (the read-only
``presets/`` JSON tier loaded by :meth:`LightmapBaker.preset_store`); keeping the engine, its
``.ui`` panel and that data co-located keeps the feature self-contained. Mirrors mayatk's
``light_utils/lightmap_baker/`` layout.
"""
