# Spectrophotometer pipeline v5 run report

This report was generated automatically from the single Colab/Jupyter pipeline.

The example files included with the notebook are test-format files only and should not be interpreted as physical measurement results.

## Main assumptions

- Spectrometer exports are assumed to be already dark-corrected.
- Dark files are displayed only as QC spectra and are not subtracted.
- Direct transmittance is calculated only for the same diode current, same current unit, same geometry and same wavelength grid.
- Residual baseline subtraction and noise-floor gating are applied before ratios and integrals.
- Air / empty holder is the direct blank. Quartz is a setup-stability control sample.

## Noise gating settings

- `analysis_window_nm`: (400.0, 750.0)
- `baseline_ranges_nm`: [(190.0, 350.0), (850.0, 1020.0)]
- `blank_min_fraction_of_peak`: 0.02
- `signal_floor_fraction_of_peak`: 0.005
- `noise_sigma_multiplier`: 5.0
- `max_valid_transmittance`: 1.2

## Outputs

- `analysis_ready_dataframe.csv`
- `raw_spectra_long.csv`
- `integral_summary.csv`
- `qc_summary.csv`
- figures in the `figures/` directory