#!/usr/bin/env python3
"""
migrate_wikilinks.py — Convert Obsidian-style [[wikilinks]] in a wiki/ tree
into standard MD links [text](relative/path.md).

Usage:
    python3 migrate_wikilinks.py <wiki-root>            # dry-run (default)
    python3 migrate_wikilinks.py <wiki-root> --apply    # actually write

Handles:
    [[Target]]                 → [Target](relative/path.md)
    [[Target|Alias]]           → [Alias](relative/path.md)
    [[folder/Target]]          → [Target](folder/path.md)
    [[folder/Target#Section]]  → [Target](folder/path.md#section)
    [[folder/Target/index|T]]  → [T](folder/Target/index.md)
    [[#Section]]               → [Section](#section)                     (same-file anchor)
    ![[image.png]]             → ![](relative/path.png)                  (best-effort resolve)

Resolution order for a wikilink target:
  1. Exact relative path under wiki/ (with or without `.md` extension).
  2. `<target>/index.md` (folder-split page).
  3. Bare stem, scanned across the whole wiki/. If multiple pages share the
     stem, the link is left unchanged and reported — you must disambiguate.

Paths with spaces are wrapped in angle brackets (`[text](<path with spaces.md>)`)
so CommonMark parsers don't mis-tokenise them.

Links that fail to resolve stay as-is; inspect the warnings and fix manually.
"""

import posixpath
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path


WIKILINK_RE = re.compile(
    r"""
    (?P<bang>!)?                    # optional image embed
    \[\[
        (?P<target>[^\]#|\n]*)      # target path/stem (may be empty for [[#section]])
        (?:\#(?P<section>[^\]|\n]*))?
        (?:\|(?P<alias>[^\]\n]+))?
    \]\]
    """,
    re.VERBOSE,
)


