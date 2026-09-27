"""Public exception types used by GridForge."""


class GridForgeError(Exception):
    """Base class for GridForge errors."""


class CaseLoadError(GridForgeError, ValueError):
    """Raised when an input cannot be converted into a GridForge case."""


class CaseValidationError(GridForgeError, ValueError):
    """Raised when a case no longer satisfies its structural contract."""


class ComponentValidationError(GridForgeError, ValueError):
    """Raised when component registration or its bus connection map is invalid."""


class ACNetworkError(GridForgeError, ValueError):
    """Raised when AC network admittance matrices cannot be constructed."""


class DCNetworkError(GridForgeError, ValueError):
    """Raised when a DC network snapshot cannot be constructed reliably."""
