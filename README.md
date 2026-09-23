# ReList

A Windows desktop application for organizing **normal anime TV episodes** into Jellyfin season folders. Episode titles come exclusively from MyAnimeList through Jikan v4. TMDB supplies show matching and the existing season/episode structure. ReList previews every file change and keeps an undo journal. Movies, specials, OVAs and uncertain media remain untouched.


## Run

Windows 10/11, Python 3.11 or newer with Tkinter, and a TMDB **v3 API key** are required. Internet access is used only for scanning metadata. No third-party Python packages are needed.

Double-click `run_relist.bat`, or run from this folder:

```powershell
python relist.py
```

Keep `relist.py`, `core.py`, `jikan.py`, `episode_index.py`, and `ui.py` together. Use a writable application folder: configuration, the `.jikan-cache` metadata cache and rollback journals are stored here, never in your media library. The API key is kept in memory and is not saved in configuration or preview exports. Jikan needs no API key.

## The workflow

1. Choose the **parent folder** containing your series folders, then enter your TMDB key.
2. Click **Scan library**. Scanning is read-only. Activity reports metadata seasons, sidecar indexing, episode mapping and collision checks. Each new scan clears the previous activity view. **Cancel scan** stops after the current metadata request (a request can take up to 25 seconds).
3. Search or filter the collection. Select a series to inspect its match, mapping counts, and explanation. Ctrl/Shift selects multiple rows; **Select eligible** selects READY/PARTIAL rows in the current filtered view.
4. If the displayed show is correct, choose **Confirm TMDB match** in the inspector. Confirm its title and ID to reuse the existing scan immediately, without another metadata request. The ID is saved for future scans. Collisions and unmapped episodes still require review; confirmation only resolves uncertainty about the show identity. If the show is incorrect, use **Correct TMDB match…**, enter a different TV-series ID, and scan again.
5. Verify the **Jikan/MAL ID and title** in the inspector notes. Automatic MAL matching requires a unique exact TV title/alias match to the matched TMDB show. If it fails or selects the wrong entry, use **Correct MAL match…**, enter the numeric ID from `myanimelist.net/anime/<ID>`, and scan again. Confirming the TMDB match cannot fix a failed MAL lookup. Click **Preview changes →** and inspect the exact source/destination paths and the **IGNORED / LEFT UNCHANGED** section. You can export this report to a text file.
6. Choose whether to remove empty episode folders, acknowledge the preview, and click **Organize files**. REVIEW/ERROR/SKIP selections cannot be applied.
7. Refresh the library in Jellyfin. Scan again in ReList before making further changes.

The light/dark theme switch saves your preference. The Death Note-inspired dark theme combines near-black panels, ivory text and muted crimson accents. The gothic `RL` monogram uses the supplied Unicode L art and a matching custom R, rendered directly without extra fonts or image files. Consolas monospace typography gives the interface a code-like appearance; the light theme uses aged-paper and ink colors. Search also supports Ctrl+F; Enter or double-click on a selected series opens its preview.

## Series states

| State | Meaning | Can organize? |
| --- | --- | --- |
| READY | Confident TV match; all discovered videos map to normal episodes; no collisions | Yes, after preview |
| PARTIAL | Confident TV match; some episodes map safely and other videos remain untouched | Mapped episodes and their sidecars only |
| REVIEW | Weak/ambiguous match, destination collision, or no eligible episode could be mapped | No |
| ERROR | Filesystem or metadata lookup failed | No |
| SKIP | No videos or no recognizable normal episodes | No |

The table's **Mapped / found** count includes already-organized episodes. The preview counts actual file changes separately. Already-organized files require no move and do not create an empty undo batch.

TMDB matching uses cleaned folder titles and, when at least two releases and 70% of candidate files agree, a shared filename series title. Release tags, dotted separators, season labels and technical suffixes are removed from search queries. Unicode titles are preserved, duplicate search-result IDs are consolidated, and a supplied year helps distinguish remakes. TMDB matching still uses title similarity. Substring-only matches and close competing candidates require review. A high similarity score is not a guarantee of the correct show: verify its name and TV-series ID in the inspector. Folder-name overrides are shared across libraries, matching previous ReList behavior.

## Library structure and naming

Each immediate child directory is treated as one TV series:

```text
Anime/
  Bleach/
    Old releases/
      [Anime] Bleach - 252.mkv
      [Anime] Bleach - 252.en.srt
  Steins;Gate/
    Show S01E03.mkv
```

