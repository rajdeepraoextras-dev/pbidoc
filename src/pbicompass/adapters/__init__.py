"""Third-party extraction adapters.

Each adapter isolates an optional dependency (e.g. ``pbixray``) behind a thin
loader, and exposes a *pure* transformation function that maps the tool's
output onto the canonical schema. The pure function is unit-testable without
the optional dependency installed.
"""

from .pbixray_adapter import build_model_from_frames, load_frames_from_pbix

__all__ = ["build_model_from_frames", "load_frames_from_pbix"]
