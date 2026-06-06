"""Compatibility helpers for dependencies that still expect older NumPy APIs."""

from __future__ import annotations

import numpy as np


def patch_numpy_for_pypower():
    """Restore NumPy symbols imported by PYPOWER but removed in NumPy 2.x."""
    if not hasattr(np, "asscalar"):
        np.asscalar = lambda a: a.item()

    if not hasattr(np, "in1d"):

        def _in1d(ar1, ar2, assume_unique=False, invert=False, *, kind=None):
            try:
                result = np.isin(
                    ar1,
                    ar2,
                    assume_unique=assume_unique,
                    invert=invert,
                    kind=kind,
                )
            except TypeError:
                result = np.isin(
                    ar1,
                    ar2,
                    assume_unique=assume_unique,
                    invert=invert,
                )
            return np.asarray(result).ravel()

        np.in1d = _in1d

    return np