If Jikan episode `mal_id: 252` maps through TMDB's season structure to season 13, episode 23, its Jikan `title` is used:

```text
Bleach/
  Season 13/
    S13E23 - Episode Title.mkv
    S13E23 - Episode Title.en.srt
```

## Episode parsing and collection context

The parser extracts series, season/episode number and episode title independently of the original separator style. It recognizes `S01E03`, `S01.E03`, `Season 1 Episode 3`, `1x03`, `EP_03`, `Episode.03`, `E03`, `Show - 003`, and release revision suffixes such as `01v2`. Bare or bracketed numbers are weaker candidates that need corroboration from the collection. Release groups, CRC tags, resolution, codecs and audio suffixes are excluded from episode-number detection.

For example:

```text
[Anime].Samurai.Champloo.(ENHANCED).Episode.01.Tempestuous.Temperaments.1080p.Dual.Audio.Bluray [D94E527C]
```

is parsed as **Samurai Champloo**, episode **1**, title **Tempestuous Temperaments**. Jikan supplies the final destination title.

ReList evaluates related files in the same directory and series family together:

- Explicit season/episode identifiers remain authoritative, unless an exact metadata title contradicts them.
- Absolute numbering is compared with season-relative numbering when a season folder, explicit neighbors or exact titles provide that alternative. If both interpretations differ, the file stays unresolved until evidence distinguishes them.
- Two distinct explicit/title anchors that agree on one numbering convention can resolve siblings with weaker names. A complete, unique `1..N` collection matching a named season's full metadata range can also establish season-relative numbering.
- Bare-number files need a coherent run of at least three unique consecutive numbered files in an identified collection, or stronger metadata/neighbor evidence. Missing numbers remain gaps; filenames are never renumbered from alphabetical order or file position.
- A file with no number can match an exact, unique metadata title only when at least two other numbered files establish the same season. Similar-looking titles alone are insufficient.
- Conflicting titles, competing numbering conventions, different multi-word series prefixes, duplicate releases and multi-episode files are not guessed through. Multiple videos mapping to the same episode block the series, even when extensions differ.

Each proposed file change shows a **MATCH** explanation in the preview. Unresolved files show the evidence that was missing or conflicting. A show still needs a confident TMDB match or your explicit confirmation before it can be applied.

Jikan's `/v4/anime/{malId}/episodes?page={page}` is fetched through its last page. Each record's `mal_id` is its absolute episode number, regardless of response order or missing records; only its `title` supplies the filename title. That absolute number maps into TMDB's positive seasons in order. TMDB Season 0 is excluded. If TMDB numbering contains gaps or duplicates, only the contiguous verified prefix is usable. Later uncertain episodes, including explicit season/episode files without a verified Jikan mapping, stay untouched. Missing, null or blank Jikan titles also leave files untouched; there is no TMDB-title or generated-title fallback. Jikan gaps never shift later episode numbers.

One MAL entry is used per series folder. MAL sometimes splits sequels/cours into separate anime entries whose numbering starts again at 1. ReList does not concatenate related entries or infer offsets: choose an entry whose episode numbers correspond to this folder's absolute numbering. Episodes outside that entry remain unchanged. Always verify the MAL entry and the season mapping in the preview.

Complete Jikan search, anime and episode responses are cached beside the application for 24 hours, including across restarts. Expired data is refreshed; a complete cache under 7 days old can be reused during a temporary outage, with an activity warning. Older data, malformed responses, duplicate IDs and partial pagination cannot supply replacement metadata. Failed pagination never replaces a complete episode cache. Delete `.jikan-cache` with ReList closed to force a fresh lookup. Overrides are stored separately as `mal_overrides` in configuration.

Requests are paced slightly slower than one per second across clients. HTTP 429 and server/network failures get up to three attempts with exponential backoff and `Retry-After` handling; long requested cooldowns fail the scan promptly. Other HTTP errors fail immediately. Cancellation interrupts pacing/backoff and is checked between requests/pages. Uncached failures produce ERROR with no file actions; missing titles produce PARTIAL or REVIEW. TMDB remains necessary for show matching and season structure, so its key is still required.

Sidecars in the same directory can accompany an episode when their stem exactly matches the video stem or continues with a separator. For example, `.en.srt`, `.ass`, `.nfo`, and `-thumb.jpg` suffixes are preserved. ReList resolves ownership against nearby videos so an ignored video's sidecar is not claimed by a shorter episode name. Ambiguous sidecars remain untouched. Any detected sidecar destination collision blocks the series.

