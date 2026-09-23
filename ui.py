"""ReList desktop workspace. All Tk access stays on the main thread."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from core import APP_DIR, APP_NAME, CONFIG_NAME, OrganizerEngine, atomic_json, path_key, read_config


class OrganizerApp(tk.Tk):
    # The supplied L is preserved verbatim; R shares its split stems and flourishes.
    LOGO_L = """⠀⠀⢀⣤⣤⣤⣤⣤⣤⣀⡀⠀⠀⢀⣀⠄⠀⠀⣀⣠⣤⡤⠤⠀⠀⠀⠀⠀
⢀⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠃⠀⣠⣾⣿⠟⠁⠀⠀⠀⠀⠀⠀⠀
⡼⠋⠁⠀⠈⠉⠙⠛⠛⠉⣡⣿⡟⠀⠀⣼⣿⣿⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⣿⣿⠁⠀⠀⣿⣿⣿⣇⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢰⣿⣿⣿⠀⠀⠘⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⠀⠀⠀⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⡀⠀⠀⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⢀⣤⣶⣶⣶⣿⣿⣿⣿⡇⠀⠀⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠈⠉⠉⠙⢿⣿⣿⣿⣿⡇⠀⠀⣿⣿⣿⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⣿⣿⡇⠀⠀⣿⣿⣿⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠶⢶⣶⣾⣿⣿⣿⠁⠀⢠⣿⣿⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠈⢿⣿⣿⡏⠀⢀⣾⣿⠟⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⡿⠀⢀⣾⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⢀⣾⡟⢁⣴⣟⣡⣤⣤⣶⣶⣶⣶⣶⣦⣤⣀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⢠⣾⣟⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣦⣴⠞
⠀⠀⠀⠀⢀⣴⡿⠿⠛⠛⠋⠉⠉⠉⠉⠉⠉⠛⠛⠿⣿⣿⣿⣿⣿⠟⠁⠀
⠀⠀⠀⠘⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠻⠟⠁"""
    LOGO_R = """⠀⠀⢀⣤⣤⣤⣤⣤⣤⣀⡀⠀⠀⢀⣀⠄⠀⣀⣠⣤⣤⣤⣤⣀⠀⠀⠀⠀
