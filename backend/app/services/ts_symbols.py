"""TypeScript / JavaScript symbols for the prefilter (no TS parser needed).

A light scanner, better than the language-agnostic regex fallback:

- strings, template literals and comments are masked, then braces are matched
  to give every declaration a line span: functions, arrow functions, classes,
  class and object-literal methods, interfaces / type aliases / enums;
- a changed line is attributed to the INNERMOST declaration around it (so a
  nested ``restoreSession`` inside a 6,000-line factory is the changed symbol,
  not the factory); a changed field of a class / interface / type changes that
  type (it is its "constructor", like a pydantic model's fields);
- thin wrappers (a declaration of at most ``WRAPPER_LINES`` lines that calls a
  changed one, e.g. ``loadSession(req) { return restoreSession('load', req) }``)
  count as changed too, ``via`` the declaration they call;
- calls on changed lines are resolved through the file's ``import``s:
  relative imports to a module (``./bridge.js`` -> ``pkg.src.bridge``),
  namespace imports (``ns.fn()``), package imports by imported name, local
  declarations to this module and ``obj.method()`` to a changed method;
- ``export { a as b } from './x'`` and ``export * from './x'`` are followed
  like Python re-exports.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
WRAPPER_LINES = 12
_ID = r"[A-Za-z_$][\w$]*"
_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "function", "new", "typeof",
    "await", "yield", "else", "do", "try", "finally", "with", "delete", "void", "in",
    "of", "instanceof", "super", "this", "import", "export", "constructor", "describe",
    "it", "test", "expect", "beforeEach", "afterEach", "beforeAll", "afterAll",
}
_FUNC = re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*({_ID})")
_VAR_FN = re.compile(
    rf"^\s*(?:export\s+)?(?:const|let|var)\s+({_ID})\s*(?::[^=]+)?=\s*(?:async\s*)?"
    rf"(?:function\b|(?:<[^>]*>\s*)?\([^)]*\)?\s*(?::[^=]+)?=>|{_ID}\s*=>|(?:<[^>]*>\s*)?\($)"
)
_VAR = re.compile(rf"^\s*(?:export\s+)?(?:const|let|var)\s+({_ID})\s*(?::[^=]+)?=")
_CLASS = re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+({_ID})")
_TYPE = re.compile(rf"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+)?(?:interface|type|enum)\s+({_ID})")
_METHOD = re.compile(
    rf"^\s*(?:(?:public|private|protected|static|readonly|override|abstract|async|get|set)\s+)*"
    rf"\*?\s*(?:\[\s*)?({_ID})\s*(?:\]\s*)?\??\s*(?:<[^>]*>)?\s*\("
)
_PROP_FN = re.compile(
    rf"^\s*({_ID})\s*:\s*(?:async\s*)?(?:function\b|(?:<[^>]*>\s*)?\([^)]*\)\s*(?::[^=]+)?=>|{_ID}\s*=>)"
)
_CALL = re.compile(rf"(?:(\b{_ID})\s*\.\s*)?\b({_ID})\s*(?:<[^<>()]*>)?\s*\(")
_IMPORT = re.compile(
    r"\bimport\s+(?:type\s+)?([\w$*{}\s,]+?)\s+from\s+['\"]([^'\"]+)['\"]", re.S
)
_REEXPORT = re.compile(r"\bexport\s+(?:type\s+)?(\*|\{[^}]*\})\s+from\s+['\"]([^'\"]+)['\"]", re.S)


def is_ts(path: str) -> bool:
    return path.endswith(TS_SUFFIXES) and not path.endswith(".d.ts")


def ts_module(path: str) -> str:
    """``pkg/src/bridge.ts`` -> ``pkg.src.bridge``; ``pkg/src/index.ts`` -> ``pkg.src``."""
    for suffix in TS_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    parts = [p for p in path.split("/") if p]
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_spec(spec: str, from_path: str) -> str | None:
    """Module name for a relative import specifier, else None."""
    if not spec.startswith("."):
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
    return ts_module(joined if joined.endswith(TS_SUFFIXES) else joined + ".ts")


_MASKED = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`",
    re.S,
)


def _blank_template(body: str) -> str:
    """Template literal body: blank the text, keep ``${...}`` expressions."""
    out = []
    depth = 0
    i = 0
    while i < len(body):
        c = body[i]
        if depth == 0 and c == "$" and body[i + 1 : i + 2] == "{":
            out.append("${")
            depth = 1
            i += 2
            continue
        if depth:
            depth += c == "{"
            depth -= c == "}"
            out.append(c)
        else:
            out.append(c if c == "\n" else " ")
        i += 1
    return "".join(out)


def _blank(m: re.Match[str]) -> str:
    text = m.group(0)
    if text[0] == "`":
        return "`" + _blank_template(text[1:-1]) + "`"
    if text[0] in "'\"":
        return text[0] + " " * (len(text) - 2) + text[-1]
    return re.sub(r"[^\n]", " ", text)


def mask(source: str) -> str:
    """Blank out comments, strings and template literals (newlines kept)."""
    return _MASKED.sub(_blank, source)


@dataclass
class Decl:
    name: str
    kind: str  # "function" | "method" | "class" | "constant"
    start: int  # 1-based lines, inclusive
    end: int
    via: str = ""


def _span_end(lines: list[str], start: int) -> int:
    """Line of the brace closing the declaration's block: the first ``{`` at
    parenthesis depth 0 (so a multi-line parameter list or a default object
    argument does not count). The declaration's own line when it has none."""
    depth = 0
    parens = 0
    opened = False
    for idx in range(start - 1, len(lines)):
        for ch in lines[idx]:
            if ch in "([":
                parens += 1
            elif ch in ")]":
                parens -= 1
            elif ch == "{":
                if parens <= 0 or opened:
                    depth += 1
                    opened = True
            elif ch == "}" and opened:
                depth -= 1
                if depth <= 0:
                    return idx + 1
            elif ch == ";" and not opened and parens <= 0:
                return idx + 1
        if not opened and idx - start >= 40:
            return start
    return len(lines)


