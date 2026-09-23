"""Release parsing and conservative, collection-aware episode resolution.

No filesystem writes, network access or dependency on the GUI. Numbers are never
assigned from list position: every decision retains its evidence for the preview.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SEP = r"[\s._]*"
SEASON_EP = re.compile(rf"(?i)(?<!\w)s(?:eason)?{SEP}(\d{{1,2}}){SEP}e(?:p(?:isode)?)?{SEP}(\d{{1,4}})(?:v\d+)?(?!\w)")
X_EP = re.compile(r"(?i)(?<!\w)(\d{1,2})x(\d{1,4})(?:v\d+)?(?!\w)")
LABELED_EP = re.compile(rf"(?i)(?<!\w)(?:episodes?|ep|e){SEP}(\d{{1,4}})(?:v\d+)?(?!\w)")
DASH_EP = re.compile(r"(?i)(?:\s+-\s*|\s*-\s+)(\d{1,4})(?:v\d+)?(?!\w)")
BARE_EP = re.compile(r"(?i)(?<![\w])(\d{1,4})(?:v\d+)?(?!\w)")
SEASON_HINT = re.compile(rf"(?i)(?<!\w)(?:season|s){SEP}(\d{{1,2}})(?!\w)")
TECH = re.compile(r"(?ix)(?<!\w)(?:\d{3,4}[pi]|[xh][ ._-]?26[45]|hevc|av1|avc|"
                  r"blu[ ._-]?ray|bdrip|brrip|webrip|web[ ._-]?dl|dvdrip|dvd|hdtv|"
                  r"dual[ ._-]?audio|multi[ ._-]?audio|aac|flac|ddp|ac3|dts|"
                  r"\d{1,2}[ ._-]?bit|enhanced|remastered|complete|batch)(?!\w)")
HARD_EXTRA = re.compile(r"(?i)(?<!\w)(?:movies?|films?|ovas?|oads?|onas?|specials?|extras?|"
                        r"bonus|recaps?|trailers?|previews?|promos?|pv|ncop|nced)(?!\w)")
SOFT_EXTRA = re.compile(r"(?i)(?<!\w)(?:pilot|prologue|epilogue|opening|ending)(?!\w)")
MULTI = re.compile(r"(?i)^\s*(?:[-+&~,;]\s*(?:s\d+e|e(?:p(?:isode)?)?[ ._]*)?\d+(?!\w)"
                   r"|e\d+|[._]\d+(?!\w))")


def words(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value).split())


def display_text(value: str) -> str:
    return " ".join(re.sub(r"[._]+", " ", value).split()).strip(" -[]()")


def clean_release(value: str) -> str:
    value = value.replace("_", " ")
    # Preserve bracketed episode numbers, discard release groups/checksums/tags.
    value = re.sub(r"\[([^]]*)\]", lambda m: " " + m[1] + " "
                   if re.fullmatch(r"\d{1,4}(?:v\d+)?", m[1], re.I) else " ", value)
    value = re.sub(r"\(([^)]*)\)", lambda m: " " if TECH.search(m[1]) or re.fullmatch(r"\d{4}", m[1]) else m[0], value)
    return value.strip(" ._-")


def clean_show(value: str) -> str:
    value = clean_release(value)
    value = SEASON_HINT.sub(" ", value)
    technical = TECH.search(value)
    if technical:
        value = value[:technical.start()]
    value = re.sub(r"(?i)\b(?:ENHANCED|complete|batch)\b", " ", value)
    return display_text(value)


@dataclass(frozen=True)
class ParsedEpisode:
    number: int | None = None
    season: int | None = None
    series: str = ""
    title: str = ""
    strength: str = "none"  # explicit / numbered / contextual / none
    reason: str = ""


def parse_release(stem: str, known_series: str = "") -> ParsedEpisode:
    text = clean_release(stem)
    if re.search(r"(?i)(?:s[ ._]*\d+[ ._]*e[ ._]*|ep(?:isode)?[ ._]*|\be[ ._]*| - )\d+(?:e\d+|\s*[-+&~,;]\s*(?:s\d+e|e)?\d+(?!\w)|\.\d+(?!\w))", text):
        return ParsedEpisode(reason="Episode range or fractional numbering")
    # Technical suffixes often contain resolutions, CRCs, years and audio counts.
    tech = TECH.search(text)
    if tech:
        text = text[:tech.start()].rstrip(" ._-")
    matches = list(SEASON_EP.finditer(text)) + list(X_EP.finditer(text))
    if matches:
        if len(matches) != 1:
            return ParsedEpisode(reason="Multiple season/episode identities")
        match = matches[0]
        season, number = int(match[1]), int(match[2])
        strength = "explicit"
        if LABELED_EP.search(text[match.end():]):
            return ParsedEpisode(reason="Multiple episode identities")
    else:
        matches = list(LABELED_EP.finditer(text))
        if not matches:
            matches = list(DASH_EP.finditer(text))
        strength = "numbered"
        if not matches:
            # Remove a known numeric series prefix before finding bare numbers.
            prefix = re.match(r"(?i)^" + r"[ ._]+".join(map(re.escape, known_series.split())) + r"(?=$|[ ._-])", text) if known_series else None
            offset = prefix.end() if prefix else 0
            search_text = text[offset:]
            matches = list(BARE_EP.finditer(search_text))
            # A second numeric token could be a title/year/episode: do not guess.
            matches = [m for m in matches if not 1900 <= int(m[1]) <= 2099]
            if len(matches) > 1:
                return ParsedEpisode(reason="Ambiguous bare numbers or episode range")
            if not matches:
                return ParsedEpisode(title=display_text(text), reason="No unique episode number")
            candidate = matches[0]
            start, end = offset + candidate.start(), offset + candidate.end()
            number, season = int(candidate[1]), None
            if number <= 0:
                return ParsedEpisode(reason="Invalid episode number")
            if MULTI.match(text[end:]):
                return ParsedEpisode(reason="Episode range or fractional numbering")
            return ParsedEpisode(number, season, clean_show(text[:start]), display_text(text[end:]), "contextual")
        if len(matches) != 1:
            return ParsedEpisode(reason="Multiple episode identities")
        match = matches[0]
        number, season = int(match[1]), None
    outside = text[:match.start()] + " " + text[match.end():]
    if LABELED_EP.search(outside) or DASH_EP.search(outside):
        return ParsedEpisode(reason="Multiple episode identities")
    if MULTI.match(text[match.end():]):
        return ParsedEpisode(reason="Episode range or fractional numbering")
    if number <= 0 or season == 0:
        return ParsedEpisode(reason="Season 0 / specials or invalid episode number")
    if season is None:
        prefix_seasons = {int(m[1]) for m in SEASON_HINT.finditer(text[:match.start()])}
        if len(prefix_seasons) > 1 or 0 in prefix_seasons:
            return ParsedEpisode(reason="Conflicting season markers or specials")
        if prefix_seasons:
            season = next(iter(prefix_seasons))
            strength = "explicit"
    prefix = clean_show(text[:match.start()])
    return ParsedEpisode(number, season, prefix, display_text(text[match.end():]), strength)


def folder_season(path: Path, root: Path) -> int | None:
    parts = [root.name, *path.relative_to(root).parts[:-1]]
    numbers = {int(m[1]) for part in parts for m in SEASON_HINT.finditer(part.replace("_", " "))}
    return next(iter(numbers)) if len(numbers) == 1 else None


def exclusion_reason(path: Path, root: Path, parsed: ParsedEpisode) -> str:
    directories = [root.name, *path.relative_to(root).parts[:-1]]
    for part in directories:
        separated = re.sub(r"[._]", " ", part)
        if HARD_EXTRA.search(separated) or SOFT_EXTRA.search(separated) or any(int(m[1]) == 0 for m in SEASON_HINT.finditer(separated)):
            return "Movie, special, OVA or extra folder"
    if HARD_EXTRA.search(re.sub(r"[._]", " ", path.stem)):
        return "Movie, special, OVA or extra"
    if parsed.reason and parsed.reason != "No unique episode number":
        return parsed.reason
    return ""


def title_key(value: str) -> str:
    return words(value)


def useful_title(value: str) -> bool:
    return bool(value) and not re.fullmatch(r"(?:episode|ep|part) \d+", value)


@dataclass(frozen=True)
class Decision:
    key: tuple[int, int] | None
    reason: str
    absolute_number: int | None = None


def resolve_collection(videos, parsed, root, aliases, by_season, absolute, check_cancelled=lambda: None):
    """Resolve related files together, without renumbering gaps or ranges.

    A directory/series family shares one numbering convention only when two
    independent anchors agree, or a complete season range corroborates its folder.
    """
    decisions = {}
    groups = defaultdict(list)
    aliases = {words(clean_show(a)) for a in aliases if a}
    titles = defaultdict(set)
    season_numbers = defaultdict(set)
    for key, meta in by_season.items():
        season_numbers[key[0]].add(key[1])
        title = title_key(meta.title)
        if useful_title(title):
            titles[title].add(key)
    absolute_keys = {n: (meta.season, meta.episode) for n, meta in absolute.items()}

    def compatible(series):
        name = words(series)
        return not name or name in aliases

    def family(item):
        name = words(item.series)
        return "show" if compatible(item.series) else name

    def exact_titles(item):
        key = title_key(item.title)
        return titles.get(key, set()) if useful_title(key) else set()

    for path in videos:
        check_cancelled()
        item = parsed[path]
        excluded = exclusion_reason(path, root, item)
        if excluded:
            decisions[path] = Decision(None, excluded)
            continue
        # A different multi-word series prefix is evidence of a mixed collection.
        if item.series and len(words(item.series).split()) >= 2 and not compatible(item.series):
            decisions[path] = Decision(None, "Filename indicates a different series")
            continue
        groups[(path.parent, family(item))].append(path)

    for (parent, _), paths in groups.items():
        check_cancelled()
        numbered = [p for p in paths if parsed[p].number is not None]
        hints = {folder_season(p, root) for p in paths} - {None, 0}
        hint = next(iter(hints)) if len(hints) == 1 else None
        # Numbering hypotheses: absolute order plus any evidence-backed season.
        explicit_seasons = {parsed[p].season for p in numbered if parsed[p].season is not None}
        title_seasons = {s for p in numbered for s, e in exact_titles(parsed[p]) if e == parsed[p].number}
        local_seasons = explicit_seasons | title_seasons | ({hint} if hint else set())
        hypotheses = {"absolute": absolute_keys}
        for season in local_seasons:
            hypotheses[f"season {season}"] = {e: (s, e) for s, e in by_season if s == season}
        # Explicit numbering or a unique exact title acts as an independent anchor.
        anchors = {}
        for path in numbered:
            item = parsed[path]
            exact = exact_titles(item)
            if item.season is not None:
                direct = (item.season, item.number)
                if direct in by_season and (not exact or direct in exact):
                    if not SOFT_EXTRA.search(re.sub(r"[._]", " ", path.stem)) or direct in exact:
                        anchors[path] = direct
            elif len(exact) == 1:
                target = next(iter(exact))
                if any(mapping.get(item.number) == target for mapping in hypotheses.values()):
                    anchors[path] = target
        viable = []
        for label, mapping in hypotheses.items():
            votes = {target for p, target in anchors.items() if mapping.get(parsed[p].number) == target}
            conflicts = any(mapping.get(parsed[p].number) != target for p, target in anchors.items())
            if len(votes) >= 2 and not conflicts:
                viable.append(label)
        # Complete season range + folder + matching release names, not file order.
        numbers = [parsed[p].number for p in numbered]
        complete = (hint is not None and len(numbers) >= 3 and len(set(numbers)) == len(numbers)
                    and set(numbers) == season_numbers.get(hint, set())
                    and set(numbers) == set(range(1, max(numbers) + 1))
                    and all(compatible(parsed[p].series) for p in numbered)
                    and all(target == (hint, parsed[p].number) for p, target in anchors.items()))
        if complete and not viable:
            viable = [f"season {hint}"]
        # Weak bare-number names need an identified release family and a run of
        # at least three distinct consecutive numbers, or explicit/title anchors.
        ordered = sorted(set(numbers))
        run = any(ordered[i:i+3] == list(range(ordered[i], ordered[i]+3)) for i in range(len(ordered)-2))
        family_supported = (run and len(numbers) == len(set(numbers))
                            and all(compatible(parsed[p].series) for p in numbered))

        for path in paths:
            check_cancelled()
            item = parsed[path]
            number = item.number
            exact = exact_titles(item)
            soft_extra = bool(SOFT_EXTRA.search(re.sub(r"[._]", " ", path.stem)))
            if number is None:
                # Missing number: only an exact unique metadata title in a season
                # established by two other numbered files is acceptable.
                text = title_key(item.title)
                for alias in sorted(aliases, key=len, reverse=True):
                    if text.startswith(alias + " "):
                        text = text[len(alias)+1:]
                        break
                exact = titles.get(text, set()) if useful_title(text) else set()
                seasons = {target[0] for target in anchors.values()}
                if len(set(anchors.values())) >= 2 and len(seasons) == 1 and len(exact) == 1 and next(iter(exact))[0] in seasons:
                    decisions[path] = Decision(next(iter(exact)), "Unique metadata title corroborated by numbered neighbors")
                else:
                    decisions[path] = Decision(None, "No unique episode number or corroborated title")
                continue
            if number <= 0:
                decisions[path] = Decision(None, "Invalid episode number")
                continue
            if item.season is not None:
                target = (item.season, number)
                if target not in by_season:
                    decisions[path] = Decision(None, "Explicit episode has no verified Jikan title")
                elif exact and target not in exact:
                    decisions[path] = Decision(None, "Episode number conflicts with metadata title")
                elif soft_extra and target not in exact:
                    decisions[path] = Decision(None, "Possible extra; title does not corroborate a normal episode")
                else:
                    decisions[path] = Decision(target, "Explicit season and episode")
                continue
            candidates = {mapping[number] for mapping in hypotheses.values() if number in mapping}
            target, reason = None, ""
            if len(exact) == 1 and next(iter(exact)) in candidates:
                target = next(iter(exact))
                reason = "Episode number corroborated by exact metadata title"
            elif exact:
                reason = "Metadata title is ambiguous or conflicts with the episode number"
            elif viable:
                agreed = {hypotheses[label].get(number) for label in viable}
                if len(agreed) == 1 and None not in agreed:
                    target = next(iter(agreed))
                    reason = "Collection numbering corroborated by " + ("complete season range" if complete else "multiple episode anchors")
                else:
                    reason = "Collection numbering is ambiguous or episode is absent"
            elif len(candidates) == 1:
                target = next(iter(candidates))
                reason = "Jikan absolute episode mapped to season/episode" if number in absolute_keys else "Season folder and metadata episode range"
            elif len(candidates) > 1:
                reason = "Season-relative and absolute numbering disagree; more evidence required"
            else:
                reason = "Episode has no Jikan title in verified season numbering"
            if target and item.strength == "contextual" and not (exact or viable or family_supported):
                target, reason = None, "Bare number needs corroborating filenames, titles or season context"
            if target and soft_extra and target not in exact:
                target, reason = None, "Possible extra; title does not corroborate a normal episode"
            decisions[path] = Decision(target, reason, number if target and absolute_keys.get(number) == target else None)
    return decisions
