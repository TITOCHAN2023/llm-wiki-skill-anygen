#!/usr/bin/env python3
"""
lint_wiki.py — Health check for an LLM Wiki.

Usage:
    python3 lint_wiki.py <wiki-root>

Example:
    python3 lint_wiki.py <your knowledge path>/wikis/ai-research

Checks:
  1. Banned ../ paths — wiki-internal links must be wiki-root-relative;
     any href containing '../' is a convention violation regardless of whether
     the target file exists.
  2. Banned `wiki/` prefixes — inside wiki/ files, links must start at the
     wiki root itself (`entities/Foo.md`), not include the `wiki/` segment.
  3. Dead links — [text](path.md) whose resolved target doesn't exist
  4. Malformed link URLs — [text](url with spaces.md) that must be wrapped in
     <...> per CommonMark. Silently dropped by strict parsers (including the
     web viewer) — they'd otherwise manifest as phantom orphan / missing-index
     warnings with no hint at the root cause.
  5. Orphan pages — wiki pages with no inbound links
  6. Missing index entries — wiki pages not listed in wiki/index.md
  7. Frequently-missing targets — hrefs referenced 3+ times but no file exists
  8. Residual wikilinks — leftover [[...]] syntax (run migrate_wikilinks.py)
  9. log/ shape — every file matches YYYYMMDD.md and has the right H1
  10. audit/ shape — every audit/*.md parses as a valid AuditEntry
  11. Audit targets — every open audit's `target` file must exist

Link conventions enforced:
  - All intra-wiki links use standard MD syntax: [text](relative/path.md).
  - Inside wiki/ files, paths are wiki-root-relative: concepts/Foo.md,
    entities/Name.md, summaries/slug.md. `../` is banned.
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
# Detects `[text](url with spaces.md)` — URL contains unescaped whitespace but
# is not wrapped in <...>. Strict CommonMark drops these silently (see
# MD_LINK_RE's `bare` branch, which forbids whitespace), producing phantom
# orphan / missing-index warnings downstream. This pattern explicitly hunts
# for the malformed shape so we can flag it with a root-cause message.
MALFORMED_LINK_RE = re.compile(
    r"""
    !?\[[^\]\n]*\]\(
        \s*
        (?!<)                               # skip the legal bracketed form
        (?P<url>
            [^)<>\n]*?                      # URL body (no parens/angles/newlines)
            \s                              # at least one raw whitespace char
            [^)<>\n]*?
            \.md
            (?:\#[^)<>\n]*)?                # optional #anchor
        )
        \s*
    \)
    """,
    re.VERBOSE,
)
# Detects raw `../foo/bar.md` path literals anywhere in the file, including
# malformed markdown links, JSON payloads, code blocks, and prose. This is the
# hard-rule check for "never write file-relative parent hops inside wiki/".
BANNED_PARENT_PATH_RE = re.compile(
    r"""
    (?P<path>
        \.\./
        [^<>"'\n)]*?
        \.md
        (?:\#[^<>"'\n)]*)?
    )
    """,
    re.VERBOSE,
)
# Detects raw absolute filesystem paths to markdown files, e.g.
# `/Users/name/wiki/entities/Foo.md`. These are banned inside wiki/ files;
# paths must be written relative to the wiki/ root instead.
ABSOLUTE_MD_PATH_RE = re.compile(
    r"""
    (?<![A-Za-z0-9._-])
    (?P<path>
        /
        [^<>"'\n)]*?
        \.md
        (?:\#[^<>"'\n)]*)?
    )
    """,
    re.VERBOSE,
)
# Detects raw `wiki/foo/bar.md` path literals inside wiki/ files. These are
# also banned because paths should be rooted at the wiki directory itself, not
# redundantly include the `wiki/` segment.
WIKI_PREFIX_MD_PATH_RE = re.compile(
    r"""
    (?<![A-Za-z0-9._/-])
    (?P<path>
        wiki/
        [^<>"'\n)]*?
        \.md
        (?:\#[^<>"'\n)]*)?
    )
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


def extract_malformed_link_urls(text: str) -> list[tuple[int, str]]:
    """Return every `[text](url)` where the URL has raw whitespace but is not
    wrapped in <...>. Each item is (line_number, offending_url).
    These are CommonMark-invalid and invisible to MD_LINK_RE — we surface them
    so users aren't debugging phantom orphan warnings."""
    out: list[tuple[int, str]] = []
    for m in MALFORMED_LINK_RE.finditer(text):
        url = m.group("url").strip()
        if EXTERNAL_URL_RE.match(url):
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        out.append((line_no, url))
    return out


def extract_banned_parent_paths(text: str) -> list[tuple[int, str]]:
    """Return every raw `../...md` path literal, regardless of context.
    Each item is (line_number, offending_path)."""
    out: list[tuple[int, str]] = []
    for m in BANNED_PARENT_PATH_RE.finditer(text):
        path = (m.group("path") or "").strip()
        if not path:
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        out.append((line_no, path))
    return out


def extract_absolute_md_paths(text: str) -> list[tuple[int, str]]:
    """Return every raw absolute `/...md` path literal.
    External URLs are filtered out elsewhere; this scanner is for filesystem
    paths embedded in markdown, JSON payloads, code blocks, or prose."""
    out: list[tuple[int, str]] = []
    for m in ABSOLUTE_MD_PATH_RE.finditer(text):
        path = (m.group("path") or "").strip()
        if not path or EXTERNAL_URL_RE.match(path):
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        out.append((line_no, path))
    return out


def extract_wiki_prefix_paths(text: str) -> list[tuple[int, str]]:
    """Return every raw `wiki/...md` path literal inside a wiki file."""
    out: list[tuple[int, str]] = []
    for m in WIKI_PREFIX_MD_PATH_RE.finditer(text):
        path = (m.group("path") or "").strip()
        if not path or EXTERNAL_URL_RE.match(path):
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        out.append((line_no, path))
    return out


def resolve_href(wiki_root: Path, href: str) -> Path | None:
    """Resolve `href` from the wiki/ root.
    Returns the resolved absolute Path if the file exists inside wiki/, else None."""
    joined = posixpath.normpath(href)
    if joined.startswith("../") or joined == "..":
        return None
    target = wiki_root / Path(joined)
    if target.exists() and target.is_file():
        return target.resolve()
    return None


def canonicalize_href_from_source(source_abs: Path, wiki_root: Path, href: str) -> str | None:
    """Interpret `href` using file-relative semantics, then convert it to the
    canonical wiki-root-relative form.

    This is only used to produce a helpful fix suggestion and to keep dead-link
    / orphan reporting accurate for broken `../` links."""
    try:
        source_dir_rel = source_abs.parent.relative_to(wiki_root).as_posix()
    except ValueError:
        return None
    joined = posixpath.normpath(posixpath.join(source_dir_rel, href))
    if joined in (".", "..") or joined.startswith("../"):
        return None
    return joined


def canonicalize_absolute_path(wiki_root: Path, abs_path: str) -> str | None:
    """Convert an absolute filesystem markdown path into wiki-root-relative
    form when it points inside `<root>/wiki/`."""
    try:
        p = Path(abs_path).resolve()
    except OSError:
        return None
    wiki_dir = wiki_root.resolve()
    try:
        return p.relative_to(wiki_dir).as_posix()
    except ValueError:
        return None


def canonicalize_wiki_prefixed_path(path: str) -> str | None:
    """Strip the redundant leading `wiki/` from a path literal."""
    if not path.startswith("wiki/"):
        return None
    rel = path[len("wiki/"):]
    return rel or None


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

    # ── Pass 1: banned ../ escape paths ─────────────────────────────────
    escape_links: list[tuple[Path, int, str, str | None]] = []
    absolute_links: list[tuple[Path, int, str, str | None]] = []
    wiki_prefix_links: list[tuple[Path, int, str, str | None]] = []
    # ── Pass 3: dead MD links ──────────────────────────────────────────────
    dead_links: list[tuple[Path, str]] = []
    missing_href_counts: dict[str, int] = defaultdict(int)
    for md_file in all_wiki_files:
        text = md_file.read_text(encoding="utf-8")
        for line_no, path in extract_banned_parent_paths(text):
            suggested = canonicalize_href_from_source(md_file, wiki_path, path)
            escape_links.append((md_file, line_no, path, suggested))
        for line_no, path in extract_absolute_md_paths(text):
            suggested = canonicalize_absolute_path(wiki_path, path)
            absolute_links.append((md_file, line_no, path, suggested))
        for line_no, path in extract_wiki_prefix_paths(text):
            suggested = canonicalize_wiki_prefixed_path(path)
            wiki_prefix_links.append((md_file, line_no, path, suggested))
        for href in extract_md_link_hrefs(text):
            target: Path | None = None
            if "../" in href or href == "..":
                suggested = canonicalize_href_from_source(md_file, wiki_path, href)
                if suggested is not None:
                    target = resolve_href(wiki_path, suggested)
                    if target is None:
                        dead_links.append((md_file, href))
                        missing_href_counts[suggested] += 1
                    elif target in wiki_file_set:
                        inbound[target].append(md_file)
                else:
                    dead_links.append((md_file, href))
                    missing_href_counts[posixpath.normpath(href)] += 1
                continue

            target = resolve_href(wiki_path, href)
            if target is None:
                dead_links.append((md_file, href))
                resolved_key = posixpath.normpath(href)
                missing_href_counts[resolved_key] += 1
            elif target in wiki_file_set:
                inbound[target].append(md_file)

    if escape_links:
        print(f"\n🔴 Banned '../' paths ({len(escape_links)}) — wiki links must be wiki-root-relative:")
        for source, line_no, href, suggested in escape_links:
            if suggested is not None:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: {suggested})")
            else:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: <cannot auto-suggest>)")
        print("   rule: inside wiki/, all paths start from wiki root — never use ../")
        issues += len(escape_links)
    else:
        print("✅ No banned '../' paths")

    if wiki_prefix_links:
        print(f"\n🔴 Banned 'wiki/' prefixes ({len(wiki_prefix_links)}) — wiki links must be wiki-root-relative:")
        for source, line_no, href, suggested in wiki_prefix_links:
            if suggested is not None:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: {suggested})")
            else:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: <cannot auto-suggest>)")
        print("   rule: inside wiki/, never prefix links with 'wiki/'")
        issues += len(wiki_prefix_links)
    else:
        print("✅ No banned 'wiki/' prefixes")

    if absolute_links:
        print(f"\n🔴 Absolute paths ({len(absolute_links)}) — wiki links must be wiki-root-relative:")
        for source, line_no, href, suggested in absolute_links:
            if suggested is not None:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: {suggested})")
            else:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: <cannot auto-suggest>)")
        print("   rule: inside wiki/, never use filesystem-absolute paths")
        issues += len(absolute_links)
    else:
        print("✅ No absolute paths")

    if dead_links:
        print(f"\n🔴 Dead links ({len(dead_links)}):")
        for source, href in dead_links:
            print(f"   {source.relative_to(root_path)} → {href}")
        issues += len(dead_links)
    else:
        print("✅ No dead links")

    # ── Pass 4: malformed link URLs (unquoted whitespace) ──────────────────
    malformed: list[tuple[Path, int, str]] = []
    for md_file in all_wiki_files:
        text = md_file.read_text(encoding="utf-8")
        for line_no, url in extract_malformed_link_urls(text):
            malformed.append((md_file, line_no, url))
    if malformed:
        print(
            f"\n🟡 Malformed link URLs ({len(malformed)}) — whitespace in URL "
            f"but not wrapped in <...>:"
        )
        for source, line_no, url in malformed:
            print(f"   {source.relative_to(root_path)}:{line_no} → ({url})")
        print(
            "   fix: wrap the URL in angle brackets, e.g. "
            "[Page Name](<path with space.md>)"
        )
        issues += len(malformed)
    else:
        print("✅ No malformed link URLs")

    # ── Pass 5: orphan pages ────────────────────────────────────────────────
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

    # ── Pass 6: missing index entries ───────────────────────────────────────
    if index_path.exists():
        index_text = index_path.read_text(encoding="utf-8")
        linked_from_index: set[Path] = set()
        for href in extract_md_link_hrefs(index_text):
            resolved = resolve_href(wiki_path, href)
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

    # ── Pass 7: frequently-missing targets ─────────────────────────────────
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

    # ── Pass 8: residual wikilinks ─────────────────────────────────────────
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

    # ── Pass 9: log/ shape ───────────────────────────────────────────────────
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

    # ── Pass 10: audit/ shape ─────────────────────────────────────────────────
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

    # ── Pass 11: audit targets exist ─────────────────────────────────────────
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
