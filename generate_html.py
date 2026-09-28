#!/usr/bin/env python3
"""Generate HTML change reports comparing two snapshots downloaded by fetch_books.py.

A snapshot is given as SOURCE[@DATE]: "stable" (latest), "stable@2026-09-28", "stable@previous"
(second latest), "beta"… By default: latest stable vs latest beta if there is a beta snapshot,
otherwise stable@previous vs stable.

Writes reports/<old>_vs_<new>/<system>/{index,<army>,core_rules}.html and reports/index.html,
which lists every comparison and game system generated so far.

Usage: python generate_html.py [--old stable@previous] [--new stable] [--system SLUG ...]
                               [--only "Battle Brothers" ...] [--old-label 3.5.1] [--new-label 3.6.0]
"""
import argparse
import html
import json
import sys
from pathlib import Path

from fetch_books import SNAPSHOTS, pair_books, snapshot_dates
from opr_diff import APP_NAME, PROFILE_STATS, build, dict_diff, html_page, html_rules_section, load_book, render_html

CORE_PAGE = "core_rules.html"
SUMMARY_FILE = "summary.json"  # per-system numbers, read back to build reports/index.html
UNCHANGED = "unchanged"  # counts() placeholder for armies without changes


def system_title(slug):
    return slug.replace("-", " ").title().replace(" Ai", " AI").replace(" Of ", " of ")


# --------------------------------------------------------------------------- snapshots

def resolve(data, spec):
    """'stable', 'stable@2026-09-28' or 'stable@previous' -> snapshot dict."""
    source, _, when = spec.partition("@")
    dates = snapshot_dates(data, source)
    if not dates:
        sys.exit(f"ERROR: no snapshots of '{source}' in {data}/{SNAPSHOTS}/; run fetch_books.py --source {source}")
    if not when or when == "latest":
        date = dates[-1]
    elif when == "previous":
        if len(dates) < 2:
            sys.exit(f"ERROR: '{source}' has a single snapshot ({dates[0]}); nothing previous to compare with")
        date = dates[-2]
    elif when in dates:
        date = when
    else:
        sys.exit(f"ERROR: no snapshot {spec}. Available for {source}: {', '.join(dates)}")
    path = Path(data) / SNAPSHOTS / source / date
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    return {"source": source, "date": date, "path": path, "label": meta.get("label"),
            "systems": meta.get("systems", []), "id": f"{source}-{date}"}


def default_specs(data):
    if snapshot_dates(data, "beta"):
        return "stable", "beta"
    return "stable@previous", "stable"


def labels_for(old, new, old_label, new_label):
    """Explicit labels win, then the snapshot's --label, then 'source date'; equal labels get the date."""
    a = old_label or old["label"] or f"{old['source']} {old['date']}"
    b = new_label or new["label"] or f"{new['source']} {new['date']}"
    if a == b:
        a, b = f"{a} ({old['date']})", f"{b} ({new['date']})"
    return a, b


# --------------------------------------------------------------------------- per army

def counts(d):
    """Numbers for the index row of one army."""
    return {
        "added": len(d["units_added"]),
        "removed": len(d["units_removed"]),
        "changed": len(d["units_changed"]),
        "cost": sum(1 for o, n in d["costs"] if o["cost"] != n["cost"]),
        "profile": sum(1 for o, n in d["costs"] if any(o[f] != n[f] for f in PROFILE_STATS)),
        "rules": "+%d / -%d / ~%d" % tuple(map(len, d["rules"][:3])),
        "spells": len(d["spells"][2]) + len(d["spells"][0]) + len(d["spells"][1]),
    }


def has_changes(d):
    added, removed, changed, notes = d["rules"]
    moved = any(v != "core rule" for k, v in notes.items() if k not in changed)
    return bool(d["units_added"] or d["units_removed"] or d["units_changed"]
                or added or removed or changed or moved or any(d["spells"]))


def core_rules(system_dir):
    path = system_dir / "_core_rules.json"
    return load_book(path)["rules"] if path.exists() else None


