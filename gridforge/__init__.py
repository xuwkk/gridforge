"""Small public API: case tables, bus connections, and AC/DC matrices."""

from importlib.metadata import PackageNotFoundError, version

from gridforge.ac import ACModel
from gridforge.case import Case
from gridforge.case_io import load_case
from gridforge.dc import DCModel
from gridforge.excel_io import load_excel
from gridforge.errors import (
    ACNetworkError,
    CaseLoadError,
    CaseValidationError,
    ComponentValidationError,
    DCNetworkError,
    GridForgeError,
)

try:
    # The import package is ``gridforge``; its distribution is ``powergridforge``.
    __version__ = version("powergridforge")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "ACModel",
    "ACNetworkError",
    "Case",
    "CaseLoadError",
    "CaseValidationError",
    "ComponentValidationError",
    "DCModel",
    "DCNetworkError",
    "GridForgeError",
    "__version__",
    "load_case",
    "load_excel",
]
