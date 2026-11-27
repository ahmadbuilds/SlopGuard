"""SlopGuard package and shared dependency risk contracts."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("slopguard")
except PackageNotFoundError:
    __version__ = "0.0.0"
CONTRACT_VERSION = "1.1.0"
