# ReList

A conservative Windows GUI tool for reorganizing an anime library into Jellyfin-friendly season and episode names.

## What it does

Given a root folder such as:

```text
L:\Media\Anime
```

the program treats each immediate child folder as one series, for example:

```text
L:\Media\Anime\
├── Bleach\
├── Naruto\
├── Re Zero\
└── Steins;Gate\
```

It can:

- identify TV series through TMDB;
- parse existing `S01E01` filenames;
- parse common absolute-number release names such as `[Anime] Bleach - 252.mkv`;
- map absolute episode numbers across TMDB's numbered seasons;
- fetch English episode titles;
- create `Season 01`, `Season 02`, etc.;
- rename files to `Series S01E01 - Episode Title.mkv`;
- move matching thumbnails/subtitles/NFO sidecars with the episode;
- preview every proposed change;
- block automatic changes for uncertain matches;
- save a rollback manifest before moving anything;
- rollback the latest applied batch.

## Important safety behavior

The tool is deliberately conservative.

A series is marked **READY** only when:

- the TMDB series match is high confidence;
- at least 95% of video files can be mapped;
- no destination collisions were detected.

Everything else is marked **REVIEW** or **ERROR** and cannot be applied until you correct the TMDB match.

The tool never overwrites an existing destination file.

## Requirements

- Windows 10/11
- Python 3.11+ recommended
- Internet access while scanning
- A free TMDB v3 API key

No third-party Python packages are required.

## Getting a TMDB API key

Create/sign in to a TMDB account, request API access, and copy the **v3 API key**.

The program asks for the key at runtime and does not store it.

## Running

Double-click:

```text
run_relist.bat
```

or run:

```powershell
python relist.py
```

## Recommended workflow

1. Stop Jellyfin before applying large changes.
2. Start ReList.
3. Choose your anime root folder.
4. Enter your TMDB API key.
5. Click **Scan Library**.
6. Review every series marked READY.
7. For a wrong/uncertain match:
   - select the row;
   - click **Set TMDB ID for Selected**;
   - enter the correct TMDB TV-series ID;
   - scan again.
8. Select READY series.
9. Click **Preview Selected**.
10. Verify the exact FROM/TO paths.
11. Click **Apply Selected READY**.
12. Start Jellyfin and scan the library.

## Example

Input:

```text
C:\Media\Anime\Bleach\Season 8\[Anime] Bleach - 252.mkv
C:\Media\Anime\Bleach\Season 8\[Anime] Bleach - 252-thumb.jpg
```

If TMDB maps absolute episode 252 to season 13 episode 23, the planned output is:

```text
L:\Media\Anime\Bleach\Season 13\Bleach S13E23 - Episode Title.mkv
L:\Media\Anime\Bleach\Season 13\Bleach S13E23 - Episode Title-thumb.jpg
```

## Parsing rules

The program currently understands:

```text
Show S01E03.mkv
Show.S01E03.1080p.mkv
[Anime] Bleach - 252.mkv
Show - 007.mkv
Show EP 12.mkv
Show Episode 12.mkv
```

For files with `SxxEyy`, that season/episode is matched directly against TMDB.

For absolute-number filenames such as `Bleach - 252`, the program flattens TMDB's numbered seasons in season/episode order and maps 252 to the 252nd numbered episode.

Season 0 / Specials are intentionally excluded from absolute-number flattening.

## Sidecar handling

Sidecars in the same folder that begin with the exact original video stem are moved and renamed with the video.

Examples:

```text
[Anime] Bleach - 252-thumb.jpg
[Anime] Bleach - 252.en.srt
[Anime] Bleach - 252.ass
```

become sidecars of the new `SxxEyy` filename.

## Rollback

Before applying changes, the program writes:

```text
<anime root>\.anime-organizer\rollback-YYYYMMDD-HHMMSS.json
```

The manifest records each completed source/destination move.

Use **Rollback Latest** to reverse the newest batch.

Rollback will refuse to overwrite a file if the original location already contains something.

## Manual TMDB overrides

If automatic matching is uncertain or wrong:

1. Select exactly one series.
2. Click **Set TMDB ID for Selected**.
3. Enter its TMDB TV-series ID.
4. Scan again.

Overrides are saved in:

```text
<anime root>\.anime-organizer-config.json
```

## What this first version intentionally does not do

Anime metadata is messy. This version does **not** automatically reorganize:

- Season 0 / specials;
- movies;
- OVAs that are not represented as normal numbered TV episodes;
- multi-episode files such as `S01E01-E02`;
- files whose episode number cannot be confidently parsed;
- series folders containing multiple distinct TV series;
- files outside an immediate top-level series folder.

It also does not modify embedded MKV metadata. That should remain a separate opt-in operation because it changes the media container rather than just filesystem organization.

## Jellyfin

For an anime library using this organizer, generally turn off:

- **Prefer embedded titles over filenames**
- **Prefer embedded episode information over filenames**

After applying changes, rescan the library in Jellyfin and refresh metadata as needed.

## Backups

Rollback is useful, but it is not a substitute for backups. If your media is important, keep a real backup before running bulk filesystem operations.
