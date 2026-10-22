"""Unit tests.

This file exists so ``tests.unit`` is a regular package rather than a namespace
portion inside one. ``tests/__init__.py`` makes ``tests`` a package, and
``test_api.py`` imports :mod:`tests.unit.api_contract`; that import resolves
either way today, but only by namespace-package rules, which are not something
to rest a suite on.
"""
