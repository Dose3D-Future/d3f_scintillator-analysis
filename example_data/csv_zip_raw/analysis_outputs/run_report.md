# Spectrophotometer analysis run report

Input folder: `/home/jackie/Pulpit/spectro_app/example_data/csv_zip_raw`
Output folder: `/home/jackie/Pulpit/spectro_app/example_data/csv_zip_raw/analysis_outputs`
Detected files: 8
Analysis groups: 6
Processed groups: 6
Written files: 23

## Processing config

- `analysis_window_nm`: `[400.0, 750.0]`
- `baseline_ranges_nm`: `[[190.0, 350.0], [850.0, 1020.0]]`
- `blank_min_fraction_of_peak`: `0.02`
- `signal_floor_fraction_of_peak`: `0.005`
- `noise_sigma_multiplier`: `5.0`
- `max_valid_transmittance`: `1.2`
- `absorbance_floor`: `0.0001`
- `smoothing_window_points`: `41`
- `smoothing_polyorder`: `3`
- `path_length_mm`: `10.0`
- `clip_negative_after_baseline`: `True`
- `normalize_by_integration_time`: `True`
- `integration_time_key`: `IntegrationTime`
- `integration_time_default`: `1.0`

## Detected files

- OK: `BX_XXX_UV_Air_0deg.csv.zip` -> BX_XXX | UV | Air | 0deg | DEFAULT [blank_air]
- OK: `BX_XXX_White_Air_0deg.csv.zip` -> BX_XXX | White | Air | 0deg | DEFAULT [blank_air]
- OK: `BX_XXX_White_RMPS470_0deg_TSWS.csv.zip` -> BX_XXX | White | RMPS470 | 0deg | TSWS [scintillator]
- OK: `BX_XXX_White_RPMS470_0deg_TSET.csv.zip` -> BX_XXX | White | RPMS470 | 0deg | TSET [scintillator]
- OK: `BX_XXX_UV_RMPS470_90deg_TSWS.csv.zip` -> BX_XXX | UV | RMPS470 | 90deg | TSWS [scintillator]
- OK: `BX_XXX_UV_RPMS470_90deg_BSEE.csv.zip` -> BX_XXX | UV | RPMS470 | 90deg | BSEE [scintillator]
- OK: `BX_XXX_White_RMPS470_90deg_TSEE.csv.zip` -> BX_XXX | White | RMPS470 | 90deg | TSEE [scintillator]
- OK: `BX_XXX_White_RMPS470_90deg_TSWS.csv.zip` -> BX_XXX | White | RMPS470 | 90deg | TSWS [scintillator]

## Warnings

No warnings.

## Written files

- `vega/BX_XXX_UV_RPMS470_90deg_BSEE_scattering.json`
- `pdf/BX_XXX_UV_RPMS470_90deg_BSEE_scattering.pdf`
- `vega/BX_XXX_UV_RMPS470_90deg_TSWS_scattering.json`
- `pdf/BX_XXX_UV_RMPS470_90deg_TSWS_scattering.pdf`
- `vega/BX_XXX_White_RPMS470_0deg_TSET_transmittance.json`
- `vega/BX_XXX_White_RPMS470_0deg_TSET_absorbance.json`
- `vega/BX_XXX_White_RPMS470_0deg_TSET_raw_data.json`
- `pdf/BX_XXX_White_RPMS470_0deg_TSET_transmittance.pdf`
- `pdf/BX_XXX_White_RPMS470_0deg_TSET_absorbance.pdf`
- `pdf/BX_XXX_White_RPMS470_0deg_TSET_raw_data.pdf`
- `vega/BX_XXX_White_RMPS470_0deg_TSWS_transmittance.json`
- `vega/BX_XXX_White_RMPS470_0deg_TSWS_absorbance.json`
- `vega/BX_XXX_White_RMPS470_0deg_TSWS_raw_data.json`
- `pdf/BX_XXX_White_RMPS470_0deg_TSWS_transmittance.pdf`
- `pdf/BX_XXX_White_RMPS470_0deg_TSWS_absorbance.pdf`
- `pdf/BX_XXX_White_RMPS470_0deg_TSWS_raw_data.pdf`
- `vega/BX_XXX_White_RMPS470_90deg_TSEE_scattering.json`
- `pdf/BX_XXX_White_RMPS470_90deg_TSEE_scattering.pdf`
- `vega/BX_XXX_White_RMPS470_90deg_TSWS_scattering.json`
- `pdf/BX_XXX_White_RMPS470_90deg_TSWS_scattering.pdf`
- `processed/detected_files.csv`
- `processed/analysis_ready_curves.csv`
- `processed/analysis_integrals.csv`