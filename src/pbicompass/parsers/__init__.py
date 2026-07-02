"""Metadata-only parsers for Power BI project files.

Public entry point: :func:`pbicompass.parsers.pbip.parse_pbip`.
"""

from .pbip import parse_pbip, detect_and_parse

__all__ = ["parse_pbip", "detect_and_parse"]
