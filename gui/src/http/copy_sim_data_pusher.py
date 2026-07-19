"""Compatibility redirect (remediation R4.4).

The canonical data pusher is gui/src/copy_sim_data_pusher.py — the file the
container's start.sh actually runs. This copy used to be a byte-identical
duplicate; it now only forwards so the two can never drift again.
"""

import os
import runpy

_CANONICAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "copy_sim_data_pusher.py")

if __name__ == "__main__":
    runpy.run_path(_CANONICAL, run_name="__main__")
