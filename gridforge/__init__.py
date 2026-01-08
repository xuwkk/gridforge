from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("gridforge")
except PackageNotFoundError:
    __version__ = "0.0.0"