"""High-level pipeline orchestration for the spectrophotometer application."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .exporter import export_all
from .file_parser import ParsedFile, group_by_plot, scan_folder, validate_dataset, validate_group
from .processor import GroupResult, ProcessingConfig, process_group


@dataclass
class PipelineRunResult:
    input_dir: Path
    output_dir: Path
    files: list[ParsedFile]
    groups: dict[str, list[ParsedFile]]
    results: list[GroupResult]
    written_files: list[Path]
    warnings: list[str] = field(default_factory=list)



def parse_pair(text: str, default: tuple[float, float]) -> tuple[float, float]:
    try:
        a, b = [float(x.strip().replace(",", ".")) for x in text.split(",")[:2]]
        return (a, b)
    except Exception:
        return default



def parse_ranges(text: str, default: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    for part in str(text).split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            a, b = [float(x.strip().replace(",", ".")) for x in part.split(",")[:2]]
            ranges.append((a, b))
        except Exception:
            continue
    return ranges or default



def files_dataframe(files: list[ParsedFile]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "file": pf.path.name,
                "path": str(pf.path),
                "series_id": pf.series_id,
                "diode": pf.diode,
                "sample": pf.sample,
                "geometry": pf.geometry,
                "config": pf.config,
                "role": pf.role,
                "measurement_type": pf.measurement_type,
                "parse_ok": pf.parse_ok,
                "parse_notes": "; ".join(pf.parse_notes),
                "base_key": pf.base_key,
                "sample_key": pf.sample_key,
            }
            for pf in files
        ]
    )



def _raw_rows(result: GroupResult) -> list[dict]:
    rows: list[dict] = []
    for sr in result.raw_spectra:
        pf = sr.parsed
        for w, raw, norm, net, gated in zip(sr.wl, sr.signal_raw, sr.signal_normalized, sr.signal_net, sr.signal_gated):
            rows.append(
                {
                    "group_key": result.group_key,
                    "analysis_type": "raw_processed_spectrum",
                    "series_id": pf.series_id,
                    "diode": pf.diode,
                    "sample": pf.sample,
                    "geometry": pf.geometry,
                    "config": pf.config,
                    "role": pf.role,
                    "source_file": pf.path.name,
                    "source_member": sr.source_member,
                    "blank_file": "",
                    "integration_time": sr.integration_time if sr.integration_time is not None else np.nan,
                    "blank_integration_time": np.nan,
                    "integration_time_norm_factor": sr.integration_time_normalization_factor,
                    "wavelength_nm": float(w),
                    "signal_raw": float(raw),
                    "signal_normalized": float(norm),
                    "signal_net": float(net),
                    "signal_gated": float(gated),
                    "transmittance": np.nan,
                    "transmittance_smooth": np.nan,
                    "absorbance": np.nan,
                    "absorbance_smooth": np.nan,
                    "scattering_net": np.nan,
                    "scattering_shape": np.nan,
                    "scattering_shape_smooth": np.nan,
                    "valid": bool(gated > 0),
                }
            )
    return rows



def curves_dataframe(results: list[GroupResult], include_raw: bool = False) -> pd.DataFrame:
    rows: list[dict] = []
    for result in results:
        if include_raw:
            rows.extend(_raw_rows(result))

        for tr in result.transmittance_results:
            pf = tr.sample.parsed
            for w, T, Ts, A, As, valid in zip(
                tr.wl,
                tr.transmittance,
                tr.transmittance_smooth,
                tr.absorbance,
                tr.absorbance_smooth,
                tr.valid_mask,
            ):
                rows.append(
                    {
                        "group_key": result.group_key,
                        "analysis_type": "transmittance_absorbance",
                        "series_id": pf.series_id,
                        "diode": pf.diode,
                        "sample": pf.sample,
                        "geometry": pf.geometry,
                        "config": pf.config,
                        "role": pf.role,
                        "source_file": pf.path.name,
                        "source_member": tr.sample.source_member,
                        "blank_file": tr.blank.parsed.path.name,
                        "integration_time": tr.sample.integration_time if tr.sample.integration_time is not None else np.nan,
                        "blank_integration_time": tr.blank.integration_time if tr.blank.integration_time is not None else np.nan,
                        "integration_time_norm_factor": tr.sample.integration_time_normalization_factor,
                        "wavelength_nm": float(w),
                        "signal_raw": np.nan,
                        "signal_normalized": np.nan,
                        "signal_net": np.nan,
                        "signal_gated": np.nan,
                        "transmittance": float(T) if np.isfinite(T) else np.nan,
                        "transmittance_smooth": float(Ts) if np.isfinite(Ts) else np.nan,
                        "absorbance": float(A) if np.isfinite(A) else np.nan,
                        "absorbance_smooth": float(As) if np.isfinite(As) else np.nan,
                        "scattering_net": np.nan,
                        "scattering_shape": np.nan,
                        "scattering_shape_smooth": np.nan,
                        "valid": bool(valid),
                    }
                )

        for sc in result.scattering_results:
            pf = sc.sample.parsed
            for w, net, shape, shape_smooth, valid in zip(
                sc.wl,
                sc.signal_net,
                sc.signal_shape,
                sc.signal_shape_smooth,
                sc.valid_mask,
            ):
                rows.append(
                    {
                        "group_key": result.group_key,
                        "analysis_type": "scattering_shape",
                        "series_id": pf.series_id,
                        "diode": pf.diode,
                        "sample": pf.sample,
                        "geometry": pf.geometry,
                        "config": pf.config,
                        "role": pf.role,
                        "source_file": pf.path.name,
                        "source_member": sc.sample.source_member,
                        "blank_file": sc.blank.parsed.path.name if sc.blank else "",
                        "integration_time": sc.sample.integration_time if sc.sample.integration_time is not None else np.nan,
                        "blank_integration_time": sc.blank.integration_time if sc.blank and sc.blank.integration_time is not None else np.nan,
                        "integration_time_norm_factor": sc.sample.integration_time_normalization_factor,
                        "wavelength_nm": float(w),
                        "signal_raw": np.nan,
                        "signal_normalized": np.nan,
                        "signal_net": np.nan,
                        "signal_gated": np.nan,
                        "transmittance": np.nan,
                        "transmittance_smooth": np.nan,
                        "absorbance": np.nan,
                        "absorbance_smooth": np.nan,
                        "scattering_net": float(net) if np.isfinite(net) else np.nan,
                        "scattering_shape": float(shape) if np.isfinite(shape) else np.nan,
                        "scattering_shape_smooth": float(shape_smooth) if np.isfinite(shape_smooth) else np.nan,
                        "valid": bool(valid),
                    }
                )
    return pd.DataFrame(rows)



def _quantity_unit(quantity: str) -> str:
    if quantity.endswith("integration_time"):
        return "IntegrationTime metadata units"
    if quantity == "fractional_loss_auc":
        return "fraction*nm"
    if quantity == "scattering_net_auc":
        return "normalised_signal*nm"
    if quantity == "scattering_shape_auc":
        return "norm*nm"
    return "nm"


def integrals_dataframe(results: list[GroupResult]) -> pd.DataFrame:
    rows: list[dict] = []
    for result in results:
        for tr in result.transmittance_results:
            pf = tr.sample.parsed
            for quantity, value in tr.integrals.items():
                rows.append(
                    {
                        "group_key": result.group_key,
                        "analysis_type": "transmittance_absorbance",
                        "series_id": pf.series_id,
                        "diode": pf.diode,
                        "sample": pf.sample,
                        "geometry": pf.geometry,
                        "config": pf.config,
                        "role": pf.role,
                        "source_file": pf.path.name,
                        "blank_file": tr.blank.parsed.path.name,
                        "quantity": quantity,
                        "value": value,
                        "unit": _quantity_unit(quantity),
                    }
                )
        for sc in result.scattering_results:
            pf = sc.sample.parsed
            for quantity, value in sc.integrals.items():
                rows.append(
                    {
                        "group_key": result.group_key,
                        "analysis_type": "scattering_shape",
                        "series_id": pf.series_id,
                        "diode": pf.diode,
                        "sample": pf.sample,
                        "geometry": pf.geometry,
                        "config": pf.config,
                        "role": pf.role,
                        "source_file": pf.path.name,
                        "blank_file": sc.blank.parsed.path.name if sc.blank else "",
                        "quantity": quantity,
                        "value": value,
                        "unit": _quantity_unit(quantity),
                    }
                )
    return pd.DataFrame(rows)



def _config_dict(cfg: ProcessingConfig) -> dict:
    d = asdict(cfg)
    d["analysis_window_nm"] = list(cfg.analysis_window_nm)
    d["baseline_ranges_nm"] = [list(x) for x in cfg.baseline_ranges_nm]
    return d



def write_report(run: PipelineRunResult, cfg: ProcessingConfig) -> Path:
    report = run.output_dir / "run_report.md"
    lines: list[str] = []
    lines.append("# Spectrophotometer analysis run report")
    lines.append("")
    lines.append(f"Input folder: `{run.input_dir}`")
    lines.append(f"Output folder: `{run.output_dir}`")
    lines.append(f"Detected files: {len(run.files)}")
    lines.append(f"Analysis groups: {len(run.groups)}")
    lines.append(f"Processed groups: {len(run.results)}")
    lines.append(f"Written files: {len(run.written_files)}")
    lines.append("")
    lines.append("## Processing config")
    lines.append("")
    for k, v in _config_dict(cfg).items():
        lines.append(f"- `{k}`: `{v}`")
    lines.append("")
    lines.append("## Detected files")
    lines.append("")
    for pf in run.files:
        status = "OK" if pf.parse_ok else "WARN"
        note = f" - {'; '.join(pf.parse_notes)}" if pf.parse_notes else ""
        lines.append(f"- {status}: `{pf.path.name}` -> {pf.display_label} [{pf.role}]{note}")
    lines.append("")
    lines.append("## Warnings")
    lines.append("")
    if run.warnings:
        for w in run.warnings:
            lines.append(f"- {w}")
    else:
        lines.append("No warnings.")
    lines.append("")
    lines.append("## Written files")
    lines.append("")
    for p in run.written_files:
        lines.append(f"- `{p.relative_to(run.output_dir)}`")
    report.write_text("\n".join(lines), encoding="utf-8")
    return report



def run_pipeline(
    input_dir: Path | str,
    output_dir: Optional[Path | str] = None,
    config: Optional[ProcessingConfig] = None,
    split_samples: bool = True,
    export_vega: bool = True,
    export_pdf: bool = True,
    recursive: bool = False,
    include_raw_curves: bool = False,
) -> PipelineRunResult:
    input_dir = Path(input_dir)
    if output_dir is None:
        output_dir = input_dir / "analysis_outputs"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = config or ProcessingConfig()
    files = scan_folder(input_dir, recursive=recursive)
    warnings: list[str] = []
    warnings.extend(validate_dataset(files))

    groups = group_by_plot(files, split_samples=split_samples)
    results: list[GroupResult] = []

    for key, group in groups.items():
        group_warnings = validate_group(group)
        warnings.extend([f"{key}: {w}" for w in group_warnings])
        try:
            result = process_group(group, cfg, group_key=key)
            warnings.extend([f"{key}: {w}" for w in result.warnings])
            results.append(result)
        except Exception as exc:
            warnings.append(f"{key}: processing failed: {exc}")

    written = export_all(results, output_dir, cfg, export_vega=export_vega, export_pdf=export_pdf)

    processed_dir = output_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    fdf = files_dataframe(files)
    cdf = curves_dataframe(results, include_raw=include_raw_curves)
    idf = integrals_dataframe(results)
    fdf.to_csv(processed_dir / "detected_files.csv", index=False)
    cdf.to_csv(processed_dir / "analysis_ready_curves.csv", index=False)
    idf.to_csv(processed_dir / "analysis_integrals.csv", index=False)
    written.extend([
        processed_dir / "detected_files.csv",
        processed_dir / "analysis_ready_curves.csv",
        processed_dir / "analysis_integrals.csv",
    ])

    if warnings:
        warn_path = output_dir / "warnings.txt"
        warn_path.write_text("\n".join(warnings), encoding="utf-8")
        written.append(warn_path)

    run = PipelineRunResult(
        input_dir=input_dir,
        output_dir=output_dir,
        files=files,
        groups=groups,
        results=results,
        written_files=written,
        warnings=warnings,
    )
    written.append(write_report(run, cfg))
    return run
