#!/usr/bin/env python3
"""
lint_wiki.py — Health check for an LLM Wiki.

Usage:
    python3 lint_wiki.py <wiki-root>

Example:
    python3 lint_wiki.py ~/wikis/ai-research

Checks:
  1. Dead links — [text](path.md) whose resolved target doesn't exist
  2. Orphan pages — wiki pages with no inbound links
  3. Missing index entries — wiki pages not listed in wiki/index.md
  4. Frequently-missing targets — hrefs referenced 3+ times but no file exists
  5. Residual wikilinks — leftover [[...]] syntax (run migrate_wikilinks.py)
  6. log/ shape — every file matches YYYYMMDD.md and has the right H1
  7. audit/ shape — every audit/*.md parses as a valid AuditEntry
  8. Audit targets — every open audit's `target` file must exist

Link conventions enforced:
  - All intra-wiki links use standard MD syntax: [text](relative/path.md).
  - Paths are resolved against the source file's own directory (POSIX semantics).
  - Paths with spaces may be wrapped in angle brackets: [text](<path with spaces.md>).

Exit codes:
  0 — no issues found
  1 — issues found (printed to stdout)
"""

import posixpath
import re
import sys
from collections import defaultdict
from pathlib import Path


# Matches [text](href) and ![alt](src). href/src may be wrapped in <...>
# (that's how paths containing spaces travel in strict CommonMark).
# We only keep those whose path ends with `.md` (anchor stripped downstream).
MD_LINK_RE = re.compile(
    r"""
    !?\[[^\]\n]*\]\(
        \s*
        (?:
            <(?P<bracketed>[^>\n]+?\.md(?:\#[^>\n]*)?)>
            |
            (?P<bare>[^()\s]+?\.md(?:\#[^()\s]*)?)
        )
        \s*
    \)
    """,
    re.VERBOSE,
)
# Leftover Obsidian-style wikilink — kept only to warn users to migrate.
RESIDUAL_WIKILINK_RE = re.compile(r"\[\[[^\]\n]+?\]\]")
LOG_FILENAME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.md$")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
EXTERNAL_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)

AUDIT_REQUIRED_FIELDS = {
    "id", "target", "target_lines", "anchor_before", "anchor_text",
    "anchor_after", "severity", "author", "source", "created", "status",
}
VALID_SEVERITIES = {"info", "suggest", "warn", "error"}
VALID_STATUSES = {"open", "resolved"}
VALID_SOURCES = {"obsidian-plugin", "web-viewer", "manual"}


def extract_md_link_hrefs(text: str) -> list[str]:
    """Return every MD-link href pointing at a .md file (anchor stripped).
    External URLs (http:, mailto:, etc.) are filtered out."""
    out: list[str] = []
    for m in MD_LINK_RE.finditer(text):
        href = (m.group("bracketed") or m.group("bare") or "").strip()
        if "#" in href:
            href = href.split("#", 1)[0]
        if not href or EXTERNAL_URL_RE.match(href):
            continue
        out.append(href)
    return out


def resolve_href(source_abs: Path, href: str) -> Path | None:
    """Resolve `href` relative to the source file's directory, POSIX-style.
    Returns the resolved absolute Path if the file exists, else None."""
    base = source_abs.parent.as_posix()
    joined = posixpath.normpath(posixpath.join(base, href))
    target = Path(joined)
    if target.exists() and target.is_file():
        return target.resolve()
    return None