⢀⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠃⣠⣾⣿⣿⠿⠛⠛⠻⣿⣿⣦⡀⠀
⡼⠋⠁⠀⠈⠉⠙⠛⠛⠉⣡⣿⡟⠀⣼⣿⣿⡿⠁⠀⠀⠀⠀⠘⣿⣿⣿⡄
⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⣿⣿⠁⠀⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⡇
⠀⠀⠀⠀⠀⠀⠀⠀⢰⣿⣿⣿⠀⠀⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⢠⣿⣿⣿⠃
⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⠀⠀⣿⣿⣿⡇⠀⠀⠀⠀⠀⣠⣾⣿⡿⠃⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⡀⠀⣿⣿⣿⣧⣀⣀⣤⣶⣿⣿⠟⠁⠀⠀
⠀⠀⠀⢀⣤⣶⣶⣶⣿⣿⣿⣿⡇⠀⣿⣿⣿⣿⣿⣿⣿⠟⠋⠀⠀⠀⠀⠀
⠀⠀⠀⠈⠉⠉⠙⢿⣿⣿⣿⣿⡇⠀⣿⣿⣿⡿⣿⣿⣦⡀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⣿⣿⡇⠀⣿⣿⣿⡇⠘⣿⣿⣿⣆⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠶⢶⣶⣾⣿⣿⣿⠁⠀⣿⣿⣿⡇⠀⠘⣿⣿⣿⣆⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠈⢿⣿⣿⡏⠀⠀⣿⣿⣿⡇⠀⠀⠘⣿⣿⣿⣆⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⡿⠀⠀⢠⣿⣿⣿⠃⠀⠀⠀⠘⣿⣿⣿⣆⠀⠀
⠀⠀⠀⠀⠀⠀⠀⢀⣾⡟⠀⠀⢀⣾⣿⣿⡟⠀⠀⠀⠀⠀⠘⣿⣿⣿⣦⡀⠀
⠀⠀⠀⠀⠀⠀⢠⣾⡟⠀⠀⣠⣾⣿⣿⡿⠁⠀⠀⠀⠀⠀⠀⠘⣿⣿⣿⣿⣶⣤⡴
⠀⠀⠀⠀⢀⣴⡿⠟⠀⣠⣾⡿⠿⠛⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⣿⣿⣿⠟⠁
⠀⠀⠀⠘⠋⠁⠀⠀⠘⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠋⠀⠀"""

    PALETTES = {
        "Dark": dict(bg="#080809", panel="#121213", inset="#0c0c0d", fg="#ece8df",
                     muted="#aaa59c", border="#323031", accent="#862b35", hover="#a23743",
                     heading="#c6bdb1", select="#342126", good="#bec9b6", warn="#d1b681", error="#e68f96"),
        "Light": dict(bg="#e9e5dc", panel="#f6f3eb", inset="#eeeae1", fg="#242123",
                      muted="#68605b", border="#cbc4b9", accent="#78252f", hover="#94303c",
                      heading="#63464a", select="#e1ced0", good="#426044", warn="#7b5b23", error="#a32d39"),
    }

    def __init__(self):
        super().__init__()
        self.title("ReList · Anime library organizer")
        self.geometry("1280x900")
        self.minsize(1080, 820)
        self.engine = None
        self.busy = False
        self.operation = None
        self.generation = 0
        self.log_queue = queue.Queue()
        self.events = queue.Queue()
        self.root_var = tk.StringVar()
        self.api_var = tk.StringVar()
        self.theme_var = tk.StringVar(value=self._settings().get("theme", "Dark"))
        if self.theme_var.get() not in self.PALETTES:
            self.theme_var.set("Dark")
        self.filter_var = tk.StringVar(value="All series")
        self.search_var = tk.StringVar()
        self.cleanup_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Choose a library to begin. Scanning never changes files.")
        self.selection_var = tk.StringVar(value="No series selected")
        self.detail_title = tk.StringVar(value="Your collection, in order.")
        self.detail_meta = tk.StringVar(value="Select a series after scanning to inspect its match and file decisions.")
        self.stats = {key: tk.StringVar(value="—") for key in ("Series", "Ready", "Partial", "Review")}
        self._build_ui()
        self.apply_theme()
        self.root_var.trace_add("write", self._root_changed)
        self.filter_var.trace_add("write", lambda *_: self._render_rows())
        self.search_var.trace_add("write", lambda *_: self._render_rows())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Control-f>", lambda _: self.search_entry.focus_set())
        self._poll_job = self.after(80, self._drain_events)
        self._controls()

    def _settings(self):
        try:
            data = json.loads((APP_DIR / CONFIG_NAME).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def toggle_theme(self):
        self.theme_var.set("Light" if self.theme_var.get() == "Dark" else "Dark")
        self.apply_theme()
        try:
            data = read_config(APP_DIR / CONFIG_NAME)
            data["theme"] = self.theme_var.get()
            atomic_json(APP_DIR / CONFIG_NAME, data)
        except (OSError, ValueError) as error:
            self.log_queue.put(f"Could not save theme preference: {error}")

    def _button(self, parent, text, command, primary=False):
        return ttk.Button(parent, text=text, command=command,
                          style="Primary.TButton" if primary else "TButton")

    def _build_ui(self):
        shell = ttk.Frame(self, padding=(24, 18))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(4, weight=1)
        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.brand = tk.Canvas(header, width=248, height=102, highlightthickness=0, borderwidth=0)
        self.brand.pack(side="left", padx=(0, 22))
        title = ttk.Frame(header)
        title.pack(side="left")
        ttk.Label(title, text="ReList", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(title, text="Every episode. In its rightful place.", style="Muted.TLabel").pack(anchor="w")
        self.theme_btn = self._button(header, "Light theme", self.toggle_theme)
        self.theme_btn.pack(side="right")

        setup = ttk.Frame(shell, style="Panel.TFrame", padding=18)
        setup.grid(row=1, column=0, sticky="ew")
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, text="01  CONNECT YOUR LIBRARY", style="EyebrowPanel.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(setup, text="Library folder", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 12))
        self.root_entry = ttk.Entry(setup, textvariable=self.root_var, font=("Consolas", 10))
        self.root_entry.grid(row=1, column=1, sticky="ew")
        self.browse_btn = self._button(setup, "Browse…", self.browse)
        self.browse_btn.grid(row=1, column=2, padx=(12, 0), sticky="ew")
        ttk.Label(setup, text="TMDB API key", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        key_row = ttk.Frame(setup, style="Panel.TFrame")
        key_row.grid(row=2, column=1, sticky="ew", pady=(10, 0))
        self.api_entry = ttk.Entry(key_row, textvariable=self.api_var, show="•", width=36, font=("Consolas", 10))
        self.api_entry.pack(side="left", fill="x", expand=True)
        ttk.Label(key_row, text="v3 key · kept in memory only", style="MutedPanel.TLabel").pack(side="left", padx=12)
        self.scan_btn = self._button(setup, "Scan library", self.scan, True)
        self.scan_btn.grid(row=2, column=2, padx=(12, 0), pady=(10, 0), sticky="ew")
        ttk.Label(setup, text="EPISODES ONLY   ·   Movies, specials, OVAs and uncertain files stay where they are.",
                  style="MutedPanel.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))

        summary = ttk.Frame(shell)
        summary.grid(row=2, column=0, sticky="ew", pady=12)
        for index, (label, variable) in enumerate(self.stats.items()):
            summary.columnconfigure(index, weight=1)
            card = ttk.Frame(summary, style="Panel.TFrame", padding=(16, 10))
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 10, 0))
            ttk.Label(card, textvariable=variable, style="Stat.TLabel").pack(side="left")
            ttk.Label(card, text=label, style="MutedPanel.TLabel").pack(side="left", padx=12)

        toolbar = ttk.Frame(shell)
        toolbar.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="02  REVIEW YOUR COLLECTION", style="Eyebrow.TLabel").pack(side="left")
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=22, font=("Consolas", 10))
        self.search_entry.pack(side="right", padx=(10, 0))
        ttk.Label(toolbar, text="Search", style="Muted.TLabel").pack(side="right", padx=(10, 0))
        self.filter_box = ttk.Combobox(toolbar, textvariable=self.filter_var, state="readonly", width=16, font=("Consolas", 10),
                                      values=("All series", "Can organize", "Needs review", "Left unchanged"))
        self.filter_box.pack(side="right")

        body = ttk.Panedwindow(shell, orient="horizontal")
        body.grid(row=4, column=0, sticky="nsew")
        library = ttk.Frame(body, style="Panel.TFrame")
        body.add(library, weight=3)
        columns = ("status", "folder", "match", "episodes")
        self.tree = ttk.Treeview(library, columns=columns, show="headings", selectmode="extended")
        for key, label, width in (("status", "State", 90), ("folder", "Series folder", 190),
                                  ("match", "TMDB match", 190), ("episodes", "Mapped / found", 135)):
            self.tree.heading(key, text=label, anchor="w")
            self.tree.column(key, width=width, minwidth=75, stretch=key in {"folder", "match"})
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(library, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(library, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        library.rowconfigure(0, weight=1)
        library.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", lambda _: self.preview_selected())
        self.tree.bind("<Return>", lambda _: self.preview_selected())
        detail = ttk.Frame(body, style="Panel.TFrame", padding=18, width=330)
        body.add(detail, weight=1)
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(3, weight=1)
        ttk.Label(detail, text="SERIES INSPECTOR", style="EyebrowPanel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(detail, textvariable=self.detail_title, style="TitlePanel.TLabel", wraplength=290).grid(row=1, column=0, sticky="w", pady=(10, 6))
        ttk.Label(detail, textvariable=self.detail_meta, style="MutedPanel.TLabel", wraplength=290).grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.detail_text = tk.Text(detail, height=7, width=30, wrap="word", state="disabled", borderwidth=0)
        self.detail_text.grid(row=3, column=0, sticky="nsew")
        self.override_btn = self._button(detail, "Correct TMDB match…", self.set_override)
        self.override_btn.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.confirm_btn = self._button(detail, "Confirm TMDB match", self.confirm_match)
        self.confirm_btn.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        self.mal_override_btn = self._button(detail, "Correct MAL match…", self.set_mal_override)
        self.mal_override_btn.grid(row=6, column=0, sticky="ew", pady=(6, 0))

        actions = ttk.Frame(shell)
        actions.grid(row=5, column=0, sticky="ew", pady=10)
        self.select_btn = self._button(actions, "Select eligible", self.select_eligible)
        self.select_btn.pack(side="left")
        ttk.Label(actions, textvariable=self.selection_var, style="Muted.TLabel").pack(side="left", padx=12)
        self.preview_btn = self._button(actions, "Preview changes  →", self.preview_selected, True)
        self.preview_btn.pack(side="right")
        self.rollback_btn = self._button(actions, "Undo latest batch…", self.rollback_latest)
        self.rollback_btn.pack(side="right", padx=(0, 10))
        activity = ttk.Frame(shell, style="Panel.TFrame", padding=(12, 8))
        activity.grid(row=6, column=0, sticky="ew")
        ttk.Label(activity, text="ACTIVITY", style="EyebrowPanel.TLabel").pack(anchor="w", pady=(0, 6))
        self.log_text = tk.Text(activity, height=3, wrap="word", state="disabled", borderwidth=0)
        self.log_text.pack(fill="x")
        footer = ttk.Frame(shell)
        footer.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=110)
        self.progress.pack(side="left", padx=(0, 12))
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        self.cancel_btn = self._button(footer, "Cancel scan", self.cancel_scan)
        self.cancel_btn.pack(side="right")

    def apply_theme(self):
        c = self.PALETTES[self.theme_var.get()]
        self.colors = c
        self.configure(bg=c["bg"])
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Consolas", 10), background=c["bg"], foreground=c["fg"])
        style.configure("TFrame", background=c["bg"])
        style.configure("Panel.TFrame", background=c["panel"])
        for name, background, foreground, font in (
            ("TLabel", "bg", "fg", ("Consolas", 10)),
            ("Muted.TLabel", "bg", "muted", ("Consolas", 9)),
            ("Panel.TLabel", "panel", "fg", ("Consolas", 10)),
            ("MutedPanel.TLabel", "panel", "muted", ("Consolas", 9)),
            ("Brand.TLabel", "bg", "fg", ("Consolas", 28, "bold")),
            ("Eyebrow.TLabel", "bg", "heading", ("Consolas", 9, "bold")),
            ("EyebrowPanel.TLabel", "panel", "heading", ("Consolas", 9, "bold")),
            ("TitlePanel.TLabel", "panel", "fg", ("Consolas", 15, "bold")),
            ("Stat.TLabel", "panel", "fg", ("Consolas", 20))):
            style.configure(name, background=c[background], foreground=c[foreground], font=font)
        style.configure("TButton", padding=(12, 8), background=c["panel"], foreground=c["fg"],
                        bordercolor=c["border"], lightcolor=c["panel"], darkcolor=c["panel"], borderwidth=1)
        style.map("TButton", background=[("active", c["select"])], foreground=[("disabled", c["muted"])])
        style.configure("Primary.TButton", background=c["accent"], foreground="#ffffff", font=("Consolas", 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", c["border"]), ("active", c["hover"])],
                  foreground=[("disabled", c["muted"]), ("!disabled", "#ffffff")])
        for name in ("TEntry", "TCombobox"):
            style.configure(name, lightcolor=c["border"], darkcolor=c["border"], fieldbackground=c["inset"], foreground=c["fg"], padding=7, bordercolor=c["border"],
                            insertcolor=c["fg"], arrowcolor=c["muted"])
            style.map(name, fieldbackground=[("readonly", c["inset"]), ("disabled", c["panel"])],
                      foreground=[("readonly", c["fg"]), ("disabled", c["muted"])])
        self.option_add("*TCombobox*Listbox.font", "{Consolas} 10")
        self.option_add("*TCombobox*Listbox.background", c["panel"])
        self.option_add("*TCombobox*Listbox.foreground", c["fg"])
        style.configure("Treeview", lightcolor=c["border"], darkcolor=c["border"], bordercolor=c["border"], background=c["panel"], fieldbackground=c["panel"], foreground=c["fg"],
                        rowheight=37, borderwidth=0)
        style.configure("Treeview.Heading", background=c["inset"], foreground=c["muted"],
                        padding=(8, 10), font=("Consolas", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", c["select"])], foreground=[("selected", c["fg"])])
        style.configure("TScrollbar", background=c["border"], troughcolor=c["panel"],
                        arrowcolor=c["muted"], bordercolor=c["panel"], lightcolor=c["border"], darkcolor=c["border"])
        style.configure("TCheckbutton", background=c["bg"], foreground=c["fg"])
        style.map("TCheckbutton", background=[("active", c["bg"])])
        style.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["panel"], borderwidth=0)
        for state, color in (("READY", "good"), ("PARTIAL", "warn"), ("REVIEW", "heading"), ("ERROR", "error"), ("SKIP", "muted")):
            self.tree.tag_configure(state, foreground=c[color])
        for widget in (self.log_text, self.detail_text):
            widget.configure(bg=c["panel"], fg=c["muted"], insertbackground=c["fg"], selectbackground=c["select"],
                             font=("Consolas", 9), highlightthickness=0)
        self.theme_btn.configure(text="Light theme" if self.theme_var.get() == "Dark" else "Dark theme")
        self._draw_monogram(c)

    def _draw_monogram(self, colors):
        """Render the Unicode art directly, avoiding missing Braille font glyphs."""
        self.brand.configure(bg=colors["bg"])
        self.brand.delete("all")
        # Unicode Braille dot order: left 1/2/3/7, right 4/5/6/8.
        dots = ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (0, 3), (1, 3))
        pitch = 1.45
        offset = 1.0
        for glyph in (self.LOGO_R, self.LOGO_L):
            lines = glyph.splitlines()
            for row, line in enumerate(lines):
                for column, character in enumerate(line):
                    bits = ord(character) - 0x2800
                    if not 0 <= bits <= 255:
                        continue
                    for bit, (dx, dy) in enumerate(dots):
                        if bits & (1 << bit):
                            x = round(offset + (column * 2 + dx) * pitch)
                            y = round(1 + (row * 4 + dy) * pitch)
                            right = round(offset + (column * 2 + dx + 1) * pitch)
                            bottom = round(1 + (row * 4 + dy + 1) * pitch)
                            self.brand.create_rectangle(x, y, right, bottom,
                                                        fill=colors["fg"], outline="")
            offset += max(map(len, lines)) * 2 * pitch + 12
        self.brand.configure(width=int(offset - 10))

    def _write_text(self, widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def _controls(self):
        selected = self.tree.selection()
        plans = bool(self.engine and self.engine.series_plans)
        for widget in (self.root_entry, self.api_entry, self.browse_btn, self.scan_btn, self.theme_btn):
            widget.configure(state="disabled" if self.busy else "normal")
        self.preview_btn.configure(state="normal" if selected and plans and not self.busy else "disabled")
        self.override_btn.configure(state="normal" if len(selected) == 1 and plans and not self.busy else "disabled")
        self.mal_override_btn.configure(state="normal" if len(selected) == 1 and plans and not self.busy else "disabled")
        plan = self.engine.series_plans.get(selected[0]) if self.engine and len(selected) == 1 else None
        can_confirm = bool(plan and plan.tmdb_id and plan.status not in {"ERROR", "SKIP"}
                           and not plan.match_confirmed and not self.busy)
        self.confirm_btn.configure(state="normal" if can_confirm else "disabled")
        self.select_btn.configure(state="normal" if plans and not self.busy else "disabled")
        self.rollback_btn.configure(state="normal" if self.root_var.get().strip() and not self.busy else "disabled")
        self.cancel_btn.configure(state="normal" if self.operation == "scan" else "disabled")

    def _invalidate(self, text):
        self.generation += 1
        if self.engine:
            self.engine.series_plans.clear()
            self.engine.actions_by_series.clear()
        self.tree.delete(*self.tree.get_children())
        for variable in self.stats.values():
            variable.set("—")
        self.status_var.set(text)
        self._selection_changed()

    def _root_changed(self, *_):
        self._invalidate("Library changed. Scan to build a fresh preview.")
        self.engine = None
        self._controls()

    def browse(self):
        value = filedialog.askdirectory(title="Choose the folder containing your anime series")
        if value:
            self.root_var.set(value)

    def _valid_root(self):
        value = self.root_var.get().strip()
        if not value or not Path(value).is_dir():
            messagebox.showerror(APP_NAME, "Choose a valid library folder containing your series folders.", parent=self)
            return None
        return Path(value).resolve()

    def _run(self, operation, work, success):
        if self.busy:
            return
        self.busy, self.operation = True, operation
        self.progress.start(12)
        self._controls()
        def worker():
            try:
                self.events.put((success, work(), None))
            except Exception as error:
                self.events.put((success, None, str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def scan(self):
        if self.busy:
            return
        self._write_text(self.log_text, "")
        root = self._valid_root()
        if root is None:
            return
        key = self.api_var.get().strip()
        if not key:
            messagebox.showerror(APP_NAME, "Enter a TMDB v3 API key to scan. Undo works without a key.", parent=self)
            return
        self._invalidate("Scanning numbered TV episodes…")
        self.engine = OrganizerEngine(root, key, self.log_queue)
        engine = self.engine
        self._run("scan", engine.scan_all, self._scan_done)

    def cancel_scan(self):
        if self.operation == "scan" and self.engine:
            self.engine.cancel_event.set()
            self.status_var.set("Cancelling after the current metadata request…")
            self.cancel_btn.configure(state="disabled")

    def _scan_done(self, plans):
        if self.engine.cancel_event.is_set():
            self._invalidate("Scan cancelled. No files changed.")
            return
        self._show_plans(plans)

    def _show_plans(self, plans):
        self.stats["Series"].set(str(len(plans)))
        self.stats["Ready"].set(str(sum(p.status == "READY" for p in plans)))
        self.stats["Partial"].set(str(sum(p.status == "PARTIAL" for p in plans)))
        self.stats["Review"].set(str(sum(p.status in {"REVIEW", "ERROR"} for p in plans)))
        self._render_rows()
        self.status_var.set("Scan complete. Select series to inspect and preview." if plans else "No series folders found. Choose their parent folder.")

    def _render_rows(self):
        if self.busy:
            return
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        if self.engine:
            query = self.search_var.get().casefold().strip()
            allowed = {"Can organize": {"READY", "PARTIAL"}, "Needs review": {"REVIEW", "ERROR"}, "Left unchanged": {"SKIP"}}.get(self.filter_var.get())
            for p in self.engine.series_plans.values():
                if allowed and p.status not in allowed:
                    continue
                if query and query not in f"{p.folder} {p.tmdb_name or ''}".casefold():
                    continue
                self.tree.insert("", "end", iid=p.folder, values=(p.status, p.folder, p.tmdb_name or "—", f"{p.planned_videos} / {p.video_count}"), tags=(p.status,))
            self.tree.selection_set([f for f in selected if self.tree.exists(f)])
        self._selection_changed()

    def _selection_changed(self, *_):
        folders = list(self.tree.selection())
        self.selection_var.set(f"{len(folders)} selected" if folders else "No series selected")
        if len(folders) == 1 and self.engine:
            p = self.engine.series_plans.get(folders[0])
            if p:
                self.detail_title.set(p.tmdb_name or p.folder)
                self.detail_meta.set(f"{p.status}  ·  TMDB {p.tmdb_id or 'unmatched'}  ·  {p.confidence:.0%} match")
                ignored = self.engine.ignored_by_series.get(p.folder, [])
                actions = self.engine.actions_for([p.folder])
                changes = sum(path_key(a.source) != path_key(a.destination) for a in actions)
                self._write_text(self.detail_text, f"{p.note}\n\n{p.planned_videos} mapped episodes\n{changes} file changes\n{len(ignored)} videos left unchanged\n\nREADY: mapped normal episodes.\nPARTIAL: mapped episodes with ignored files.\nREVIEW / ERROR: apply blocked.\n\nPreview shows exact paths and reasons.")
        else:
            self.detail_title.set("Your collection, in order." if not folders else f"{len(folders)} series selected")
            self.detail_meta.set("Select a series to see its TMDB match and file decisions." if not folders else "Preview the selected series together before organizing.")
            self._write_text(self.detail_text, "Only confidently mapped episodes can be organized. Movies, specials and uncertain media remain untouched.\n\nUse Ctrl or Shift to select several series.")
        self._controls()

    def select_eligible(self):
        if self.engine:
            self.tree.selection_set([f for f in self.tree.get_children() if self.engine.series_plans[f].status in {"READY", "PARTIAL"}])

    def confirm_match(self):
        folders = self.tree.selection()
        if self.busy or not self.engine or len(folders) != 1:
            return
        plan = self.engine.series_plans.get(folders[0])
        if not plan or not plan.tmdb_id:
            return
        if not messagebox.askyesno("Confirm TMDB match",
                f"Confirm this TV series is correct?\n\nFolder: {plan.folder}\n"
                f"Match: {plan.tmdb_name}\nTMDB TV-series ID: {plan.tmdb_id}\n\n"
                "The existing scan will be reused. Episode and collision checks still apply.", parent=self):
            return
        try:
            confirmed = self.engine.confirm_match(folders[0])
            self.generation += 1
            self._show_plans(list(self.engine.series_plans.values()))
            self.status_var.set("Match confirmed. Preview the changes; no rescan needed."
                                if confirmed.status in {"READY", "PARTIAL"}
                                else "Match confirmed. File mapping or collision issues still require review.")
        except Exception as error:
            messagebox.showerror(APP_NAME, str(error), parent=self)

    def set_override(self):
        folders = self.tree.selection()
        if self.busy or not self.engine or len(folders) != 1:
            return
        value = simpledialog.askinteger("Correct TMDB match", f"TV-series ID for {folders[0]}:\nFind it in the TMDB TV show's URL.", minvalue=1, parent=self)
        if value:
            try:
                self.engine.save_override(folders[0], value)
                self._invalidate("TMDB override saved. Scan again to verify the new match.")
            except Exception as error:
                messagebox.showerror(APP_NAME, str(error), parent=self)

    def set_mal_override(self):
        folders = self.tree.selection()
        if self.busy or not self.engine or len(folders) != 1:
            return
        value = simpledialog.askinteger("Correct MAL match",
            f"MyAnimeList anime ID for {folders[0]}:\nFind it in myanimelist.net/anime/<ID>.\n"
            "This entry's episode numbers must match the library's absolute numbering.",
            minvalue=1, parent=self)
        if value:
            try:
                self.engine.save_mal_override(folders[0], value)
                self._invalidate("MAL override saved. Scan again to load Jikan episode titles.")
            except Exception as error:
                messagebox.showerror(APP_NAME, str(error), parent=self)

    def preview_report(self, folders):
        lines = ["ReList · Change preview", f"Library: {self.engine.root}", "Only normal numbered TV episodes are eligible.", ""]
        for folder in folders:
            p = self.engine.series_plans[folder]
            lines.extend([f"{folder}  [{p.status}]", f"TMDB: {p.tmdb_name or 'Unmatched'} / {p.tmdb_id or '—'}", p.note, ""])
            for action in self.engine.actions_for([folder]):
                label = "ALREADY ORGANIZED" if path_key(action.source) == path_key(action.destination) else action.kind.upper()
                if p.status not in {"READY", "PARTIAL"}:
                    label += " · BLOCKED / CANDIDATE ONLY"
                lines.extend([label, f"MATCH: {action.match_reason or 'Existing episode mapping'}", f"FROM: {action.source}", f"  TO: {action.destination}", ""])
            reasons = self.engine.ignored_reasons.get(folder, {})
            ignored = self.engine.ignored_by_series.get(folder, [])
            if ignored:
                lines.append("IGNORED / LEFT UNCHANGED")
                for value in ignored:
                    lines.extend([value, f"  Reason: {reasons.get(value, 'Unresolved episode')}"])
            lines.extend(["", "─" * 70, ""])
        return "\n".join(lines)

    def preview_selected(self):
        folders = list(self.tree.selection())
        if self.busy or not folders or not self.engine:
            return
        token = self.generation
        engine = self.engine
        report = self.preview_report(folders)
        actions = engine.actions_for(folders)
        changes = [a for a in actions if path_key(a.source) != path_key(a.destination)]
        eligible = all(engine.series_plans[f].status in {"READY", "PARTIAL"} for f in folders)
        win = tk.Toplevel(self)
        win.title("ReList · Review exact changes")
        x = self.winfo_rootx() + (self.winfo_width() - 1040) // 2
        y = self.winfo_rooty() + (self.winfo_height() - 730) // 2
        win.geometry(f"1040x730{x:+d}{y:+d}")
        win.minsize(780, 560)
        win.configure(bg=self.colors["bg"])
        win.transient(self)
        win.grab_set()
        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="03  PREVIEW & ORGANIZE", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(frame, text=f"{len(changes)} file changes · {len(folders)} series", font=("Consolas", 19, "bold")).pack(anchor="w", pady=(8, 4))
        message = "Review exact paths below. A rollback journal is saved before the first move."
        if not eligible:
            message = "Apply blocked: selection includes series that need review. Candidate paths are informational."
        elif not changes:
            message = "No file changes needed. These episodes are already organized."
        ttk.Label(frame, text=message, style="Muted.TLabel", wraplength=960).pack(anchor="w", pady=(0, 12))
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill="both", expand=True)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        preview = tk.Text(text_frame, wrap="none", font=("Consolas", 10), bg=self.colors["panel"], fg=self.colors["fg"],
                          selectbackground=self.colors["select"], relief="flat", padx=12, pady=12)
        preview.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(text_frame, command=preview.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(text_frame, orient="horizontal", command=preview.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        preview.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self._write_text(preview, report)
        acknowledged = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Remove empty episode folders after organizing (recorded for undo)", variable=self.cleanup_var).pack(anchor="w", pady=(14, 4))
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(8, 0))
        self._button(bottom, "Export preview…", lambda: self._export(report, win)).pack(side="left")
        apply_button = self._button(bottom, "Organize files", lambda: commit(), True)
        apply_button.pack(side="right")
        apply_button.configure(state="disabled")
        self._button(bottom, "Close", win.destroy).pack(side="right", padx=8)
        ttk.Checkbutton(frame, text="I have reviewed the file paths and TMDB/MAL matches.", variable=acknowledged,
                        command=lambda: apply_button.configure(state="normal" if acknowledged.get() and eligible and changes else "disabled")).pack(anchor="w", pady=(8, 0))
        def commit():
            if token != self.generation or engine is not self.engine or self.busy:
                messagebox.showerror(APP_NAME, "This preview is outdated. Scan and preview again.", parent=win)
                win.destroy()
                return
            cleanup = self.cleanup_var.get()
            win.destroy()
            self.status_var.set("Organizing files… Keep ReList open until it finishes.")
            self._run("apply", lambda: engine.apply_actions(actions, cleanup=cleanup), self._apply_done)

    def _export(self, report, parent):
        path = filedialog.asksaveasfilename(parent=parent, title="Export change preview", defaultextension=".txt",
                                          initialfile="ReList-preview.txt", filetypes=[("Text files", "*.txt")])
        if path:
            try:
                Path(path).write_text(report, encoding="utf-8")
            except OSError as error:
                messagebox.showerror(APP_NAME, str(error), parent=parent)

    def _apply_done(self, manifest):
        self._invalidate("Files organized. Scan again for a fresh view; refresh your Jellyfin library.")
        messagebox.showinfo(APP_NAME, f"Files organized successfully.\n\nUndo journal:\n{manifest}\n\nRefresh your library in Jellyfin.", parent=self)

    def rollback_latest(self):
        root = self._valid_root()
        if root is None or self.busy:
            return
        engine = OrganizerEngine(root, "", self.log_queue)
        manifest = engine.latest_rollback_manifest()
        if not manifest:
            messagebox.showinfo(APP_NAME, "No pending rollback batch was found for this library.", parent=self)
            return
        if messagebox.askyesno("Undo latest batch", f"Restore the previous paths from this batch?\n\n{manifest.name}\n\nExisting files will never be overwritten.", parent=self):
            self._invalidate("Restoring the previous layout…")
            self._run("rollback", lambda: engine.rollback_manifest(manifest), lambda _: self._invalidate("Undo complete. Scan again to refresh the library."))

    def _drain_events(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if self.operation == "scan":
                    self.status_var.set(str(msg).strip())
                self.log_text.configure(state="normal")
                self.log_text.insert("end", str(msg) + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        try:
            while True:
                success, result, error = self.events.get_nowait()
                operation = self.operation
                self.busy, self.operation = False, None
                self.progress.stop()
                if error:
                    self._invalidate("Scan stopped. No files changed." if operation == "scan" else "Operation stopped. Check activity and use Undo to recover completed moves.")
                    if not (operation == "scan" and self.engine and self.engine.cancel_event.is_set()):
                        messagebox.showerror(APP_NAME, error, parent=self)
                else:
                    success(result)
                self._controls()
        except queue.Empty:
            pass
        self._poll_job = self.after(80, self._drain_events)

    def destroy(self):
        if hasattr(self, "_poll_job"):
            self.after_cancel(self._poll_job)
        super().destroy()

    def _close(self):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Wait for the operation to finish before closing. You can cancel an active scan.", parent=self)
            return
        self.destroy()