def declarations(source: str) -> list[Decl]:
    masked = mask(source)
    lines = masked.splitlines()
    out: list[Decl] = []
    class_spans: list[tuple[int, int]] = []
    for no, line in enumerate(lines, start=1):
        m = _CLASS.match(line) or _TYPE.match(line)
        if m:
            end = _span_end(lines, no)
            out.append(Decl(m.group(1), "class", no, end))
            if _CLASS.match(line):
                class_spans.append((no, end))
            continue
        m = _FUNC.match(line) or _VAR_FN.match(line)
        if m:
            out.append(Decl(m.group(1), "function", no, _span_end(lines, no)))
            continue
        m = _PROP_FN.match(line)
        if m and m.group(1) not in _KEYWORDS:
            out.append(Decl(m.group(1), "method", no, _span_end(lines, no)))
            continue
        m = _METHOD.match(line)
        if m and m.group(1) not in _KEYWORDS and "=>" not in line and not line.rstrip().endswith(";"):
            end = _span_end(lines, no)
            body_start = lines[no - 1:end]
            # A declaration has its block right after the parameter list: the
            # first `{` follows a `)` (optionally a return type), not a call.
            head = " ".join(body_start[:4])
            if re.search(r"\)\s*(?::[^{;]+)?\{", head) and not re.search(r"\)\s*[;,]\s*$", head.split("{")[0]):
                out.append(Decl(m.group(1), "method", no, end))
                continue
        m = _VAR.match(line)
        if m and line.startswith(("export", "const", "let", "var")):
            out.append(Decl(m.group(1), "constant", no, _span_end(lines, no)))
    return out


def _innermost(decls: list[Decl], line: int) -> Decl | None:
    best = None
    for d in decls:
        if d.start <= line <= d.end and (best is None or d.end - d.start < best.end - best.start):
            best = d
    return best


@dataclass
class Imports:
    names: dict[str, tuple[str, str]]  # local -> (module or "pkg:<spec>", imported name)
    namespaces: dict[str, str]  # local -> module


