# !/usr/bin/python
# coding=utf-8
"""Maya-side import recipes for the Maya bridge.

Each ``*.py`` here is an executable Maya Python script with ``__KEY__`` placeholders that
:class:`MayaBridge` substitutes (FBX path + parameter values) before launching Maya, which exec's
the rendered script via ``maya -command``. A ``BRIDGE_MODES = (...)`` constant declares the
supported modes. Underscore-prefixed files are ignored by template discovery.
"""
