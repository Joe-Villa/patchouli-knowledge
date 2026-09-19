"""Compat shim: mod resolution lives in corpus.py (PNG render kept for later)."""

from __future__ import annotations

from corpus import load_catalog, resolve_mod_root

__all__ = ["load_catalog", "resolve_mod_root"]
