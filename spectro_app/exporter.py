"""
exporter.py
===========

Export GroupResult objects to:

* Vega-Lite JSON - editable in Vega Editor
* PDF - rendered through matplotlib

The default exporter writes separate transmittance and absorbance plots. This
matches the measurement workflow where each sample/diode/geometry/config should
be inspectable without accidental overplotting.
"""

from __future__ import annotations

import json
import re
import csv
import zipfile
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Optional

import numpy as np

if __package__:
    from .processor import GroupResult, ProcessingConfig, QuantumConversionResult, ScatteringResult, TransmittanceResult
else:
    from processor import GroupResult, ProcessingConfig, QuantumConversionResult, ScatteringResult, TransmittanceResult
    
_PALETTE = [
    "#2166ac", "#d6604d", "#4dac26", "#b2abd2",
    "#f4a582", "#92c5de", "#e08214", "#543005",
]


def _safe_slug(text: str) -> str:
    text = text.strip().replace(" ", "_")
    text = re.sub(r"[^A-Za-z0-9_.\-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_") or "plot"


def _color(i: int) -> str:
    return _PALETTE[i % len(_PALETTE)]


def _base_spec(title: str, x_label: str, y_label: str) -> dict:
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": {"text": title, "fontSize": 14, "fontWeight": "bold"},
        "width": 760,
        "height": 420,
        "config": {
            "axis": {"labelFontSize": 11, "titleFontSize": 12},
            "legend": {"labelFontSize": 10, "titleFontSize": 11},
        },
        "encoding": {
            "x": {
                "field": "wl",
                "type": "quantitative",
                "title": x_label,
                "scale": {"zero": False},
            }
        },
        "layer": [],
    }


def _shade_layer(lo: float, hi: float) -> dict:
    return {
        "name": "valid-region",
        "data": {"values": [{"lo": lo, "hi": hi}]},
        "mark": {"type": "rect", "opacity": 0.08, "color": "#888888"},
        "encoding": {"x": {"field": "lo", "type": "quantitative"}, "x2": {"field": "hi"}},
    }


def _hline_layer(y_val: float, color: str = "#999999") -> dict:
    return {
        "name": "reference-line",
        "data": {"values": [{"y": y_val}]},
        "mark": {"type": "rule", "strokeDash": [4, 4], "color": color, "strokeWidth": 1},
        "encoding": {"y": {"field": "y", "type": "quantitative"}},
    }


def _series_layer(all_rows: list[dict], y_field: str, y_title: str) -> dict:
    return {
        "name": "series-lines",
        "data": {"values": all_rows},
        "mark": {"type": "line", "strokeWidth": 2},
        "encoding": {
            "y": {
                "field": y_field,
                "type": "quantitative",
                "title": y_title,
                "scale": {"zero": False},
            },
            "color": {
                "field": "series",
                "type": "nominal",
                "title": "Sample",
                "scale": {"range": _PALETTE},
            },
            "tooltip": [
                {"field": "wl", "type": "quantitative", "title": "λ (nm)", "format": ".1f"},
                {"field": y_field, "type": "quantitative", "title": y_title, "format": ".5f"},
                {"field": "series", "type": "nominal", "title": "Sample"},
            ],
        },
        "transform": [{"filter": f"datum.{y_field} != null"}],
    }


def _transmittance_rows(tr_list: list[TransmittanceResult], y_field: str) -> list[dict]:
    all_rows: list[dict] = []
    for tr in tr_list:
        label = tr.sample.parsed.display_label
        src = tr.transmittance_smooth if y_field == "T" else tr.absorbance_smooth
        for w, v in zip(tr.wl, src):
            all_rows.append({"wl": float(w), y_field: float(v) if np.isfinite(v) else None, "series": label})
    return all_rows


def _scattering_rows(sc_list: list[ScatteringResult]) -> list[dict]:
    all_rows: list[dict] = []
    for sc in sc_list:
        label = sc.sample.parsed.display_label
        for w, v in zip(sc.wl, sc.signal_shape_smooth):
            all_rows.append({"wl": float(w), "S": float(v) if np.isfinite(v) else None, "series": label})
    return all_rows


def _quantum_conversion_rows(qc_list: list[QuantumConversionResult]) -> list[dict]:
    all_rows: list[dict] = []
    for qc in qc_list:
        incoming_label = f"Incoming UV ({qc.blank.parsed.sample}, {qc.blank.parsed.geometry})"
        emitted_label = f"Emitted - {qc.sample.parsed.display_label}"
        for w, incoming, emitted in zip(qc.wl, qc.incoming_energy_signal, qc.emitted_energy_signal):
            all_rows.append({
                "wl": float(w),
                "E": float(incoming) if np.isfinite(incoming) else None,
                "series": incoming_label,
            })
            all_rows.append({
                "wl": float(w),
                "E": float(emitted) if np.isfinite(emitted) else None,
                "series": emitted_label,
            })
    return all_rows


