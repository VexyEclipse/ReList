#!/usr/bin/env python3
"""
ReList
Safe Jellyfin-oriented anime library organizer for Windows.

Features:
- Select a root anime folder (e.g. L:\Media\Anime)
- Identify immediate child folders as series
- Search TMDB for TV-series metadata
- Supports manual TMDB ID overrides
- Parses SxxEyy filenames and absolute-number release names such as:
    [Judas] Bleach - 252.mkv
- Maps absolute numbers across TMDB's numbered seasons (Season 0 skipped by default)
- Renames to:
    Series Name S13E23 - Episode Title.mkv
- Moves episodes into:
    Season 13\
- Renames/moves matching sidecars:
    -thumb.jpg, .srt, .ass, .ssa, .vtt, .nfo, etc.
- Preview only until Apply is pressed
- Never overwrites existing files
- Writes JSON rollback manifests
- Rollback latest applied batch
- Skips uncertain matches instead of guessing

No third-party Python packages required.
"""

from __future__ import annotations

import difflib
import json
import os
import queue
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_NAME = "ReList"
CONFIG_NAME = ".anime-organizer-config.json"
LOG_DIR_NAME = ".anime-organizer"
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".webm"}
SIDE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp",
    ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx",
    ".nfo"
}

INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SPACE_RE = re.compile(r"\s+")
SE_RE = re.compile(r"(?i)(?:^|[\s._\-\[\(])S(\d{1,2})E(\d{1,3})(?:E\d{1,3})?(?:$|[\s._\-\]\)])")
ABS_PATTERNS = [
    re.compile(r"(?i)\s-\s0*(\d{1,4})(?:\s|$)"),   # [Judas] Bleach - 252
    re.compile(r"(?i)(?:^|[\s._\-\[\(])EP(?:ISODE)?\s*0*(\d{1,4})(?:$|[\s._\-\]\)])"),
    re.compile(r"(?i)(?:^|[\s._\-\[\(])E0*(\d{1,4})(?:$|[\s._\-\]\)])"),
]

RELEASE_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*")


def safe_filename(name: str) -> str:
    name = name.replace(":", " - ")
    name = INVALID_WIN_CHARS.sub("", name)
    name = SPACE_RE.sub(" ", name).strip()
    name = re.sub(r"\s*-\s*", " - ", name)
    name = re.sub(r"(?:\s-\s){2,}", " - ", name)
    name = name.rstrip(" .-")
    return name or "Untitled"


def normalize_title(name: str) -> str:
    s = RELEASE_PREFIX_RE.sub("", name)
    s = re.sub(r"\[[^\]]+\]|\([^\)]+\)", " ", s)
    s = re.sub(r"(?i)\b(season|s)\s*\d+\b", " ", s)
    s = re.sub(r"(?i)\b(complete|dual audio|multi audio|1080p|720p|2160p|bluray|blu-ray|web[- ]?dl|hevc|x265|x264|av1|10bit|10-bit)\b", " ", s)
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s).lower()
    return SPACE_RE.sub(" ", s).strip()


def clean_series_folder_name(name: str) -> str:
    s = RELEASE_PREFIX_RE.sub("", name)
    s = re.sub(r"\[[^\]]+\]", " ", s)
    s = re.sub(r"(?i)\b(complete|dual audio|multi audio|1080p|720p|2160p|bluray|blu-ray|web[- ]?dl|hevc|x265|x264|av1|10bit|10-bit)\b", " ", s)
    return SPACE_RE.sub(" ", s).strip(" .-_") or name


def parse_episode_identity(stem: str):
    """
    Returns:
      ("season", season_num, episode_num)
      ("absolute", absolute_num, None)
      None
    """
    m = SE_RE.search(stem)
    if m:
        return ("season", int(m.group(1)), int(m.group(2)))

    for pat in ABS_PATTERNS:
        matches = list(pat.finditer(stem))
        if matches:
            # Prefer the last likely episode token.
            return ("absolute", int(matches[-1].group(1)), None)
    return None


def sidecar_suffix_from_stem(video_stem: str, side_stem: str):
    """
    If sidecar stem is video stem + suffix, preserve the suffix.
    Example:
      video: [Judas] Bleach - 252
      side:  [Judas] Bleach - 252-thumb
      -> -thumb
    """
    if side_stem.lower().startswith(video_stem.lower()):
        return side_stem[len(video_stem):]
    return None


class TmdbClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        self.base = "https://api.themoviedb.org/3"

    def _get(self, path: str, params=None):
        params = dict(params or {})
        params["api_key"] = self.api_key
        params.setdefault("language", "en-US")
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Anime-Library-Organizer/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"TMDB HTTP {e.code}: {body[:300]}")
        except Exception as e:
            raise RuntimeError(f"TMDB request failed: {e}")

    def search_tv(self, query: str):
        return self._get("/search/tv", {"query": query, "include_adult": "false"}).get("results", [])

    def tv_details(self, tmdb_id: int):
        return self._get(f"/tv/{tmdb_id}")

    def season_details(self, tmdb_id: int, season_number: int):
        return self._get(f"/tv/{tmdb_id}/season/{season_number}")


@dataclass
class EpisodeMeta:
    season: int
    episode: int
    title: str
    air_date: str | None = None


@dataclass
class MoveAction:
    series_folder: str
    source: str
    destination: str
    kind: str
    absolute_number: int | None
    season: int
    episode: int
    episode_title: str


@dataclass
class SeriesPlan:
    folder: str
    source_name: str
    tmdb_id: int | None
    tmdb_name: str | None
    confidence: float
    video_count: int
    planned_videos: int
    status: str
    note: str


class OrganizerEngine:
    def __init__(self, root: Path, api_key: str, log_queue: queue.Queue):
        self.root = root
        self.client = TmdbClient(api_key)
        self.log_queue = log_queue
        self.overrides = {}
        self.series_plans: dict[str, SeriesPlan] = {}
        self.actions_by_series: dict[str, list[MoveAction]] = {}
        self.metadata_cache = {}

    def log(self, msg):
        self.log_queue.put(msg)

    @property
    def config_path(self):
        return self.root / CONFIG_NAME

    def load_overrides(self):
        self.overrides = {}
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.overrides = data.get("tmdb_overrides", {})
            except Exception as e:
                self.log(f"Warning: could not read overrides: {e}")

    def save_override(self, folder_name: str, tmdb_id: int):
        self.load_overrides()
        self.overrides[folder_name] = int(tmdb_id)
        payload = {"tmdb_overrides": self.overrides}
        self.config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_series_dirs(self):
        ignored = {LOG_DIR_NAME}
        return sorted(
            [
                p for p in self.root.iterdir()
                if p.is_dir() and p.name not in ignored and not p.name.startswith(".")
            ],
            key=lambda p: p.name.lower(),
        )

    def choose_match(self, folder_name: str):
        if folder_name in self.overrides:
            tmdb_id = int(self.overrides[folder_name])
            details = self.client.tv_details(tmdb_id)
            return details, 1.0, "Manual TMDB override"

        query = clean_series_folder_name(folder_name)
        results = self.client.search_tv(query)
        if not results:
            return None, 0.0, "No TMDB results"

        nq = normalize_title(query)
        scored = []
        for r in results[:10]:
            names = [r.get("name") or "", r.get("original_name") or ""]
            best = 0.0
            for n in names:
                nn = normalize_title(n)
                if not nn:
                    continue
                if nn == nq and nq:
                    score = 1.0
                elif nq and (nn in nq or nq in nn):
                    score = 0.91
                else:
                    score = difflib.SequenceMatcher(None, nq, nn).ratio()
                best = max(best, score)
            scored.append((best, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        confidence, best_result = scored[0]
        details = self.client.tv_details(int(best_result["id"]))
        return details, confidence, "Automatic TMDB search"

    def get_episode_maps(self, tmdb_id: int):
        if tmdb_id in self.metadata_cache:
            return self.metadata_cache[tmdb_id]

        details = self.client.tv_details(tmdb_id)
        numbered_seasons = [
            s for s in details.get("seasons", [])
            if isinstance(s.get("season_number"), int) and s["season_number"] > 0
        ]
        numbered_seasons.sort(key=lambda s: s["season_number"])

        by_season = {}
        absolute = {}
        abs_idx = 0

        for s in numbered_seasons:
            sn = int(s["season_number"])
            sd = self.client.season_details(tmdb_id, sn)
            episodes = sorted(sd.get("episodes", []), key=lambda e: int(e.get("episode_number") or 0))
            for e in episodes:
                en = int(e.get("episode_number") or 0)
                if en <= 0:
                    continue
                title = (e.get("name") or f"Episode {en}").strip()
                meta = EpisodeMeta(
                    season=sn,
                    episode=en,
                    title=title,
                    air_date=e.get("air_date"),
                )
                by_season[(sn, en)] = meta
                abs_idx += 1
                absolute[abs_idx] = meta
            # Gentle API pacing.
            time.sleep(0.05)

        self.metadata_cache[tmdb_id] = (details, by_season, absolute)
        return self.metadata_cache[tmdb_id]

    def find_videos(self, series_dir: Path):
        return sorted(
            [p for p in series_dir.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS],
            key=lambda p: str(p).lower(),
        )

    def plan_series(self, series_dir: Path):
        videos = self.find_videos(series_dir)
        if not videos:
            return SeriesPlan(
                folder=series_dir.name,
                source_name=series_dir.name,
                tmdb_id=None,
                tmdb_name=None,
                confidence=0.0,
                video_count=0,
                planned_videos=0,
                status="SKIP",
                note="No video files found",
            ), []

        details, confidence, match_note = self.choose_match(series_dir.name)
        if not details:
            return SeriesPlan(
                folder=series_dir.name,
                source_name=series_dir.name,
                tmdb_id=None,
                tmdb_name=None,
                confidence=0.0,
                video_count=len(videos),
                planned_videos=0,
                status="REVIEW",
                note=match_note,
            ), []

        tmdb_id = int(details["id"])
        canonical_name = details.get("name") or clean_series_folder_name(series_dir.name)
        _, by_season, absolute = self.get_episode_maps(tmdb_id)

        actions = []
        unmatched = 0
        collision = 0

        all_files = [p for p in series_dir.rglob("*") if p.is_file()]
        by_parent = {}
        for f in all_files:
            by_parent.setdefault(f.parent, []).append(f)

        for video in videos:
            ident = parse_episode_identity(video.stem)
            if not ident:
                unmatched += 1
                continue

            meta = None
            abs_num = None
            if ident[0] == "season":
                meta = by_season.get((ident[1], ident[2]))
            else:
                abs_num = ident[1]
                meta = absolute.get(abs_num)

            if not meta:
                unmatched += 1
                continue

            dest_dir = series_dir / f"Season {meta.season:02d}"
            base = safe_filename(f"{canonical_name} S{meta.season:02d}E{meta.episode:02d} - {meta.title}")
            dest_video = dest_dir / f"{base}{video.suffix.lower()}"

            if dest_video.resolve() != video.resolve() and dest_video.exists():
                collision += 1
                continue

            actions.append(MoveAction(
                series_folder=series_dir.name,
                source=str(video),
                destination=str(dest_video),
                kind="video",
                absolute_number=abs_num,
                season=meta.season,
                episode=meta.episode,
                episode_title=meta.title,
            ))

            # Move sidecars in the same folder sharing the exact original video stem.
            for side in by_parent.get(video.parent, []):
                if side == video or side.suffix.lower() not in SIDE_EXTS:
                    continue
                suffix = sidecar_suffix_from_stem(video.stem, side.stem)
                if suffix is None:
                    continue
                dest_side = dest_dir / f"{base}{suffix}{side.suffix.lower()}"
                if dest_side.resolve() != side.resolve() and dest_side.exists():
                    continue
                actions.append(MoveAction(
                    series_folder=series_dir.name,
                    source=str(side),
                    destination=str(dest_side),
                    kind="sidecar",
                    absolute_number=abs_num,
                    season=meta.season,
                    episode=meta.episode,
                    episode_title=meta.title,
                ))

        planned_videos = sum(1 for a in actions if a.kind == "video")
        ratio = planned_videos / len(videos) if videos else 0

        # Conservative decision:
        # auto-ready only when the title match is strong and almost all videos mapped.
        if confidence >= 0.90 and ratio >= 0.95 and collision == 0:
            status = "READY"
        else:
            status = "REVIEW"

        note_parts = [match_note]
        if unmatched:
            note_parts.append(f"{unmatched} video(s) could not be mapped")
        if collision:
            note_parts.append(f"{collision} destination collision(s)")
        if ratio < 0.95:
            note_parts.append(f"{ratio:.0%} of videos mapped")

        plan = SeriesPlan(
            folder=series_dir.name,
            source_name=series_dir.name,
            tmdb_id=tmdb_id,
            tmdb_name=canonical_name,
            confidence=confidence,
            video_count=len(videos),
            planned_videos=planned_videos,
            status=status,
            note="; ".join(note_parts),
        )
        return plan, actions

    def scan_all(self):
        self.load_overrides()
        self.series_plans.clear()
        self.actions_by_series.clear()

        dirs = self.list_series_dirs()
        self.log(f"Found {len(dirs)} top-level series folder(s).")

        for i, series_dir in enumerate(dirs, 1):
            self.log(f"[{i}/{len(dirs)}] Scanning {series_dir.name}...")
            try:
                plan, actions = self.plan_series(series_dir)
            except Exception as e:
                plan = SeriesPlan(
                    folder=series_dir.name,
                    source_name=series_dir.name,
                    tmdb_id=None,
                    tmdb_name=None,
                    confidence=0.0,
                    video_count=len(self.find_videos(series_dir)),
                    planned_videos=0,
                    status="ERROR",
                    note=str(e),
                )
                actions = []
            self.series_plans[series_dir.name] = plan
            self.actions_by_series[series_dir.name] = actions
            self.log(
                f"  {plan.status}: {plan.tmdb_name or 'Unmatched'} | "
                f"{plan.planned_videos}/{plan.video_count} videos | "
                f"confidence {plan.confidence:.0%}"
            )

        return list(self.series_plans.values())

    def actions_for(self, folders: list[str]):
        result = []
        for f in folders:
            result.extend(self.actions_by_series.get(f, []))
        return result

    def apply_actions(self, actions: list[MoveAction]):
        if not actions:
            raise RuntimeError("No actions to apply.")

        log_dir = self.root / LOG_DIR_NAME
        log_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        manifest_path = log_dir / f"rollback-{stamp}.json"

        # Write intended manifest before touching data.
        manifest = {
            "created_at": datetime.now().isoformat(),
            "root": str(self.root),
            "actions": [asdict(a) for a in actions],
            "completed": [],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        completed = []
        try:
            for idx, action in enumerate(actions, 1):
                src = Path(action.source)
                dst = Path(action.destination)

                # Skip no-op actions.
                try:
                    if src.resolve() == dst.resolve():
                        continue
                except Exception:
                    pass

                if not src.exists():
                    raise RuntimeError(f"Source disappeared: {src}")
                if dst.exists():
                    raise RuntimeError(f"Refusing to overwrite existing file: {dst}")

                dst.parent.mkdir(parents=True, exist_ok=True)
                self.log(f"[{idx}/{len(actions)}] {src.name} -> {dst}")
                shutil.move(str(src), str(dst))

                completed.append({
                    "source": str(src),
                    "destination": str(dst),
                })

                manifest["completed"] = completed
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        except Exception:
            self.log("Apply failed. Completed moves remain recorded in the rollback manifest.")
            raise

        self.log(f"Apply complete. Rollback manifest: {manifest_path}")
        return manifest_path

    def latest_rollback_manifest(self):
        log_dir = self.root / LOG_DIR_NAME
        if not log_dir.exists():
            return None
        items = sorted(log_dir.glob("rollback-*.json"), reverse=True)
        return items[0] if items else None

    def rollback_manifest(self, manifest_path: Path):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        completed = data.get("completed", [])
        if not completed:
            raise RuntimeError("Manifest contains no completed moves.")

        # Reverse in reverse order.
        for idx, item in enumerate(reversed(completed), 1):
            original = Path(item["source"])
            current = Path(item["destination"])

            if not current.exists():
                self.log(f"Rollback skip, current file missing: {current}")
                continue
            if original.exists():
                raise RuntimeError(f"Cannot rollback because original path already exists: {original}")

            original.parent.mkdir(parents=True, exist_ok=True)
            self.log(f"Rollback [{idx}/{len(completed)}] {current.name} -> {original}")
            shutil.move(str(current), str(original))

        rollback_marker = manifest_path.with_suffix(".rolled-back.json")
        data["rolled_back_at"] = datetime.now().isoformat()
        rollback_marker.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.log("Rollback complete.")


class OrganizerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1180x760")
        self.minsize(980, 650)

        self.log_queue = queue.Queue()
        self.engine = None
        self.scanning = False

        self.root_var = tk.StringVar()
        self.api_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose your anime library folder and enter a TMDB API key.")

        self._build_ui()
        self.after(100, self._drain_log)

    def _build_ui(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.LabelFrame(outer, text="Library", padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Anime root folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.root_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Browse…", command=self.browse).grid(row=0, column=2)

        ttk.Label(top, text="TMDB API key:").grid(row=1, column=0, sticky="w", pady=(8,0))
        ttk.Entry(top, textvariable=self.api_var, show="•").grid(row=1, column=1, sticky="ew", padx=8, pady=(8,0))
        ttk.Label(top, text="Free TMDB v3 API key").grid(row=1, column=2, sticky="w", pady=(8,0))
        top.columnconfigure(1, weight=1)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=10)

        self.scan_btn = ttk.Button(controls, text="Scan Library", command=self.scan)
        self.scan_btn.pack(side="left")

        self.override_btn = ttk.Button(controls, text="Set TMDB ID for Selected", command=self.set_override)
        self.override_btn.pack(side="left", padx=(8,0))

        self.preview_btn = ttk.Button(controls, text="Preview Selected", command=self.preview_selected)
        self.preview_btn.pack(side="left", padx=(8,0))

        self.apply_btn = ttk.Button(controls, text="Apply Selected READY", command=self.apply_selected)
        self.apply_btn.pack(side="left", padx=(8,0))

        self.rollback_btn = ttk.Button(controls, text="Rollback Latest", command=self.rollback_latest)
        self.rollback_btn.pack(side="left", padx=(8,0))

        ttk.Label(controls, textvariable=self.status_var).pack(side="right")

        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)

        columns = ("status", "folder", "match", "confidence", "videos", "planned", "tmdb", "note")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "status": "Status",
            "folder": "Folder",
            "match": "TMDB Match",
            "confidence": "Confidence",
            "videos": "Videos",
            "planned": "Mapped",
            "tmdb": "TMDB ID",
            "note": "Notes",
        }
        widths = {
            "status": 75, "folder": 190, "match": 220, "confidence": 90,
            "videos": 65, "planned": 65, "tmdb": 80, "note": 300
        }
        for c in columns:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="w")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        log_box = ttk.LabelFrame(outer, text="Log", padding=6)
        log_box.pack(fill="both", pady=(10,0))
        self.log_text = tk.Text(log_box, height=10, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def browse(self):
        p = filedialog.askdirectory(title="Choose anime library root")
        if p:
            self.root_var.set(p)

    def validate_inputs(self):
        root = Path(self.root_var.get().strip())
        key = self.api_var.get().strip()
        if not root.is_dir():
            messagebox.showerror(APP_NAME, "Choose a valid anime root folder.")
            return None
        if not key:
            messagebox.showerror(APP_NAME, "Enter your TMDB v3 API key.")
            return None
        return root, key

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for b in [self.scan_btn, self.override_btn, self.preview_btn, self.apply_btn, self.rollback_btn]:
            b.configure(state=state)
        self.scanning = busy

    def scan(self):
        valid = self.validate_inputs()
        if not valid:
            return
        root, key = valid
        self._set_busy(True)
        self.status_var.set("Scanning…")
        self.tree.delete(*self.tree.get_children())
        self.engine = OrganizerEngine(root, key, self.log_queue)

        def work():
            try:
                plans = self.engine.scan_all()
                self.after(0, lambda: self._show_plans(plans))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
            finally:
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def _show_plans(self, plans):
        self.tree.delete(*self.tree.get_children())
        ready = 0
        review = 0
        for p in plans:
            iid = p.folder
            self.tree.insert(
                "", "end", iid=iid,
                values=(
                    p.status,
                    p.folder,
                    p.tmdb_name or "",
                    f"{p.confidence:.0%}",
                    p.video_count,
                    p.planned_videos,
                    p.tmdb_id or "",
                    p.note,
                )
            )
            if p.status == "READY":
                ready += 1
            elif p.status in {"REVIEW", "ERROR"}:
                review += 1
        self.status_var.set(f"{ready} READY, {review} need review")

    def selected_folders(self):
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo(APP_NAME, "Select one or more series first.")
        return sel

    def set_override(self):
        if not self.engine:
            messagebox.showinfo(APP_NAME, "Scan the library first.")
            return
        sel = self.selected_folders()
        if len(sel) != 1:
            if sel:
                messagebox.showinfo(APP_NAME, "Select exactly one series.")
            return
        folder = sel[0]
        val = simpledialog.askinteger(APP_NAME, f"Enter TMDB TV-series ID for:\n{folder}", minvalue=1)
        if not val:
            return
        try:
            self.engine.save_override(folder, val)
            messagebox.showinfo(APP_NAME, "Override saved. Scan the library again.")
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    def preview_selected(self):
        if not self.engine:
            messagebox.showinfo(APP_NAME, "Scan the library first.")
            return
        folders = self.selected_folders()
        if not folders:
            return
        actions = self.engine.actions_for(folders)
        if not actions:
            messagebox.showinfo(APP_NAME, "No planned actions for the selected series.")
            return

        win = tk.Toplevel(self)
        win.title("Preview Changes")
        win.geometry("1050x650")

        text = tk.Text(win, wrap="none")
        text.pack(fill="both", expand=True)
        for a in actions:
            if a.kind == "video":
                text.insert("end", f"\n{a.series_folder} — S{a.season:02d}E{a.episode:02d} — {a.episode_title}\n")
            text.insert("end", f"  {a.kind.upper():7} FROM: {a.source}\n")
            text.insert("end", f"          TO:   {a.destination}\n")
        text.configure(state="disabled")

    def apply_selected(self):
        if not self.engine:
            messagebox.showinfo(APP_NAME, "Scan the library first.")
            return
        folders = self.selected_folders()
        if not folders:
            return

        non_ready = [
            f for f in folders
            if self.engine.series_plans.get(f) and self.engine.series_plans[f].status != "READY"
        ]
        if non_ready:
            messagebox.showerror(
                APP_NAME,
                "Apply is blocked for non-READY series.\n\n"
                "Fix their match with 'Set TMDB ID for Selected', then scan again.\n\n"
                + "\n".join(non_ready[:15])
            )
            return

        actions = self.engine.actions_for(folders)
        if not actions:
            messagebox.showinfo(APP_NAME, "No actions to apply.")
            return

        video_count = sum(1 for a in actions if a.kind == "video")
        side_count = sum(1 for a in actions if a.kind == "sidecar")
        ok = messagebox.askyesno(
            APP_NAME,
            f"This will MOVE/RENAME {video_count} video(s) and {side_count} sidecar file(s).\n\n"
            "Existing destination files will never be overwritten.\n"
            "A rollback manifest will be written before changes begin.\n\n"
            "Continue?"
        )
        if not ok:
            return

        self._set_busy(True)
        self.status_var.set("Applying changes…")

        def work():
            try:
                manifest = self.engine.apply_actions(actions)
                self.after(0, lambda: messagebox.showinfo(
                    APP_NAME,
                    f"Apply completed.\n\nRollback manifest:\n{manifest}\n\n"
                    "Now rescan the library in Jellyfin."
                ))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(
                    APP_NAME,
                    "Apply stopped:\n\n" + str(e) +
                    "\n\nUse Rollback Latest if you need to undo completed moves."
                ))
            finally:
                self.after(0, lambda: self._set_busy(False))
                self.after(0, lambda: self.status_var.set("Finished"))

        threading.Thread(target=work, daemon=True).start()

    def rollback_latest(self):
        valid = self.validate_inputs()
        if not valid:
            return
        root, key = valid
        if self.engine is None or self.engine.root != root:
            self.engine = OrganizerEngine(root, key, self.log_queue)

        manifest = self.engine.latest_rollback_manifest()
        if not manifest:
            messagebox.showinfo(APP_NAME, "No rollback manifest was found.")
            return

        ok = messagebox.askyesno(
            APP_NAME,
            f"Rollback the latest batch?\n\n{manifest}\n\n"
            "Rollback will stop if an original path already contains a file."
        )
        if not ok:
            return

        self._set_busy(True)

        def work():
            try:
                self.engine.rollback_manifest(manifest)
                self.after(0, lambda: messagebox.showinfo(APP_NAME, "Rollback completed."))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
            finally:
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def _drain_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
        except queue.Empty:
            pass
        self.after(100, self._drain_log)


if __name__ == "__main__":
    app = OrganizerApp()
    app.mainloop()
