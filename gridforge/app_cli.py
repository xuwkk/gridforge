"""
Console launcher for the GridForge Streamlit app.
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    try:
        from streamlit.web import cli as stcli
    except ImportError as exc:
        raise SystemExit(
            "The GridForge app requires Streamlit. Install it with `pip install \"gridforge[app]\"`."
        ) from exc

    app_path = os.path.join(os.path.dirname(__file__), "config_app.py")
    sys.argv = ["streamlit", "run", app_path]
    raise SystemExit(stcli.main())