def imports(source: str, path: str) -> Imports:
    names: dict[str, tuple[str, str]] = {}
    namespaces: dict[str, str] = {}
    for clause, spec in _IMPORT.findall(source):
        module = resolve_spec(spec, path) or f"pkg:{spec}"
        clause = clause.strip()
        ns = re.match(rf"\*\s+as\s+({_ID})", clause)
        if ns:
            namespaces[ns.group(1)] = module
            continue
        default = re.match(rf"({_ID})\s*(?:,|$)", clause)
        if default and default.group(1) != "type":
            names[default.group(1)] = (module, "default")
        braces = re.search(r"\{([^}]*)\}", clause)
        if braces:
            for item in braces.group(1).split(","):
                item = re.sub(r"^\s*type\s+", "", item).strip()
                if not item:
                    continue
                parts = re.split(r"\s+as\s+", item)
                names[parts[-1].strip()] = (module, parts[0].strip())
    return Imports(names, namespaces)


def reexports(source: str, path: str) -> dict[str, str]:
    """``name -> "module:name"`` for ``export {..} from`` and, under keys
    ``*0``, ``*1``..., ``"module:*"`` for ``export * from``."""
    out: dict[str, str] = {}
    stars = 0
    for what, spec in _REEXPORT.findall(source):
        module = resolve_spec(spec, path)
        if module is None:
            continue
        if what == "*":
            out[f"*{stars}"] = f"{module}:*"
            stars += 1
            continue
        for item in what.strip("{} \n").split(","):
            item = re.sub(r"^\s*type\s+", "", item).strip()
            if not item:
                continue
            parts = re.split(r"\s+as\s+", item)
            out[parts[-1].strip()] = f"{module}:{parts[0].strip()}"
    return out


def changed_decls(source: str, hunks: list[tuple[int, int]]) -> list[Decl]:
    """Innermost declarations around changed lines, plus thin wrappers that
    call one of them (``via``)."""
    decls = declarations(source)
    changed: dict[tuple[str, int], Decl] = {}
    for h_start, h_end in hunks:
        for line in range(h_start, h_end + 1):
            d = _innermost(decls, line)
            if d is not None:
                changed[(d.name, d.start)] = d
    masked_lines = mask(source).splitlines()
    names = {d.name for d in changed.values() if d.kind in ("function", "method")}
    for w in decls:
        if (w.name, w.start) in changed or w.end - w.start + 1 > WRAPPER_LINES:
            continue
        body = "\n".join(masked_lines[w.start - 1 : w.end])
        for name in sorted(names):
            if name != w.name and re.search(rf"(?<![\w$.]){re.escape(name)}\s*\(", body):
                changed[(w.name, w.start)] = Decl(w.name, w.kind, w.start, w.end, via=name)
                break
    return sorted(changed.values(), key=lambda d: d.start)


def calls_on_lines(source: str, path: str, hunks: list[tuple[int, int]], local: set[str]) -> set[tuple[str, str]]:
    """(module | ``.`` for a method | ``?`` unresolved, name) for calls on changed lines."""
    imp = imports(source, path)
    this = ts_module(path)
    lines = mask(source).splitlines()
    out: set[tuple[str, str]] = set()
    for h_start, h_end in hunks:
        for line in lines[max(h_start - 1, 0) : h_end]:
            for obj, name in _CALL.findall(line):
                if name in _KEYWORDS:
                    continue
                if obj:
                    if obj in imp.namespaces:
                        out.add((imp.namespaces[obj], name))
                    else:
                        out.add((".", name))
                elif name in imp.names:
                    module, original = imp.names[name]
                    out.add((module, original if original != "default" else name))
                elif name in local:
                    out.add((this, name))
                else:
                    out.add(("?", name))
    return out


def decl_lines(source: str, name: str) -> list[str]:
    """Source lines of the first declaration called ``name`` (for snippets)."""
    for d in declarations(source):
        if d.name == name:
            return source.splitlines()[d.start - 1 : d.end]
    return []