def parse_frontmatter(text: str) -> dict | None:
    """Minimal YAML-ish frontmatter parser. Handles the flat key:value fields
    and one-level lists/arrays actually used by audit files. Does not handle
    arbitrary YAML — intentional, to avoid a pyyaml dependency."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    result: dict = {}
    i = 0
    lines = body.split("\n")
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        val = rest.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                parts = [p.strip() for p in inner.split(",")]
                parsed: list = []
                for p in parts:
                    if p.isdigit() or (p.startswith("-") and p[1:].isdigit()):
                        parsed.append(int(p))
                    else:
                        parsed.append(p.strip('"').strip("'"))
                result[key] = parsed
        elif val.startswith('"') and val.endswith('"'):
            result[key] = val[1:-1].replace("\\n", "\n").replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'"):
            result[key] = val[1:-1]
        else:
            result[key] = val
        i += 1
    return result


def lint(root: str) -> int:
    root_path = Path(root).resolve()
    wiki_path = root_path / "wiki"
    log_path = root_path / "log"
    audit_path = root_path / "audit"

    if not wiki_path.exists():
        print(f"ERROR: wiki/ directory not found at {wiki_path}", file=sys.stderr)
        return 1

    all_wiki_files = [p.resolve() for p in wiki_path.rglob("*.md")]
    wiki_file_set = set(all_wiki_files)
    index_path = (wiki_path / "index.md").resolve()

    issues = 0
    inbound: dict[Path, list[Path]] = defaultdict(list)

    # ── Pass 1: dead MD links ──────────────────────────────────────────────
    dead_links: list[tuple[Path, str]] = []
    missing_href_counts: dict[str, int] = defaultdict(int)
    for md_file in all_wiki_files:
        text = md_file.read_text(encoding="utf-8")
        for href in extract_md_link_hrefs(text):
            target = resolve_href(md_file, href)
            if target is None:
                dead_links.append((md_file, href))
                # Normalise for grouping — key on (source-dir + href) resolved path.
                resolved_key = posixpath.normpath(
                    posixpath.join(md_file.parent.as_posix(), href)
                )
                missing_href_counts[resolved_key] += 1
            elif target in wiki_file_set:
                inbound[target].append(md_file)

    if dead_links:
        print(f"\n🔴 Dead links ({len(dead_links)}):")
        for source, href in dead_links:
            print(f"   {source.relative_to(root_path)} → {href}")
        issues += len(dead_links)
    else:
        print("✅ No dead links")

    # ── Pass 2: orphan pages ────────────────────────────────────────────────
    orphans = [
        p for p in all_wiki_files
        if p != index_path and p not in inbound
    ]
    if orphans:
        print(f"\n🟡 Orphan pages ({len(orphans)}) — no inbound links from other wiki pages:")
        for p in orphans:
            print(f"   {p.relative_to(root_path)}")
        issues += len(orphans)
    else:
        print("✅ No orphan pages")

    # ── Pass 3: missing index entries ───────────────────────────────────────
    if index_path.exists():
        index_text = index_path.read_text(encoding="utf-8")
        linked_from_index: set[Path] = set()
        for href in extract_md_link_hrefs(index_text):
            resolved = resolve_href(index_path, href)
            if resolved:
                linked_from_index.add(resolved)
        not_in_index = [
            p for p in all_wiki_files
            if p != index_path and p not in linked_from_index
        ]
        if not_in_index:
            print(f"\n🟡 Pages missing from index.md ({len(not_in_index)}):")
            for p in not_in_index:
                print(f"   {p.relative_to(root_path)}")
            issues += len(not_in_index)
        else:
            print("✅ All pages in index.md")
    else:
        print("⚠️  wiki/index.md not found — skipping index check")

    # ── Pass 4: frequently-missing targets ─────────────────────────────────
    frequent_missing = [
        (key, count) for key, count in missing_href_counts.items() if count >= 3
    ]
    if frequent_missing:
        print(f"\n🟡 Missing link targets referenced 3+ times ({len(frequent_missing)}):")
        for key, count in sorted(frequent_missing, key=lambda x: -x[1]):
            # Print as relative to root when possible for readability.
            try:
                rel = str(Path(key).resolve().relative_to(root_path))
            except ValueError:
                rel = key
            print(f"   {rel} — referenced {count}x")
        issues += len(frequent_missing)
    else:
        print("✅ No frequently-missing targets")

    # ── Pass 5: residual wikilinks ─────────────────────────────────────────
    residual_hits: list[tuple[Path, int]] = []
    for md_file in all_wiki_files:
        text = md_file.read_text(encoding="utf-8")
        hits = len(RESIDUAL_WIKILINK_RE.findall(text))
        if hits:
            residual_hits.append((md_file, hits))
    if residual_hits:
        total = sum(h for _, h in residual_hits)
        print(f"\n🟡 Residual [[wikilinks]] ({total} in {len(residual_hits)} files) — run:")
        print(f"   python3 scripts/migrate_wikilinks.py {root}")
        for p, hits in residual_hits[:10]:
            print(f"   {p.relative_to(root_path)} — {hits} hit(s)")
        if len(residual_hits) > 10:
            print(f"   … and {len(residual_hits) - 10} more file(s)")
        issues += total
    else:
        print("✅ No residual [[wikilinks]]")

    # ── Pass 6: log/ shape ───────────────────────────────────────────────────
    if log_path.exists() and log_path.is_dir():
        log_issues: list[str] = []
        for p in sorted(log_path.iterdir()):
            if p.is_dir():
                continue
            if p.name == ".gitkeep":
                continue
            m = LOG_FILENAME_RE.match(p.name)
            if not m:
                log_issues.append(f"   {p.relative_to(root_path)} — filename doesn't match YYYYMMDD.md")
                continue
            y, mo, d = m.groups()
            iso = f"{y}-{mo}-{d}"
            first_line = p.read_text(encoding="utf-8").splitlines()[:1]
            if not first_line or first_line[0].strip() != f"# {iso}":
                log_issues.append(f"   {p.relative_to(root_path)} — expected H1 '# {iso}'")
        if log_issues:
            print(f"\n🟡 log/ shape issues ({len(log_issues)}):")
            for s in log_issues:
                print(s)
            issues += len(log_issues)
        else:
            print("✅ log/ shape OK")
    else:
        print("⚠️  log/ directory not found — skipping log shape check")

    # ── Pass 7: audit/ shape ─────────────────────────────────────────────────
    audit_targets_to_check: list[tuple[str, str]] = []
    if audit_path.exists() and audit_path.is_dir():
        audit_files = [
            p for p in audit_path.rglob("*.md") if p.name != ".gitkeep"
        ]
        audit_issues: list[str] = []
        for p in audit_files:
            text = p.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            rel = p.relative_to(root_path)
            if fm is None:
                audit_issues.append(f"   {rel} — missing YAML frontmatter")
                continue
            missing = AUDIT_REQUIRED_FIELDS - set(fm.keys())
            if missing:
                audit_issues.append(
                    f"   {rel} — missing fields: {', '.join(sorted(missing))}"
                )
                continue
            if fm["severity"] not in VALID_SEVERITIES:
                audit_issues.append(
                    f"   {rel} — invalid severity '{fm['severity']}' (expected {sorted(VALID_SEVERITIES)})"
                )
            if fm["source"] not in VALID_SOURCES:
                audit_issues.append(
                    f"   {rel} — invalid source '{fm['source']}'"
                )
            expected_status = "resolved" if "resolved" in p.parts else "open"
            if fm["status"] != expected_status:
                audit_issues.append(
                    f"   {rel} — status '{fm['status']}' doesn't match directory (expected '{expected_status}')"
                )
            if fm["status"] == "open":
                audit_targets_to_check.append((fm["id"], fm["target"]))

        if audit_issues:
            print(f"\n🔴 audit/ shape issues ({len(audit_issues)}):")
            for s in audit_issues:
                print(s)
            issues += len(audit_issues)
        else:
            print(f"✅ audit/ shape OK ({len(audit_files)} files)")
    else:
        print("⚠️  audit/ directory not found — skipping audit shape check")

    # ── Pass 8: audit targets exist ──────────────────────────────────────────
    missing_targets: list[tuple[str, str]] = []
    for audit_id, target in audit_targets_to_check:
        target_path = root_path / target
        if not target_path.exists():
            alt = wiki_path / target
            if not alt.exists():
                missing_targets.append((audit_id, target))
    if missing_targets:
        print(f"\n🔴 Open audits with missing target files ({len(missing_targets)}):")
        for audit_id, target in missing_targets:
            print(f"   {audit_id} → {target}")
        issues += len(missing_targets)
    elif audit_targets_to_check:
        print("✅ All open-audit targets exist")

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    if issues == 0:
        print("✅ Wiki is healthy — no issues found")
    else:
        print(f"⚠️  {issues} issue(s) found — review above and fix before next ingest")

    return 0 if issues == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(lint(sys.argv[1]))