def slug_anchor(text: str) -> str:
    """Approximates markdown-it-anchor / GitHub slug rules."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def build_page_index(
    wiki_dir: Path,
) -> tuple[dict[str, Path], dict[str, list[Path]], list[tuple[str, Path]]]:
    """Three indices:
      - `primary`: full-path keys (with and without `.md`). Always unambiguous.
      - `by_stem`: stem → [paths]. Ambiguous when the list has ≥2 entries.
      - `all_rels`: [(rel_posix, abs_path)]. Used for Obsidian-style suffix match.
    """
    primary: dict[str, Path] = {}
    by_stem: dict[str, list[Path]] = defaultdict(list)
    all_rels: list[tuple[str, Path]] = []
    for p in wiki_dir.rglob("*.md"):
        abs_p = p.resolve()
        rel = p.relative_to(wiki_dir).as_posix()
        primary[rel] = abs_p
        primary[rel[:-3]] = abs_p  # without .md
        by_stem[p.stem].append(abs_p)
        all_rels.append((rel, abs_p))
    return primary, by_stem, all_rels


def lookup_target(
    target: str,
    primary: dict[str, Path],
    by_stem: dict[str, list[Path]],
    all_rels: list[tuple[str, Path]],
    wiki_dir: Path,
) -> tuple[Path | None, str | None]:
    """Returns (resolved_path, error). Error is None if resolved; else a
    short reason string for the warning log."""
    t = target.strip().strip("/")
    if not t:
        return None, "empty target"
    # 1. Exact path under wiki/ with or without .md.
    if t in primary:
        return primary[t], None
    if (t + ".md") in primary:
        return primary[t + ".md"], None
    # 2. Folder-split index.
    for key in (t + "/index", t + "/index.md"):
        if key in primary:
            return primary[key], None
    # 3. Obsidian-style suffix match: any page whose path ends with `t.md` or `t/index.md`.
    needles = (t + ".md", t + "/index.md")
    suffix_matches: list[Path] = []
    for rel, abs_p in all_rels:
        if any(rel == n or rel.endswith("/" + n) for n in needles):
            suffix_matches.append(abs_p)
    if len(suffix_matches) == 1:
        return suffix_matches[0], None
    if len(suffix_matches) > 1:
        rels = ", ".join(str(m.relative_to(wiki_dir)) for m in suffix_matches)
        return None, f"ambiguous suffix (matches: {rels})"
    # 4. Bare stem (only when target has no slash).
    if "/" not in t:
        stem_matches = by_stem.get(t, [])
        if len(stem_matches) == 1:
            return stem_matches[0], None
        if len(stem_matches) > 1:
            rels = ", ".join(str(m.relative_to(wiki_dir)) for m in stem_matches)
            return None, f"ambiguous stem (matches: {rels})"
    return None, "no matching page"


def relpath_posix(target_abs: Path, source_abs: Path) -> str:
    return posixpath.relpath(target_abs.as_posix(), source_abs.parent.as_posix())


def encode_href(path: str) -> str:
    return f"<{path}>" if " " in path else path


def display_from_target(target: str) -> str:
    """`folder/Foo` → `Foo`; `folder/Foo/index` → `Foo`."""
    name = target.rstrip("/").split("/")[-1]
    if name == "index":
        parts = target.rstrip("/").split("/")
        if len(parts) >= 2:
            name = parts[-2]
    return name


def rewrite_text(
    source_abs: Path,
    text: str,
    primary: dict[str, Path],
    by_stem: dict[str, list[Path]],
    all_rels: list[tuple[str, Path]],
    wiki_dir: Path,
) -> tuple[str, list[str], list[str]]:
    resolved: list[str] = []
    unresolved: list[str] = []

    def sub(m: re.Match) -> str:
        bang = m.group("bang") or ""
        raw_target = (m.group("target") or "").strip()
        section = (m.group("section") or "").strip()
        alias = (m.group("alias") or "").strip()
        original = m.group(0)

        # [[#Section]] — same-file anchor. Obsidian treats empty target as current page.
        if not raw_target and section:
            display = alias or section
            return f"[{display}](#{slug_anchor(section)})"

        if not raw_target:
            unresolved.append(f"{original} — empty target")
            return original

        if bang:
            # Image embed. Try source-relative path, then wiki-root-relative.
            for candidate in (
                source_abs.parent / raw_target,
                source_abs.parent.parent.parent / "raw" / "assets" / raw_target,
            ):
                if candidate.exists():
                    rel = relpath_posix(candidate.resolve(), source_abs)
                    resolved.append(f"image {original} → ![{alias}]({rel})")
                    return f"![{alias}]({encode_href(rel)})"
            unresolved.append(f"{original} — image not found")
            return original

        target_path, err = lookup_target(raw_target, primary, by_stem, all_rels, wiki_dir)
        if target_path is None:
            unresolved.append(f"{original} — {err}")
            return original

        rel = relpath_posix(target_path, source_abs)
        anchor = f"#{slug_anchor(section)}" if section else ""
        display = alias or display_from_target(raw_target)
        resolved.append(f"{original} → [{display}]({rel}{anchor})")
        return f"[{display}]({encode_href(rel + anchor)})"

    new_text = WIKILINK_RE.sub(sub, text)
    return new_text, resolved, unresolved


def migrate(root: str, apply: bool) -> int:
    root_path = Path(root).resolve()
    wiki_dir = root_path / "wiki"
    if not wiki_dir.exists():
        print(f"ERROR: wiki/ not found at {wiki_dir}", file=sys.stderr)
        return 1

    primary, by_stem, all_rels = build_page_index(wiki_dir)

    changed_files = 0
    total_rewrites = 0
    total_unresolved = 0

    for md in sorted(wiki_dir.rglob("*.md")):
        md_abs = md.resolve()
        text = md.read_text(encoding="utf-8")
        if "[[" not in text:
            continue
        new_text, resolved, unresolved = rewrite_text(
            md_abs, text, primary, by_stem, all_rels, wiki_dir
        )
        if not resolved and not unresolved:
            continue
        changed_files += 1
        total_rewrites += len(resolved)
        total_unresolved += len(unresolved)
        rel = md.relative_to(root_path)
        print(f"\n── {rel}")
        for r in resolved:
            print(f"  ✓ {r}")
        for u in unresolved:
            print(f"  ⚠️  unresolved: {u}")
        if apply and new_text != text:
            md.write_text(new_text, encoding="utf-8")

    verb = "applied" if apply else "would apply"
    print(f"\n{'─' * 40}")
    print(f"{verb}: {total_rewrites} rewrite(s) across {changed_files} file(s)")
    if total_unresolved:
        print(f"⚠️  {total_unresolved} unresolved wikilink(s) left in place — inspect above")
    if not apply and total_rewrites:
        print("\nDry-run only. Re-run with --apply to write changes.")

    return 0 if total_unresolved == 0 else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    sys.exit(migrate(args[0], apply))