def _scale_to_unit_peak(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if not finite.any():
        return np.full_like(values, np.nan, dtype=float)
    peak = float(np.nanmax(np.abs(values[finite])))
    if peak <= 0 or not np.isfinite(peak):
        return np.full_like(values, np.nan, dtype=float)
    return values / peak


def _plot_smooth(values: np.ndarray, window: int = 41) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if finite.sum() < 5:
        return values.copy()
    n = values.size
    w = min(int(window), n if n % 2 == 1 else n - 1)
    if w < 5:
        return values.copy()
    if w % 2 == 0:
        w -= 1
    x = np.arange(n)
    filled = np.interp(x, x[finite], values[finite])
    kernel = np.ones(w, dtype=float) / float(w)
    smoothed = np.convolve(filled, kernel, mode="same")
    smoothed[~finite] = np.nan
    return smoothed


def _quantum_conversion_scaled_rows(qc_list: list[QuantumConversionResult]) -> list[dict]:
    all_rows: list[dict] = []
    for qc in qc_list:
        incoming_label = f"Incoming UV scaled ({qc.blank.parsed.sample}, {qc.blank.parsed.geometry})"
        emitted_label = f"Emitted scaled - {qc.sample.parsed.display_label}"
        incoming_scaled = _scale_to_unit_peak(qc.incoming_energy_signal)
        emitted_scaled = _scale_to_unit_peak(qc.emitted_energy_signal)
        for w, incoming, emitted in zip(qc.wl, incoming_scaled, emitted_scaled):
            all_rows.append({
                "wl": float(w),
                "E_scaled": float(incoming) if np.isfinite(incoming) else None,
                "series": incoming_label,
            })
            all_rows.append({
                "wl": float(w),
                "E_scaled": float(emitted) if np.isfinite(emitted) else None,
                "series": emitted_label,
            })
    return all_rows


def _unique_raw_spectra(results: list[GroupResult]) -> list:
    seen: set[str] = set()
    spectra = []
    for result in results:
        for sr in result.raw_spectra:
            key = str(sr.parsed.path)
            if key in seen:
                continue
            seen.add(key)
            spectra.append(sr)
    spectra.sort(key=lambda sr: (sr.parsed.series_id, sr.parsed.diode, sr.parsed.geometry, sr.parsed.sample, sr.parsed.config, sr.parsed.path.name))
    return spectra


def _all_raw_measurement_rows(results: list[GroupResult]) -> list[dict]:
    all_rows: list[dict] = []
    for sr in _unique_raw_spectra(results):
        smooth = _plot_smooth(sr.signal_normalized)
        raw_label = f"{sr.parsed.display_label} raw"
        smooth_label = f"{sr.parsed.display_label} smooth"
        for w, raw, smoothed in zip(sr.wl, sr.signal_normalized, smooth):
            all_rows.append({
                "wl": float(w),
                "I": float(raw) if np.isfinite(raw) else None,
                "series": raw_label,
            })
            all_rows.append({
                "wl": float(w),
                "I": float(smoothed) if np.isfinite(smoothed) else None,
                "series": smooth_label,
            })
    return all_rows


def _raw_comparison_rows(tr_list: list[TransmittanceResult]) -> list[dict]:
    all_rows: list[dict] = []
    seen_blank_labels: set[str] = set()
    for i, tr in enumerate(tr_list):
        blank_label = f"Air reference ({tr.blank.parsed.diode}, {tr.blank.parsed.geometry})"
        if blank_label not in seen_blank_labels:
            for w, v in zip(tr.blank.wl, tr.blank.signal_normalized):
                all_rows.append({
                    "wl": float(w),
                    "I": float(v) if np.isfinite(v) else None,
                    "series": blank_label,
                })
            seen_blank_labels.add(blank_label)
        sample_label = tr.sample.parsed.display_label
        for w, v in zip(tr.sample.wl, tr.sample.signal_normalized):
            all_rows.append({
                "wl": float(w),
                "I": float(v) if np.isfinite(v) else None,
                "series": sample_label,
            })
    return all_rows


def _add_valid_region(spec: dict, valid_mask: np.ndarray, wl: np.ndarray) -> None:
    wl_valid = wl[valid_mask]
    if len(wl_valid) > 0:
        spec["layer"].append(_shade_layer(float(wl_valid[0]), float(wl_valid[-1])))


def _transmittance_spec(tr_list: list[TransmittanceResult], group_key: str) -> dict:
    spec = _base_spec(f"Transmittance - {group_key}", "Wavelength (nm)", "Transmittance T")
    spec["layer"].append(_hline_layer(1.0))
    if tr_list:
        _add_valid_region(spec, tr_list[0].valid_mask, tr_list[0].wl)
    spec["layer"].append(_series_layer(_transmittance_rows(tr_list, "T"), "T", "Transmittance T"))
    return spec


def _absorbance_spec(tr_list: list[TransmittanceResult], group_key: str) -> dict:
    spec = _base_spec(f"Absorbance - {group_key}", "Wavelength (nm)", "Absorbance A")
    if tr_list:
        _add_valid_region(spec, tr_list[0].valid_mask, tr_list[0].wl)
    spec["layer"].append(_series_layer(_transmittance_rows(tr_list, "A"), "A", "Absorbance A"))
    return spec


def _raw_comparison_spec(tr_list: list[TransmittanceResult], group_key: str) -> dict:
    y_title = "Raw signal / IntegrationTime" if tr_list and tr_list[0].sample.integration_time_normalization_factor != 1.0 else "Raw signal"
    spec = _base_spec(f"Raw measurement comparison - {group_key}", "Wavelength (nm)", y_title)
    if tr_list:
        _add_valid_region(spec, tr_list[0].valid_mask, tr_list[0].wl)
    spec["layer"].append(_series_layer(_raw_comparison_rows(tr_list), "I", y_title))
    return spec


def _scattering_spec(sc_list: list[ScatteringResult], group_key: str) -> dict:
    spec = _base_spec(f"Scattering shape 90deg - {group_key}", "Wavelength (nm)", "Normalised scattering intensity")
    spec["layer"].append(_series_layer(_scattering_rows(sc_list), "S", "Normalised scattering"))
    return spec


def _quantum_conversion_spec(qc_list: list[QuantumConversionResult], group_key: str) -> dict:
    spec = _base_spec(f"Quantum conversion - {group_key}", "Wavelength (nm)", "Energy-weighted signal")
    if qc_list:
        _add_valid_region(spec, qc_list[0].valid_mask, qc_list[0].wl)
    spec["layer"].append(_series_layer(_quantum_conversion_rows(qc_list), "E", "Energy-weighted signal"))
    return spec


def _quantum_conversion_scaled_spec(qc_list: list[QuantumConversionResult], group_key: str) -> dict:
    spec = _base_spec(f"Quantum conversion scaled peaks - {group_key}", "Wavelength (nm)", "Scaled energy-weighted signal")
    if qc_list:
        _add_valid_region(spec, qc_list[0].valid_mask, qc_list[0].wl)
    spec["layer"].append(_series_layer(_quantum_conversion_scaled_rows(qc_list), "E_scaled", "Scaled energy-weighted signal"))
    return spec


def _all_raw_measurements_spec(results: list[GroupResult]) -> dict:
    spec = _base_spec("All raw measurements", "Wavelength (nm)", "Signal / IntegrationTime")
    spec["layer"].append(_series_layer(_all_raw_measurement_rows(results), "I", "Signal / IntegrationTime"))
    return spec


def _plot_single_pdf(
    out_path: Path,
    title: str,
    x_label: str,
    y_label: str,
    series: list[tuple[np.ndarray, np.ndarray, str]],
    valid_region: Optional[tuple[float, float]] = None,
    hline: Optional[float] = None,
    interest_region: Optional[tuple[float, float]] = None,
    fit_overlays: Optional[list[tuple[float, float, str]]] = None,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5.6))
    _draw_plot(
        ax,
        title,
        x_label,
        y_label,
        series,
        valid_region=valid_region,
        hline=hline,
        interest_region=interest_region,
        fit_overlays=fit_overlays,
    )
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", dpi=300)
    fig.savefig(out_path.with_suffix(".png"), bbox_inches="tight", dpi=300)

    plt.close(fig)


