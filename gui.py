from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from modules.config import ConfigError, load_config
from modules.pipeline import PublisherPipeline
from modules.run_logger import RunLogger


class PublisherGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("BookPublisher - Pruefrunden")
        self.geometry("980x720")
        self.minsize(820, 580)

        self.config_data = load_config()
        self.selected_path = tk.StringVar(value=str(self.config_data.default_input_path))
        self.full_review = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Bereit.")
        self.last_report_dir: Path | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self._build_ui()
        self.after(200, self._poll_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)
        ttk.Label(header, text="BookPublisher", font=("Segoe UI", 18, "bold")).pack(anchor=tk.W)
        ttk.Label(
            header,
            text="Ordner auswaehlen, Pruefrunde starten, Report lesen, Buch anpassen, naechste Runde starten.",
        ).pack(anchor=tk.W, pady=(4, 0))

        path_frame = ttk.LabelFrame(root, text="Buchordner")
        path_frame.pack(fill=tk.X, pady=(16, 8))
        ttk.Entry(path_frame, textvariable=self.selected_path).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=8)
        ttk.Button(path_frame, text="Ordner waehlen", command=self._choose_folder).pack(side=tk.LEFT, padx=(0, 8))

        options = ttk.LabelFrame(root, text="Pruefmodus")
        options.pack(fill=tk.X, pady=8)
        ttk.Radiobutton(
            options,
            text="Schnelle Produktionspruefung ohne API (empfohlen fuer jede Anpassungsrunde)",
            variable=self.full_review,
            value=False,
        ).pack(anchor=tk.W, padx=8, pady=(8, 2))
        ttk.Radiobutton(
            options,
            text="Vollreview mit KI (Lektorat, Verlagshaus-Gutachten, Launch-Texte)",
            variable=self.full_review,
            value=True,
        ).pack(anchor=tk.W, padx=8, pady=(2, 8))

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=8)
        self.start_button = ttk.Button(actions, text="Pruefrunde starten", command=self._start_round)
        self.start_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(actions, text="Reportordner oeffnen", command=self._open_report_dir, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT, padx=8)
        ttk.Label(actions, textvariable=self.status).pack(side=tk.LEFT, padx=12)

        guide = ttk.LabelFrame(root, text="Arbeitsweise")
        guide.pack(fill=tk.X, pady=8)
        ttk.Label(
            guide,
            text=(
                "1. Produktionsordner mit DOCX, Cover und Metadaten waehlen.  "
                "2. Pruefrunde starten.  "
                "3. FIX/REVIEW-Punkte im Report abarbeiten.  "
                "4. Dateien im Buchordner anpassen.  "
                "5. Neue Pruefrunde starten."
            ),
            wraplength=900,
        ).pack(anchor=tk.W, padx=8, pady=8)

        report_frame = ttk.LabelFrame(root, text="Aktueller Report")
        report_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.report_text = tk.Text(report_frame, wrap=tk.WORD, font=("Consolas", 10), height=20)
        scroll = ttk.Scrollbar(report_frame, command=self.report_text.yview)
        self.report_text.configure(yscrollcommand=scroll.set)
        self.report_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8), pady=8)
        self._set_report_text("Noch kein Report. Starte eine Pruefrunde.")

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(initialdir=self.selected_path.get() or str(Path.home()))
        if path:
            self.selected_path.set(path)

    def _set_busy(self, busy: bool) -> None:
        self.start_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def _set_report_text(self, text: str) -> None:
        self.report_text.configure(state=tk.NORMAL)
        self.report_text.delete("1.0", tk.END)
        self.report_text.insert(tk.END, text)
        self.report_text.configure(state=tk.DISABLED)

    def _start_round(self) -> None:
        input_path = Path(self.selected_path.get().strip())
        if not input_path.exists():
            messagebox.showerror("Ordner nicht gefunden", f"Dieser Ordner existiert nicht:\n{input_path}")
            return

        if self.full_review.get():
            confirmed = messagebox.askokcancel(
                "KI-API wird aufgerufen",
                "Der Vollreview ruft die Claude-API auf.\n\n"
                "Geschaetzte Kosten:\n"
                "  ~ €0.30 – 0.50 pro Runde\n"
                "  ~ €2 – 4 gesamt fuer ein vollstaendiges Buch\n\n"
                "ANTHROPIC_API_KEY muss in der .env-Datei eingetragen sein.\n\n"
                "Fortfahren?",
            )
            if not confirmed:
                return

        self._set_busy(True)
        self.open_button.configure(state=tk.DISABLED)
        self.status.set("Pruefrunde laeuft...")
        self._set_report_text("Pruefrunde laeuft. Das Fenster kann offen bleiben.")

        thread = threading.Thread(target=self._run_round_worker, args=(input_path, self.full_review.get()), daemon=True)
        thread.start()

    def _run_round_worker(self, input_path: Path, full_review: bool) -> None:
        try:
            config = load_config()
            logger = RunLogger(config.project_root / "logs")
            pipeline = PublisherPipeline(config, logger)
            summary = pipeline.run_round(input_path, full_review=full_review)
            report_path = self._latest_report_path(config.project_root / "artifacts", summary)
            report_text = report_path.read_text(encoding="utf-8") if report_path and report_path.exists() else str(summary)
            self.events.put(("done", {"summary": summary, "report_path": report_path, "report_text": report_text}))
        except Exception as exc:
            self.events.put(("error", exc))

    def _latest_report_path(self, artifact_dir: Path, summary: dict) -> Path | None:
        projects = summary.get("projects") or []
        if not projects:
            return None
        project_id = projects[0]["project_id"]
        round_id = summary["round_id"]
        round_dir = artifact_dir / "rounds" / project_id / round_id
        self.last_report_dir = round_dir
        preferred = round_dir / "beginner_summary.md"
        if preferred.exists():
            return preferred
        fallback = round_dir / "industrial_qa_report.md"
        if fallback.exists():
            return fallback
        fallback = round_dir / "final_publisher_summary.md"
        return fallback if fallback.exists() else None

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "done":
                    data = payload if isinstance(payload, dict) else {}
                    summary = data.get("summary", {})
                    self.status.set(f"Fertig: {summary.get('round_id', 'Runde')} ({summary.get('mode', 'Modus')})")
                    self._set_report_text(str(data.get("report_text", "")))
                    self.open_button.configure(state=tk.NORMAL if self.last_report_dir else tk.DISABLED)
                    self._set_busy(False)
                elif kind == "error":
                    self.status.set("Fehler.")
                    self._set_busy(False)
                    messagebox.showerror("Pruefrunde fehlgeschlagen", str(payload))
                    self._set_report_text(str(payload))
        except queue.Empty:
            pass
        self.after(200, self._poll_events)

    def _open_report_dir(self) -> None:
        if not self.last_report_dir:
            return
        try:
            os.startfile(self.last_report_dir)
        except OSError as exc:
            messagebox.showerror("Ordner konnte nicht geoeffnet werden", str(exc))


def main() -> int:
    try:
        app = PublisherGui()
        app.mainloop()
        return 0
    except ConfigError as exc:
        messagebox.showerror("Konfiguration fehlt", str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
