#!/usr/bin/env python3
"""Compare two OPR Army Forge army book exports and write a changelog (Markdown + HTML).

Usage: python opr_diff.py OLD.json NEW.json [-o OUTPUT_BASENAME]
"""
import argparse
import difflib
import html
import json
import re
import sys
from pathlib import Path


# --------------------------------------------------------------------------- loading

def load_book(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: {path} is not valid JSON (truncated file?): {e}")


def version_label(path, book):
    m = re.search(r"(\d+)[_.](\d+)[_.](\d+)", Path(path).stem)
    return ".".join(m.groups()) if m else book.get("versionString", Path(path).stem)


# --------------------------------------------------------------------------- normalization

def rule_label(rule):
    if rule.get("label"):
        return rule["label"]
    rating = rule.get("rating")
    return f"{rule['name']}({rating})" if rating not in (None, "") else rule["name"]


def gain_label(gain):
    if gain.get("type") == "ArmyBookItem":
        inner = ", ".join(rule_label(r) for r in gain.get("content", []))
        label = f"{gain['name']} ({inner})" if inner else gain["name"]
    else:
        label = gain.get("label") or gain["name"]
    count = gain.get("count", 1)
    if not count or count == 1 or re.match(rf"{count}x ", label):
        return label
    return f"{count}x {label}"


def option_cost(option, unit_id):
    for c in option.get("costs", []):
        if c.get("unitId") == unit_id:
            return c.get("cost")
    if option.get("costs"):
        return option["costs"][0].get("cost")
    return option.get("cost")


def unique_key(key, taken):
    if key not in taken:
        return key
    n = 2
    while f"{key} ({n})" in taken:
        n += 1
    return f"{key} ({n})"


def normalize_unit(unit, packages):
    upgrades = {}
    for pkg_uid in unit.get("upgrades", []):
        for section in packages.get(pkg_uid, {}).get("sections", []):
            options = {}
            for opt in section.get("options", []):
                key = " + ".join(g["name"] for g in opt.get("gains", [])) or opt.get("label", "?")
                label = opt.get("label") or " + ".join(gain_label(g) for g in opt["gains"])
                options[unique_key(key, options)] = f"{label} · {fmt_cost(option_cost(opt, unit['id']))}"
            upgrades[unique_key(section["label"], upgrades)] = options

    def keyed(entries, label):
        out = {}
        for x in entries:
            out[unique_key(x["name"], out)] = label(x)
        return out

    return {
        "name": unit["name"],
        "cost": unit.get("cost"),
        "quality": unit.get("quality"),
        "defense": unit.get("defense"),
        "size": unit.get("size"),
        "rules": keyed(unit.get("rules", []), rule_label),
        "weapons": keyed(unit.get("weapons", []), gain_label),
        "items": keyed(unit.get("items", []), gain_label),
        "upgrades": upgrades,
    }


def normalize_book(book, core_rules=None):
    """core_rules: the "rules" list of /api/rules/common/<system>, used only for rule descriptions
    (Impact, Transport… have no text in the army book)."""
    packages = {p["uid"]: p for p in book.get("upgradePackages", [])}
    rules = {r["name"]: r.get("description", "") for r in book.get("specialRules", [])}
    return {
        "rule_text": {**{r["name"]: r.get("description", "") for r in core_rules or []}, **rules},
        # The book's rule list also carries the core rules its own rules/spells mention (coreType set),
        # so those appearing or disappearing is not a change to the army.
        "core_rules": {r["name"] for r in book.get("specialRules", []) if r.get("coreType") is not None},
        "units": {u["id"]: normalize_unit(u, packages) for u in book.get("units", [])},
        "order": [u["id"] for u in book.get("units", [])],
        "rules": rules,
        "spells": {
            s["name"]: f"({s.get('threshold')}) {s.get('effect', '')}" for s in book.get("spells", [])
        },
    }


# --------------------------------------------------------------------------- diffing
#
# A unit diff is a list of rows (kind, what, old_segments, new_segments):
#   kind      add / del / mod, or "" for an unchanged upgrade section header
#   segments  [(style, text)] with style None (plain), "del", "add" or "note" (muted extra text);
#             an empty list renders as "—".

def fmt_stat(field, value):
    if value is None:
        return "-"
    return f"{value}+" if field in ("quality", "defense") else str(value)


def fmt_cost(cost):
    if cost is None:
        return "?"
    return "free" if cost == 0 else f"+{cost} pts"


def text_key(text):
    """Comparison key ignoring case, punctuation and whitespace (pure rewording is not a change)."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def word_segments(old, new):
    """Word-level diff of two strings -> (old_segments, new_segments)."""
    a, b = old.split(), new.split()
    seg_a, seg_b = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            seg_a.append((None, " ".join(a[i1:i2])))
            seg_b.append((None, " ".join(b[j1:j2])))
            continue
        if i2 > i1:
            seg_a.append(("del", " ".join(a[i1:i2])))
        if j2 > j1:
            seg_b.append(("add", " ".join(b[j1:j2])))
    return seg_a, seg_b


def row(kind, what, old, new, old_note="", new_note=""):
    if kind == "add":
        segs = ([], [("add", new)])
    elif kind == "del":
        segs = ([("del", old)], [])
    elif kind == "mod":
        segs = word_segments(old, new)
    else:
        segs = ([(None, old)], [(None, new)])
    if old_note:
        segs[0].append(("note", old_note))
    if new_note:
        segs[1].append(("note", new_note))
    return (kind, what, *segs)


def keyed_rows(old, new, pair=False):
    """Changed entries of two {key: label} dicts -> [(kind, old_label, new_label)], new order first.
    With pair=True, removed and added entries with similar text are shown as replacements
    (5x CCW (A1) -> 5x Strike (A1)); unrelated ones stay as separate add/del rows."""
    removed = [k for k in old if k not in new]
    added = [k for k in new if k not in old]
    replaced = {}
    if pair:
        def words(label):  # compare profiles only, not the " · +N pts" option cost
            return label.split(" · ")[0].split()
        scored = sorted(((difflib.SequenceMatcher(None, words(old[r]), words(new[a])).ratio(), a, r)
                         for a in added for r in removed), reverse=True)
        for score, a, r in scored:
            if score >= 0.5 and a not in replaced and r not in replaced.values():
                replaced[a] = r
    out = []
    for k in new:
        if k in replaced:
            out.append(("mod", old[replaced[k]], new[k]))
        elif k not in old:
            out.append(("add", None, new[k]))
        elif old[k] != new[k]:
            out.append(("mod", old[k], new[k]))
    return out + [("del", old[k], None) for k in removed if k not in replaced.values()]


def pair_sections(old, new):
    """Pair upgrade sections by label, then leftover ones by shared options (renamed sections).
    Returns [(old_label | None, new_label | None)] in new order, removed sections last."""
    pairs = {n: n for n in new if n in old}
    left_old = [o for o in old if o not in new]
    scored = []
    for n in (n for n in new if n not in old):
        for o in left_old:
            shared = len(old[o].keys() & new[n].keys())
            ratio = difflib.SequenceMatcher(None, o, n).ratio()
            if shared or ratio > 0.6:
                scored.append((shared, ratio, o, n))
    used = set()
    for _, _, o, n in sorted(scored, reverse=True):
        if o not in used and n not in pairs:
            pairs[n] = o
            used.add(o)
    result = [(pairs.get(n), n) for n in new]
    return result + [(o, None) for o in old if o not in new and o not in used]


def diff_unit(old, new, old_rules, new_rules):
    rows = []
    if old["name"] != new["name"]:
        rows.append(row("mod", "Name", old["name"], new["name"]))
    if old["cost"] != new["cost"]:
        rows.append(row("mod", "Cost", f"{old['cost']} pts", f"{new['cost']} pts"))
    for field, label in (("size", "Size"), ("quality", "Quality"), ("defense", "Defense")):
        if old[field] != new[field]:
            rows.append(row("mod", label, fmt_stat(field, old[field]), fmt_stat(field, new[field])))

    for field, label in (("rules", "Rule"), ("weapons", "Weapon"), ("items", "Item")):
        for kind, a, b in keyed_rows(old[field], new[field], pair=field != "rules"):
            notes = {}
            if field == "rules" and kind in ("add", "del"):
                name = re.sub(r"\(.*\)$", "", a or b).strip()
                side = "old_note" if kind == "del" else "new_note"
                notes[side] = (old_rules if kind == "del" else new_rules).get(name, "")
            rows.append(row(kind, label, a, b, **notes))

    for o_sec, n_sec in pair_sections(old["upgrades"], new["upgrades"]):
        opts = keyed_rows(old["upgrades"].get(o_sec, {}), new["upgrades"].get(n_sec, {}), pair=True)
        if not opts and o_sec == n_sec:
            continue
        sec_kind = "add" if o_sec is None else "del" if n_sec is None else "mod" if o_sec != n_sec else ""
        rows.append(row(sec_kind, "Upgrade", o_sec, n_sec))
        rows += [row(kind, "↳", a, b) for kind, a, b in opts]
    return rows


def dict_diff(old, new):
    added = {k: new[k] for k in sorted(new.keys() - old.keys())}
    removed = {k: old[k] for k in sorted(old.keys() - new.keys())}
    changed = {k: (old[k], new[k]) for k in sorted(old.keys() & new.keys()) if text_key(old[k]) != text_key(new[k])}
    return added, removed, changed


def rules_diff(old, new):
    """Like dict_diff, ignoring core rules entering/leaving the list (they're only there because the
    book mentions them, e.g. Blast in a 3.5.1 spell). Returns (added, removed, changed, notes) where
    notes maps a rule name to a tag: core rule, or army rule that became a core rule and vice versa."""
    added, removed, changed = dict_diff(old["rules"], new["rules"])
    oc, nc = old["core_rules"], new["core_rules"]
    notes = {}
    for k in old["rules"].keys() & new["rules"].keys():
        if k in nc and k not in oc:
            notes[k] = "becomes a core rule"
        elif k in oc and k not in nc:
            notes[k] = "no longer a core rule"
        elif k in nc:
            notes[k] = "core rule"
    return ({k: v for k, v in added.items() if k not in nc},
            {k: v for k, v in removed.items() if k not in oc}, changed, notes)


def build(old_book, new_book, core_old=None, core_new=None):
    return compute_diff(normalize_book(old_book, core_old), normalize_book(new_book, core_new))


def match_units(old, new):
    """{new_id: old_id}: same id first (stable in most books, and catches renames), then the
    leftovers by exact name (some books regenerate ids, e.g. the Titan Lords Disciples)."""
    ou, nu = old["units"], new["units"]
    matched = {uid: uid for uid in new["order"] if uid in ou}
    by_name = {ou[uid]["name"]: uid for uid in old["order"] if uid not in matched.values()}
    for uid in new["order"]:
        if uid not in matched and nu[uid]["name"] in by_name:
            matched[uid] = by_name.pop(nu[uid]["name"])
    return matched


def compute_diff(old, new):
    ou, nu = old["units"], new["units"]
    matched = match_units(old, new)
    common = [(matched[uid], uid) for uid in new["order"] if uid in matched]
    unit_rows = {n: diff_unit(ou[o], nu[n], old["rule_text"], new["rule_text"]) for o, n in common}
    return {
        "units_added": [nu[uid] for uid in new["order"] if uid not in matched],
        "units_removed": [ou[uid] for uid in old["order"] if uid not in matched.values()],
        "units_changed": [(ou[o], nu[n], unit_rows[n]) for o, n in common if unit_rows[n]],
        "costs": [(ou[o], nu[n]) for o, n in common],
        "rules": rules_diff(old, new),
        "spells": dict_diff(old["spells"], new["spells"]),
    }


# --------------------------------------------------------------------------- rendering

APP_NAME = "Forge Diff"
DISCLAIMER = f"{APP_NAME} — unofficial change reports for OPR Army Forge books. Not affiliated with OnePageRules."
MARK = {"add": "➕", "del": "➖", "mod": "✏️", "": ""}
PROFILE_STATS = ("size", "quality", "defense")


def unit_summary(u):
    return (f"{u['size']}x Q{u['quality']}+ D{u['defense']}+ — {u['cost']} pts. "
            f"Rules: {', '.join(u['rules'].values()) or '-'}. Weapons: {', '.join(u['weapons'].values()) or '-'}")


def unit_header(o, n):
    """One-line profile under each unit heading."""
    stats = [f"{o['cost']} → {n['cost']} pts ({n['cost'] - o['cost']:+d})" if o["cost"] != n["cost"] else f"{n['cost']} pts"]
    for field, label in (("size", "Size"), ("quality", "Quality"), ("defense", "Defense")):
        a, b = fmt_stat(field, o[field]), fmt_stat(field, n[field])
        stats.append(f"{label} {a} → {b}" if a != b else f"{label} {b}")
    return " · ".join(stats)


def stat_cell(old, new, field, fmt):
    """Profile cell: 'X' when unchanged, 'X → Y' otherwise; fmt(text, css_class) styles it.
    Quality/Defense are rolls (lower is better), so an increase is marked as a nerf."""
    a, b = fmt_stat(field, old[field]), fmt_stat(field, new[field])
    if old[field] == new[field]:
        return fmt(a, "")
    if field == "size":
        cls = "mod"
    else:
        cls = "del" if new[field] > old[field] else "add"
    return fmt(f"{a} → {b}", cls)


def book_info(meta, side):
    """'versionString 3.5.3, edited 2026-06-18, modified 2026-09-11' (dates only when meta has them)."""
    parts = [f"versionString {meta[side + '_vs']}"]
    for key, label in (("edited", "edited"), ("modified", "modified")):
        if meta.get(f"{side}_{key}"):
            parts.append(f"{label} {meta[side + '_' + key][:10]}")
    return ", ".join(parts)


def md_escape(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def md_segments(segs):
    if not segs:
        return "—"
    out = []
    for style, text in segs:
        t = md_escape(text)
        out.append({"del": f"~~{t}~~", "add": f"**{t}**", "note": f"<br>*{t}*"}.get(style, t))
    return " ".join(out).replace(" <br>", "<br>")


def html_segments(segs):
    if not segs:
        return "<span class='muted'>—</span>"
    e = html.escape
    out = []
    for style, text in segs:
        out.append({"del": f"<del class='del'>{e(text)}</del>", "add": f"<ins class='add'>{e(text)}</ins>",
                    "note": f"<br><span class='muted'>{e(text)}</span>"}.get(style, e(text)))
    return " ".join(out).replace(" <br>", "<br>")


def render_md(d, meta):
    out = [f"# {meta['name']} — changes {meta['old']} → {meta['new']}", ""]
    out += [f"*{meta['system']}*. Files: `{meta['old_file']}` ({book_info(meta, 'old')}) → "
            f"`{meta['new_file']}` ({book_info(meta, 'new')}).", ""]
    out += summary_lines(d, lambda s: f"- {s}") + [""]
    out += ["Legend: ➕ added · ➖ removed · ✏️ changed · ~~strikethrough~~ = before · **bold** = after", ""]

    out += ["## Unit profiles", "",
            f"| Unit | Size | Quality | Defense | Cost {meta['old']} | Cost {meta['new']} | Δ |",
            "|---|:---:|:---:|:---:|---:|---:|---:|"]
    for o, n in d["costs"]:
        delta = n["cost"] - o["cost"]
        name = n["name"] if o["name"] == n["name"] else f"{o['name']} → {n['name']}"
        stats = " | ".join(stat_cell(o, n, f, lambda t, cls: f"**{t}**" if cls else t) for f in PROFILE_STATS)
        out.append(f"| {md_escape(name)} | {stats} | {o['cost']} | {n['cost']} | {'**%+d**' % delta if delta else '='} |")
    out.append("")

    if d["units_added"]:
        out += ["## New units", ""] + [f"- **{u['name']}** — {md_escape(unit_summary(u))}" for u in d["units_added"]] + [""]
    if d["units_removed"]:
        out += ["## Removed units", ""] + [f"- **{u['name']}** — {md_escape(unit_summary(u))}" for u in d["units_removed"]] + [""]

    out += ["## Changed units", ""]
    if not d["units_changed"]:
        out += ["No changes.", ""]
    for o, n, rows in d["units_changed"]:
        out += [f"### {n['name']}", "", unit_header(o, n), "",
                f"| | | {meta['old']} | {meta['new']} |", "|---|---|---|---|"]
        for kind, what, a, b in rows:
            if what == "Upgrade":
                out.append(f"| {MARK[kind]} | **Upgrade** | {md_segments(a)} | {md_segments(b)} |")
            else:
                out.append(f"| {MARK[kind]} | {what} | {md_segments(a)} | {md_segments(b)} |")
        out.append("")

    for title, (added, removed, changed, *notes) in (("Special rules", d["rules"]), ("Spells", d["spells"])):
        notes = notes[0] if notes else {}
        moved = {k: v for k, v in notes.items() if k not in changed and v != "core rule"}
        out += [f"## {title}", ""]
        if not (added or removed or changed or moved):
            out += ["No changes.", ""]
            continue
        out += [f"- ➕ **{k}**: {md_escape(v)}" for k, v in added.items()]
        out += [f"- ➖ **{k}**: {md_escape(v)}" for k, v in removed.items()]
        for k, (a, b) in changed.items():
            a, b = word_segments(a, b)
            tag = f" *({notes[k]})*" if k in notes else ""
            out += [f"- ✏️ **{k}**{tag}", f"  - {meta['old']}: {md_segments(a)}", f"  - {meta['new']}: {md_segments(b)}"]
        out += [f"- ✏️ **{k}** *({v})*, same text" for k, v in moved.items()]
        out.append("")
    out += ["---", "", f"*{DISCLAIMER}*", ""]
    return "\n".join(out)


def summary_lines(d, fmt):
    cost_changes = sum(1 for o, n in d["costs"] if o["cost"] != n["cost"])
    profile = {f: sum(1 for o, n in d["costs"] if o[f] != n[f]) for f in PROFILE_STATS}
    return [fmt(s) for s in (
        f"New units: {len(d['units_added'])}",
        f"Removed units: {len(d['units_removed'])}",
        f"Changed units: {len(d['units_changed'])} ({cost_changes} with cost changes)",
        f"Profile changes: Quality {profile['quality']}, Defense {profile['defense']}, Size {profile['size']}",
        "Special rules: +%d / -%d / ~%d" % tuple(map(len, d["rules"][:3])),
        "Spells: +%d / -%d / ~%d" % tuple(map(len, d["spells"])),
    )]


HTML_STYLE = """
:root{--bg:#fafaf8;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--add:#e5f4e8;--add-fg:#1f6b34;
--del:#fbe6e4;--del-fg:#a3261b;--mod:#fdf3dc;--mod-fg:#8a5a00;--card:#fff;--sec:#f0efea;--link:#1a5fb4;--link-visited:#6a3fb0}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#9c9b95;--line:#34332f;
--add:#173322;--add-fg:#8fd6a4;--del:#3b1a17;--del-fg:#f0a39b;--mod:#382c12;--mod-fg:#f0cd7a;--card:#1f1f1d;--sec:#2a2a27;--link:#8cb8ff;--link-visited:#c4a8ff}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px}
main{max-width:1100px;margin:auto}h1{font-size:1.6rem;margin:0 0 4px}h2{margin-top:2.2rem;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{margin:1.8rem 0 .1rem}p.stats{margin:0 0 .5rem;color:var(--muted)}.muted{color:var(--muted);font-size:13px}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--card);font-size:14px}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}td.num{text-align:right;font-variant-numeric:tabular-nums}
table.unit td:first-child{width:1.6em;text-align:center}table.unit td:nth-child(2){width:5.5em;color:var(--muted)}
table.unit td:nth-child(3),table.unit td:nth-child(4){width:45%}
tr.add td{background:var(--add)}tr.del td{background:var(--del)}tr.mod td{background:var(--mod)}
tr.sec td{font-weight:600;border-top:2px solid var(--muted)}tr.sec:not(.add):not(.del):not(.mod) td{background:var(--sec)}
td.sub{text-align:right}
.add{color:var(--add-fg)}.del{color:var(--del-fg)}.mod{color:var(--mod-fg)}b.add,b.del,b.mod,ins,del{font-weight:600}ins,del{text-decoration:none}
tr.add,tr.del,tr.mod{color:inherit}
a{color:var(--link);text-underline-offset:2px}a:visited{color:var(--link-visited)}a:hover{text-decoration-thickness:2px}
ul.summary{background:var(--card);border:1px solid var(--line);padding:12px 12px 12px 32px}
footer{margin-top:3rem;padding-top:.8rem;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
"""


def render_html(d, meta, back_link=None):
    return html_page(f"{meta['name']} {meta['old']} → {meta['new']}", html_body(d, meta), back_link)


def html_body(d, meta):
    e = html.escape
    out = [f"<h1>{e(meta['name'])} — changes {e(meta['old'])} → {e(meta['new'])}</h1>",
           f"<p class='muted'>{e(meta['system'])}. <code>{e(meta['old_file'])}</code> ({e(book_info(meta, 'old'))}) → "
           f"<code>{e(meta['new_file'])}</code> ({e(book_info(meta, 'new'))})</p>",
           "<ul class='summary'>", *summary_lines(d, lambda s: f"<li>{e(s)}</li>"), "</ul>"]

    out += ["<h2>Unit profiles</h2><div class='scroll'><table>",
            f"<tr><th>Unit</th><th>Size</th><th>Quality</th><th>Defense</th>"
            f"<th>Cost {e(meta['old'])}</th><th>Cost {e(meta['new'])}</th><th>Δ</th></tr>"]
    for o, n in d["costs"]:
        delta = n["cost"] - o["cost"]
        cls = "" if not delta else ("del" if delta > 0 else "add")
        name = n["name"] if o["name"] == n["name"] else f"{o['name']} → {n['name']}"
        stats = "".join(f"<td class='num'>{stat_cell(o, n, f, lambda t, c: f'<b class={c}>{e(t)}</b>' if c else e(t))}</td>"
                        for f in PROFILE_STATS)
        out.append(f"<tr><td>{e(name)}</td>{stats}<td class='num'>{o['cost']}</td><td class='num'>{n['cost']}</td>"
                   f"<td class='num'><b class='{cls}'>{'%+d' % delta if delta else '='}</b></td></tr>")
    out.append("</table></div>")

    for title, units, kind in (("New units", d["units_added"], "add"), ("Removed units", d["units_removed"], "del")):
        if units:
            out += [f"<h2>{title}</h2><div class='scroll'><table>"]
            out += [f"<tr class='{kind}'><td><b>{e(u['name'])}</b></td><td>{e(unit_summary(u))}</td></tr>" for u in units]
            out.append("</table></div>")

    out.append("<h2>Changed units</h2>")
    if not d["units_changed"]:
        out.append("<p>No changes.</p>")
    for o, n, rows in d["units_changed"]:
        out += [f"<h3>{e(n['name'])}</h3><p class='stats'>{e(unit_header(o, n))}</p>",
                "<div class='scroll'><table class='unit'>",
                f"<tr><th></th><th></th><th>{e(meta['old'])}</th><th>{e(meta['new'])}</th></tr>"]
        for kind, what, a, b in rows:
            cls = " ".join(filter(None, [kind, "sec" if what == "Upgrade" else ""]))
            sub = " class='sub'" if what == "↳" else ""
            out.append(f"<tr class='{cls}'><td>{MARK[kind]}</td><td{sub}>{e(what)}</td>"
                       f"<td>{html_segments(a)}</td><td>{html_segments(b)}</td></tr>")
        out.append("</table></div>")

    for title, entry in (("Special rules", d["rules"]), ("Spells", d["spells"])):
        out += html_rules_section(title, entry, (meta["old"], meta["new"]))
    return out


def html_page(title, body, back_link=None, back_text="← All armies"):
    e = html.escape
    back = f"<p><a href='{e(back_link)}'>{e(back_text)}</a></p>" if back_link else ""
    tab = title if title == APP_NAME else f"{title} · {APP_NAME}"
    return "\n".join([f"<title>{e(tab)}</title>",
                      f"<meta charset='utf-8'><style>{HTML_STYLE}</style><main>{back}", *body,
                      f"<footer>{e(DISCLAIMER)}</footer></main>"])


def html_rules_section(title, entry, labels=("Before", "After")):
    """<h2> + table for a dict_diff/rules_diff result: (added, removed, changed[, notes])."""
    e = html.escape
    added, removed, changed, *notes = entry
    notes = notes[0] if notes else {}
    moved = {k: v for k, v in notes.items() if k not in changed and v != "core rule"}
    out = [f"<h2>{title}</h2>"]
    if not (added or removed or changed or moved):
        return out + ["<p>No changes.</p>"]
    out.append("<div class='scroll'><table><tr><th></th><th>Name</th><th>Text</th></tr>")
    out += [f"<tr class='add'><td>➕</td><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in added.items()]
    out += [f"<tr class='del'><td>➖</td><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in removed.items()]
    for k, (a, b) in changed.items():
        a, b = word_segments(a, b)
        tag = f"<br><span class='muted'>{e(notes[k])}</span>" if k in notes else ""
        out.append(f"<tr class='mod'><td>✏️</td><td>{e(k)}{tag}</td><td><span class='del'>{e(labels[0])}:</span> {html_segments(a)}<br>"
                   f"<span class='add'>{e(labels[1])}:</span> {html_segments(b)}</td></tr>")
    out += [f"<tr class='mod'><td>✏️</td><td>{e(k)}</td><td>{e(v)}, same text</td></tr>" for k, v in moved.items()]
    return out + ["</table></div>"]


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("-o", "--output", help="output basename (default: CHANGES_<old>_vs_<new>)")
    ap.add_argument("--core-old", help="_core_rules.json of the old version (descriptions of core rules)")
    ap.add_argument("--core-new", help="_core_rules.json of the new version")
    args = ap.parse_args()

    old_book, new_book = load_book(args.old), load_book(args.new)
    meta = {
        "name": new_book.get("name", "Army book"),
        "system": new_book.get("gameSystemSlug", ""),
        "old": version_label(args.old, old_book),
        "new": version_label(args.new, new_book),
        "old_file": Path(args.old).name, "new_file": Path(args.new).name,
        "old_vs": old_book.get("versionString", "?"), "new_vs": new_book.get("versionString", "?"),
        "old_edited": old_book.get("editedAt"), "new_edited": new_book.get("editedAt"),
        "old_modified": old_book.get("modifiedAt"), "new_modified": new_book.get("modifiedAt"),
    }
    core_old = load_book(args.core_old)["rules"] if args.core_old else None
    core_new = load_book(args.core_new)["rules"] if args.core_new else None
    diff = build(old_book, new_book, core_old, core_new)

    base = args.output or f"CHANGES_{meta['old']}_vs_{meta['new']}"
    Path(f"{base}.md").write_text(render_md(diff, meta), encoding="utf-8")
    Path(f"{base}.html").write_text(render_html(diff, meta), encoding="utf-8")
    print(f"Wrote {base}.md and {base}.html")
    print("\n".join(summary_lines(diff, lambda s: f"  {s}")))


if __name__ == "__main__":
    main()
