"""Tkinter GUI for the spectrophotometer analysis pipeline."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .file_parser import scan_folder, validate_dataset
from .pipeline import parse_pair, parse_ranges, run_pipeline
from .processor import ProcessingConfig


class SpectroApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spectrophotometer analysis")
        self.geometry("1180x760")
        self.minsize(980, 620)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.analysis_window_var = tk.StringVar(value="400,750")
        self.baseline_ranges_var = tk.StringVar(value="190,350;850,1020")
        self.smooth_window_var = tk.StringVar(value="41")
        self.smooth_poly_var = tk.StringVar(value="3")
        self.split_samples_var = tk.BooleanVar(value=True)
        self.export_pdf_var = tk.BooleanVar(value=True)
        self.export_vega_var = tk.BooleanVar(value=True)
        self.recursive_var = tk.BooleanVar(value=False)
        self.include_raw_var = tk.BooleanVar(value=False)
        self.normalize_integration_var = tk.BooleanVar(value=True)

        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._build_ui()
        self.after(120, self._poll_queue)

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 5}

        top = ttk.Frame(self)
        top.pack(fill=tk.X, **pad)

        ttk.Label(top, text="Input folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(top, text="Choose...", command=self.choose_input).grid(row=0, column=2)

        ttk.Label(top, text="Output folder").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=5)
        ttk.Button(top, text="Choose...", command=self.choose_output).grid(row=1, column=2)
        top.columnconfigure(1, weight=1)

        cfg = ttk.LabelFrame(self, text="Processing settings")
        cfg.pack(fill=tk.X, **pad)

        ttk.Label(cfg, text="Analysis window [nm]").grid(row=0, column=0, sticky="w")
        ttk.Entry(cfg, width=14, textvariable=self.analysis_window_var).grid(row=0, column=1, sticky="w")
        ttk.Label(cfg, text="Baseline ranges [nm]").grid(row=0, column=2, sticky="w", padx=(20, 0))
        ttk.Entry(cfg, width=24, textvariable=self.baseline_ranges_var).grid(row=0, column=3, sticky="w")
        ttk.Label(cfg, text="Smoothing window").grid(row=0, column=4, sticky="w", padx=(20, 0))
        ttk.Entry(cfg, width=8, textvariable=self.smooth_window_var).grid(row=0, column=5, sticky="w")
        ttk.Label(cfg, text="Polyorder").grid(row=0, column=6, sticky="w", padx=(20, 0))
        ttk.Entry(cfg, width=8, textvariable=self.smooth_poly_var).grid(row=0, column=7, sticky="w")

        opts = ttk.Frame(self)
        opts.pack(fill=tk.X, **pad)
        ttk.Checkbutton(opts, text="Separate plots per sample", variable=self.split_samples_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(opts, text="Export PDF", variable=self.export_pdf_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(opts, text="Export Vega-Lite JSON", variable=self.export_vega_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(opts, text="Recursive scan", variable=self.recursive_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(opts, text="Include raw curves in CSV", variable=self.include_raw_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(opts, text="Normalize by IntegrationTime", variable=self.normalize_integration_var).pack(side=tk.LEFT, padx=5)
        ttk.Button(opts, text="Scan folder", command=self.scan_current_folder).pack(side=tk.RIGHT, padx=5)
        self.run_button = ttk.Button(opts, text="Run analysis", command=self.run_analysis)
        self.run_button.pack(side=tk.RIGHT, padx=5)

        middle = ttk.Panedwindow(self, orient=tk.VERTICAL)
        middle.pack(fill=tk.BOTH, expand=True, **pad)

        table_frame = ttk.LabelFrame(middle, text="Detected files")
        middle.add(table_frame, weight=3)
        columns = ("file", "series", "diode", "sample", "geometry", "config", "role", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        headings = {
            "file": "File",
            "series": "Series",
            "diode": "Diode",
            "sample": "Sample",
            "geometry": "Geometry",
            "config": "Config",
            "role": "Role",
            "status": "Status",
        }
        widths = {"file": 300, "series": 80, "diode": 70, "sample": 120, "geometry": 80, "config": 100, "role": 120, "status": 170}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w")
        yscroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        log_frame = ttk.LabelFrame(middle, text="Log")
        middle.add(log_frame, weight=2)
        self.log_text = tk.Text(log_frame, wrap="word", height=10)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def choose_input(self) -> None:
        folder = filedialog.askdirectory(title="Choose input folder")
        if not folder:
            return
        self.input_var.set(folder)
        if not self.output_var.get().strip():
            self.output_var.set(str(Path(folder) / "analysis_outputs"))
        self.scan_current_folder()

    def choose_output(self) -> None:
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.output_var.set(folder)

    def log(self, msg: str) -> None:
        self.log_text.insert(tk.END, msg.rstrip() + "\n")
        self.log_text.see(tk.END)

    def _make_config(self) -> ProcessingConfig:
        default = ProcessingConfig()
        try:
            smooth_window = int(self.smooth_window_var.get())
            smooth_poly = int(self.smooth_poly_var.get())
        except ValueError as exc:
            raise ValueError("Smoothing window and polyorder must be integers") from exc
        return ProcessingConfig(
            analysis_window_nm=parse_pair(self.analysis_window_var.get(), default.analysis_window_nm),
            baseline_ranges_nm=parse_ranges(self.baseline_ranges_var.get(), default.baseline_ranges_nm),
            smoothing_window_points=smooth_window,
            smoothing_polyorder=smooth_poly,
            normalize_by_integration_time=self.normalize_integration_var.get(),
        )

    def scan_current_folder(self) -> None:
        folder_text = self.input_var.get().strip()
        if not folder_text:
            messagebox.showwarning("No input folder", "Choose an input folder first.")
            return
        folder = Path(folder_text)
        self.tree.delete(*self.tree.get_children())
        try:
            files = scan_folder(folder, recursive=self.recursive_var.get())
        except Exception as exc:
            messagebox.showerror("Scan failed", str(exc))
            return
        for pf in files:
            status = "OK" if pf.parse_ok else "; ".join(pf.parse_notes)
            self.tree.insert(
                "",
                tk.END,
                values=(pf.path.name, pf.series_id, pf.diode, pf.sample, pf.geometry, pf.config, pf.role, status),
            )
        self.log(f"Scanned: {folder}")
        self.log(f"Detected parseable files: {len(files)}")
        warnings = validate_dataset(files)
        if warnings:
            self.log("Warnings:")
            for warning in warnings:
                self.log(f"  - {warning}")

    def run_analysis(self) -> None:
        input_text = self.input_var.get().strip()
        if not input_text:
            messagebox.showwarning("No input folder", "Choose an input folder first.")
            return
        output_text = self.output_var.get().strip() or str(Path(input_text) / "analysis_outputs")
        try:
            cfg = self._make_config()
        except Exception as exc:
            messagebox.showerror("Invalid config", str(exc))
            return

        self.run_button.configure(state=tk.DISABLED)
        self.log("Starting analysis...")

        kwargs = dict(
            input_dir=Path(input_text),
            output_dir=Path(output_text),
            config=cfg,
            split_samples=self.split_samples_var.get(),
            export_vega=self.export_vega_var.get(),
            export_pdf=self.export_pdf_var.get(),
            recursive=self.recursive_var.get(),
            include_raw_curves=self.include_raw_var.get(),
        )
        thread = threading.Thread(target=self._run_worker, kwargs=kwargs, daemon=True)
        thread.start()

    def _run_worker(self, **kwargs) -> None:
        try:
            run = run_pipeline(**kwargs)
            self._queue.put(("done", run))
        except Exception as exc:
            self._queue.put(("error", exc))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "done":
                    run = payload
                    self.log(f"Done. Output: {run.output_dir}")
                    self.log(f"Processed groups: {len(run.results)}")
                    self.log(f"Written files: {len(run.written_files)}")
                    if run.warnings:
                        self.log(f"Warnings: {len(run.warnings)}. See warnings.txt and run_report.md.")
                    self.run_button.configure(state=tk.NORMAL)
                    messagebox.showinfo("Analysis complete", f"Output written to:\n{run.output_dir}")
                elif kind == "error":
                    self.log(f"ERROR: {payload}")
                    self.run_button.configure(state=tk.NORMAL)
                    messagebox.showerror("Analysis failed", str(payload))
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)



def main() -> None:
    app = SpectroApp()
    app.mainloop()


if __name__ == "__main__":
    main()