def load_books(system_dir):
    return json.loads((system_dir / "books.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- rendering

def render_index(title, rows, labels, core_changes, snaps):
    e = html.escape
    old, new = labels
    body = [f"<h1>{e(title)} — changes {e(old)} → {e(new)}</h1>",
            f"<p class='muted'>Army Forge {e(snaps[0]['source'])} snapshot of {e(snaps[0]['date'])} ({e(old)}) "
            f"compared with the {e(snaps[1]['source'])} snapshot of {e(snaps[1]['date'])} ({e(new)}). "
            f"Core rules: <a href='{CORE_PAGE}'>{core_changes} changes</a>.</p>",
            "<div class='scroll'><table>",
            "<tr><th></th><th>Army</th><th>New units</th><th>Removed</th><th>Changed</th>"
            "<th>Cost changes</th><th>Profile changes</th><th>Special rules</th><th>Changed spells</th></tr>"]
    for o, n, slug, c in rows:
        name = (n or o)["name"]
        if o and n and o["name"] != n["name"]:
            name = f"{o['name']} → {n['name']}"
        if c is None:
            kind, icon = ("add", "➕") if n else ("del", "➖")
            where = f"only in {new}" if n else f"only in {old}"
            body.append(f"<tr class='{kind}'><td>{icon}</td><td>{e(name)}</td>"
                        f"<td colspan='7' class='muted'>{e(where)}</td></tr>")
            continue
        if c == UNCHANGED:
            body.append(f"<tr><td></td><td class='muted'>{e(name)}</td>"
                        f"<td colspan='7' class='muted'>no changes</td></tr>")
            continue
        cells = "".join(f"<td class='num'>{c[k] or '–'}</td>"
                        for k in ("added", "removed", "changed", "cost", "profile"))
        body.append(f"<tr><td></td><td><a href='{e(slug)}.html'>{e(name)}</a></td>{cells}"
                    f"<td class='num'>{e(c['rules'])}</td><td class='num'>{c['spells'] or '–'}</td></tr>")
    body.append("</table></div>")
    return html_page(f"{title} {old} → {new}", body, back_link="../../index.html", back_text="← All reports")


def render_core(title, old_rules, new_rules, labels):
    e = html.escape
    to_dict = lambda rules: {r["name"]: r.get("description", "") for r in rules or []}
    entry = dict_diff(to_dict(old_rules), to_dict(new_rules))
    body = [f"<h1>{e(title)} core rules — changes {e(labels[0])} → {e(labels[1])}</h1>",
            f"<p class='muted'>Common rules of {e(title)} (/api/rules/common).</p>",
            *html_rules_section("Core rules", entry, labels)]
    return html_page(f"{title} core rules {labels[0]} → {labels[1]}", body, back_link="index.html"), sum(map(len, entry))


def render_reports_index(summaries):
    e = html.escape
    body = [f"<h1>{APP_NAME}</h1>",
            "<p class='muted'>Change reports for OPR Army Forge books. "
            "Each row compares two Army Forge snapshots of one game system.</p>",
            "<div class='scroll'><table>",
            "<tr><th>Comparison</th><th>Game system</th><th>Armies compared</th><th>With changes</th>"
            "<th>Only in old</th><th>Only in new</th><th>Core rule changes</th></tr>"]
    for s in summaries:
        body.append(f"<tr><td>{e(s['comparison_title'])}</td>"
                    f"<td><a href='{e(s['comparison'])}/{e(s['system'])}/index.html'>{e(s['title'])}</a></td>"
                    + "".join(f"<td class='num'>{s[k] or '–'}</td>"
                              for k in ("compared", "with_changes", "old_only", "new_only", "core_changes"))
                    + "</tr>")
    body.append("</table></div>")
    return html_page(APP_NAME, body)


# --------------------------------------------------------------------------- generation

def generate_system(system, snaps, out, labels, only):
    """Write out/ (one system of one comparison) and return its summary (also saved as summary.json)."""
    title = system_title(system)
    old_dir, new_dir = (s["path"] / system for s in snaps)
    old_books, new_books = load_books(old_dir), load_books(new_dir)
    if only:
        old_books = [b for b in old_books if b["name"].lower() in only]
        new_books = [b for b in new_books if b["name"].lower() in only]
    out.mkdir(parents=True, exist_ok=True)
    core_old, core_new = core_rules(old_dir), core_rules(new_dir)

    rows = []
    for o, n in pair_books(old_books, new_books):
        slug = (n or o)["slug"]
        if not (o and n):
            rows.append((o, n, slug, None))
            continue
        if o["uid"] == n["uid"] and o.get("modifiedAt") == n.get("modifiedAt"):
            rows.append((o, n, slug, UNCHANGED))
            continue
        old_book, new_book = load_book(old_dir / o["file"]), load_book(new_dir / n["file"])
        d = build(old_book, new_book, core_old, core_new)
        if not has_changes(d):
            rows.append((o, n, slug, UNCHANGED))
            continue
        meta = {
            "name": n["name"], "system": new_book.get("gameSystemSlug", system),
            "old": labels[0], "new": labels[1],
            "old_file": f"{snaps[0]['source']} {snaps[0]['date']}: {o['name']}",
            "new_file": f"{snaps[1]['source']} {snaps[1]['date']}: {n['name']}",
            "old_vs": old_book.get("versionString", "?"), "new_vs": new_book.get("versionString", "?"),
            "old_edited": old_book.get("editedAt"), "new_edited": new_book.get("editedAt"),
            "old_modified": old_book.get("modifiedAt"), "new_modified": new_book.get("modifiedAt"),
        }
        (out / f"{slug}.html").write_text(render_html(d, meta, back_link="index.html"), encoding="utf-8")
        rows.append((o, n, slug, counts(d)))

    core_html, core_changes = render_core(title, core_old, core_new, labels)
    (out / CORE_PAGE).write_text(core_html, encoding="utf-8")
    (out / "index.html").write_text(render_index(title, rows, labels, core_changes, snaps), encoding="utf-8")

    summary = {
        "comparison": out.parent.name, "comparison_title": f"{labels[0]} → {labels[1]}",
        "system": system, "title": title,
        "compared": sum(1 for o, n, _, _ in rows if o and n),
        "with_changes": sum(1 for *_, c in rows if c not in (None, UNCHANGED)),
        "old_only": sum(1 for o, n, _, _ in rows if not n),
        "new_only": sum(1 for o, n, _, _ in rows if not o),
        "core_changes": core_changes,
    }
    (out / SUMMARY_FILE).write_text(json.dumps(summary), encoding="utf-8")
    print(f"  {title}: {summary['compared']} armies compared, {summary['with_changes']} with changes"
          f" -> {out / 'index.html'}")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="data directory of fetch_books.py (default: data)")
    ap.add_argument("--out", default="reports", help="output directory (default: reports)")
    ap.add_argument("--old", help="old snapshot, SOURCE[@DATE|@previous]")
    ap.add_argument("--new", help="new snapshot, SOURCE[@DATE|@previous]")
    ap.add_argument("--system", action="append", default=[], metavar="SLUG",
                    help="game system, repeatable (default: every system in both snapshots)")
    ap.add_argument("--old-label", help="header label of the old snapshot (default: its --label, or source + date)")
    ap.add_argument("--new-label", help="header label of the new snapshot")
    ap.add_argument("--only", action="append", default=[], metavar="NAME",
                    help="only this army (case-insensitive); repeatable")
    args = ap.parse_args()

    default_old, default_new = default_specs(args.data)
    snaps = (resolve(args.data, args.old or default_old), resolve(args.data, args.new or default_new))
    if snaps[0]["path"] == snaps[1]["path"]:
        sys.exit(f"ERROR: both sides are the same snapshot ({snaps[0]['id']})")
    common = [s for s in snaps[1]["systems"] if s in snaps[0]["systems"]]
    systems = args.system or common
    missing = [s for s in systems if s not in common]
    if missing:
        sys.exit(f"ERROR: {missing} not in both snapshots. Common systems: {', '.join(common) or 'none'}")

    labels = labels_for(*snaps, args.old_label, args.new_label)
    comparison = Path(args.out) / f"{snaps[0]['id']}_vs_{snaps[1]['id']}"
    print(f"{snaps[0]['id']} ({labels[0]}) vs {snaps[1]['id']} ({labels[1]})")
    only = {n.lower() for n in args.only}
    for system in systems:
        generate_system(system, snaps, comparison / system, labels, only)

    # The top index lists every comparison and system generated so far, newest comparison first.
    summaries = [json.loads(p.read_text(encoding="utf-8")) for p in Path(args.out).glob(f"*/*/{SUMMARY_FILE}")]
    summaries.sort(key=lambda s: s["system"])
    summaries.sort(key=lambda s: s["comparison"], reverse=True)
    (Path(args.out) / "index.html").write_text(render_reports_index(summaries), encoding="utf-8")
    print(f"\nWrote {Path(args.out) / 'index.html'} ({len(summaries)} reports)")


if __name__ == "__main__":
    main()
