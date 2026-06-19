# !/usr/bin/python
# coding=utf-8
"""Built-in lightmap quality presets (read-only JSON tier).

This dir needs an ``__init__.py`` so setuptools ``packages.find`` discovers it and the
``package-data`` ``*.json`` ships in the wheel — a bare data dir is source-only, which would
break :meth:`LightmapBaker.from_preset` for pip installs. Mirror of mayatk's preset packaging.
"""
