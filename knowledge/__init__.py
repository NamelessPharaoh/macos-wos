"""Vendored game knowledge base (building/troop/research tables) plus the
pure utilities the refresh script and the calculators share.

Layering rule (E1): this package imports nothing from `native/` — the arrow
points the other way. `native/screen.py` re-exports the parsers that used to
live there so the ten reader modules that import them are untouched.
"""
