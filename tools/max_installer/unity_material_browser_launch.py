"""Open the window, always from the files that are on disk right now.

3ds Max keeps a Python interpreter alive for the whole session, so an updated
plugin keeps running the version imported at the first click until somebody
restarts Max. That turns every fix into "restart and try again", which is a
poor way to find out whether the fix worked.

This file is executed with `python.ExecuteFile`, which does not cache, so it
drops the loaded packages and imports them again. It lives beside them in the
scripts folder rather than inside them: a module that deletes itself from
`sys.modules` while it is running is a puzzle, and this way it never has to.
"""

import sys
from pathlib import Path

PACKAGES = ("unity_material_max", "unity_pipeline_core")

folder = str(Path(__file__).resolve().parent)
if folder not in sys.path:
    sys.path.insert(0, folder)

# The window that is already up belongs to the code about to be dropped, so it
# is closed first: two windows, one of them dead, is worse than none.
previous = sys.modules.get("unity_material_max.dialog")
window = getattr(previous, "_window", None)
if window is not None:
    try:
        window.close()
    except Exception:  # noqa: BLE001  (Qt may have deleted it already)
        pass

for name in [item for item in list(sys.modules) if item.split(".")[0] in PACKAGES]:
    del sys.modules[name]

from unity_material_max import dialog  # noqa: E402  (only valid after the purge)

dialog.show()
