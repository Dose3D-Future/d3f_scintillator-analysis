"""
file_parser.py
==============

Filename parser for spectrophotometer data files.

Expected convention:

    BX_XXX_<Diode>_<Sample>_<Geometry>_<Config>

Examples:

    BX_001_UV_Air_0deg_TENT.txt
    BX_001_White_RPMS470_0deg_TENT.csv
    BX_001_UV_RPMS470_90deg_TENT.txt
    BX_001_White_Quartz_0deg_TENT.csv
    BX_001_UV_RPMS470_90deg_TENT.csv.zip

Meaning:

    series_id   : BX_XXX
    diode       : UV | White | LED365nm | LED472nm | ...
    sample      : Air | RPMS470 | Quartz | ...
    geometry    : 0deg | 90deg
    config      : orientation/geometry label, e.g. TENT, FLAT, SIDE_A

The parser is intentionally permissive. Unknown samples are treated as
scintillators. Unknown diode/geometry tokens are marked with parse notes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


DIODE_TOKENS = {
    "uv",
    "white",
    "led472nm",
    "led470nm",
    "led365nm",
    "365nm",
    "470nm",
    "472nm",
    "blue",
    "bkg",
    "background",
}
GEOMETRY_TOKENS = {"0deg", "90deg", "0degree", "90degree"}
AIR_TOKENS = {"air", "blank", "empty", "powietrze"}
QUARTZ_TOKENS = {"quartz", "sio2", "fused-silica", "fusedsilica", "kwarc"}

_SERIES_RE = re.compile(r"^([A-Za-z]{1,8}_\d{1,6}|[A-Za-z]{1,8}_X{1,6})$")

SUPPORTED_DATA_SUFFIXES = (
    ".txt",
    ".csv",
    ".dat",
    ".txt.zip",
    ".csv.zip",
    ".dat.zip",
    ".zip",
)


def data_stem(path: Path | str) -> str:
    """Return the logical measurement name without data/archive suffixes.

    `Path.stem` is not enough for files such as `name.csv.zip`, because it
    would leave `name.csv`. This helper strips known spectrometer suffixes in
    the right order.
    """
    name = Path(path).name
    low = name.lower()
    for suffix in sorted(SUPPORTED_DATA_SUFFIXES, key=len, reverse=True):
        if low.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def is_supported_data_file(path: Path | str) -> bool:
    low = Path(path).name.lower()
    return any(low.endswith(suffix) for suffix in SUPPORTED_DATA_SUFFIXES)


@dataclass
class ParsedFile:
    path: Path
    series_id: str
    diode: str
    sample: str
    geometry: str
    config: str
    role: str
    measurement_type: str
    raw_name: str = field(default="")
    parse_ok: bool = field(default=True)
    parse_notes: list[str] = field(default_factory=list)

    @property
    def reference_key_tuple(self) -> tuple[str, str, str]:
        """Blank/reference matching key: series + diode + optical geometry.

        The Air measurement has no cube orientation, so it must not be matched
        by config. One Air reference can therefore serve many sample configs
        measured with the same diode and angle.
        """
        return (self.series_id, self.diode, self.geometry)

    @property
    def reference_key(self) -> str:
        return "_".join(self.reference_key_tuple)

    @property
    def base_key_tuple(self) -> tuple[str, str, str, str]:
        """Plot key: series + diode + geometry + sample/cube config."""
        return (self.series_id, self.diode, self.geometry, self.config)

    @property
    def base_key(self) -> str:
        return "_".join(self.base_key_tuple)

    @property
    def sample_key(self) -> str:
        return f"{self.series_id}_{self.diode}_{self.sample}_{self.geometry}_{self.config}"

    @property
    def group_key(self) -> str:
        """Backward-compatible default: one plot per sample."""
        return self.sample_key if self.role != "blank_air" else self.base_key

    @property
    def display_label(self) -> str:
        return f"{self.series_id} | {self.diode} | {self.sample} | {self.geometry} | {self.config}"

    def __str__(self) -> str:
        return self.display_label


def _classify_role(sample_lower: str) -> str:
    if sample_lower in AIR_TOKENS:
        return "blank_air"
    if sample_lower in QUARTZ_TOKENS:
        return "quartz"
    return "scintillator"


def _is_background_diode(diode: str) -> bool:
    return diode.lower() in {"bkg", "background"}


def _normalise_geometry(tok: str) -> str:
    low = tok.lower()
    if low in {"0degree"}:
        return "0deg"
    if low in {"90degree"}:
        return "90deg"
    return tok


def parse_filename(path: Path | str) -> Optional[ParsedFile]:
    """
    Parse a spectrophotometer filename.

    Returns ParsedFile when the name has enough structure, otherwise None.
    The function accepts extra underscores in sample/config labels.
    """
    path = Path(path)
    stem = data_stem(path)
    parts = stem.split("_")

    if len(parts) < 5:
        return None

    notes: list[str] = []

    series_id_raw = f"{parts[0]}_{parts[1]}"
    if not _SERIES_RE.match(series_id_raw):
        notes.append("Series ID does not match expected pattern like BX_001")

    remaining = parts[2:]
    diode: Optional[str] = None
    geometry: Optional[str] = None
    sample_tokens: list[str] = []
    config_tokens: list[str] = []
    after_geometry = False

    for tok in remaining:
        tok_low = tok.lower()
        if diode is None and tok_low in DIODE_TOKENS:
            diode = tok
            continue
        if geometry is None and tok_low in GEOMETRY_TOKENS:
            geometry = _normalise_geometry(tok)
            after_geometry = True
            continue
        if after_geometry:
            config_tokens.append(tok)
        else:
            sample_tokens.append(tok)

    if diode is None:
        diode = "Unknown"
        notes.append("Diode token not recognised")
    if geometry is None:
        geometry = "0deg"
        notes.append("Geometry not found - defaulting to 0deg")

    sample = "_".join(sample_tokens) if sample_tokens else "Unknown"
    config = "_".join(config_tokens) if config_tokens else "DEFAULT"
    role = "background" if _is_background_diode(diode) else _classify_role(sample.lower())
    measurement_type = "scattering" if geometry.lower() == "90deg" else "transmittance"

    return ParsedFile(
        path=path,
        series_id=series_id_raw,
        diode=diode,
        sample=sample,
        geometry=geometry,
        config=config,
        role=role,
        measurement_type=measurement_type,
        raw_name=stem,
        parse_ok=len(notes) == 0,
        parse_notes=notes,
    )


def scan_folder(
    folder: Path | str,
    extensions: tuple[str, ...] = SUPPORTED_DATA_SUFFIXES,
    recursive: bool = False,
) -> list[ParsedFile]:
    """Scan a folder for parseable spectrophotometer data files."""
    folder = Path(folder)
    results: list[ParsedFile] = []
    iterator = folder.rglob if recursive else folder.glob

    # Scan all files once and filter by supported suffixes. This avoids missing
    # compound names such as `.csv.zip` and avoids duplicate matches.
    for fp in sorted(iterator("*")):
        if not fp.is_file():
            continue
        low = fp.name.lower()
        if not any(low.endswith(ext.lower()) for ext in extensions):
            continue
        pf = parse_filename(fp)
        if pf is not None:
            results.append(pf)

    results.sort(key=lambda pf: (pf.series_id, pf.geometry, pf.diode, pf.sample, pf.config, pf.path.name))
    return results


def group_by_plot(files: list[ParsedFile], split_samples: bool = True) -> dict[str, list[ParsedFile]]:
    """
    Group files into analysis/plot groups.

    Sample plots stay separated by sample/config, but Air references are matched
    by series_id + diode + geometry only. This reflects the measurement setup:
    the blank path has no cube orientation, while the scintillator/quartz sample
    can have many orientations.

    Example:
        BX_XXX_UV_Air_0deg.csv.zip

    is attached to all of these, if present:
        BX_XXX_UV_RPMS470_0deg_TSET.csv.zip
        BX_XXX_UV_RPMS470_0deg_TSWS.csv.zip
        BX_XXX_UV_Quartz_0deg_SIDE_A.csv.zip

    If split_samples=True, every non-air sample receives a separate group.
    If split_samples=False, samples with the same series + diode + geometry +
    config are plotted together, still using the shared Air reference.
    """
    blanks_by_reference: dict[tuple[str, str, str], list[ParsedFile]] = defaultdict(list)
    backgrounds_by_series_geometry: dict[tuple[str, str], list[ParsedFile]] = defaultdict(list)
    samples_by_base: dict[tuple[str, str, str, str], list[ParsedFile]] = defaultdict(list)

    for pf in files:
        if pf.role == "blank_air":
            blanks_by_reference[pf.reference_key_tuple].append(pf)
        elif pf.role == "background":
            backgrounds_by_series_geometry[(pf.series_id, pf.geometry)].append(pf)
        elif pf.role in {"scintillator", "quartz"}:
            samples_by_base[pf.base_key_tuple].append(pf)

    groups: dict[str, list[ParsedFile]] = {}
    used_blank_keys: set[tuple[str, str, str]] = set()

    for base_tuple, samples in sorted(samples_by_base.items()):
        series_id, diode, geometry, _config = base_tuple
        ref_key = (series_id, diode, geometry)
        blanks = blanks_by_reference.get(ref_key, [])
        if diode.lower() == "uv" and geometry.lower() == "90deg":
            blanks = [*blanks, *blanks_by_reference.get((series_id, diode, "0deg"), [])]
        backgrounds = backgrounds_by_series_geometry.get((series_id, geometry), [])
        if blanks:
            used_blank_keys.add(ref_key)

        if split_samples:
            for sample in sorted(samples, key=lambda pf: (pf.sample, pf.config, pf.path.name)):
                key = sample.sample_key
                groups[key] = [sample, *blanks, *backgrounds]
        else:
            key = "_".join(base_tuple)
            groups[key] = [*sorted(samples, key=lambda pf: (pf.sample, pf.config, pf.path.name)), *blanks, *backgrounds]

    # Do not create plot groups from standalone Air references. They are useful
    # only as references for sample groups; otherwise they would produce noisy
    # "missing sample" warnings and empty plots.
    return groups

def validate_group(group: list[ParsedFile]) -> list[str]:
    """Return warning strings for a single plot group."""
    warnings: list[str] = []
    if not group:
        return ["Empty group"]

    geometries = {pf.geometry for pf in group if not (pf.role == "blank_air" and pf.geometry.lower() == "0deg")}
    if len(geometries) > 1:
        warnings.append(f"Mixed geometries in group: {sorted(geometries)}")

    diodes = {pf.diode for pf in group if pf.role != "background"}
    if len(diodes) > 1:
        warnings.append(f"Multiple diodes in one group: {sorted(diodes)}")

    series_ids = {pf.series_id for pf in group}
    if len(series_ids) > 1:
        warnings.append(f"Multiple series IDs in one group: {sorted(series_ids)}")

    # Air/blank files usually have DEFAULT config, while samples carry the
    # actual cube orientation. Only sample configs should be checked here.
    sample_configs = {pf.config for pf in group if pf.role not in {"blank_air", "background"}}
    if len(sample_configs) > 1:
        warnings.append(f"Multiple sample configurations in one group: {sorted(sample_configs)}")

    mtypes = {pf.measurement_type for pf in group if not (pf.role == "blank_air" and pf.geometry.lower() == "0deg")}
    if len(mtypes) > 1:
        warnings.append(f"Mixed measurement types in one group: {sorted(mtypes)}")

    representative = next((pf for pf in group if pf.role != "blank_air"), group[0])

    if representative.measurement_type == "transmittance":
        has_blank = any(pf.role == "blank_air" for pf in group)
        has_sample = any(pf.role in {"scintillator", "quartz"} for pf in group)
        if not has_blank:
            warnings.append("Transmittance group has no Air blank/reference")
        if not has_sample:
            warnings.append("Transmittance group has no scintillator/quartz sample")

    if representative.measurement_type == "scattering":
        has_sample = any(pf.role in {"scintillator", "quartz"} for pf in group)
        if not has_sample:
            warnings.append("Scattering group has no scintillator/quartz sample")

    for pf in group:
        if not pf.parse_ok:
            warnings.append(f"{pf.path.name}: {'; '.join(pf.parse_notes)}")

    return warnings

def validate_dataset(files: list[ParsedFile]) -> list[str]:
    """Dataset-level sanity checks for the current measurement convention."""
    warnings: list[str] = []
    if not files:
        return ["No parseable files found"]

    if len(files) < 6:
        warnings.append(
            f"Only {len(files)} parseable files found. The minimal current campaign usually has 6 files: "
            "Air UV 0deg, Air White 0deg, scintillator UV 0deg, scintillator White 0deg, "
            "scintillator UV 90deg and scintillator White 90deg."
        )

    for pf in files:
        if not pf.parse_ok:
            warnings.append(f"{pf.path.name}: {'; '.join(pf.parse_notes)}")

    blanks_by_reference: dict[tuple[str, str, str], list[ParsedFile]] = defaultdict(list)
    samples_by_reference: dict[tuple[str, str, str], list[ParsedFile]] = defaultdict(list)

    for pf in files:
        if pf.role == "blank_air":
            blanks_by_reference[pf.reference_key_tuple].append(pf)
        elif pf.role in {"scintillator", "quartz"}:
            samples_by_reference[pf.reference_key_tuple].append(pf)

    for ref_key, samples in sorted(samples_by_reference.items()):
        series_id, diode, geometry = ref_key
        if geometry.lower() == "0deg" and not blanks_by_reference.get(ref_key):
            configs = sorted({pf.config for pf in samples})
            warnings.append(
                f"{series_id}/{diode}/{geometry}: missing Air blank/reference "
                f"for sample config(s): {', '.join(configs)}"
            )

    # Standalone Air references are allowed. A folder can contain a reference
    # measurement that is not used by the currently present sample subset.
    return warnings
