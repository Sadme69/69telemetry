"""Memory helpers for keeping RSS low on small (512MB) containers.

The heavy paths in this app (FastF1 + pandas) allocate large, short-lived
DataFrames. CPython's garbage collector frees the *Python* objects, but glibc's
allocator often keeps the freed arenas mapped, so the process RSS stays high
even after the data is gone. ``release_memory()`` runs a GC pass and then asks
glibc to return free pages to the OS via ``malloc_trim``. Combined with
``MALLOC_ARENA_MAX=2`` (see Dockerfile) this keeps memory from creeping up
session after session.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import logging

logger = logging.getLogger(__name__)

# Resolve glibc's malloc_trim once. It only exists on Linux/glibc; on other
# platforms (macOS dev machines, musl) we silently skip the trim.
_libc = None
try:
    _libc_name = ctypes.util.find_library("c")
    if _libc_name:
        _libc = ctypes.CDLL(_libc_name)
        if not hasattr(_libc, "malloc_trim"):
            _libc = None
except Exception:  # pragma: no cover - defensive
    _libc = None


def release_memory() -> None:
    """Collect garbage and return freed heap pages to the OS.

    Safe to call frequently; it is cheap when there is nothing to free.
    """
    gc.collect()
    if _libc is not None:
        try:
            _libc.malloc_trim(0)
        except Exception:  # pragma: no cover - defensive
            pass