## Always left untouched

- Season 0 / `S00E##`, `Specials`, and `Season 00` content. ReList never requests TMDB Season 0.
- Files or folders marked as movies, films, OVAs/OADs/ONAs, specials, extras, bonus content, recaps or trailers. Opening/ending and similar extra folders are also excluded.
- Unrecognized names, multi-episode/range/fractional numbering, and episodes without a Jikan title mapped to verified season numbering.
- Symbolic links and Windows junction/reparse points within series folders.
- Videos directly in the selected root, outside an immediate series folder.

These rules apply even when an extra contains a normal-looking `S01E01` token. Soft markers such as “Pilot,” “Prologue,” “Epilogue,” “Opening” and “Ending” in a filename can be normal episode titles only when the exact Jikan title and episode evidence corroborate that interpretation. Conservative keyword matching may also exclude a genuine episode whose title contains an extras keyword. Review the reason; there is no unsafe force-apply switch. Multiple distinct shows combined in one folder must be separated by the user before organizing.

ReList does not search TMDB's movie database, modify media contents, or edit embedded MKV metadata.

## File safety and undo

Before a batch starts, ReList validates every action against the current READY/PARTIAL plan, checks source file identities, rejects duplicate destinations, checks library boundaries and linked paths, and refuses existing destinations. Moves stay on the same filesystem and use a no-overwrite operation. A source changed since scanning requires a new scan.

Journals live in:

```text
ReList/
  .anime-organizer-config.json
  .anime-organizer/
    rollback-<timestamp>-<unique-id>.json
```

Each journal is written atomically, flushed to disk, and records an intended move **before** that move starts. Completed moves, newly created directories and removed empty directories are tracked. The application invalidates plans after apply, undo, root changes or overrides, and prevents closing through its normal close button during an operation.

**Undo latest batch…** works without an API key or network connection. It selects the latest unrestored batch for the chosen library and validates all remaining moves before restoring anything. It refuses occupied original paths, missing moved files, changed file identities and paths outside the library. Interrupted apply/undo can be retried using the saved journal when file state is unambiguous. If state is ambiguous, it stops and leaves files untouched; do not delete the journal.

Existing v0.7 manifests remain supported. Old manifests lack file identity snapshots, so recovery has fewer checks. Existing `.rolled-back.json` markers are respected and restored batches are not offered again. Earlier versions did not durably journal every move; this update cannot reconstruct information an old manifest never recorded.

Cleanup uses empty-directory removal only, preserves top-level series folders, and skips extras directories. A folder containing any item, including hidden files or ignored media, is preserved. Undo recreates recorded removed directories and removes newly created destination directories only if still empty.

File identity checks use size, modification time and filesystem identity, not a full content hash. Keep other organizers from modifying files during a batch. Rollback is not a substitute for a real backup, and disk/filesystem failure can still prevent recovery. Keep the application and its journals when upgrading.

## Jellyfin

After organizing, rescan/refresh the library. If embedded titles or episode information conflict with filenames, review Jellyfin's preferences for embedded metadata.

## Architecture and tests

- `relist.py`: stable launcher.
- `core.py`: TMDB show matching/season structure, MAL matching, Jikan title mapping, series plans, preflight validation and recovery journals. No Tkinter dependency.
- `jikan.py`: Jikan v4 pagination, pacing/retries, cancellation and versioned complete-response caches. [Jikan API documentation](https://docs.api.jikan.moe/).
- `episode_index.py`: structured release parsing, extras classification and collection-aware episode resolution. No network or filesystem writes.
- `ui.py`: themed Tkinter workspace, main-thread event queue, background operations, previews and UI state invalidation.
- `tests/test_core.py`: synthetic metadata and filesystem regression tests, including interruption recovery.
- `tests/test_episode_index.py`: messy filenames, collection inference, ambiguous/conflicting evidence, metadata gaps and inferred-action rollback tests.
- `tests/test_ui.py`: real Tk widget tests for filtering, selection, themes, resizing and preview gating.
- `tests/test_jikan.py`: pagination, caching, outages, retries, missing titles, MAL overrides and absolute-to-season mapping.

Run from the application folder:

```powershell
python -m unittest discover -s tests -v
```

Tests use isolated, disposable fixtures; they do not access real media or contact TMDB/Jikan. GUI tests require a desktop/Tk installation.