def _draw_plot(
    ax,
    title: str,
    x_label: str,
    y_label: str,
    series: list[tuple[np.ndarray, np.ndarray, str]],
    valid_region: Optional[tuple[float, float]] = None,
    hline: Optional[float] = None,
    interest_region: Optional[tuple[float, float]] = None,
    fit_overlays: Optional[list[tuple[float, float, str]]] = None,
) -> None:
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)

    if valid_region is not None:
        ax.axvspan(valid_region[0], valid_region[1], alpha=0.08, color="grey", label="_nolegend_")
    if interest_region is not None:
        ax.axvspan(interest_region[0], interest_region[1], alpha=0.12, color="#f4a582", label="interest window")
    if hline is not None:
        ax.axhline(hline, linestyle="--", linewidth=0.9, color="grey")

    fit_equations: list[str] = []
    for i, (wl, values, label) in enumerate(series):
        ax.plot(wl, values, color=_color(i), linewidth=1.8, label=label)
        if fit_overlays and i < len(fit_overlays) and interest_region is not None:
            slope, intercept, fit_label = fit_overlays[i]
            if np.isfinite(slope) and np.isfinite(intercept):
                x_fit = _extended_fit_range(interest_region, wl)
                y_fit = slope * x_fit + intercept
                ax.plot(x_fit, y_fit, color=_color(i), linewidth=1.4, linestyle=":", label=f"{fit_label} fit")
                fit_equations.append(f"{fit_label}: {_linear_equation_text(slope, intercept)}")

    ax.grid(alpha=0.22)
    ax.legend(fontsize=7, loc="best")
    if fit_equations:
        ax.text(
            0.015,
            0.985,
            "\n".join(fit_equations),
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=7,
            bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#bbbbbb", "alpha": 0.82},
        )


def _extended_fit_range(interest_region: tuple[float, float], wl: np.ndarray) -> np.ndarray:
    lo, hi = interest_region
    width = hi - lo
    if not np.isfinite(width) or width <= 0:
        return np.array([lo, hi], dtype=float)

    x_lo = lo - 0.5 * width
    x_hi = hi + 0.5 * width
    finite_wl = wl[np.isfinite(wl)]
    if finite_wl.size:
        x_lo = max(x_lo, float(np.nanmin(finite_wl)))
        x_hi = min(x_hi, float(np.nanmax(finite_wl)))
    return np.array([x_lo, x_hi], dtype=float)


def _linear_equation_text(slope: float, intercept: float) -> str:
    sign = "+" if intercept >= 0 else "-"
    return f"y = {_fmt_float(slope, 4)}x {sign} {_fmt_float(abs(intercept), 4)}"


def _valid_region_from_tr(tr: TransmittanceResult) -> Optional[tuple[float, float]]:
    wl_valid = tr.wl[tr.valid_mask]
    if len(wl_valid) == 0:
        return None
    return float(wl_valid[0]), float(wl_valid[-1])


