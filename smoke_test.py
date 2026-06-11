from pathlib import Path
from spectro_app.pipeline import run_pipeline

raw = Path('example_data/raw')
out = Path('example_data/out_smoke')
run = run_pipeline(raw, out)
print(f'Detected files: {len(run.files)}')
print(f'Processed groups: {len(run.results)}')
print(f'Written files: {len(run.written_files)}')
print(f'Output: {run.output_dir}')
