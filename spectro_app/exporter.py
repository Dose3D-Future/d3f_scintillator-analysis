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
from pathlib import Path
from typing import Optional

import numpy as np

if __package__:
    from .processor import GroupResult, ProcessingConfig, ScatteringResult, TransmittanceResult
else:
    from processor import GroupResult, ProcessingConfig, ScatteringResult, TransmittanceResult
    
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


def _plot_single_pdf(
    out_path: Path,
    title: str,
    x_label: str,
    y_label: str,
    series: list[tuple[np.ndarray, np.ndarray, str]],
    valid_region: Optional[tuple[float, float]] = None,
    hline: Optional[float] = None,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5.6))
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)

    if valid_region is not None:
        ax.axvspan(valid_region[0], valid_region[1], alpha=0.08, color="grey", label="_nolegend_")
    if hline is not None:
        ax.axhline(hline, linestyle="--", linewidth=0.9, color="grey")

    for i, (wl, values, label) in enumerate(series):
        ax.plot(wl, values, color=_color(i), linewidth=1.8, label=label)

    ax.grid(alpha=0.22)
    ax.legend(fontsize=7, loc="best")
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", dpi=300)
    fig.savefig(out_path.with_suffix(".png"), bbox_inches="tight", dpi=300)

    plt.close(fig)


def _valid_region_from_tr(tr: TransmittanceResult) -> Optional[tuple[float, float]]:
    wl_valid = tr.wl[tr.valid_mask]
    if len(wl_valid) == 0:
        return None
    return float(wl_valid[0]), float(wl_valid[-1])


def _save_pdf_transmittance(tr_list: list[TransmittanceResult], out_path: Path, group_key: str) -> None:
    series = [(tr.wl, tr.transmittance_smooth, tr.sample.parsed.display_label) for tr in tr_list]
    valid = _valid_region_from_tr(tr_list[0]) if tr_list else None
    _plot_single_pdf(out_path, f"Transmittance - {group_key}", "Wavelength (nm)", "Transmittance T", series, valid, 1.0)


def _save_pdf_absorbance(tr_list: list[TransmittanceResult], out_path: Path, group_key: str) -> None:
    series = [(tr.wl, tr.absorbance_smooth, tr.sample.parsed.display_label) for tr in tr_list]
    valid = _valid_region_from_tr(tr_list[0]) if tr_list else None
    _plot_single_pdf(out_path, f"Absorbance - {group_key}", "Wavelength (nm)", "Absorbance A", series, valid, None)


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
            p = out_dir / "vega" / f"{key}_transmittance.json"
            p.write_text(json.dumps(_transmittance_spec(tr_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
            p = out_dir / "vega" / f"{key}_absorbance.json"
            p.write_text(json.dumps(_absorbance_spec(tr_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
            p = out_dir / "vega" / f"{key}_raw_data.json"
            p.write_text(json.dumps(_raw_comparison_spec(tr_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
        if export_pdf:
            p = out_dir / "pdf" / f"{key}_transmittance.pdf"
            _save_pdf_transmittance(tr_list, p, result.group_key)
            written.append(p)
            p = out_dir / "pdf" / f"{key}_absorbance.pdf"
            _save_pdf_absorbance(tr_list, p, result.group_key)
            written.append(p)
            p = out_dir / "pdf" / f"{key}_raw_data.pdf"
            _save_pdf_raw_comparison(tr_list, p, result.group_key)
            written.append(p)

    if result.scattering_results:
        sc_list = result.scattering_results
        if export_vega:
            p = out_dir / "vega" / f"{key}_scattering.json"
            p.write_text(json.dumps(_scattering_spec(sc_list, result.group_key), indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(p)
        if export_pdf:
            p = out_dir / "pdf" / f"{key}_scattering.pdf"
            _save_pdf_scattering(sc_list, p, result.group_key)
            written.append(p)

    return written


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
    return all_written
