#!/usr/bin/env python3
"""UI-independent planning, Jikan titles, TMDB structure and file transactions.

Only positive numbered TV episodes are organized. All plans are read-only until
apply; manifests are durable write-ahead journals stored beside the application.
"""

from __future__ import annotations

import difflib
import json
import os
import queue
import re
import stat
import uuid
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from jikan import JikanClient, JikanError
from episode_index import (parse_release, clean_show, words, exclusion_reason,
                           resolve_collection)

APP_NAME = "ReList"
APP_DIR = Path(__file__).resolve().parent
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
EXTRA_KEYWORDS_RE = re.compile(
    r"(?i)(?:^|[\s._\-\[\(])"
    r"(movies?|films?|ovas?|oads?|onas?|specials?|bonus|extras?|recap|"
    r"pilot|prologue|epilogue|trailer|preview|promo|pv|ncop|nced|opening|ending)"
    r"(?:$|[\s._\-\]\)])"
)
EXTRA_FOLDER_NAMES = {
    "movie", "movies", "film", "films",
    "ova", "ovas", "oad", "oads", "ona", "onas",
    "special", "specials", "extra", "extras", "bonus"
}


class ScanCancelled(RuntimeError):
    pass


def atomic_json(path: Path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_config(path: Path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Configuration must contain a JSON object; existing file was preserved.")
    return data


def path_key(path):
    return str(Path(path).resolve()).casefold()


def is_link(path: Path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def file_identity(path: Path):
    info = path.stat()
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "inode": info.st_ino, "device": info.st_dev}


def move_no_replace(source: Path, destination: Path):
    # Windows rename fails when the destination exists; never use shutil.move's
    # copy/delete fallback, which can overwrite or leave partial media files.
    if os.name == "nt":
        os.rename(source, destination)
    else:
        os.link(source, destination)
        source.unlink()


def safe_filename(name: str) -> str:
    name = name.replace(":", " - ")
    name = INVALID_WIN_CHARS.sub("", name)
    name = SPACE_RE.sub(" ", name).strip()
    name = re.sub(r"\s*-\s*", " - ", name)
    name = re.sub(r"(?:\s-\s){2,}", " - ", name)
    name = name.rstrip(" .-")
    return name or "Untitled"


def normalize_title(name: str) -> str:
    return words(clean_show(name))


def clean_series_folder_name(name: str) -> str:
    return clean_show(name) or name


def parse_episode_identity(stem: str):
    """Compatibility wrapper; weak bare numbers require collection context."""
    parsed = parse_release(stem)
    if parsed.reason or parsed.strength not in {"numbered", "explicit"}:
        return None
    if parsed.season is not None:
        return ("season", parsed.season, parsed.number)
    return ("absolute", parsed.number, None)


def is_probable_extra(path: Path, series_root: Path) -> bool:
    """Return True for files that strongly look like movies/OVAs/special extras."""
    try:
        rel_parts = path.relative_to(series_root).parts[:-1]
    except Exception:
        rel_parts = path.parts[:-1]

    for part in rel_parts:
        normalized = normalize_title(part)
        if normalized in EXTRA_FOLDER_NAMES or EXTRA_KEYWORDS_RE.search(part) or re.fullmatch(r"(?i)(?:season|s)\s*0+", part):
            return True

    return bool(EXTRA_KEYWORDS_RE.search(series_root.name) or EXTRA_KEYWORDS_RE.search(path.stem))


def sidecar_suffix_from_stem(video_stem: str, side_stem: str):
    """
    If sidecar stem is video stem + suffix, preserve the suffix.
    Example:
      video: [Judas] Bleach - 252
      side:  [Judas] Bleach - 252-thumb
      -> -thumb
    """
    if side_stem.lower().startswith(video_stem.lower()):
        suffix = side_stem[len(video_stem):]
        if not suffix or suffix[0] in ".-_ ":
            return suffix
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
            raise RuntimeError(f"TMDB HTTP {e.code}: {body[:300].replace(self.api_key, '[redacted]')}")
        except Exception as e:
            raise RuntimeError(f"TMDB request failed: {str(e).replace(self.api_key, '[redacted]')}")

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
    fingerprint: dict | None = None
    match_reason: str = ""


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
    mapping_safe: bool = False
    match_confirmed: bool = False


class OrganizerEngine:
    def __init__(self, root: Path, api_key: str, log_queue: queue.Queue):
        self.root = root.resolve()
        self.client = TmdbClient(api_key)
        self.log_queue = log_queue
        self.overrides = {}
        self.series_plans: dict[str, SeriesPlan] = {}
        self.actions_by_series: dict[str, list[MoveAction]] = {}
        self.ignored_by_series: dict[str, list[str]] = {}
        self.cancel_event = threading.Event()
        self.jikan = JikanClient(APP_DIR / ".jikan-cache", self.check_cancelled,
                                self.cancel_event.wait, self.log)
        self.mal_overrides = {}
        self.ignored_reasons: dict[str, dict[str, str]] = {}

    def log(self, msg):
        self.log_queue.put(msg)

    @property
    def config_path(self):
        # Keep ReList's own configuration beside the application instead of
        # writing hidden files into the user's media library.
        return APP_DIR / CONFIG_NAME

    def load_overrides(self):
        self.overrides = {}
        self.mal_overrides = {}
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.overrides = data.get("tmdb_overrides", {})
                self.mal_overrides = data.get("mal_overrides", {})
            except Exception as e:
                self.log(f"Warning: could not read overrides: {e}")

    def save_override(self, folder_name: str, tmdb_id: int):
        payload = read_config(self.config_path)
        self.overrides = dict(payload.get("tmdb_overrides", {}))
        self.overrides[folder_name] = int(tmdb_id)
        payload["tmdb_overrides"] = self.overrides
        atomic_json(self.config_path, payload)

    def save_mal_override(self, folder_name: str, mal_id: int):
        if type(mal_id) is not int or mal_id <= 0:
            raise ValueError("Enter a positive MyAnimeList anime ID.")
        payload = read_config(self.config_path)
        overrides = dict(payload.get("mal_overrides", {}))
        overrides[folder_name] = mal_id
        payload["mal_overrides"] = overrides
        atomic_json(self.config_path, payload)
        self.mal_overrides = overrides

    def choose_mal_match(self, folder_name, details):
        manual_id = self.mal_overrides.get(folder_name)
        if manual_id is not None:
            if type(manual_id) is not int or manual_id <= 0:
                raise JikanError("Invalid MAL override; set a positive anime ID")
            match = self.jikan.anime(manual_id)
        else:
            names = [details.get("name"), details.get("original_name")]
            names = list(dict.fromkeys(n for n in names if n))
            normalized = {normalize_title(n) for n in names}
            matches = {}
            for name in names:
                for result in self.jikan.search_anime(name):
                    aliases = [result.get("title"), result.get("title_english"),
                               result.get("title_japanese")]
                    aliases += [t.get("title") for t in result.get("titles", []) if isinstance(t, dict)]
                    if result.get("type") == "TV" and any(
                            normalize_title(n) in normalized for n in aliases if isinstance(n, str) and n):
                        matches[result["mal_id"]] = result
            if len(matches) != 1:
                raise JikanError("No unique exact MAL TV match; use Correct MAL match and scan again")
            match = next(iter(matches.values()))
        if match.get("type") != "TV":
            raise JikanError("The MAL entry is not a normal TV anime; select its TV anime ID")
        return match

    def list_series_dirs(self):
        ignored = {LOG_DIR_NAME}
        return sorted(
            [
                p for p in self.root.iterdir()
                if p.is_dir() and not is_link(p) and p.name not in ignored and not p.name.startswith(".")
            ],
            key=lambda p: p.name.lower(),
        )

    def choose_match(self, folder_name: str, filename_hint: str = ""):
        if folder_name in self.overrides:
            tmdb_id = int(self.overrides[folder_name])
            details = self.client.tv_details(tmdb_id)
            return details, 1.0, "Manual TMDB override"

        queries = [clean_series_folder_name(folder_name)]
        if filename_hint and normalize_title(filename_hint) != normalize_title(queries[0]):
            queries.append(filename_hint)
        results = {}
        for query in queries:
            self.check_cancelled()
            for result in self.client.search_tv(query)[:10]:
                results[result["id"]] = result
        if not results:
            return None, 0.0, "No TMDB results"

        scored = []
        year = re.search(r"\((19\d{2}|20\d{2})\)", folder_name)
        for result in results.values():
            best = 0.0
            for query in queries:
                nq = normalize_title(query)
                for name in [result.get("name") or "", result.get("original_name") or ""]:
                    nn = normalize_title(name)
                    if not nn or not nq:
                        continue
                    if nn == nq:
                        score = 1.0
                    elif nn in nq or nq in nn:
                        score = 0.85
                    else:
                        score = difflib.SequenceMatcher(None, nq, nn).ratio()
                    best = max(best, score)
            if year and result.get("first_air_date") and not result["first_air_date"].startswith(year[1]):
                best = min(best, 0.85)
            scored.append((best, result))

        scored.sort(key=lambda x: x[0], reverse=True)
        confidence, best_result = scored[0]
        note = "Automatic TMDB search" + (" with consistent release-name evidence" if filename_hint else "")
        if len(scored) > 1 and confidence - scored[1][0] < 0.08:
            confidence = min(confidence, 0.89)
            note = "Similar TMDB matches; set a TV-series ID to confirm"
        details = self.client.tv_details(int(best_result["id"]))
        return details, confidence, note

    def get_episode_maps(self, tmdb_id: int, details=None, mal_id=None):
        details = details if details is not None else self.client.tv_details(tmdb_id)
        if mal_id is None:
            raise JikanError("A MAL anime ID is required for episode titles")
        jikan_episodes = self.jikan.episodes(mal_id)

        all_seasons = [
            s for s in details.get("seasons", [])
            if isinstance(s.get("season_number"), int) and s["season_number"] > 0
        ]
        all_seasons.sort(key=lambda s: s["season_number"])

        by_season = {}
        absolute = {}
        abs_idx = 0
        prefix_valid = True
        expected_season = 1
        incomplete = False

        for season_index, s in enumerate(all_seasons, 1):
            sn = int(s["season_number"])

            self.check_cancelled()
            # Only positive numbered seasons are requested.
            self.log(f"  Fetching TMDB season {sn} ({season_index}/{len(all_seasons)})...")
            sd = self.client.season_details(tmdb_id, sn)
            episodes = sorted(
                sd.get("episodes", []),
                key=lambda e: int(e.get("episode_number") or 0)
            )

            numbers = [int(e.get("episode_number") or 0) for e in episodes]
            expected = int(s.get("episode_count", len(episodes)))
            if sn != expected_season:
                prefix_valid = False
                incomplete = True
            expected_season = sn + 1
            counts = Counter(numbers)
            expected_episode = 1
            for e in episodes:
                en = int(e.get("episode_number") or 0)
                if en <= 0 or counts[en] != 1:
                    prefix_valid = False
                    incomplete = True
                    continue

                meta = EpisodeMeta(
                    season=sn,
                    episode=en,
                    title="",  # TMDB provides numbering only, never episode titles.
                    air_date=e.get("air_date"),
                )
                if en != expected_episode:
                    prefix_valid = False
                    incomplete = True
                expected_episode = en + 1
                if prefix_valid:
                    abs_idx += 1
                    absolute[abs_idx] = meta
            if numbers != list(range(1, expected + 1)):
                prefix_valid = False
                incomplete = True

            time.sleep(0.05)

        if incomplete:
            self.log(f"Incomplete TMDB numbering: only the first {len(absolute)} verified absolute episodes are usable; later uncertain files stay untouched.")
        titled_absolute = {}
        for record in jikan_episodes:
            number = record["mal_id"]
            title = record.get("title")
            meta = absolute.get(number)
            if meta is None or not isinstance(title, str) or not title.strip():
                continue
            meta.title = title.strip()
            by_season[(meta.season, meta.episode)] = meta
            titled_absolute[number] = meta
        missing = len(absolute) - len(titled_absolute)
        if missing:
            self.log(f"  {missing} episode(s) lack a Jikan title; corresponding files stay unchanged.")
        return details, by_season, titled_absolute

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise ScanCancelled("Scan cancelled. No files changed.")

    def find_files(self, series_dir: Path):
        files = []
        for parent, dirs, names in os.walk(series_dir, followlinks=False):
            self.check_cancelled()
            dirs[:] = [d for d in dirs if not is_link(Path(parent) / d)]
            files.extend(Path(parent) / n for n in names if not is_link(Path(parent) / n))
        return sorted(files, key=lambda p: str(p).casefold())

    def find_videos(self, series_dir: Path):
        return [p for p in self.find_files(series_dir) if p.suffix.lower() in VIDEO_EXTS]

    def index_sidecars(self, files, videos):
        """Find the longest unambiguous video stem once per sidecar.

        Include ignored videos in the index: their sidecars must stay with them.
        Cost depends on filename length, not the number of videos in the folder.
        """
        stems = {}
        for video in videos:
            self.check_cancelled()
            stems.setdefault((video.parent, video.stem.lower()), []).append(video)
        result = {}
        for index, side in enumerate(files, 1):
            self.check_cancelled()
            if index % 1000 == 0:
                self.log(f"  Indexing file {index}/{len(files)}...")
            if side.suffix.lower() not in SIDE_EXTS:
                continue
            stem = side.stem.lower()
            lengths = [len(stem)] + [i for i, ch in enumerate(stem) if ch in ".-_ "][::-1]
            for length in lengths:
                owners = stems.get((side.parent, stem[:length]))
                if owners:
                    if len(owners) == 1:
                        result.setdefault(owners[0], []).append(side)
                    break
        return result

    def plan_series(self, series_dir: Path):
        self.log("  Reading library files...")
        all_files = self.find_files(series_dir)
        videos = [p for p in all_files if p.suffix.lower() in VIDEO_EXTS]
        self.log(f"  Found {len(videos)} videos and {len(all_files) - len(videos)} other files.")
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

        parsed = {v: parse_release(v.stem, clean_show(series_dir.name)) for v in videos}
        self.ignored_reasons[series_dir.name] = {
            str(v): exclusion_reason(v, series_dir, parsed[v]) for v in videos
            if exclusion_reason(v, series_dir, parsed[v])
        }
        candidates = [v for v in videos if str(v) not in self.ignored_reasons[series_dir.name]]
        self.ignored_by_series[series_dir.name] = list(self.ignored_reasons[series_dir.name])
        if not any(parsed[v].number is not None for v in candidates):
            for video in candidates:
                self.ignored_reasons[series_dir.name][str(video)] = parsed[video].reason or "No corroborated normal episode"
            self.ignored_by_series[series_dir.name] = list(self.ignored_reasons[series_dir.name])
            return SeriesPlan(series_dir.name, series_dir.name, None, None, 0.0,
                              len(videos), 0, "SKIP", "No normal episodes; all videos left unchanged"), []
        names = Counter(normalize_title(parsed[v].series) for v in candidates if parsed[v].series)
        filename_hint = ""
        if names:
            dominant, count = names.most_common(1)[0]
            if count >= 2 and count >= len(candidates) * 0.7:
                filename_hint = next(parsed[v].series for v in candidates if normalize_title(parsed[v].series) == dominant)
        details, confidence, match_note = self.choose_match(series_dir.name, filename_hint)
        if not details:
            for video in candidates:
                self.ignored_reasons[series_dir.name][str(video)] = "No TMDB TV-series match"
            self.ignored_by_series[series_dir.name] = list(self.ignored_reasons[series_dir.name])
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
        mal_match = self.choose_mal_match(series_dir.name, details)
        mal_id = mal_match["mal_id"]
        match_note += f"; Jikan/MAL {mal_id}: {mal_match.get('title') or canonical_name} (episode titles)"
        _, by_season, absolute = self.get_episode_maps(tmdb_id, details, mal_id)
        decisions = resolve_collection(videos, parsed, series_dir,
            [canonical_name, details.get("original_name", ""), clean_show(series_dir.name)],
            by_season, absolute, self.check_cancelled)

        actions = []
        ignored = []
        unresolved = []
        collision = 0

        self.log("  Indexing episode sidecars...")
        sidecars = self.index_sidecars(all_files, videos)

        for video_index, video in enumerate(videos, 1):
            if video_index == 1 or video_index % 100 == 0 or video_index == len(videos):
                self.log(f"  Mapping video {video_index}/{len(videos)}...")
            self.check_cancelled()
            decision = decisions[video]
            meta = by_season.get(decision.key)
            abs_num = decision.absolute_number
            if not meta:
                unresolved.append(str(video))
                self.ignored_reasons[series_dir.name][str(video)] = decision.reason
                continue

            dest_dir = series_dir / f"Season {meta.season}"
            base = safe_filename(f"S{meta.season:02d}E{meta.episode:02d} - {meta.title}")
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
                fingerprint=file_identity(video),
                match_reason=decision.reason,
            ))

            # Move sidecars in the same folder sharing the exact original video stem.
            for side in sidecars.get(video, []):
                self.check_cancelled()
                suffix = sidecar_suffix_from_stem(video.stem, side.stem)
                if suffix is None:
                    continue
                dest_side = dest_dir / f"{base}{suffix}{side.suffix.lower()}"
                if dest_side.resolve() != side.resolve() and dest_side.exists():
                    collision += 1
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
                    fingerprint=file_identity(side),
                    match_reason="Sidecar of episode: " + decision.reason,
                ))

        self.log(f"  Checking {len(actions)} planned file paths for collisions...")
        destinations, sources = [], []
        for action in actions:
            self.check_cancelled()
            destinations.append(path_key(action.destination))
            sources.append(path_key(action.source))
        collision += len(destinations) - len(set(destinations))
        collision += len(sources) - len(set(sources))
        episode_targets = [(a.season, a.episode) for a in actions if a.kind == "video"]
        duplicate_episodes = len(episode_targets) - len(set(episode_targets))
        collision += duplicate_episodes
        planned_videos = len(episode_targets)

        # A confidently identified show can now be PARTIAL even when movies,
        # OVAs, specials or other files cannot be mapped. Apply only touches
        # the files for which ReList has an exact metadata match.
        #
        # REVIEW is reserved for weak show identification, collisions, or a
        # show where no video at all can be safely mapped.
        if confidence >= 0.90 and planned_videos > 0 and collision == 0:
            status = "PARTIAL" if (ignored or unresolved) else "READY"
        else:
            status = "REVIEW"

        note_parts = [match_note]
        if ignored:
            note_parts.append(f"{len(ignored)} extra/unrecognized video(s) ignored")
        if unresolved:
            note_parts.append(f"{len(unresolved)} unresolved video(s) left unchanged")
        if duplicate_episodes:
            note_parts.append(f"{duplicate_episodes} duplicate episode mapping(s); no release chosen automatically")
        if collision:
            note_parts.append(f"{collision} destination collision(s)")

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
            mapping_safe=planned_videos > 0 and collision == 0,
        )
        self.ignored_by_series[series_dir.name] = ignored + unresolved
        return plan, actions

    def confirm_match(self, folder: str):
        """Confirm only the show identity; never waive mapping or file checks."""
        plan = self.series_plans.get(folder)
        if not plan or not plan.tmdb_id or plan.status in {"ERROR", "SKIP"}:
            raise RuntimeError("Scan this series successfully before confirming its match.")
        # Persist first so a failed config write cannot partially confirm a plan.
        self.save_override(folder, plan.tmdb_id)
        plan.match_confirmed = True
        plan.confidence = 1.0
        details = plan.note.split("; ", 1)
        plan.note = "TMDB match confirmed by you"
        if len(details) > 1:
            plan.note += "; " + details[1]
        if plan.mapping_safe:
            plan.status = "PARTIAL" if self.ignored_by_series.get(folder) else "READY"
        else:
            plan.status = "REVIEW"
        self.log(f"Match confirmed: {folder} → {plan.tmdb_name} (TMDB {plan.tmdb_id}); {plan.status}")
        return plan

    def ignore_reason(self, video: Path, series_dir: Path):
        parsed = parse_release(video.stem, clean_show(series_dir.name))
        return exclusion_reason(video, series_dir, parsed) or parsed.reason

    def scan_all(self):
        self.load_overrides()
        self.series_plans.clear()
        self.actions_by_series.clear()
        self.ignored_by_series.clear()
        self.ignored_reasons.clear()

        dirs = self.list_series_dirs()
        self.log(f"Found {len(dirs)} top-level series folder(s).")

        for i, series_dir in enumerate(dirs, 1):
            self.check_cancelled()
            self.log(f"[{i}/{len(dirs)}] Scanning {series_dir.name}...")
            try:
                plan, actions = self.plan_series(series_dir)
            except ScanCancelled:
                raise
            except Exception as e:
                plan = SeriesPlan(
                    folder=series_dir.name,
                    source_name=series_dir.name,
                    tmdb_id=None,
                    tmdb_name=None,
                    confidence=0.0,
                    video_count=0,
                    planned_videos=0,
                    status="ERROR",
                    note=str(e),
                )
                actions = []
            self.series_plans[series_dir.name] = plan
            self.actions_by_series[series_dir.name] = actions
            self.ignored_by_series.setdefault(series_dir.name, [])
            self.log(
                f"  {plan.status}: {plan.tmdb_name or 'Unmatched'} | "
                f"{plan.planned_videos}/{plan.video_count} videos | "
                f"confidence {plan.confidence:.0%}"
            )

        self.log(f"Scan complete: {len(self.series_plans)} series processed. No files changed.")
        return list(self.series_plans.values())

    def actions_for(self, folders: list[str]):
        result = []
        for f in folders:
            result.extend(self.actions_by_series.get(f, []))
        return result

    def checked_path(self, value, series_folder=None):
        path = Path(value)
        if not path.is_absolute():
            raise RuntimeError(f"Expected an absolute library path: {path}")
        root = self.root
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise RuntimeError(f"Path is outside this library: {path}")
        if len(relative.parts) < 2 or ".." in relative.parts:
            raise RuntimeError(f"Path is not inside a series folder: {path}")
        if series_folder and relative.parts[0] != series_folder:
            raise RuntimeError(f"Path does not belong to series {series_folder}: {path}")
        cursor = root
        for part in ("",) + relative.parts:
            cursor = cursor / part
            if os.path.lexists(cursor) and is_link(cursor):
                raise RuntimeError(f"Linked paths are left untouched: {cursor}")
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f"Path escapes the library: {path}")
        return path

    def preflight(self, actions):
        if not actions:
            raise RuntimeError("No changes to apply.")
        sources, destinations = set(), set()
        for action in actions:
            plan = self.series_plans.get(action.series_folder)
            if not plan or plan.status not in {"READY", "PARTIAL"}:
                raise RuntimeError("Only current READY/PARTIAL plans can be applied. Scan again.")
            if action not in self.actions_by_series.get(action.series_folder, []):
                raise RuntimeError("Action is not part of the current scan.")
            if action.season <= 0 or action.episode <= 0:
                raise RuntimeError("Only normal numbered episodes can be organized.")
            src = self.checked_path(action.source, action.series_folder)
            dst = self.checked_path(action.destination, action.series_folder)
            if not src.is_file() or action.fingerprint != file_identity(src):
                raise RuntimeError(f"Source changed since the scan. Scan again: {src}")
            for path, seen in ((src, sources), (dst, destinations)):
                key = path_key(path)
                if key in seen:
                    raise RuntimeError(f"Duplicate source or destination: {path}")
                seen.add(key)
            if src.resolve() != dst.resolve() and os.path.lexists(dst):
                raise RuntimeError(f"Refusing to overwrite: {dst}")
            ancestor = dst.parent
            while not ancestor.exists():
                ancestor = ancestor.parent
            if not ancestor.is_dir() or ancestor.stat().st_dev != src.stat().st_dev:
                raise RuntimeError(f"Destination must be on the same volume: {dst}")

    def apply_actions(self, actions: list[MoveAction], cleanup=True):
        self.preflight(actions)
        actions = [a for a in actions if path_key(a.source) != path_key(a.destination)]
        if not actions:
            raise RuntimeError("Selected episodes are already organized. No files changed.")
        log_dir = APP_DIR / LOG_DIR_NAME
        log_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        manifest_path = log_dir / f"rollback-{stamp}-{uuid.uuid4().hex[:8]}.json"
        manifest = {"version": 2, "created_at": datetime.now().isoformat(),
                    "root": str(self.root), "actions": [asdict(a) for a in actions],
                    "completed": [], "pending": None, "created_directories": [],
                    "removed_empty_directories": []}
        atomic_json(manifest_path, manifest)
        try:
            for idx, action in enumerate(actions, 1):
                src = self.checked_path(action.source, action.series_folder)
                dst = self.checked_path(action.destination, action.series_folder)
                if not src.is_file() or file_identity(src) != action.fingerprint:
                    raise RuntimeError(f"Source changed: {src}")
                if os.path.lexists(dst):
                    raise RuntimeError(f"Refusing to overwrite: {dst}")
                if not dst.parent.exists():
                    manifest["created_directories"].append(str(dst.parent))
                item = {"source": str(src), "destination": str(dst), "fingerprint": action.fingerprint}
                manifest["pending"] = item
                atomic_json(manifest_path, manifest)
                dst.parent.mkdir(parents=True, exist_ok=True)
                move_no_replace(src, dst)
                manifest["completed"].append(item)
                manifest["pending"] = None
                atomic_json(manifest_path, manifest)
                self.log(f"[{idx}/{len(actions)}] {src.name} → {dst}")
            if cleanup:
                for folder in sorted({a.series_folder for a in actions}):
                    series_root = self.root / folder
                    directories = []
                    for parent, dirs, _ in os.walk(series_root, followlinks=False):
                        dirs[:] = [d for d in dirs if not is_link(Path(parent) / d)]
                        directories.extend(Path(parent) / d for d in dirs)
                    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                        self.checked_path(str(directory), folder)
                        # Extra folders are protected, including empty ones.
                        if is_probable_extra(directory / "placeholder", series_root):
                            continue
                        if any(directory.iterdir()):
                            continue
                        manifest["cleanup_pending"] = str(directory)
                        atomic_json(manifest_path, manifest)
                        try:
                            directory.rmdir()
                        except OSError:
                            pass
                        else:
                            manifest["removed_empty_directories"].append(str(directory))
                        manifest.pop("cleanup_pending", None)
                        atomic_json(manifest_path, manifest)
            manifest["completed_at"] = datetime.now().isoformat()
            atomic_json(manifest_path, manifest)
        except Exception:
            self.log(f"Apply stopped. Recover with rollback: {manifest_path}")
            raise
        finally:
            self.series_plans.clear()
            self.actions_by_series.clear()
        self.log(f"Apply complete. Rollback manifest: {manifest_path}")
        return manifest_path

    def latest_rollback_manifest(self):
        log_dir = APP_DIR / LOG_DIR_NAME
        for path in sorted(log_dir.glob("rollback-*.json"), reverse=True):
            if path.name.endswith(".rolled-back.json") or path.with_suffix(".rolled-back.json").exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if path_key(data["root"]) != path_key(self.root) or data.get("rolled_back_at"):
                    continue
                if data.get("completed") or data.get("pending"):
                    return path
            except (ValueError, KeyError, OSError):
                self.log(f"Could not read rollback history: {path.name}")
        return None

    def rollback_manifest(self, manifest_path: Path):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if path_key(data.get("root", "")) != path_key(self.root):
            raise RuntimeError("Rollback belongs to a different library.")
        if data.get("rolled_back_at") or manifest_path.with_suffix(".rolled-back.json").exists():
            raise RuntimeError("This batch has already been restored.")
        completed = list(data.get("completed", []))
        pending = data.get("pending")
        if pending:
            src, dst = self.checked_path(pending["source"]), self.checked_path(pending["destination"])
            if not src.exists() and dst.is_file() and file_identity(dst) == pending.get("fingerprint"):
                if pending not in completed:
                    completed.append(pending)
            elif not (src.is_file() and not dst.exists() and file_identity(src) == pending.get("fingerprint")):
                raise RuntimeError("Interrupted move is ambiguous. Files were left untouched.")
        if not completed and not pending:
            raise RuntimeError("Manifest contains no completed moves.")
        restored = set(data.get("restored_sources", []))
        rollback_pending = data.get("rollback_pending")
        if rollback_pending:
            src = self.checked_path(rollback_pending["source"])
            dst = self.checked_path(rollback_pending["destination"])
            if src.is_file() and not dst.exists() and file_identity(src) == rollback_pending.get("fingerprint"):
                restored.add(str(src))
            elif not (dst.is_file() and not src.exists() and file_identity(dst) == rollback_pending.get("fingerprint")):
                raise RuntimeError("Interrupted rollback is ambiguous. Files were left untouched.")
        # Validate the entire remaining batch before restoring any file.
        remaining = []
        sources, destinations = set(), set()
        for item in reversed(completed):
            src, dst = self.checked_path(item["source"]), self.checked_path(item["destination"])
            if path_key(src) in sources or path_key(dst) in destinations:
                raise RuntimeError("Duplicate paths in rollback manifest.")
            sources.add(path_key(src))
            destinations.add(path_key(dst))
            if str(src) in restored:
                continue
            if os.path.lexists(src) or not dst.is_file():
                raise RuntimeError(f"Rollback blocked: original exists or moved file is missing: {src}")
            identity = file_identity(dst)
            if item.get("fingerprint") and identity != item["fingerprint"]:
                raise RuntimeError(f"Moved file changed; leaving it untouched: {dst}")
            remaining.append((src, dst, identity))
        directories = list(data.get("removed_empty_directories", []))
        if data.get("cleanup_pending"):
            directories.append(data["cleanup_pending"])
        for value in directories + data.get("created_directories", []):
            self.checked_path(value)
        data["completed"] = completed
        data["pending"] = None
        data["restored_sources"] = sorted(restored)
        data["rollback_pending"] = None
        atomic_json(manifest_path, data)
        for src, dst, identity in remaining:
            self.checked_path(str(src))
            self.checked_path(str(dst))
            if not dst.is_file() or file_identity(dst) != identity or os.path.lexists(src):
                raise RuntimeError(f"Files changed during rollback: {dst}")
            data["rollback_pending"] = {"source": str(src), "destination": str(dst), "fingerprint": identity}
            atomic_json(manifest_path, data)
            src.parent.mkdir(parents=True, exist_ok=True)
            move_no_replace(dst, src)
            restored.add(str(src))
            data["restored_sources"] = sorted(restored)
            data["rollback_pending"] = None
            atomic_json(manifest_path, data)
            self.log(f"Restored: {src}")
        for value in directories:
            Path(value).mkdir(parents=True, exist_ok=True)
        for value in reversed(data.get("created_directories", [])):
            try:
                Path(value).rmdir()
            except OSError:
                pass
        data["restored_sources"] = sorted(restored)
        data["pending"] = None
        data["rollback_pending"] = None
        data["rolled_back_at"] = datetime.now().isoformat()
        atomic_json(manifest_path, data)
        atomic_json(manifest_path.with_suffix(".rolled-back.json"), data)
        self.series_plans.clear()
        self.actions_by_series.clear()
        self.log("Rollback complete.")
