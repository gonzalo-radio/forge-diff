# Forge Diff

The project is published as **Forge Diff**. The name and the disclaimer live in `APP_NAME` / `DISCLAIMER` in `opr_diff.py`:
- `html_page` appends " · Forge Diff" to the tab title and adds the disclaimer as a footer on every page; the Markdown output has the footer too.
- The top index is titled "Forge Diff".
- Don't use "One Page Rules" or "Army Forge" as the project name. The "unofficial… Not affiliated with OnePageRules" disclaimer must stay on every page.

Generates comparisons of OPR (Army Forge) army books between two dated **snapshots**, for one or more game systems (Grimdark Future, Firefight…).
- For now it compares the stable site against the beta.
- The beta is temporary; later on the stable site will be compared against itself between two dates.

## Usage

All scripts use the standard library only.

**Download (`fetch_books.py`)** — creates a snapshot dated today for each source:
```
python fetch_books.py --system all                            # stable and beta, every game system
python fetch_books.py --source stable --system all --label 3.6.1
```
- `--source stable|beta|all`: repeatable; defaults to `all`.
- `--system SLUG`: repeatable, or `all`; defaults to `grimdark-future` only.
- `--label`: version name stored in the snapshot and used in report headers.
- `--only NAME`: restrict to one army (repeatable).
- `--full`: download everything, reusing nothing.
- `--keep`: keep today's snapshot even if it is identical to the previous one.
- **Incremental download:** if a book has the same `uid` and `modifiedAt` as in the previous snapshot of that source, it is reused via a hardlink instead of downloaded. With few changes an update takes seconds.
- **Same day:** a second download on the same day overwrites the systems it downloads and keeps the others.
- **Unchanged snapshot:** a new snapshot identical to the previous one of that source is deleted. Identical means the same books, none downloaded because of a different `modifiedAt`, and the same core rules. That way `stable@previous` is always the last version that actually differs. It is not deleted with `--only`, `--full` or `--keep`.
- **`--only`:** creates a partial snapshot, so the other armies would show up as removed when comparing. For tests, use it with `--out` pointing somewhere else.

**Publishing (GitHub):**
- **Workflow `.github/workflows/update.yml`:** runs daily and on demand.
  - Restores `data/` and `reports/` from the `snapshots` branch into `store/`.
  - Fetches the stable site. The beta step has `continue-on-error`: once the beta is gone, the run carries on with stable only.
  - Generates stable vs beta (labels from the repo variables `STABLE_LABEL` / `BETA_LABEL`) and `stable@previous` vs `stable`.
  - Commits to the `snapshots` branch and deploys `store/reports` to GitHub Pages.
- `README.md` is the public face of the project; this file holds the implementation notes.

**Generate (`generate_html.py`)** — compares two snapshots:
```
python generate_html.py                                        # default (see below)
python generate_html.py --old stable@previous --new stable     # stable vs its previous snapshot
python generate_html.py --old stable@2026-09-28 --new stable@2026-12-01 --system grimdark-future
```
- **SPEC:** `stable` or `beta` is the latest snapshot of that source; `SOURCE@YYYY-MM-DD` a specific one; `SOURCE@previous` the second latest.
- **Default:** latest stable vs latest beta if there are beta snapshots; otherwise `stable@previous` vs `stable`.
- **Labels:** `--old-label` / `--new-label` win; otherwise the snapshot's `label`; otherwise `"<source> <date>"`. If both labels are equal, the date is appended to each.
- **Systems:** without `--system`, every system present in both snapshots.
- **Output:**
  - `reports/<source>-<date>_vs_<source>-<date>/<system>/`: `index.html`, `<army>.html`, `core_rules.html` and `summary.json`.
  - `reports/index.html`: one row per comparison and system. It is built from every `summary.json`, so it includes comparisons from other runs.
- **Armies without changes:** same `uid` and `modifiedAt`, or an empty diff according to `has_changes`. They show up in the index as "no changes", without a page.
- `data/`, `reports/` and `store/` are generated and ignored by `.gitignore`.

**`data/` layout:**
```
data/snapshots/<source>/<YYYY-MM-DD>/meta.json            {source, host, date, fetched_at, label, systems}
data/snapshots/<source>/<YYYY-MM-DD>/<system>/books.json  [{uid, name, slug, file, modifiedAt, editedAt, versionString, factionId, factionRelation}]
data/snapshots/<source>/<YYYY-MM-DD>/<system>/<army>.json, _core_rules.json
```

**Two standalone files:**
```
python opr_diff.py old.json new.json [-o output_name] [--core-old data/snapshots/stable/2026-09-28/grimdark-future/_core_rules.json --core-new …]
```
- Writes `CHANGES_<old>_vs_<new>.md` and `.html`.
- Versions come from the file name (`_3_6_0` → `3.6.0`); if it has none, from `versionString`.

**Code structure:**
- `generate_html.py` imports from `opr_diff.py` (`build`, `render_html`, `html_page`, `html_rules_section`, `dict_diff`) and from `fetch_books.py` (`pair_books`, `snapshot_dates`, `SNAPSHOTS`).
- All the diff and rendering logic lives in `opr_diff.py`.

**Checks after any change:**
- Comparing a file with itself must report 0 changes everywhere.
- Look at the HTML in a browser: `chromium --headless --screenshot=... file://...`.

## Army Forge API (`fetch_books.py`)

- **Hosts:**
  - Stable: `https://army-forge.onepagerules.com`
  - Beta: `https://army-forge-beta.onepagerules.com`
