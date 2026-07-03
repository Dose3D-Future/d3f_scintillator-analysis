"""Command-line entry point for batch analysis."""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .pipeline import parse_pair, parse_ranges, run_pipeline
    from .processor import ProcessingConfig
else:
    from pipeline import parse_pair, parse_ranges, run_pipeline
    from processor import ProcessingConfig



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run spectrophotometer folder analysis.")
    p.add_argument("input_dir", type=Path, help="Folder containing spectrum files")
    p.add_argument("output_dir", type=Path, nargs="?", help="Output folder; default: input_dir/analysis_outputs")
    p.add_argument("--analysis-window", default="400,750", help="Analysis window in nm, e.g. 400,750")
    p.add_argument("--interest-window", default="470,570", help="ROI used for final-report AUC and linear-fit angle, e.g. 470,570")
    p.add_argument("--baseline-ranges", default="190,550;650,1020", help="Baseline ranges, e.g. 190,550;650,1020")
    p.add_argument("--smooth-window", type=int, default=41)
    p.add_argument("--smooth-polyorder", type=int, default=3)
    p.add_argument("--no-pdf", action="store_true", help="Do not export PDF plots")
    p.add_argument("--no-vega", action="store_true", help="Do not export Vega-Lite JSON plots")
    p.add_argument("--overlay-samples", action="store_true", help="Plot all samples sharing series/diode/geometry/config together")
    p.add_argument("--recursive", action="store_true", help="Scan subfolders recursively")
    p.add_argument("--include-raw-curves", action="store_true", help="Also export raw/net/gated spectra to analysis_ready_curves.csv")
    p.add_argument("--no-integration-normalization", action="store_true", help="Do not divide signals by IntegrationTime metadata")
    return p



def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    default_cfg = ProcessingConfig()
    cfg = ProcessingConfig(
        analysis_window_nm=parse_pair(args.analysis_window, default_cfg.analysis_window_nm),
        interest_window_nm=parse_pair(args.interest_window, default_cfg.interest_window_nm),
        baseline_ranges_nm=parse_ranges(args.baseline_ranges, default_cfg.baseline_ranges_nm),
        smoothing_window_points=args.smooth_window,
        smoothing_polyorder=args.smooth_polyorder,
        normalize_by_integration_time=not args.no_integration_normalization,
    )
    run = run_pipeline(
        args.input_dir,
        args.output_dir,
        cfg,
        split_samples=not args.overlay_samples,
        export_vega=not args.no_vega,
        export_pdf=not args.no_pdf,
        recursive=args.recursive,
        include_raw_curves=args.include_raw_curves,
    )
    print(f"Detected files: {len(run.files)}")
    print(f"Processed groups: {len(run.results)}")
    print(f"Written files: {len(run.written_files)}")
    print(f"Output: {run.output_dir}")
    if run.warnings:
        print(f"Warnings: {len(run.warnings)} - see warnings.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
