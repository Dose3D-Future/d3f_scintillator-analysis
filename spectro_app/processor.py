"""
processor.py
============

Core spectrophotometer data processing logic.

Main assumptions:

* Spectrometer exports are already dark-corrected.
* Residual baseline/noise handling is applied before ratios.
* Direct transmittance is sample / Air blank.
* 90deg scattering is a shape/integral diagnostic; Air blank is optional.
* When IntegrationTime metadata is available, every spectrum is normalised as
  signal / IntegrationTime before baseline correction, noise gating, ratios and
  integrals. This makes measurements with different exposure/integration times
  comparable inside one campaign.
"""

from __future__ import annotations

import io
import zipfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    from scipy.signal import savgol_filter
    _SCIPY_OK = True
except ImportError:  # pragma: no cover - optional dependency
    _SCIPY_OK = False


if __package__:
    from .file_parser import ParsedFile
else:
    from file_parser import ParsedFile


@dataclass
class ProcessingConfig:
    analysis_window_nm: tuple[float, float] = (400.0, 750.0)
    interest_window_nm: tuple[float, float] = (470.0, 570.0)
    baseline_ranges_nm: list[tuple[float, float]] = field(
        default_factory=lambda: [(190.0, 350.0), (850.0, 1020.0)]
    )
    blank_min_fraction_of_peak: float = 0.02
    signal_floor_fraction_of_peak: float = 0.005
    noise_sigma_multiplier: float = 5.0
    max_valid_transmittance: float = 1.20
    absorbance_floor: float = 1e-4
    smoothing_window_points: int = 41
    smoothing_polyorder: int = 3
    path_length_mm: float = 10.0
    clip_negative_after_baseline: bool = True
    normalize_by_integration_time: bool = True
    integration_time_key: str = "IntegrationTime"
    integration_time_default: float = 1.0


