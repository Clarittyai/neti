"""A test that asserts a string is in source must not be satisfiable by a comment.

Twice in one session a test in this repository passed for the wrong reason, the same way both
times — the author wrote a comment explaining the rule, and the comment contained the string the
assertion was looking for:

    # ... against its `rounded-lg`, a 10% active tint ...
    assert "rounded-lg" in nav              # green, with the radius deleted

That is worse than having no test. A test that cannot fail is a green light attached to nothing,
and it is attached to exactly the property somebody thought was important enough to pin.

The dangerous shape is narrow and worth naming precisely: **a positive `in` assertion against text
read from a source file.** The negative form (`assert "x" not in source`) fails on a comment rather
than passing, which is noisy but never silent — the safe direction.

So this walks the test suite's own AST, finds that shape, and requires the text to have come
through `tests.support.code_of`, which strips comments and docstrings first.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
CODE_SUFFIXES = ("py", "tsx", "ts", "css", "js")


def _reads_source(node: ast.AST) -> str | None:
    """`x.read_text(...)` or `inspect.getsource(...)` → the call's rough source, else None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in {"read_text", "getsource"}:
        return ast.unparse(node)
    if isinstance(func, ast.Name) and func.id in {"getsource", "code_of"}:
        return ast.unparse(node)
    return None


def _names_bound_to_raw_source(tree: ast.AST) -> set[str]:
    """Variables holding text read from a code file *without* going through `code_of`.

    **Taint propagates.** The real bug this exists for did not assert against the variable that was
    read — it sliced one out of it:

        shell = Path("Shell.tsx").read_text(...)     # raw
        nav = shell[shell.index(...) : ...]          # still raw, and the assertion is on this

    A checker that only tracked direct assignment would have watched its own motivating case go
    past. So anything derived from a raw name is raw, to a fixed point.
    """
    raw: set[str] = set()
    assigns: list[tuple[str, ast.expr]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        assigns.append((target.id, node.value))
        call = _reads_source(node.value)
        if call is None or "code_of" in call:
            continue  # already stripped — that is the whole point
        # Only source files. A test reading README.md or a CHANGELOG has no comments to be fooled
        # by, and demanding the helper there would be ceremony.
        if any(f".{s}" in call for s in CODE_SUFFIXES) or "getsource" in call:
            raw.add(target.id)

    changed = True
    while changed:
        changed = False
        for name, value in assigns:
            if name in raw:
                continue
            used = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
            if used & raw:
                raw.add(name)
                changed = True
    return raw


def _positive_in_assertions(tree: ast.AST, names: set[str]) -> list[tuple[int, str]]:
    """`assert "literal" in <name>` where `<name>` is unstripped source."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        for cmp_node in ast.walk(node.test):
            if not isinstance(cmp_node, ast.Compare) or len(cmp_node.ops) != 1:
                continue
            if not isinstance(cmp_node.ops[0], ast.In):  # `not in` is ast.NotIn — the safe form
                continue
            if not isinstance(cmp_node.left, ast.Constant) or not isinstance(
                cmp_node.left.value, str
            ):
                continue
            right = cmp_node.comparators[0]
            if isinstance(right, ast.Name) and right.id in names:
                found.append((node.lineno, ast.unparse(cmp_node)))
    return found


def test_no_test_asserts_against_source_it_has_not_stripped() -> None:
    """The rule, enforced on the suite that wrote it."""
    offenders: list[str] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        raw = _names_bound_to_raw_source(tree)
        for line, expr in _positive_in_assertions(tree, raw):
            offenders.append(f"{path.relative_to(TESTS)}:{line}: {expr[:78]}")

    assert not offenders, (
        "these assert a string is present in source that still contains its own comments, so the "
        "comment explaining the rule satisfies the test. Read it with `tests.support.code_of`:\n  "
        + "\n  ".join(offenders)
    )


def test_the_checker_can_actually_see_the_shape() -> None:
    """A meta-test that never fires is the failure it exists to prevent, one level up."""
    bad = ast.parse(
        'shell = Path("a.tsx").read_text(encoding="utf-8")\nassert "rounded-lg" in shell\n'
    )
    good = ast.parse('shell = code_of("a.tsx")\nassert "rounded-lg" in shell\n')
    negative = ast.parse(
        'src = Path("a.py").read_text(encoding="utf-8")\nassert "isatty" not in src\n'
    )

    assert _positive_in_assertions(bad, _names_bound_to_raw_source(bad)), "missed the real shape"

    # The motivating case sliced a second variable out of the first, and a checker that only
    # tracked direct assignment would have missed exactly the bug it was written for.
    derived = ast.parse(
        'shell = Path("a.tsx").read_text(encoding="utf-8")\n'
        "nav = shell[shell.index('x'):]\n"
        'assert "rounded-lg" in nav\n'
    )
    assert _positive_in_assertions(derived, _names_bound_to_raw_source(derived)), (
        "taint has to propagate through a slice, or the motivating bug walks past"
    )
    assert not _positive_in_assertions(good, _names_bound_to_raw_source(good)), "code_of is the fix"
    assert not _positive_in_assertions(negative, _names_bound_to_raw_source(negative)), (
        "`not in` fails on a comment rather than passing — noisy, never silent"
    )
