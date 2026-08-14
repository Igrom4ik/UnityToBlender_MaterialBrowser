"""Unity Material Browser for 3ds Max.

Phase A comes from `unity_pipeline_core` unchanged and needs no Max at all.
This package is phase B: it turns a `ConversionPlan` into a Physical Material
and writes `.mat` libraries that the Material/Map Browser opens on its own.

The split repeats the one that keeps the core clean: `recipe.py` is plain
Python and decides *what* to build, `material_builder.py` and `library_build.py`
talk to `pymxs` and only carry it out. Everything worth testing therefore runs
without 3ds Max.
"""

from __future__ import annotations

__version__ = "0.1.1"