def _coerce_numeric_series(s: pd.Series) -> pd.Series:
    # Supports decimal comma in semicolon-separated exports.
    return pd.to_numeric(s.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")


def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1250", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_spectrum_text(path: Path) -> tuple[str, str]:
    """Read plain text or the first spectrum-like member of a .zip archive."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            members = [n for n in zf.namelist() if not n.endswith("/")]
            preferred = [
                n for n in members
                if Path(n).name.lower().endswith((".csv", ".txt", ".dat"))
            ]
            if not preferred and members:
                preferred = [members[0]]
            if not preferred:
                raise ValueError(f"ZIP archive is empty: {path.name}")
            member = preferred[0]
            return _decode_bytes(zf.read(member)), member
    return _decode_bytes(path.read_bytes()), ""


def _clean_metadata_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _split_metadata_and_data(text: str) -> tuple[dict[str, str], str]:
    """Extract #Key;Value metadata and return only numeric spectrum rows."""
    metadata: dict[str, str] = {}
    data_lines: list[str] = []
    in_data = False
    saw_data_section = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()

        if low == "[data]":
            in_data = True
            saw_data_section = True
            continue
        if low == "[endoffile]":
            break
        if line.startswith("[") and line.endswith("]"):
            continue

        if line.startswith("#"):
            body = line[1:]
            if ";" in body:
                key, value = body.split(";", 1)
                metadata[key.strip()] = _clean_metadata_value(value)
            continue

        if in_data or not saw_data_section:
            data_lines.append(line)

    return metadata, "\n".join(data_lines)


def _metadata_float(metadata: dict[str, str], key: str) -> Optional[float]:
    key_low = key.lower()
    value: Optional[str] = None
    for k, v in metadata.items():
        if k.lower() == key_low:
            value = v
            break
    if value is None:
        return None
    try:
        val = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    if not np.isfinite(val) or val <= 0:
        return None
    return val


def load_spectrum(path: Path | str) -> pd.DataFrame:
    """
    Load a spectrum from TXT, CSV or CSV/TXT-in-ZIP.

    Supported examples:

    * raw two-column `.txt` / `.csv` files,
    * Thorlabs-like `.csv` files with `[SpectrumHeader]`, `#IntegrationTime;...`
      and `[Data]`,
    * `.csv.zip` archives containing one such CSV file.

    Returns a DataFrame with columns `wavelength_nm` and `signal_raw`.
    Metadata is stored in `df.attrs["metadata"]`; the detected integration time
    is stored in `df.attrs["integration_time"]` when present.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    text, member = _read_spectrum_text(path)
    metadata, data_text = _split_metadata_and_data(text)
    if not data_text.strip():
        raise ValueError(f"No numeric [Data] section found in spectrum file: {path.name}")

    attempts = ["\t", ";", ",", r"\s+"]
    last_error: Optional[Exception] = None
    for sep in attempts:
        try:
            df = pd.read_csv(
                io.StringIO(data_text),
                sep=sep,
                comment="#",
                header=None,
                names=["wavelength_nm", "signal_raw"],
                dtype=str,
                engine="python",
                on_bad_lines="skip",
            )
            if df.shape[1] < 2:
                continue
            df = df.iloc[:, :2].copy()
            df["wavelength_nm"] = _coerce_numeric_series(df["wavelength_nm"])
            df["signal_raw"] = _coerce_numeric_series(df["signal_raw"])
            df = df.dropna(subset=["wavelength_nm", "signal_raw"]).reset_index(drop=True)
            if len(df) >= 10 and df["wavelength_nm"].between(100, 2000).all():
                df = df.sort_values("wavelength_nm").drop_duplicates("wavelength_nm").reset_index(drop=True)
                df = df.astype({"wavelength_nm": float, "signal_raw": float})
                df.attrs["metadata"] = metadata
                df.attrs["source_member"] = member
                df.attrs["integration_time"] = _metadata_float(metadata, "IntegrationTime")
                return df
        except Exception as exc:  # pragma: no cover - best effort parser attempts
            last_error = exc
            continue

    if last_error is not None:
        raise ValueError(f"Cannot parse spectrum file: {path.name}; last error: {last_error}") from last_error
    raise ValueError(f"Cannot parse spectrum file: {path.name}")


def _baseline_noise(
    wl: np.ndarray,
    signal: np.ndarray,
    ranges: list[tuple[float, float]],
) -> tuple[float, float]:
    mask = np.zeros(len(wl), dtype=bool)
    for lo, hi in ranges:
        mask |= (wl >= lo) & (wl <= hi)
    if not mask.any():
        return 0.0, 0.0
    baseline_vals = signal[mask]
    return float(np.nanmedian(baseline_vals)), float(np.nanstd(baseline_vals))


def _apply_noise_gate(
    signal: np.ndarray,
    peak: float,
    floor_frac: float,
    noise_std: float,
    sigma_mult: float,
) -> np.ndarray:
    floor = max(floor_frac * peak, sigma_mult * noise_std)
    gated = signal.copy()
    gated[gated < floor] = 0.0
    return gated


def _smooth(arr: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    """NaN-safe Savitzky-Golay smoothing with fallback to original array."""
    arr = np.asarray(arr, dtype=float)
    if not _SCIPY_OK or arr.size < max(5, polyorder + 2):
        return arr.copy()

    finite = np.isfinite(arr)
    if finite.sum() < max(5, polyorder + 2):
        return arr.copy()

    n = arr.size
    w = int(window)
    if w % 2 == 0:
        w += 1
    max_w = finite.sum() if finite.sum() % 2 == 1 else finite.sum() - 1
    w = min(w, n if n % 2 == 1 else n - 1, max_w)
    if w <= polyorder + 1 or w < 5:
        return arr.copy()

    x = np.arange(n)
    filled = np.interp(x, x[finite], arr[finite])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        smoothed = savgol_filter(filled, window_length=w, polyorder=polyorder)
    smoothed[~finite] = np.nan
    return smoothed


def _safe_ratio(sample: np.ndarray, reference: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    out = np.full_like(sample, np.nan, dtype=float)
    mask = valid_mask & (reference > 0) & np.isfinite(sample) & np.isfinite(reference)
    out[mask] = sample[mask] / reference[mask]
    return out


def _same_grid(a: np.ndarray, b: np.ndarray, tol: float = 0.5) -> bool:
    if len(a) != len(b):
        return False
    return bool(np.nanmax(np.abs(a - b)) < tol)


def _integrate(wl: np.ndarray, values: np.ndarray, mask: np.ndarray) -> float:
    if not mask.any():
        return float("nan")
    wl_m = wl[mask]
    v_m = np.where(np.isfinite(values[mask]), values[mask], 0.0)
    if hasattr(np, "trapezoid"):
        return float(np.trapezoid(v_m, wl_m))
    return float(np.trapz(v_m, wl_m))


def _linear_fit_observables(wl: np.ndarray, values: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    finite = mask & np.isfinite(wl) & np.isfinite(values)
    if finite.sum() < 2:
        return {
            "auc": float("nan"),
            "slope": float("nan"),
            "intercept": float("nan"),
            "angle_deg": float("nan"),
            "n_points": float(finite.sum()),
        }

    x = wl[finite]
    y = values[finite]
    slope, intercept = np.polyfit(x, y, 1)
    return {
        "auc": _integrate(wl, values, finite),
        "slope": float(slope),
        "intercept": float(intercept),
        "angle_deg": float(np.degrees(np.arctan(slope))),
        "n_points": float(finite.sum()),
    }


@dataclass
class SpectrumResult:
    parsed: ParsedFile
    wl: np.ndarray
    signal_raw: np.ndarray
    signal_normalized: np.ndarray
    signal_net: np.ndarray
    signal_gated: np.ndarray
    baseline_offset: float
    noise_std: float
    metadata: dict[str, str] = field(default_factory=dict)
    source_member: str = ""
    integration_time: Optional[float] = None
    integration_time_normalization_factor: float = 1.0


@dataclass
class TransmittanceResult:
    sample: SpectrumResult
    blank: SpectrumResult
    wl: np.ndarray
    transmittance: np.ndarray
    transmittance_smooth: np.ndarray
    absorbance: np.ndarray
    absorbance_smooth: np.ndarray
    valid_mask: np.ndarray
    integrals: dict[str, float] = field(default_factory=dict)


@dataclass
class ScatteringResult:
    sample: SpectrumResult
    blank: Optional[SpectrumResult]
    wl: np.ndarray
    signal_net: np.ndarray
    signal_shape: np.ndarray
    signal_shape_smooth: np.ndarray
    valid_mask: np.ndarray
    integrals: dict[str, float] = field(default_factory=dict)


@dataclass
class GroupResult:
    group_key: str
    geometry: str
    diode: str
    config: str
    series_id: str = ""
    sample: str = ""
    transmittance_results: list[TransmittanceResult] = field(default_factory=list)
    scattering_results: list[ScatteringResult] = field(default_factory=list)
    raw_spectra: list[SpectrumResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _process_spectrum(pf: ParsedFile, cfg: ProcessingConfig) -> SpectrumResult:
    df = load_spectrum(pf.path)
    wl = df["wavelength_nm"].to_numpy(dtype=float)
    raw = df["signal_raw"].to_numpy(dtype=float)
    metadata = dict(df.attrs.get("metadata") or {})
    source_member = str(df.attrs.get("source_member") or "")
    integration_time = _metadata_float(metadata, cfg.integration_time_key)

    if cfg.normalize_by_integration_time:
        norm_factor = integration_time if integration_time is not None else float(cfg.integration_time_default)
        if not np.isfinite(norm_factor) or norm_factor <= 0:
            norm_factor = 1.0
    else:
        norm_factor = 1.0

    signal = raw / norm_factor

    offset, noise_std = _baseline_noise(wl, signal, cfg.baseline_ranges_nm)
    net = signal - offset
    if cfg.clip_negative_after_baseline:
        net = np.clip(net, 0, None)

    peak = float(np.nanmax(net)) if net.size and np.isfinite(np.nanmax(net)) else 0.0
    gated = _apply_noise_gate(
        net,
        peak,
        cfg.signal_floor_fraction_of_peak,
        noise_std,
        cfg.noise_sigma_multiplier,
    )

    return SpectrumResult(
        parsed=pf,
        wl=wl,
        signal_raw=raw,
        signal_normalized=signal,
        signal_net=net,
        signal_gated=gated,
        baseline_offset=offset,
        noise_std=noise_std,
        metadata=metadata,
        source_member=source_member,
        integration_time=integration_time,
        integration_time_normalization_factor=float(norm_factor),
    )


def process_group(
    group: list[ParsedFile],
    cfg: ProcessingConfig,
    group_key: Optional[str] = None,
) -> GroupResult:
    """Process one plot group and return a structured result."""
    if not group:
        raise ValueError("Empty group")

    representative = next((pf for pf in group if pf.role != "blank_air"), group[0])
    result = GroupResult(
        group_key=group_key or representative.group_key,
        geometry=representative.geometry,
        diode=representative.diode,
        config=representative.config,
        series_id=representative.series_id,
        sample=representative.sample,
    )

    for pf in group:
        try:
            sr = _process_spectrum(pf, cfg)
            result.raw_spectra.append(sr)
        except Exception as exc:
            result.warnings.append(f"Could not load {pf.path.name}: {exc}")

    analysis_lo, analysis_hi = cfg.analysis_window_nm
    interest_lo, interest_hi = cfg.interest_window_nm

    if representative.measurement_type == "transmittance":
        blank_sr = next((sr for sr in result.raw_spectra if sr.parsed.role == "blank_air"), None)
        if blank_sr is None:
            result.warnings.append("No Air blank found - transmittance cannot be calculated")
            return result

        blank_wl = blank_sr.wl
        blank_gated = blank_sr.signal_gated
        blank_peak = float(np.nanmax(blank_gated)) if blank_gated.size else 0.0
        blank_threshold = cfg.blank_min_fraction_of_peak * blank_peak
        analysis_mask = (blank_wl >= analysis_lo) & (blank_wl <= analysis_hi)
        valid_blank = (blank_gated > 0) & (blank_gated >= blank_threshold)
        valid_ratio = valid_blank & analysis_mask

        for sr in result.raw_spectra:
            if sr.parsed.role not in {"scintillator", "quartz"}:
                continue
            if not _same_grid(sr.wl, blank_wl):
                result.warnings.append(f"{sr.parsed.path.name}: wavelength grid mismatch with blank - skipped")
                continue

            T = _safe_ratio(sr.signal_gated, blank_gated, valid_ratio)
            T = np.where(T > cfg.max_valid_transmittance, np.nan, T)
            T_smooth = _smooth(T, cfg.smoothing_window_points, cfg.smoothing_polyorder)
            A = np.where(np.isfinite(T), -np.log10(np.maximum(T, cfg.absorbance_floor)), np.nan)
            A_smooth = _smooth(A, cfg.smoothing_window_points, cfg.smoothing_polyorder)

            trusted = valid_ratio & np.isfinite(T)
            interest_mask = trusted & (blank_wl >= interest_lo) & (blank_wl <= interest_hi)
            t_interest = _linear_fit_observables(blank_wl, T_smooth, interest_mask)
            a_interest = _linear_fit_observables(blank_wl, A_smooth, interest_mask)
            integrals = {
                "transmittance_auc": _integrate(blank_wl, T, trusted),
                "absorbance_auc": _integrate(blank_wl, A, trusted),
                "fractional_loss_auc": _integrate(blank_wl, 1.0 - T, trusted),
                "interest_transmittance_auc": t_interest["auc"],
                "interest_transmittance_fit_slope": t_interest["slope"],
                "interest_transmittance_fit_intercept": t_interest["intercept"],
                "interest_transmittance_fit_angle_deg": t_interest["angle_deg"],
                "interest_transmittance_fit_points": t_interest["n_points"],
                "interest_absorbance_auc": a_interest["auc"],
                "interest_absorbance_fit_slope": a_interest["slope"],
                "interest_absorbance_fit_intercept": a_interest["intercept"],
                "interest_absorbance_fit_angle_deg": a_interest["angle_deg"],
                "interest_absorbance_fit_points": a_interest["n_points"],
                "sample_integration_time": float(sr.integration_time) if sr.integration_time is not None else float("nan"),
                "blank_integration_time": float(blank_sr.integration_time) if blank_sr.integration_time is not None else float("nan"),
            }
            result.transmittance_results.append(
                TransmittanceResult(
                    sample=sr,
                    blank=blank_sr,
                    wl=blank_wl,
                    transmittance=T,
                    transmittance_smooth=T_smooth,
                    absorbance=A,
                    absorbance_smooth=A_smooth,
                    valid_mask=trusted,
                    integrals=integrals,
                )
            )

    else:
        scatter_blank = next((sr for sr in result.raw_spectra if sr.parsed.role == "blank_air"), None)
        for sr in result.raw_spectra:
            if sr.parsed.role not in {"scintillator", "quartz"}:
                continue

            wl = sr.wl
            y = sr.signal_gated.copy()
            if scatter_blank is not None and _same_grid(wl, scatter_blank.wl):
                y_net = np.clip(y - scatter_blank.signal_gated, 0, None)
            else:
                y_net = y

            denom = float(np.nanmax(np.abs(y_net))) if y_net.size else 0.0
            shape = y_net / denom if denom > 0 else np.full_like(y_net, np.nan, dtype=float)
            shape_smooth = _smooth(shape, cfg.smoothing_window_points, cfg.smoothing_polyorder)
            valid = (wl >= analysis_lo) & (wl <= analysis_hi) & np.isfinite(shape)
            integrals = {
                "scattering_net_auc": _integrate(wl, y_net, valid),
                "scattering_shape_auc": _integrate(wl, shape, valid),
                "sample_integration_time": float(sr.integration_time) if sr.integration_time is not None else float("nan"),
                "blank_integration_time": float(scatter_blank.integration_time) if scatter_blank and scatter_blank.integration_time is not None else float("nan"),
            }
            result.scattering_results.append(
                ScatteringResult(
                    sample=sr,
                    blank=scatter_blank,
                    wl=wl,
                    signal_net=y_net,
                    signal_shape=shape,
                    signal_shape_smooth=shape_smooth,
                    valid_mask=valid,
                    integrals=integrals,
                )
            )

    return result
