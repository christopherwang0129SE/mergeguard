"""Cheap static prefilter that runs before any LLM call.

For each branch we extract, from the files it changed:

- ``defs``: functions / classes whose body the branch touched;
- ``calls``: names the branch calls from inside the code it touched.

Python files are parsed with :mod:`ast`. Other languages use a regex
heuristic (``def`` / ``function`` / ``func`` / ``fn`` names and ``name(`` call
sites on changed lines).

A pair is a model candidate only when the branches share a file or when one
branch changes a symbol the other defines or calls (PR A edits ``def f``,
PR B calls ``f``). Candidates are ranked and only the top K are sent to
Nemotron; everything else keeps the git score.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas import BranchDiff
from app.services import git_service

# Names too generic to count as a meaningful overlap.
_IGNORED_NAMES = {
    "__init__", "__repr__", "__str__", "__eq__", "__hash__", "main", "setUp",
    "tearDown", "print", "len", "str", "int", "float", "dict", "list", "set",
    "tuple", "range", "round", "min", "max", "isinstance", "super", "open",
}
_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "function", "def", "func",
    "fn", "elif", "with", "await", "new", "typeof", "sizeof", "not", "and", "or",
    "assert", "print", "lambda", "yield", "in", "is", "else", "try", "except",
}
_REGEX_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?"
    r"(?:def|function\*?|func|fn|sub)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"
)
_REGEX_ARROW = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?"
    r"(?:\([^)]*\)|[A-Za-z_]\w*)\s*=>"
)
_REGEX_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
MAX_FILE_BYTES = 400_000


@dataclass
class BranchSymbols:
    defs: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class PairSignal:
    shared_files: int
    overlapping_files: int
    symbols: tuple[str, ...]

    @property
    def is_candidate(self) -> bool:
        return self.shared_files > 0 or bool(self.symbols)

    def rank_key(self, git_score: float) -> tuple:
        return (self.overlapping_files, len(self.symbols), self.shared_files, git_score)


def _in_hunks(start: int, end: int, hunks: list[tuple[int, int]]) -> bool:
    return any(start <= h_end and h_start <= end for h_start, h_end in hunks)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def python_symbols(source: str, hunks: list[tuple[int, int]]) -> BranchSymbols | None:
    """Changed defs and their calls for one Python file. ``None`` if unparsable."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    out = BranchSymbols()
    changed_defs: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            if _in_hunks(start, end, hunks):
                changed_defs.append(node)
                if not isinstance(node, ast.ClassDef):
                    out.defs.add(node.name)
                elif _in_hunks(start, node.lineno, hunks):
                    out.defs.add(node.name)
    # Calls made from inside changed functions, plus calls on changed lines.
    for node in changed_defs:
        if isinstance(node, ast.ClassDef):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = _call_name(sub)
                if name:
                    out.calls.add(name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            end = getattr(node, "end_lineno", None) or node.lineno
            if _in_hunks(node.lineno, end, hunks):
                name = _call_name(node)
                if name:
                    out.calls.add(name)
    # Changed module-level constants / assignments (e.g. MAX_DISCOUNT = 20).
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            end = getattr(node, "end_lineno", None) or node.lineno
            if _in_hunks(node.lineno, end, hunks):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        out.defs.add(target.id)
    return out


def regex_symbols(source: str, hunks: list[tuple[int, int]]) -> BranchSymbols:
    """Language-agnostic heuristic: def/function names and call sites."""
    out = BranchSymbols()
    lines = source.splitlines()
    def_lines: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        match = _REGEX_DEF.match(line) or _REGEX_ARROW.match(line)
        if match:
            def_lines.append((index, match.group(1)))
    for start, end in hunks:
        # Defs declared on a changed line, or the nearest def above the hunk.
        enclosing = [name for line_no, name in def_lines if line_no <= start]
        if enclosing:
            out.defs.add(enclosing[-1])
        for line_no, name in def_lines:
            if start <= line_no <= end:
                out.defs.add(name)
        for line in lines[max(start - 1, 0) : end]:
            for call in _REGEX_CALL.findall(line):
                out.calls.add(call)
    out.calls -= _KEYWORDS
    out.defs -= _KEYWORDS
    return out


def file_symbols(path: str, source: str, hunks: list[tuple[int, int]]) -> BranchSymbols:
    if path.endswith(".py"):
        parsed = python_symbols(source, hunks)
        if parsed is not None:
            return parsed
    return regex_symbols(source, hunks)


def branch_symbols(repo: Path, diff: BranchDiff) -> BranchSymbols:
    """Union of changed symbols across a branch's changed files."""
    total = BranchSymbols()
    for changed in diff.files:
        if not changed.hunks:
            continue
        try:
            source = git_service.show_file(repo, diff.branch, changed.path)
        except git_service.GitError:
            continue
        if source is None or len(source) > MAX_FILE_BYTES:
            continue
        syms = file_symbols(changed.path, source, changed.hunks)
        total.defs |= syms.defs
        total.calls |= syms.calls
    total.defs -= _IGNORED_NAMES
    total.calls -= _IGNORED_NAMES
    return total


def symbol_overlap(a: BranchSymbols, b: BranchSymbols) -> tuple[str, ...]:
    """Symbols one branch changes that the other also changes or calls."""
    hits = (a.defs & (b.defs | b.calls)) | (b.defs & a.calls)
    return tuple(sorted(hits))
