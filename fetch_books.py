#!/usr/bin/env python3
"""Download every official army book of one or more game systems from Army Forge into a dated snapshot.

Each source (the stable site and its beta) gets its own snapshot per day:
  data/snapshots/<source>/<YYYY-MM-DD>/meta.json                {source, host, date, fetched_at, label, systems}
  data/snapshots/<source>/<YYYY-MM-DD>/<system>/books.json      one entry per book (uid, name, file, modifiedAt…)
  data/snapshots/<source>/<YYYY-MM-DD>/<system>/<army>.json     army books
  data/snapshots/<source>/<YYYY-MM-DD>/<system>/_core_rules.json

Books whose modifiedAt didn't change since the previous snapshot are hardlinked instead of downloaded.
A second run on the same day overwrites the systems it downloads. A new snapshot identical to the
previous one is dropped (use --keep to keep it).

Usage: python fetch_books.py [--source stable|beta|all] [--system SLUG ... | --system all]
                             [--label 3.5.1] [--only "Battle Brothers" ...] [--full] [--keep]
"""
import argparse
import datetime
import difflib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# The book endpoint needs the numeric id (the slug gives a 500); the listing takes the slug.
GAME_SYSTEMS = {
    "grimdark-future": 2,
    "grimdark-future-firefight": 3,
    "age-of-fantasy": 4,
    "age-of-fantasy-skirmish": 5,
    "age-of-fantasy-regiments": 6,
    "age-of-fantasy-quest": 7,
    "age-of-fantasy-quest-ai": 8,
    "grimdark-future-star-quest": 9,
    "grimdark-future-star-quest-ai": 10,
}
HOSTS = {
    "stable": "https://army-forge.onepagerules.com",
    "beta": "https://army-forge-beta.onepagerules.com",
}
SNAPSHOTS = "snapshots"
BOOK_FIELDS = ("uid", "name", "modifiedAt", "editedAt", "versionString", "factionId", "factionRelation")
NAME_SIMILARITY = 0.75  # pairs renamed books, e.g. "Wormhole Daemons of War" -> "Daemons of War"


