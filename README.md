# Spectrophotometer GUI pipeline

This is a small desktop/batch pipeline for spectrophotometer datasets measured with Air/scintillator/quartz samples in direct transmission and 90-degree scattering geometry.

## Supported input files

The pipeline supports:

```text
*.txt
*.csv.zip
```

For ZIP files, the program reads the first spectrum-like member inside the archive, usually the inner `.csv` file. Thorlabs-style CSV exports with metadata are supported, for example:

```text
#IntegrationTime;4000.000000
#Date;20260609
#Time;14412019
[Data]
1.940950928e+02;2.347147558e-03
...
[EndOfFile]
```

When `IntegrationTime` is present, the signal used for all processing is:

```text
signal_normalized(lambda) = signal_raw(lambda) / IntegrationTime
```

This normalization is applied before baseline correction, noise gating, transmittance ratios, absorbance and scattering integrals. It is enabled by default. Plain TXT files without metadata are still supported; then the normalization factor is `1.0`.

## Filename convention

Use filenames like:

```text
BX_001_UV_Air_0deg.csv.zip
BX_001_White_Air_0deg.csv.zip
BX_001_UV_RPMS470_0deg_TENT.csv.zip
BX_001_White_RPMS470_0deg_TENT.csv.zip
BX_001_UV_RPMS470_90deg_TENT.csv.zip
BX_001_White_RPMS470_90deg_TENT.csv.zip
```

General form:

```text
BX_XXX_<Diode>_<Sample>_<Geometry>_<Config>.<ext>
```

Where:

- `BX_XXX` is the scintillator/sample series ID. Placeholder-style names such as `BX_XXX` are accepted for draft/testing data.
- `Diode` is usually `UV` or `White`.
- `Sample` is `Air`, `RPMS470`, `Quartz`, etc.
- `Geometry` is `0deg` for direct transmittance or `90deg` for scattering.
- `Config` is the cube orientation/setup label, e.g. `TENT`, `FLAT`, `SIDE_A`, `TSWS`.

For `Air` references, `Config` is optional and usually omitted. The same Air reference is reused for every sample orientation with the same `BX_XXX + Diode + Geometry`. For example, `BX_001_White_Air_0deg.csv.zip` is used as the blank for both `BX_001_White_RPMS470_0deg_TENT.csv.zip` and `BX_001_White_RPMS470_0deg_SIDE_A.csv.zip`.

Unknown non-Air/non-Quartz samples are treated as scintillators.

## Installation

From the project folder:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Or without editable install:

```bash
pip install -r requirements.txt
```

## GUI usage

From the folder containing this README:

```bash
python -m spectro_app
```

or after installation:

```bash
spectro-gui
```

Choose the input folder, choose or accept the output folder, scan files, then run analysis.

The GUI has a checkbox `Normalize by IntegrationTime`. Leave it enabled for mixed integration times. Disable it only when you intentionally want to reproduce raw-count behaviour.

## CLI usage

```bash
python -m spectro_app.cli /path/to/raw_data /path/to/output
```

Common options:

```bash
python -m spectro_app.cli /path/to/raw_data \
  --analysis-window 400,750 \
  --baseline-ranges '190,350;850,1020' \
  --smooth-window 41
```

By default, plots are split per sample and sample orientation. This avoids accidental overplotting when the folder contains several orientations or quartz/scintillator controls. Air references are matched without using orientation/config, because the blank path has no cube orientation.

To overlay all samples sharing the same series, diode, geometry and config:

```bash
python -m spectro_app.cli /path/to/raw_data --overlay-samples
```

To disable IntegrationTime normalization:

```bash
python -m spectro_app.cli /path/to/raw_data --no-integration-normalization
```

## Outputs

The pipeline writes:

```text
analysis_outputs/
  pdf/
    *_transmittance.pdf
    *_absorbance.pdf
    *_scattering.pdf
  vega/
    *_transmittance.json
    *_absorbance.json
    *_scattering.json
  processed/
    detected_files.csv
    analysis_ready_curves.csv
    analysis_integrals.csv
  warnings.txt
  run_report.md
```

The Vega-Lite JSON files can be opened in the [Vega Editor](https://vega.github.io/editor/) and edited further.

`analysis_ready_curves.csv` includes the columns `integration_time`, `blank_integration_time`, `integration_time_norm_factor`, `signal_raw`, `signal_normalized`, `signal_net` and `signal_gated`. If raw curves are not exported, those signal columns are populated only where they are directly relevant to the curve type.

## Processing model

Direct transmittance is calculated after integration-time normalization, residual baseline subtraction and noise gating:

```text
T(lambda) = I_sample_normalized(lambda) / I_air_blank_normalized(lambda)
A(lambda) = -log10(T(lambda))
```

Before the ratio, spectra are residual-baseline-subtracted and noise-gated. This prevents meaningless ratio spikes where the Air blank signal is effectively only noise.

For 90-degree scattering, the pipeline currently computes a net gated signal and a normalised shape curve. Air blank for scattering is optional. If no 90-degree Air blank is present, the sample gated signal is used directly for shape diagnostics.
