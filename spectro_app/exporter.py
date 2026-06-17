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

    for i, (wl, values, label) in enumerate(series):
        ax.plot(wl, values, color=_color(i), linewidth=1.8, label=label)
        if fit_overlays and i < len(fit_overlays) and interest_region is not None:
            slope, intercept, fit_label = fit_overlays[i]
            if np.isfinite(slope) and np.isfinite(intercept):
                x_fit = np.array([interest_region[0], interest_region[1]], dtype=float)
                y_fit = slope * x_fit + intercept
                ax.plot(x_fit, y_fit, color=_color(i), linewidth=1.4, linestyle=":", label=f"{fit_label} fit")

    ax.grid(alpha=0.22)
    ax.legend(fontsize=7, loc="best")


def _valid_region_from_tr(tr: TransmittanceResult) -> Optional[tuple[float, float]]:
    wl_valid = tr.wl[tr.valid_mask]
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
            _save_pdf_transmittance(tr_list, p, result.group_key, config)
            written.append(p)
            p = out_dir / "pdf" / f"{key}_absorbance.pdf"
            _save_pdf_absorbance(tr_list, p, result.group_key, config)
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


def _fmt_float(value: float, digits: int = 5) -> str:
    if value is None or not np.isfinite(value):
        return "nan"
    return f"{value:.{digits}g}"


def _observable_rows(results: list[GroupResult]) -> list[list[str]]:
    rows: list[list[str]] = []
    for result in results:
        for tr in result.transmittance_results:
            label = tr.sample.parsed.display_label
            rows.append([
                result.group_key,
                label,
                _fmt_float(tr.integrals.get("interest_transmittance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_transmittance_fit_angle_deg", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_auc", float("nan"))),
                _fmt_float(tr.integrals.get("interest_absorbance_fit_angle_deg", float("nan"))),
            ])
    return rows


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


def _report_table_pages(pdf, rows: list[list[str]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    columns = ["Group", "Sample", "T ROI AUC", "T fit angle [deg]", "A ROI AUC", "A fit angle [deg]"]
    if not rows:
        _report_text_page(pdf, "ROI observables", ["No transmittance/absorbance observables were calculated."])
        return

    page_size = 18
    for start in range(0, len(rows), page_size):
        chunk = rows[start:start + page_size]
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        ax.set_title("ROI observables", fontsize=16, weight="bold", pad=18)
        col_widths = [0.29, 0.29, 0.13, 0.13, 0.13, 0.13]
        table = ax.table(cellText=chunk, colLabels=columns, loc="center", cellLoc="left", colLoc="left",colWidths=col_widths)
        table.auto_set_font_size(False)
        table.set_fontsize(7)
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
        _report_table_pages(pdf, _observable_rows(results))

        for result in results:
            if result.transmittance_results:
                tr_list = result.transmittance_results
                valid = _valid_region_from_tr(tr_list[0])
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

            if result.scattering_results:
                sc_list = result.scattering_results
                _report_plot_page(
                    pdf,
                    f"Scattering shape 90deg - {result.group_key}",
                    "Wavelength (nm)",
                    "Normalised scattering intensity",
                    [(sc.wl, sc.signal_shape_smooth, sc.sample.parsed.display_label) for sc in sc_list],
                )

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
    if export_pdf:
        all_written.append(_write_final_report_pdf(results, Path(out_dir), config))
    return all_written