- **Listing:** `/api/army-books?filters=official&gameSystemSlug=<slug>`.
- **Book:** `/api/army-books/<uid>?gameSystem=<id>`. Use the numeric system id; `gameSystemSlug` returns a 500 here.
- **System ids** (`GAME_SYSTEMS` in `fetch_books.py`):

  | id | slug |
  |---|---|
  | 2 | grimdark-future |
  | 3 | grimdark-future-firefight |
  | 4 | age-of-fantasy |
  | 5 | age-of-fantasy-skirmish |
  | 6 | age-of-fantasy-regiments |
  | 7 | age-of-fantasy-quest |
  | 8 | age-of-fantasy-quest-ai |
  | 9 | grimdark-future-star-quest |
  | 10 | grimdark-future-star-quest-ai |

- **One book serves several systems:** the same `uid` works for GF and for Firefight; the `gameSystem` parameter transforms it.
  - Firefight only includes the valid units: Battle Brothers has 13 of 26.
  - Squads come as single models: Battle Brother goes from size 5 and 150 pts to size 1 and 30 pts.
  - Unit `id`s are kept and the structure is the same, so `opr_diff.py` works unchanged.
- **Core rules:** `/api/rules/common/<id>` → `{"rules": [...], "traits": [...]}`.
  - Saved as `_core_rules.json` inside each system of the snapshot.
  - They carry no date or version, so they are always downloaded.
  - They provide the text of Impact, Transport, Blast…, which the books don't include.
  - `traits` are campaign skills and aren't used.
- **Dates and version:** only exist at book level, both in the listing and in the book.
  - `versionString`: version of the book, not of the rules.
  - `editedAt`: last content edit.
  - `modifiedAt`: changes more often and matches the HTTP `Last-Modified` header. There is no `ETag`.
  - There are no dates per unit, nor in the core rules.
- **Volatile ids:** the `id` of upgrade sections changes on every request. Two downloads of the same book are not byte-identical; don't use that field.
- **Pairing books between the two snapshots** with `pair_books`, which runs when generating:
  1. same `uid`;
  2. same `name`;
  3. name similarity ≥ 0.75, preferring pairs with the same `factionId` and `factionRelation`.
  - Many books change `uid` between hosts.
  - Wormhole Daemons of X becomes Daemons of X. There is a new "Wormhole Daemons" book with 0 units that must NOT be paired with them.

## Input files

- Army Forge JSON exports (keys `units`, `upgradePackages`, `specialRules`, `spells`…).
- If `json.load` fails the file is probably truncated (it happened once with a manual copy at 4096 bytes); download it again.
- The book's `versionString` doesn't match the rules version (the stable 3.5.1 books say 3.5.3, the 3.6.0 beta says 3.5.1). Report labels come from the file name or from `--label`; `versionString` is only shown in the header.

## JSON format details that matter

- **Units:**
  - Matched first by `id`, which catches renames such as Attack APC → Attack Vehicle.
  - The rest are matched by exact name (`match_units`). Some books regenerate `id`s: all the Titan Lords … Disciples, part of Alien Hives and Jackals.
- **`specialRules`:**
  - Contains the army's own rules (`coreType: null`) and also the core rules mentioned by spells or by other rules' text (`coreType` set).
  - A core rule entering or leaving that list is NOT a change. Examples: Blast was in 3.5.1 only because of a spell; Artillery, because of the Re-Position Artillery rule.
  - A rule changing type (Shred becomes a core rule) and a core rule changing text are reported.
- **Core rules such as Impact, Transport or Ambush:** have no text in the book. Their description comes from `_core_rules.json` when given (`normalize_book(book, core_rules)` → `rule_text`).
- **Upgrades:**
  - `unit.upgrades` are uids of `upgradePackages` → `sections` → `options`.
  - The cost of an option for a unit is in `option.costs[].unitId`.
  - An option's key is the names of its `gains`.
  - Renamed sections are paired by the options they share.
- **Weapons:** some `label`s already include the count (`2x Walker Fist`). Don't add the `Nx` prefix again.

## Presentation preferences (requested by the maintainer)

- **Language:** all report text and console messages in **English**.
- **System index (`reports/<comparison>/<system>/index.html`):**
  - One row per army with new, removed and changed units, cost and profile changes, rules +/-/~ and spells.
  - Books that only exist on one side are marked with ➕ / ➖.
  - Links to `core_rules.html`.
- **Profile table:** at the top, with Size, Quality, Defense, the cost of each version and Δ. Quality and Defense are shown as `3+`; a higher number is worse, so it goes in red.
- **Changed units:**
  - One table per unit whose column headers are just the version labels (`3.5.1 | 3.6.0`), not "Before/After".
  - One row per changed element.
  - Only the words that change are highlighted.
  - Similar replacements share a row (CCW → Strike). They are paired by similarity ≥ 0.5, ignoring the price.
  - Upgrades are grouped by section, with the options below (↳).
- **Icons:** only ➕ / ➖ / ✏️ for added / removed / changed, no words.
- **HTML:**
  - Red for what is removed and green for what is new, **no strikethrough**.
  - Unchanged text keeps the normal color.
  - Supports light and dark mode.
  - Links use the `--link` / `--link-visited` tokens: dark blue in light mode, light blue in dark mode. The browser's default blue was unreadable in dark mode.
  - In changed special rules and spells, the before and after lines are labelled with the version (`3.5.1:` / `3.6.0:`).
- **Markdown:** uses `~~strikethrough~~` for what is removed and `**bold**` for what is new, since it has no colors.
- **Rules in unit rows:** rules that are added or removed carry their description in the cell.
- **Rule and spell text:** compared ignoring case and punctuation, so a wording-only change (Bane, Defense, Tough) doesn't show up as a change.