def get_json(url, retries=2, pause=0.3):
    """Return (parsed, raw_text); parsing validates the download isn't truncated."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "forge-diff"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            time.sleep(pause)
            return data, raw
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == retries:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 * (attempt + 1))


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def snapshot_dates(data, source):
    """Dates (YYYY-MM-DD) of the snapshots of a source, oldest first."""
    root = Path(data) / SNAPSHOTS / source
    return sorted(p.name for p in root.iterdir() if (p / "meta.json").exists()) if root.exists() else []


def pair_books(old, new):
    """Pair book entries by uid, then by exact name, then by similar name.
    Returns [(old_entry | None, new_entry | None)]."""
    pairs = []
    left_o, left_n = list(old), list(new)

    def take(match):
        for o in list(left_o):
            n = next((n for n in left_n if match(o, n)), None)
            if n:
                pairs.append((o, n))
                left_o.remove(o)
                left_n.remove(n)

    take(lambda o, n: o["uid"] == n["uid"])
    take(lambda o, n: o["name"] == n["name"])

    def score(o, n):
        # Same faction and relation (e.g. both "Aspect" of Wormhole Daemons) outweighs a closer name:
        # "Wormhole Daemons of War" is "Daemons of War", not the new parent book "Wormhole Daemons".
        same_faction = o.get("factionId") and o.get("factionId") == n.get("factionId") \
            and o.get("factionRelation") == n.get("factionRelation")
        ratio = difflib.SequenceMatcher(None, o["name"], n["name"]).ratio()
        return ratio, ratio + (0.5 if same_faction else 0)

    scored = sorted(((*score(o, n), i, j) for i, o in enumerate(left_o) for j, n in enumerate(left_n)),
                    key=lambda x: x[1], reverse=True)
    used_o, used_n = set(), set()
    for ratio, _, i, j in scored:
        if ratio >= NAME_SIMILARITY and i not in used_o and j not in used_n:
            pairs.append((left_o[i], left_n[j]))
            used_o.add(i)
            used_n.add(j)
    pairs += [(o, None) for i, o in enumerate(left_o) if i not in used_o]
    pairs += [(None, n) for j, n in enumerate(left_n) if j not in used_n]
    return sorted(pairs, key=lambda p: (p[1] or p[0])["name"].lower())


def previous_books(data, source, today, system):
    """({uid: (entry, path)}, system_dir) from the latest earlier snapshot of this source that has
    the system; ({}, None) if there is none."""
    for date in reversed(snapshot_dates(data, source)):
        if date >= today:
            continue
        system_dir = Path(data) / SNAPSHOTS / source / date / system
        books = system_dir / "books.json"
        if books.exists():
            return {b["uid"]: (b, system_dir / b["file"])
                    for b in json.loads(books.read_text(encoding="utf-8"))}, system_dir
    return {}, None


def reuse(src, dst):
    """Hardlink an unchanged book from the previous snapshot (copy if links aren't possible)."""
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def fetch_system(source, host, system, snapshot, data, today, only, full):
    """Download one game system of one source into snapshot/<system>/.
    Returns (failed URLs, whether anything differs from the previous snapshot)."""
    system_id = GAME_SYSTEMS[system]
    out = snapshot / system
    out.mkdir(parents=True, exist_ok=True)
    listing, _ = get_json(f"{host}/api/army-books?filters=official&gameSystemSlug={system}")
    if only:
        listing = [b for b in listing if b["name"].lower() in only]
    previous, previous_dir = previous_books(data, source, today, system)

    core, raw = get_json(f"{host}/api/rules/common/{system_id}")  # no timestamp: always downloaded
    (out / "_core_rules.json").write_text(raw, encoding="utf-8")
    previous_core = previous_dir / "_core_rules.json" if previous_dir else None
    changed = (previous_dir is None or {b["uid"] for b in listing} != set(previous)
               or not previous_core.exists() or json.loads(previous_core.read_text(encoding="utf-8")) != core)

    books, failed, downloaded, reused = [], [], 0, 0
    slugs = set()
    for entry in sorted(listing, key=lambda b: b["name"].lower()):
        slug = slugify(entry["name"])
        if slug in slugs:
            slug = f"{slug}-{entry['uid']}"
        slugs.add(slug)
        path = out / f"{slug}.json"
        old = previous.get(entry["uid"])
        if not full and old and old[0].get("modifiedAt") == entry.get("modifiedAt") and old[1].exists():
            reuse(old[1], path)
            reused += 1
        else:
            try:
                _, raw = get_json(f"{host}/api/army-books/{entry['uid']}?gameSystem={system_id}")
            except RuntimeError as e:
                failed.append(str(e))
                continue
            path.write_text(raw, encoding="utf-8")
            downloaded += 1
        books.append({**{k: entry.get(k) for k in BOOK_FIELDS}, "slug": slug, "file": path.name})

    (out / "books.json").write_text(json.dumps(books, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {system}: {len(books)} books ({downloaded} downloaded, {reused} unchanged and reused)")
    return failed, changed or downloaded > 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", default=[], choices=[*HOSTS, "all"],
                    help="stable, beta or all (default: all); repeatable")
    ap.add_argument("--system", action="append", default=[], metavar="SLUG",
                    help=f"game system, repeatable, or 'all' (default: grimdark-future). One of: {', '.join(GAME_SYSTEMS)}")
    ap.add_argument("--label", help="version label stored in the snapshot (e.g. 3.5.1), used in report headers")
    ap.add_argument("--only", action="append", default=[], metavar="NAME",
                    help="only this army (case-insensitive); repeatable")
    ap.add_argument("--full", action="store_true", help="download every book, even if unchanged")
    ap.add_argument("--keep", action="store_true",
                    help="keep today's snapshot even if nothing changed since the previous one")
    ap.add_argument("--out", default="data", help="data directory (default: data)")
    ap.add_argument("--stable-host", default=HOSTS["stable"])
    ap.add_argument("--beta-host", default=HOSTS["beta"])
    args = ap.parse_args()

    systems = args.system or ["grimdark-future"]
    if "all" in systems:
        systems = list(GAME_SYSTEMS)
    unknown = [s for s in systems if s not in GAME_SYSTEMS]
    if unknown:
        sys.exit(f"ERROR: unknown game system {unknown}. Valid: {', '.join(GAME_SYSTEMS)}")
    sources = list(HOSTS) if not args.source or "all" in args.source else args.source
    hosts = {"stable": args.stable_host.rstrip("/"), "beta": args.beta_host.rstrip("/")}
    only = {n.lower() for n in args.only}
    today = datetime.date.today().isoformat()

    failed = []
    for source in sources:
        snapshot = Path(args.out) / SNAPSHOTS / source / today
        is_new = not snapshot.exists()
        snapshot.mkdir(parents=True, exist_ok=True)
        meta_path = snapshot / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        print(f"== {source} → {snapshot}")
        changed = False
        for system in systems:
            system_failed, system_changed = fetch_system(source, hosts[source], system, snapshot, args.out,
                                                         today, only, args.full)
            failed += system_failed
            changed |= system_changed
        # A new snapshot identical to the previous one is dropped, so "stable@previous vs stable"
        # always compares the last two versions that actually differ.
        if is_new and not changed and not (only or args.full or args.keep):
            shutil.rmtree(snapshot)
            print(f"  no changes since the previous {source} snapshot; today's snapshot not kept")
            continue
        meta.update({
            "source": source, "host": hosts[source], "date": today,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "label": args.label or meta.get("label"),
            "systems": sorted(set(meta.get("systems", [])) | set(systems)),
        })
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if failed:
        print("ERRORS:\n  " + "\n  ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