def _valid_region_from_qc(qc: QuantumConversionResult) -> Optional[tuple[float, float]]:
    wl_valid = qc.wl[qc.valid_mask]
    if len(wl_valid) == 0:
        return None
    return float(wl_valid[0]), float(wl_valid[-1])


def _interest_region(config: Optional[ProcessingConfig]) -> Optional[tuple[float, float]]:
    if config is None:
        return None
    return float(config.interest_window_nm[0]), float(config.interest_window_nm[1])


def _fit_overlays(tr_list: list[TransmittanceResult], prefix: str) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for tr in tr_list:
        slope = tr.integrals.get(f"interest_{prefix}_fit_slope", float("nan"))
        intercept = tr.integrals.get(f"interest_{prefix}_fit_intercept", float("nan"))
        out.append((float(slope), float(intercept), tr.sample.parsed.display_label))
    return out


def _save_pdf_transmittance(
    tr_list: list[TransmittanceResult],
    out_path: Path,
    group_key: str,
    config: Optional[ProcessingConfig] = None,
) -> None:
    series = [(tr.wl, tr.transmittance_smooth, tr.sample.parsed.display_label) for tr in tr_list]
    valid = _valid_region_from_tr(tr_list[0]) if tr_list else None
    _plot_single_pdf(
        out_path,
        f"Transmittance - {group_key}",
        "Wavelength (nm)",
        "Transmittance T",
        series,
        valid,
        1.0,
        _interest_region(config),
        _fit_overlays(tr_list, "transmittance"),
    )


def _save_pdf_absorbance(
    tr_list: list[TransmittanceResult],
    out_path: Path,
    group_key: str,
    config: Optional[ProcessingConfig] = None,
) -> None:
    series = [(tr.wl, tr.absorbance_smooth, tr.sample.parsed.display_label) for tr in tr_list]
    valid = _valid_region_from_tr(tr_list[0]) if tr_list else None
    _plot_single_pdf(
        out_path,
        f"Absorbance - {group_key}",
        "Wavelength (nm)",
        "Absorbance A",
        series,
        valid,
        None,
        _interest_region(config),
        _fit_overlays(tr_list, "absorbance"),
    )


def _save_pdf_raw_comparison(tr_list: list[TransmittanceResult], out_path: Path, group_key: str) -> None:
    series: list[tuple[np.ndarray, np.ndarray, str]] = []
    seen_blank_labels: set[str] = set()
    for tr in tr_list:
        blank_label = f"Air reference ({tr.blank.parsed.diode}, {tr.blank.parsed.geometry})"
        if blank_label not in seen_blank_labels:
            series.append((tr.blank.wl, tr.blank.signal_normalized, blank_label))
            seen_blank_labels.add(blank_label)
        series.append((tr.sample.wl, tr.sample.signal_normalized, tr.sample.parsed.display_label))
    valid = _valid_region_from_tr(tr_list[0]) if tr_list else None
    normalized = any((tr.sample.integration_time_normalization_factor != 1.0 or tr.blank.integration_time_normalization_factor != 1.0) for tr in tr_list)
    y_label = "Raw signal / IntegrationTime" if normalized else "Raw signal"
    _plot_single_pdf(out_path, f"Raw measurement comparison - {group_key}", "Wavelength (nm)", y_label, series, valid, None)


def _save_pdf_scattering(sc_list: list[ScatteringResult], out_path: Path, group_key: str) -> None:
    series = [(sc.wl, sc.signal_shape_smooth, sc.sample.parsed.display_label) for sc in sc_list]
    _plot_single_pdf(
        out_path,
        f"Scattering shape 90deg - {group_key}",
        "Wavelength (nm)",
        "Normalised scattering intensity",
        series,
        None,
        None,
    )


def _save_pdf_quantum_conversion(qc_list: list[QuantumConversionResult], out_path: Path, group_key: str) -> None:
    series: list[tuple[np.ndarray, np.ndarray, str]] = []
    seen_incoming: set[str] = set()
    for qc in qc_list:
        incoming_label = f"Incoming UV ({qc.blank.parsed.sample}, {qc.blank.parsed.geometry})"
        if incoming_label not in seen_incoming:
            series.append((qc.wl, qc.incoming_energy_signal, incoming_label))
            seen_incoming.add(incoming_label)
        series.append((qc.wl, qc.emitted_energy_signal, f"Emitted - {qc.sample.parsed.display_label}"))
    valid = _valid_region_from_qc(qc_list[0]) if qc_list else None
    _plot_single_pdf(
        out_path,
        f"Quantum conversion - {group_key}",
        "Wavelength (nm)",
        "Energy-weighted signal",
        series,
        valid,
        None,
    )


def _save_pdf_quantum_conversion_scaled(qc_list: list[QuantumConversionResult], out_path: Path, group_key: str) -> None:
    series: list[tuple[np.ndarray, np.ndarray, str]] = []
    seen_incoming: set[str] = set()
    for qc in qc_list:
        incoming_label = f"Incoming UV scaled ({qc.blank.parsed.sample}, {qc.blank.parsed.geometry})"
        if incoming_label not in seen_incoming:
            series.append((qc.wl, _scale_to_unit_peak(qc.incoming_energy_signal), incoming_label))
            seen_incoming.add(incoming_label)
        series.append((qc.wl, _scale_to_unit_peak(qc.emitted_energy_signal), f"Emitted scaled - {qc.sample.parsed.display_label}"))
    valid = _valid_region_from_qc(qc_list[0]) if qc_list else None
    _plot_single_pdf(
        out_path,
        f"Quantum conversion scaled peaks - {group_key}",
        "Wavelength (nm)",
        "Scaled energy-weighted signal",
        series,
        valid,
        None,
    )


