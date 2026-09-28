# Forge Diff

Unofficial change reports for [OPR Army Forge](https://army-forge.onepagerules.com) army books.

Forge Diff downloads the official army books of every game system from Army Forge, keeps dated
snapshots of them and generates HTML reports of what changed between two snapshots: unit costs and
profiles, weapons, upgrades, special rules, spells and core rules. It compares:

- the **stable** site against the **beta** (while there is one), and
- the stable site against its **previous snapshot**, so every update gets its own changelog.

> Forge Diff is a fan project. It is not affiliated with or endorsed by OnePageRules.

## How it works

| Script | What it does |
|---|---|
| `fetch_books.py` | Downloads a dated snapshot (`data/snapshots/<source>/<YYYY-MM-DD>/`) per source. Books whose `modifiedAt` didn't change are reused, and a snapshot identical to the previous one is dropped. |
| `generate_html.py` | Compares two snapshots and writes `reports/<old>_vs_<new>/<system>/`, plus `reports/index.html` listing every report. |
| `opr_diff.py` | The diff and rendering engine. Also works on its own with two army book JSON files. |

Only the Python 3 standard library is needed.

## Local usage

```sh
python fetch_books.py --system all                  # stable + beta, every game system
python generate_html.py                             # latest stable vs latest beta
python generate_html.py --old stable@previous --new stable
python generate_html.py --old stable@2026-09-28 --new stable --system grimdark-future
```

Then open `reports/index.html`. Useful options:

- `fetch_books.py`
  - `--source stable|beta|all`
  - `--system SLUG|all`
  - `--label 3.6.1`: name of the version, shown in report headers
  - `--only "Battle Brothers"`
  - `--full`: download everything, even unchanged books
  - `--keep`: keep today's snapshot even if nothing changed
- `generate_html.py`
  - `--old` / `--new SOURCE[@YYYY-MM-DD|@previous]`
  - `--old-label` / `--new-label`
  - `--system`, `--only`

Game systems: `grimdark-future`, `grimdark-future-firefight`, `grimdark-future-star-quest`,
`grimdark-future-star-quest-ai`, `age-of-fantasy`, `age-of-fantasy-skirmish`,
`age-of-fantasy-regiments`, `age-of-fantasy-quest`, `age-of-fantasy-quest-ai`.

## Publishing with GitHub Pages

The workflow in `.github/workflows/update.yml` runs every day (and on demand from the Actions tab):

1. Restores the previous snapshots from the `snapshots` branch.
2. Fetches the stable site and the beta. If the beta is gone, it carries on with stable only.
3. Generates stable vs beta, and stable vs its previous snapshot.
4. Commits the new snapshots and reports to the `snapshots` branch.
5. Deploys the reports to GitHub Pages.

Setup:

1. **Settings → Pages → Build and deployment → Source:** choose *GitHub Actions*.
2. **Settings → Actions → General → Workflow permissions:** choose *Read and write permissions*.
3. Optional, under **Settings → Secrets and variables → Actions → Variables:** set `STABLE_LABEL` and
   `BETA_LABEL` (e.g. `3.5.1` and `3.6.0`) to show version numbers instead of dates in the stable vs
   beta headers.
4. Run the workflow once from the **Actions** tab. The first run downloads everything, which takes
   about 10–20 minutes.
