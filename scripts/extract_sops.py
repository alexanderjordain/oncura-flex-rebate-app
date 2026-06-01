"""Extract SOP content from the Oncura Accounting Master Reference docx.

Used during development to ground app behavior in the canonical SOPs without
having to scroll through the 200-page Word doc. Reproducible and self-documenting
so the next operator can run the same lookups.

Default doc path:
  C:\\Users\\AlexanderJordain\\OneDrive - Oncura Partners\\Attachments\\
    Oncura_Accounting_Master_Reference-5-28-26.docx

Usage examples (run from the repo root):

  # List every SOP section header in the document
  python scripts/extract_sops.py --list

  # Dump the full content of a specific SOP (matches Cash SOP-9, Accounting SOP-11, etc.)
  python scripts/extract_sops.py --sop "Cash SOP-9"
  python scripts/extract_sops.py --sop "Accounting SOP-11"

  # Search for any paragraph mentioning a keyword (case-insensitive)
  python scripts/extract_sops.py --search "overage cutoff"
  python scripts/extract_sops.py --search "credit memo retraction"

  # Override the docx path (e.g., when Marty publishes a new version)
  python scripts/extract_sops.py --list --file "C:\\path\\to\\new.docx"

Requires `python-docx` (pip install python-docx). Not in requirements.txt because
this is a dev-only tool, not something the deployed app needs.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_DOC = Path(
    r"C:\Users\AlexanderJordain\OneDrive - Oncura Partners\Attachments"
    r"\Oncura_Accounting_Master_Reference-5-28-26.docx"
)

# Matches "Cash SOP-9.", "Accounting SOP-11.", "Manager SOP-8.", "Controller SOP-2." etc.
SOP_HEADER_RE = re.compile(
    r"^\s*(Cash|Accounting|Manager|Controller)\s*SOP[-\s]?(\d+)\s*[.:]\s*(.*)$",
    re.IGNORECASE,
)


def _load_paragraphs(doc_path: Path):
    try:
        from docx import Document
    except ImportError:
        sys.exit(
            "python-docx is not installed. Run `pip install python-docx` and re-run.\n"
            "(This is a dev-only tool — not in requirements.txt.)"
        )
    if not doc_path.exists():
        sys.exit(f"Document not found: {doc_path}")
    doc = Document(str(doc_path))
    return [p.text for p in doc.paragraphs]


def _find_sop_indices(paragraphs):
    """Return [(line_index, family, number, title)] for every SOP header found."""
    hits = []
    for i, t in enumerate(paragraphs):
        m = SOP_HEADER_RE.match(t)
        if m:
            family, number, title = m.group(1), int(m.group(2)), m.group(3).strip()
            hits.append((i, family, number, title))
    return hits


def cmd_list(paragraphs):
    hits = _find_sop_indices(paragraphs)
    if not hits:
        print("No SOP headers found.")
        return
    by_family: dict[str, list] = {}
    for line, family, number, title in hits:
        by_family.setdefault(family, []).append((line, number, title))
    for family in sorted(by_family):
        print(f"\n=== {family} SOPs ===")
        for line, number, title in sorted(by_family[family], key=lambda r: r[1]):
            print(f"  SOP-{number:<3}  (line {line:>5})  {title}")


def cmd_sop(paragraphs, query: str):
    """Print the body of a specific SOP. Query is a substring of the header line.
    e.g., 'Cash SOP-9' matches 'Cash SOP-9. FLEX Finance Co Payment Import'."""
    hits = _find_sop_indices(paragraphs)
    if not hits:
        print("No SOP headers found.")
        return
    matches = [
        (idx, family, number, title)
        for idx, family, number, title in hits
        if query.lower().replace(" ", "") in f"{family}SOP-{number}".lower().replace("-", "-")
    ]
    if not matches:
        print(f"No SOP matched {query!r}. Try --list to see what's available.")
        return
    # Sort hits by line number so we can compute the next-SOP boundary
    sorted_hits = sorted(hits, key=lambda r: r[0])
    start_lines = [r[0] for r in sorted_hits]
    for start_idx, family, number, title in matches:
        # Find the next SOP header after this one — that's the body end
        next_starts = [s for s in start_lines if s > start_idx]
        end_idx = next_starts[0] if next_starts else len(paragraphs)
        print(f"\n{'=' * 78}")
        print(f"{family} SOP-{number}. {title}")
        print(f"  (lines {start_idx}–{end_idx - 1} of the source doc)")
        print("=" * 78)
        for j in range(start_idx + 1, end_idx):
            txt = paragraphs[j].strip()
            if txt:
                print(f"  {txt}")
        print()


def cmd_search(paragraphs, query: str):
    """Print every paragraph containing the query string (case-insensitive),
    with line numbers + a few surrounding paragraphs for context."""
    q = query.lower()
    hits = [i for i, p in enumerate(paragraphs) if q in p.lower()]
    if not hits:
        print(f"No paragraph matches {query!r}.")
        return
    print(f"\n{len(hits)} paragraph(s) matched {query!r}:\n")
    seen = set()
    for h in hits:
        # Group close-by hits — show 1 paragraph before/after
        if any(abs(h - s) < 3 for s in seen):
            continue
        seen.add(h)
        print(f"--- line {h} ---")
        for j in range(max(0, h - 1), min(len(paragraphs), h + 2)):
            txt = paragraphs[j].strip()
            marker = ">>" if j == h else "  "
            if txt:
                print(f"  {marker} [{j}] {txt}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Extract SOP content from the Oncura Accounting Master Reference docx.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--file", type=Path, default=DEFAULT_DOC,
        help=f"Path to the .docx (default: {DEFAULT_DOC})",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List every SOP section header.")
    group.add_argument("--sop", metavar="QUERY",
                       help="Dump the body of a specific SOP, e.g. 'Cash SOP-9'.")
    group.add_argument("--search", metavar="KEYWORD",
                       help="Print every paragraph containing KEYWORD (case-insensitive).")
    args = parser.parse_args()

    paragraphs = _load_paragraphs(args.file)
    # On Windows, force stdout to UTF-8 so smart-quotes / em-dashes don't crash cp1252
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.list:
        cmd_list(paragraphs)
    elif args.sop:
        cmd_sop(paragraphs, args.sop)
    elif args.search:
        cmd_search(paragraphs, args.search)


if __name__ == "__main__":
    main()
