"""Read-only storage analysis for the Windows Support Diagnostic Toolkit."""

__version__ = "0.1.0"

from .classifier import ClassificationOptions
from .scanner import (
    ProgressUpdate,
    ScanConfigurationError,
    ScannerOptions,
    StorageScanner,
)

__all__ = [
    "ClassificationOptions",
    "ProgressUpdate",
    "ScanConfigurationError",
    "ScannerOptions",
    "StorageScanner",
]
