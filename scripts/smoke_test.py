"""Pre-deploy smoke test — catches the class of bug that broke us on Streamlit Cloud.

What it checks (fast, no streamlit runtime needed):
  1. Every .py file in app.py / core/ / pages/ parses as valid Python.
  2. No emoji codepoints anywhere in the source — the UI style guide mandates
     Material Symbols (`:material/...:`) instead. Enforced statically because
     the rule was previously convention-only and regressions are silent.
  3. Every core/ module imports cleanly with the real streamlit installed.
  4. Every `module.attr` reference in pages/*.py resolves against the imported module.
     This is the static check that would have caught the build_import_from_payments
     AttributeError before it hit production.

Exit code:
  0 = clean. Cloud deploy is safe.
  1 = something will break on Cloud. Fix before pushing.

Wire into CI via .github/workflows/smoke.yml.
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FLEXREBATE_LOCAL", "1")  # let auth.require_login bypass for static import


def syntax_check(files: list[Path]) -> list[str]:
    errors = []
    for f in files:
        try:
            ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError as e:
            errors.append(f"SYNTAX: {f.relative_to(ROOT)}: line {e.lineno}: {e.msg}")
    return errors


# No-emoji rule (docs/UI_STYLE_GUIDE.md): flag emoji-presentation codepoints
# only. Textual dingbats the app intentionally uses are NOT flagged — check
# marks U+2713/U+2715/U+2717 (fuzzy-match buttons), nav triangles U+25B6/25C0,
# box drawing, dashes, math symbols.
_EMOJI_RANGES = (
    (0x1F000, 0x1FFFF),  # all SMP emoji blocks (emoticons, pictographs, flags)
    (0x2600, 0x26FF),    # misc symbols (sun, warning sign, phone, ...)
    (0x2B00, 0x2BFF),    # arrows + stars (up/down arrows, star, heavy circle)
    (0xFE00, 0xFE0F),    # variation selectors (force emoji presentation)
)
_EMOJI_SINGLES = frozenset({
    0x203C, 0x2049, 0x2705, 0x2708, 0x2709, 0x270A, 0x270B, 0x270C, 0x270D,
    0x270F, 0x2712, 0x2714, 0x2716, 0x2728, 0x274C, 0x274E, 0x2753, 0x2754,
    0x2755, 0x2757, 0x2764, 0x2795, 0x2796, 0x2797, 0x27A1, 0x27B0, 0x27BF,
})


def _is_emoji(cp: int) -> bool:
    return cp in _EMOJI_SINGLES or any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def emoji_check(files: list[Path]) -> list[str]:
    errors = []
    for f in files:
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            hit = next((ch for ch in line if _is_emoji(ord(ch))), None)
            if hit is not None:
                errors.append(
                    f"EMOJI:  {f.relative_to(ROOT)}:{lineno}: U+{ord(hit):04X} — "
                    "no emojis in UI text; use Material Symbols (:material/...:)"
                )
    return errors


def import_core_modules() -> tuple[dict, list[str]]:
    core_dir = ROOT / "core"
    modules, errors = {}, []
    for f in sorted(core_dir.glob("*.py")):
        if f.stem == "__init__":
            continue
        full = f"core.{f.stem}"
        try:
            modules[f.stem] = importlib.import_module(full)
        except Exception as e:
            errors.append(f"IMPORT: {full}: {type(e).__name__}: {e}")
    return modules, errors


def find_aliased_imports(tree: ast.AST) -> dict[str, str]:
    """Return {local_name: 'core.<module>'} for `from core import X, Y as Z` patterns."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core":
            for alias in node.names:
                local = alias.asname or alias.name
                out[local] = alias.name
    return out


def check_page_references(page: Path, core_modules: dict) -> list[str]:
    src = page.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(page))
    except SyntaxError:
        return []  # already reported by syntax_check
    aliased = find_aliased_imports(tree)
    errors = []
    rel = page.relative_to(ROOT)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        local_name = node.value.id
        attr = node.attr
        if local_name not in aliased:
            continue
        mod_name = aliased[local_name]
        mod = core_modules.get(mod_name)
        if mod is None:
            errors.append(f"REF:    {rel}:{node.lineno}: core.{mod_name} not importable (skipped)")
            continue
        if not hasattr(mod, attr):
            errors.append(
                f"MISSING:{rel}:{node.lineno}: core.{mod_name}.{attr} does not exist "
                f"(referenced from {local_name}.{attr})"
            )
    return errors


def main() -> int:
    print(f"Smoke test :: root={ROOT}")
    py_files = (
        [ROOT / "app.py"]
        + sorted((ROOT / "core").glob("*.py"))
        + sorted((ROOT / "pages").glob("*.py"))
    )
    py_files = [f for f in py_files if f.exists()]

    # Stage 1: syntax
    syn_errors = syntax_check(py_files)
    if syn_errors:
        print("\n".join(syn_errors))
        return 1
    print(f"  OK syntax ({len(py_files)} files)")

    # Stage 1.5: no-emoji lint
    emo_errors = emoji_check(py_files)
    if emo_errors:
        print("\n".join(emo_errors))
        return 1
    print(f"  OK no-emoji ({len(py_files)} files)")

    # Stage 2: import core modules
    core_modules, imp_errors = import_core_modules()
    if imp_errors:
        print("\n".join(imp_errors))
        return 1
    print(f"  OK core imports ({len(core_modules)} modules)")

    # Stage 3: cross-module reference check for pages
    pages_dir = ROOT / "pages"
    ref_errors: list[str] = []
    pages = sorted(pages_dir.glob("*.py"))
    for p in pages:
        ref_errors.extend(check_page_references(p, core_modules))
    if ref_errors:
        print("\n".join(ref_errors))
        return 1
    print(f"  OK references ({len(pages)} pages)")

    print("All checks passed — Cloud deploy is safe to push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
