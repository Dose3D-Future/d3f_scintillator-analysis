"""Spectrophotometer GUI/pipeline package."""

from .file_parser import ParsedFile, parse_filename, scan_folder, group_by_plot, validate_group
from .processor import ProcessingConfig, process_group, load_spectrum
from .pipeline import run_pipeline, PipelineRunResult

__all__ = [
    "ParsedFile",
    "parse_filename",
    "scan_folder",
    "group_by_plot",
    "validate_group",
    "ProcessingConfig",
    "process_group",
    "load_spectrum",
    "run_pipeline",
    "PipelineRunResult",
]