def _save_pdf_all_raw_measurements(results: list[GroupResult], out_path: Path) -> None:
    series: list[tuple[np.ndarray, np.ndarray, str]] = []
    for sr in _unique_raw_spectra(results):
        series.append((sr.wl, sr.signal_normalized, f"{sr.parsed.display_label} raw"))
        series.append((sr.wl, _plot_smooth(sr.signal_normalized), f"{sr.parsed.display_label} smooth"))
    normalized = any(sr.integration_time_normalization_factor != 1.0 for sr in _unique_raw_spectra(results))
    y_label = "Signal / IntegrationTime" if normalized else "Signal"
    _plot_single_pdf(out_path, "All raw measurements", "Wavelength (nm)", y_label, series, None, None)


def export_group(
    result: GroupResult,
    out_dir: Path,
    config: Optional[ProcessingConfig] = None,
    export_vega: bool = True,
    export_pdf: bool = True,
) -> list[Path]:
    out_dir = Path(out_dir)
    written: list[Path] = []
    key = _safe_slug(result.group_key)

    if export_vega:
        (out_dir / "vega").mkdir(parents=True, exist_ok=True)
    if export_pdf:
        (out_dir / "pdf").mkdir(parents=True, exist_ok=True)

    if result.transmittance_results:
        tr_list = result.transmittance_results
        if export_vega:
            p = out_dir / "vega" / f"{key}_raw_data.json"
            p.write_text(json.dumps(_raw_comparison_spec(tr_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
            p = out_dir / "vega" / f"{key}_transmittance.json"
            p.write_text(json.dumps(_transmittance_spec(tr_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
            p = out_dir / "vega" / f"{key}_absorbance.json"
            p.write_text(json.dumps(_absorbance_spec(tr_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
        if export_pdf:
            p = out_dir / "pdf" / f"{key}_raw_data.pdf"
            _save_pdf_raw_comparison(tr_list, p, result.group_key)
            written.append(p)
            p = out_dir / "pdf" / f"{key}_transmittance.pdf"
            _save_pdf_transmittance(tr_list, p, result.group_key, config)
            written.append(p)
            p = out_dir / "pdf" / f"{key}_absorbance.pdf"
            _save_pdf_absorbance(tr_list, p, result.group_key, config)
            written.append(p)

    if result.quantum_conversion_results:
        qc_list = result.quantum_conversion_results
        if export_vega:
            p = out_dir / "vega" / f"{key}_quantum_conversion.json"
            p.write_text(json.dumps(_quantum_conversion_spec(qc_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
            p = out_dir / "vega" / f"{key}_quantum_conversion_scaled.json"
            p.write_text(json.dumps(_quantum_conversion_scaled_spec(qc_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
        if export_pdf:
            p = out_dir / "pdf" / f"{key}_quantum_conversion.pdf"
            _save_pdf_quantum_conversion(qc_list, p, result.group_key)
            written.append(p)
            p = out_dir / "pdf" / f"{key}_quantum_conversion_scaled.pdf"
            _save_pdf_quantum_conversion_scaled(qc_list, p, result.group_key)
            written.append(p)

    return written


def _fmt_float(value: float, digits: int = 5) -> str:
    if value is None or not np.isfinite(value):
        return "nan"
    return f"{value:.{digits}g}"


def _observable_columns() -> list[str]:
    return [
        "Group",
        "Sample",
        "Analysis",
        "T ROI AUC",
        "T fit angle [deg]",
        "T fit a",
        "T fit b",
        "A ROI AUC",
        "A fit angle [deg]",
        "A fit a",
        "A fit b",
        "QC efficiency",
        "Emitted energy AUC",
        "Incoming energy AUC",
    ]


def _transmittance_observable_columns() -> list[str]:
    return [
        "Group",
        "Sample",
        "T ROI AUC",
        "T fit angle [deg]",
        "T fit a",
        "T fit b",
        "A ROI AUC",
        "A fit angle [deg]",
        "A fit a",
        "A fit b",
    ]


def _quantum_observable_columns() -> list[str]:
    return [
        "Group",
        "Sample",
        "QC efficiency",
        "Emitted energy AUC",
        "Incoming energy AUC",
    ]


def _transmittance_observable_rows(results: list[GroupResult]) -> list[list[str]]:
    rows: list[list[str]] = []
    for result in results:
        for tr in result.transmittance_results:
            rows.append([
                result.group_key,
                tr.sample.parsed.display_label,
                _fmt_float(tr.integrals.get("interest_transmittance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_slope", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_intercept", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_slope", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_intercept", float("nan"))),
            ])
    return rows


def _quantum_observable_rows(results: list[GroupResult]) -> list[list[str]]:
    rows: list[list[str]] = []
    for result in results:
        for qc in result.quantum_conversion_results:
            rows.append([
                result.group_key,
                qc.sample.parsed.display_label,
                _fmt_float(qc.integrals.get("quantum_conversion_efficiency", float("nan"))),
                _fmt_float(qc.integrals.get("emitted_energy_auc", float("nan"))),
                _fmt_float(qc.integrals.get("incoming_energy_auc", float("nan"))),
            ])
    return rows


def _observable_rows(results: list[GroupResult]) -> list[list[str]]:
    rows: list[list[str]] = []
    for result in results:
        for tr in result.transmittance_results:
            label = tr.sample.parsed.display_label
            rows.append([
                result.group_key,
                label,
                "transmittance_absorbance",
                _fmt_float(tr.integrals.get("interest_transmittance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_slope", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_intercept", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_slope", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_intercept", float("nan"))),
                "",
                "",
                "",
            ])
        for qc in result.quantum_conversion_results:
            label = qc.sample.parsed.display_label
            rows.append([
                result.group_key,
                label,
                "quantum_conversion",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                _fmt_float(qc.integrals.get("quantum_conversion_efficiency", float("nan"))),
                _fmt_float(qc.integrals.get("emitted_energy_auc", float("nan"))),
                _fmt_float(qc.integrals.get("incoming_energy_auc", float("nan"))),
            ])
    return rows


def _observable_rows_by_diode(results: list[GroupResult]) -> dict[str, list[list[str]]]:
    rows_by_diode: dict[str, list[list[str]]] = defaultdict(list)
    for result in results:
        for tr in result.transmittance_results:
            rows_by_diode[tr.sample.parsed.diode].append([
                result.group_key,
                tr.sample.parsed.display_label,
                "transmittance_absorbance",
                _fmt_float(tr.integrals.get("interest_transmittance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_slope", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_intercept", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_slope", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_intercept", float("nan"))),
                "",
                "",
                "",
            ])
        for qc in result.quantum_conversion_results:
            rows_by_diode[qc.sample.parsed.diode].append([
                result.group_key,
                qc.sample.parsed.display_label,
                "quantum_conversion",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                _fmt_float(qc.integrals.get("quantum_conversion_efficiency", float("nan"))),
                _fmt_float(qc.integrals.get("emitted_energy_auc", float("nan"))),
                _fmt_float(qc.integrals.get("incoming_energy_auc", float("nan"))),
            ])
    return dict(rows_by_diode)


def _excel_col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _excel_sheet_name(name: str, used: set[str]) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", name).strip() or "Sheet"
    cleaned = cleaned[:31]
    candidate = cleaned
    suffix = 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = f"{cleaned[:31 - len(tail)]}{tail}"
        suffix += 1
    used.add(candidate)
    return candidate


def _worksheet_xml(rows: list[list[str]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>',
        '<sheetFormatPr defaultRowHeight="15"/>',
        '<cols>',
    ]
    for idx in range(1, len(rows[0]) + 1):
        width = 34 if idx in {1, 2} else 16
        lines.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
    lines.append('</cols><sheetData>')
    for r_idx, row in enumerate(rows, start=1):
        lines.append(f'<row r="{r_idx}">')
        for c_idx, value in enumerate(row, start=1):
            ref = f"{_excel_col_name(c_idx)}{r_idx}"
            text = escape(str(value), quote=False)
            lines.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        lines.append('</row>')
    lines.append(
        f'</sheetData><tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>'
    )
    return "".join(lines)


def _write_observable_rows_xlsx(results: list[GroupResult], out_dir: Path) -> Path:
    out_path = out_dir / "final_report_observables.xlsx"
    used_names: set[str] = set()
    sheets: list[tuple[str, list[list[str]]]] = []
    tr_by_diode: dict[str, list[list[str]]] = defaultdict(list)
    qc_by_diode: dict[str, list[list[str]]] = defaultdict(list)
    for result in results:
        for tr in result.transmittance_results:
            tr_by_diode[tr.sample.parsed.diode].append([
                result.group_key,
                tr.sample.parsed.display_label,
                _fmt_float(tr.integrals.get("interest_transmittance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_slope", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_intercept", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_slope", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_intercept", float("nan"))),
            ])
        for qc in result.quantum_conversion_results:
            qc_by_diode[qc.sample.parsed.diode].append([
                result.group_key,
                qc.sample.parsed.display_label,
                _fmt_float(qc.integrals.get("quantum_conversion_efficiency", float("nan"))),
                _fmt_float(qc.integrals.get("emitted_energy_auc", float("nan"))),
                _fmt_float(qc.integrals.get("incoming_energy_auc", float("nan"))),
            ])

    for diode, rows in sorted(tr_by_diode.items()):
        sheets.append((_excel_sheet_name(f"{diode}_T", used_names), [_transmittance_observable_columns(), *rows]))
    for diode, rows in sorted(qc_by_diode.items()):
        sheets.append((_excel_sheet_name(f"{diode}_QC", used_names), [_quantum_observable_columns(), *rows]))
    if not sheets:
        sheets.append((_excel_sheet_name("Observables", used_names), [_observable_columns()]))

    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, (name, _rows) in enumerate(sheets, start=1)
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        for idx, _sheet in enumerate(sheets, start=1)
    )
    workbook_rels += (
        f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        f'<Override PartName="/xl/tables/table{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>'
        for idx, _sheet in enumerate(sheets, start=1)
    )

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            f'{overrides}</Types>'
        ))
        zf.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            '</Relationships>'
        ))
        zf.writestr("docProps/core.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Spectrophotometer observables</dc:title></cp:coreProperties>'
        ))
        zf.writestr("docProps/app.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            '<Application>Spectrophotometer analysis</Application></Properties>'
        ))
        zf.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{workbook_sheets}</sheets></workbook>'
        ))
        zf.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{workbook_rels}</Relationships>'
        ))
        zf.writestr("xl/styles.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
            '</styleSheet>'
        ))
        for idx, (_name, rows) in enumerate(sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", _worksheet_xml(rows))
            zf.writestr(f"xl/worksheets/_rels/sheet{idx}.xml.rels", (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table{idx}.xml"/>'
                '</Relationships>'
            ))
            columns = rows[0]
            ref = f"A1:{_excel_col_name(len(columns))}{max(2, len(rows))}"
            table_cols = "".join(
                f'<tableColumn id="{col_idx}" name="{escape(name)}"/>'
                for col_idx, name in enumerate(columns, start=1)
            )
            zf.writestr(f"xl/tables/table{idx}.xml", (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="{idx}" name="Table{idx}" displayName="Table{idx}" ref="{ref}" totalsRowShown="0">'
                f'<autoFilter ref="{ref}"/><tableColumns count="{len(columns)}">{table_cols}</tableColumns>'
                '<tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/>'
                '</table>'
            ))
    return out_path


def _write_observable_rows_csv(results: list[GroupResult], out_dir: Path) -> Path:
    out_path = out_dir / "final_report_observables.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_observable_columns())
        writer.writerows(_observable_rows(results))
    return out_path


def _write_analysis_observable_csvs(results: list[GroupResult], out_dir: Path) -> list[Path]:
    written: list[Path] = []
    tables = [
        (
            out_dir / "final_report_transmittance_observables.csv",
            _transmittance_observable_columns(),
            _transmittance_observable_rows(results),
        ),
        (
            out_dir / "final_report_quantum_conversion_observables.csv",
            _quantum_observable_columns(),
            _quantum_observable_rows(results),
        ),
    ]
    for path, columns, rows in tables:
        if not rows:
            continue
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)
        written.append(path)
    return written


def _report_text_page(pdf, title: str, lines: list[str]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(11.69, 8.27))
    fig.text(0.06, 0.92, title, fontsize=18, weight="bold")
    y = 0.84
    for line in lines:
        fig.text(0.06, y, line, fontsize=10)
        y -= 0.045
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _report_table_pages(pdf, title: str, columns: list[str], rows: list[list[str]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not rows:
        _report_text_page(pdf, title, ["No observables were calculated."])
        return

    page_size = 18
    for start in range(0, len(rows), page_size):
        chunk = rows[start:start + page_size]

        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        ax.set_title(title, fontsize=16, weight="bold", pad=18)

        weights = [
            1.0 if col == "Group"
            else 3.5 if col == "Sample"
            else 1.0
            for col in columns
        ]

        col_widths = [w / sum(weights) for w in weights]

        table = ax.table(
            cellText=chunk,
            colLabels=columns,
            loc="center",
            cellLoc="left",
            colLoc="left",
            colWidths=col_widths,
        )

        table.auto_set_font_size(False)
        table.set_fontsize(6)
        table.scale(1, 1.35)

        for (row, _col), cell in table.get_celld().items():
            if row == 0:
                cell.set_text_props(weight="bold")
                cell.set_facecolor("#eeeeee")

        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def _report_plot_page(
    pdf,
    title: str,
    x_label: str,
    y_label: str,
    series: list[tuple[np.ndarray, np.ndarray, str]],
    valid_region: Optional[tuple[float, float]] = None,
    hline: Optional[float] = None,
    interest_region: Optional[tuple[float, float]] = None,
    fit_overlays: Optional[list[tuple[float, float, str]]] = None,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    _draw_plot(
        ax,
        title,
        x_label,
        y_label,
        series,
        valid_region=valid_region,
        hline=hline,
        interest_region=interest_region,
        fit_overlays=fit_overlays,
    )
    fig.tight_layout()
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _write_final_report_pdf(
    results: list[GroupResult],
    out_dir: Path,
    config: Optional[ProcessingConfig],
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.backends.backend_pdf import PdfPages

    pdf_dir = out_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "final_report.pdf"
    interest = _interest_region(config)
    interest_label = f"{interest[0]:g}-{interest[1]:g} nm" if interest else "not configured"

    with PdfPages(report_path) as pdf:
        _report_text_page(
            pdf,
            "Spectrophotometer final report",
            [
                f"Groups: {len(results)}",
                f"Interest window: {interest_label}",
                "ROI AUC and linear-fit angle are calculated from smoothed transmittance/absorbance curves.",
                "The following pages contain observables first, then all generated plot pages.",
            ],
        )
        _report_table_pages(
            pdf,
            "Transmittance observables",
            _transmittance_observable_columns(),
            _transmittance_observable_rows(results),
        )
        _report_table_pages(
            pdf,
            "Quantum conversion observables",
            _quantum_observable_columns(),
            _quantum_observable_rows(results),
        )

        for result in results:
            if result.transmittance_results:
                tr_list = result.transmittance_results
                valid = _valid_region_from_tr(tr_list[0])
                raw_series: list[tuple[np.ndarray, np.ndarray, str]] = []
                seen_blank_labels: set[str] = set()
                for tr in tr_list:
                    blank_label = f"Air reference ({tr.blank.parsed.diode}, {tr.blank.parsed.geometry})"
                    if blank_label not in seen_blank_labels:
                        raw_series.append((tr.blank.wl, tr.blank.signal_normalized, blank_label))
                        seen_blank_labels.add(blank_label)
                    raw_series.append((tr.sample.wl, tr.sample.signal_normalized, tr.sample.parsed.display_label))
                normalized = any((tr.sample.integration_time_normalization_factor != 1.0 or tr.blank.integration_time_normalization_factor != 1.0) for tr in tr_list)
                y_label = "Raw signal / IntegrationTime" if normalized else "Raw signal"
                _report_plot_page(pdf, f"Raw measurement comparison - {result.group_key}", "Wavelength (nm)", y_label, raw_series, valid)
                _report_plot_page(
                    pdf,
                    f"Transmittance - {result.group_key}",
                    "Wavelength (nm)",
                    "Transmittance T",
                    [(tr.wl, tr.transmittance_smooth, tr.sample.parsed.display_label) for tr in tr_list],
                    valid,
                    1.0,
                    interest,
                    _fit_overlays(tr_list, "transmittance"),
                )
                _report_plot_page(
                    pdf,
                    f"Absorbance - {result.group_key}",
                    "Wavelength (nm)",
                    "Absorbance A",
                    [(tr.wl, tr.absorbance_smooth, tr.sample.parsed.display_label) for tr in tr_list],
                    valid,
                    None,
                    interest,
                    _fit_overlays(tr_list, "absorbance"),
                )

            if result.quantum_conversion_results:
                qc_list = result.quantum_conversion_results
                series: list[tuple[np.ndarray, np.ndarray, str]] = []
                seen_incoming: set[str] = set()
                for qc in qc_list:
                    incoming_label = f"Incoming UV ({qc.blank.parsed.sample}, {qc.blank.parsed.geometry})"
                    if incoming_label not in seen_incoming:
                        series.append((qc.wl, qc.incoming_energy_signal, incoming_label))
                        seen_incoming.add(incoming_label)
                    series.append((qc.wl, qc.emitted_energy_signal, f"Emitted - {qc.sample.parsed.display_label}"))
                _report_plot_page(
                    pdf,
                    f"Quantum conversion - {result.group_key}",
                    "Wavelength (nm)",
                    "Energy-weighted signal",
                    series,
                    _valid_region_from_qc(qc_list[0]),
                )
                scaled_series: list[tuple[np.ndarray, np.ndarray, str]] = []
                seen_incoming_scaled: set[str] = set()
                for qc in qc_list:
                    incoming_label = f"Incoming UV scaled ({qc.blank.parsed.sample}, {qc.blank.parsed.geometry})"
                    if incoming_label not in seen_incoming_scaled:
                        scaled_series.append((qc.wl, _scale_to_unit_peak(qc.incoming_energy_signal), incoming_label))
                        seen_incoming_scaled.add(incoming_label)
                    scaled_series.append((qc.wl, _scale_to_unit_peak(qc.emitted_energy_signal), f"Emitted scaled - {qc.sample.parsed.display_label}"))
                _report_plot_page(
                    pdf,
                    f"Quantum conversion scaled peaks - {result.group_key}",
                    "Wavelength (nm)",
                    "Scaled energy-weighted signal",
                    scaled_series,
                    _valid_region_from_qc(qc_list[0]),
                )

        raw_series: list[tuple[np.ndarray, np.ndarray, str]] = []
        for sr in _unique_raw_spectra(results):
            raw_series.append((sr.wl, sr.signal_normalized, f"{sr.parsed.display_label} raw"))
            raw_series.append((sr.wl, _plot_smooth(sr.signal_normalized), f"{sr.parsed.display_label} smooth"))
        if raw_series:
            normalized = any(sr.integration_time_normalization_factor != 1.0 for sr in _unique_raw_spectra(results))
            y_label = "Signal / IntegrationTime" if normalized else "Signal"
            _report_plot_page(pdf, "All raw measurements", "Wavelength (nm)", y_label, raw_series)

    return report_path


def export_all(
    results: list[GroupResult],
    out_dir: Path,
    config: Optional[ProcessingConfig] = None,
    export_vega: bool = True,
    export_pdf: bool = True,
) -> list[Path]:
    all_written: list[Path] = []
    for r in results:
        all_written.extend(export_group(r, out_dir, config, export_vega=export_vega, export_pdf=export_pdf))
    out_dir = Path(out_dir)
    if _unique_raw_spectra(results):
        if export_vega:
            (out_dir / "vega").mkdir(parents=True, exist_ok=True)
            p = out_dir / "vega" / "all_raw_measurements.json"
            p.write_text(json.dumps(_all_raw_measurements_spec(results), indent=2, ensure_ascii=False), encoding="utf-8")
            all_written.append(p)
        if export_pdf:
            (out_dir / "pdf").mkdir(parents=True, exist_ok=True)
            p = out_dir / "pdf" / "all_raw_measurements.pdf"
            _save_pdf_all_raw_measurements(results, p)
            all_written.append(p)
    all_written.append(_write_observable_rows_csv(results, out_dir))
    all_written.extend(_write_analysis_observable_csvs(results, out_dir))
    all_written.append(_write_observable_rows_xlsx(results, out_dir))
    if export_pdf:
        all_written.append(_write_final_report_pdf(results, out_dir, config))
    return all_written
