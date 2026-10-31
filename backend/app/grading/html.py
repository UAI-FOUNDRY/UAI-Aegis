"""HTML questions: the student writes a page; each test is a list of checks it must pass.

The page is never rendered or run. Like SQL questions, it is parsed inside a small
Python harness in the sandbox (standard library only), and the harness reports which
checks passed. Each test's input is the checks, one per line:

    doctype                              the page starts with <!DOCTYPE html>
    exists  SELECTOR                     at least one element matches
    missing SELECTOR                     no element matches
    count   SELECTOR = N                 also ==, >=, <=, >, <
    text    SELECTOR contains WORDS      some match's text contains WORDS (any case)
    text    SELECTOR = WORDS             some match's text is exactly WORDS
    attr    SELECTOR NAME                every match has the attribute
    attr    SELECTOR NAME = VALUE        every match has the attribute with this value
    # comment lines and blank lines are ignored

Selectors: tag, *, #id, .class, [attr], [attr=value], [attr^=v], [attr$=v], [attr*=v],
combined (input.big[type=email]), descendant (form input) and child (ul > li),
and groups (h1, h2). Values may be quoted.

grader_config:
  partial_credit  true (default): a test earns the share of its checks that pass;
                  false: a test passes only if every check passes
"""

from __future__ import annotations

from typing import Any

from .checklist import checklist_text, checklist_verdict
from .executor import ExecRequest, ExecResult
from .registry import Check, Grader, RunTarget, TestSpec, _base_config, register

# Runs in the sandbox (and on the server, to check tests as they are saved). Standard library only.
_LIB = r'''
import html.parser
import json
import re
import sys

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}
MAX_CHECKS = 200

# Starting one of these closes an open element of the listed kinds (HTML's optional end tags).
_BLOCKS = ["address", "article", "aside", "blockquote", "div", "dl", "fieldset", "footer", "form", "h1", "h2",
           "h3", "h4", "h5", "h6", "header", "hr", "main", "nav", "ol", "p", "pre", "section", "table", "ul"]
AUTO_CLOSE = {block: {"p"} for block in _BLOCKS}
AUTO_CLOSE.update({
    "li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "option": {"option"},
    "tr": {"tr", "td", "th"}, "td": {"td", "th"}, "th": {"td", "th"},
    "thead": {"tbody", "tr", "td", "th"}, "tbody": {"thead", "tbody", "tr", "td", "th"},
    "tfoot": {"thead", "tbody", "tr", "td", "th"},
})
# Searching for an element to auto-close stops at these (a nested list's <li> must not close the outer one).
BOUNDARY = {"ul", "ol", "dl", "table", "select", "body", "html", "button", "div", "section", "article", "nav",
            "form", "template"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.parent = parent

    def text(self):
        parts = []

        def walk(node):
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag not in ("script", "style"):
                    walk(child)

        walk(self)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()


class Builder(html.parser.HTMLParser):
    """Lenient tree builder: unclosed tags close with their parent, void tags never open."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.current = self.root
        self.doctype = False

    def handle_decl(self, decl):
        if decl.strip().lower().startswith("doctype html"):
            self.doctype = True

    def _node(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        return node

    def _auto_close(self, tag):
        """Close elements whose end tag HTML lets you leave out (<li>A<li>B, <p>x<div>), as browsers do."""
        closes = AUTO_CLOSE.get(tag)
        if not closes:
            return
        node = self.current
        while node is not None and node.tag != "#root":
            if node.tag in closes:
                self.current = node.parent
                return
            if node.tag in BOUNDARY:
                return
            node = node.parent

    def handle_starttag(self, tag, attrs):
        self._auto_close(tag)
        node = self._node(tag, attrs)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self._node(tag, attrs)

    def handle_endtag(self, tag):
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_page(source):
    builder = Builder()
    builder.feed(source)
    builder.close()
    return builder


# ------------------------------------------------------------------ selectors

_NAME = r"[A-Za-z_][\w-]*"
_COMPOUND = re.compile(r"(?P<tag>" + _NAME + r"|\*)?(?P<rest>(?:#[\w-]+|\.[\w-]+|\[[^\]]*\])*)$")
_PART = re.compile(r"#([\w-]+)|\.([\w-]+)|\[\s*([\w:-]+)\s*(?:([\^$*]?=)\s*(\"[^\"]*\"|'[^']*'|[^\]]*?))?\s*\]")


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _compound(text):
    match = _COMPOUND.match(text)
    if not text or not match:
        raise ValueError("cannot read selector part '%s'" % text)
    tag = (match.group("tag") or "*").lower()
    ids, classes, attrs = [], [], []
    rest = match.group("rest")
    pos = 0
    while pos < len(rest):
        part = _PART.match(rest, pos)
        if not part:
            raise ValueError("cannot read selector part '%s'" % text)
        if part.group(1):
            ids.append(part.group(1))
        elif part.group(2):
            classes.append(part.group(2))
        else:
            op = part.group(4)
            attrs.append((part.group(3).lower(), op, _unquote(part.group(5)) if op else None))
        pos = part.end()
    return (tag, ids, classes, attrs)


def _split(selector):
    """'form > input.x' -> ['form', '>', 'input.x'], respecting [..] and quotes."""
    tokens, buf, depth, quote = [], "", 0, None
    for ch in selector:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'" and depth:
            quote = ch
            buf += ch
        elif ch == "[":
            depth += 1
            buf += ch
        elif ch == "]":
            depth -= 1
            buf += ch
        elif depth == 0 and (ch.isspace() or ch == ">"):
            if buf:
                tokens.append(buf)
                buf = ""
            if ch == ">":
                tokens.append(">")
        else:
            buf += ch
    if depth or quote:
        raise ValueError("unclosed [ or quote in selector '%s'" % selector)
    if buf:
        tokens.append(buf)
    return tokens


def parse_selector(selector):
    """Returns a list of groups; each group is [(combinator, compound), ...]."""
    groups = []
    for group in selector.split(","):
        tokens = _split(group.strip())
        if not tokens:
            raise ValueError("empty selector in '%s'" % selector)
        parts, combinator = [], None
        for token in tokens:
            if token == ">":
                if combinator == ">" or not parts:
                    raise ValueError("misplaced '>' in selector '%s'" % selector)
                combinator = ">"
                continue
            parts.append((combinator or " ", _compound(token)))
            combinator = None
        if combinator:
            raise ValueError("selector '%s' ends with '>'" % selector)
        groups.append(parts)
    return groups


def _matches(node, compound):
    tag, ids, classes, attrs = compound
    if tag != "*" and node.tag != tag:
        return False
    if any(node.attrs.get("id") != i for i in ids):
        return False
    have = node.attrs.get("class", "").split()
    if any(c not in have for c in classes):
        return False
    for name, op, value in attrs:
        if name not in node.attrs:
            return False
        actual = node.attrs[name]
        if op == "=" and actual != value:
            return False
        if op == "^=" and not actual.startswith(value):
            return False
        if op == "$=" and not actual.endswith(value):
            return False
        if op == "*=" and value not in actual:
            return False
    return True


def _chain(node, parts, i):
    combinator, compound = parts[i]
    if not _matches(node, compound):
        return False
    if i == 0:
        return True
    parent = node.parent
    if combinator == ">":
        return parent is not None and parent.tag != "#root" and _chain(parent, parts, i - 1)
    while parent is not None and parent.tag != "#root":
        if _chain(parent, parts, i - 1):
            return True
        parent = parent.parent
    return False


def select(root, groups):
    found = []

    def walk(node):
        for child in node.children:
            if isinstance(child, Node):
                if any(_chain(child, parts, len(parts) - 1) for parts in groups):
                    found.append(child)
                walk(child)

    walk(root)
    return found


# --------------------------------------------------------------------- checks

_COUNT = re.compile(r"^count\s+(.+?)\s*(==|=|>=|<=|>|<)\s*(\d+)$")
_TEXT = re.compile(r"^text\s+(.+?)\s+(contains|=)\s+(.+)$")
_ATTR = re.compile(r"^attr\s+(.+?)\s+([\w:-]+)(?:\s*=\s*(.*))?$")


def parse_checks(text):
    checks = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        word = line.split(None, 1)[0].lower()
        try:
            if word == "doctype" and line.lower() == "doctype":
                check = {"kind": "doctype"}
            elif word in ("exists", "missing"):
                sel = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
                check = {"kind": word, "sel": sel.strip()}
            elif word == "count":
                m = _COUNT.match(line)
                if not m:
                    raise ValueError("write: count SELECTOR = NUMBER")
                check = {"kind": "count", "sel": m.group(1), "op": m.group(2), "n": int(m.group(3))}
            elif word == "text":
                m = _TEXT.match(line)
                if not m:
                    raise ValueError("write: text SELECTOR contains WORDS  (or = WORDS)")
                check = {"kind": "text", "sel": m.group(1), "op": m.group(2), "value": _unquote(m.group(3))}
            elif word == "attr":
                m = _ATTR.match(line)
                if not m:
                    raise ValueError("write: attr SELECTOR NAME  (or NAME = VALUE)")
                value = m.group(3)
                check = {"kind": "attr", "sel": m.group(1), "name": m.group(2).lower(),
                         "value": _unquote(value) if value is not None and value.strip() else None}
            else:
                raise ValueError("unknown check '%s' (use doctype, exists, missing, count, text or attr)" % word)
            if "sel" in check:
                if not check["sel"]:
                    raise ValueError("a selector is missing")
                check["groups"] = parse_selector(check["sel"])
        except ValueError as exc:
            raise ValueError("line %d: %s" % (number, exc))
        check["line"] = line
        checks.append(check)
    if not checks:
        raise ValueError("write at least one check, e.g.: exists form")
    if len(checks) > MAX_CHECKS:
        raise ValueError("at most %d checks per test" % MAX_CHECKS)
    return checks


def run_check(page, check):
    kind = check["kind"]
    if kind == "doctype":
        return page.doctype, "" if page.doctype else "the page does not start with <!DOCTYPE html>"
    found = select(page.root, check["groups"])
    if kind == "exists":
        return bool(found), "" if found else "nothing matches"
    if kind == "missing":
        return not found, "" if not found else "found %d" % len(found)
    if kind == "count":
        n, op, want = len(found), check["op"], check["n"]
        ok = {"=": n == want, "==": n == want, ">=": n >= want, "<=": n <= want, ">": n > want, "<": n < want}[op]
        return ok, "" if ok else "found %d" % n
    if kind == "text":
        if not found:
            return False, "nothing matches"
        texts = [node.text() for node in found]
        if check["op"] == "contains":
            ok = any(check["value"].lower() in t.lower() for t in texts)
        else:
            ok = any(t == re.sub(r"\s+", " ", check["value"]).strip() for t in texts)
        return ok, "" if ok else "text was: " + " | ".join(t[:60] for t in texts[:3])
    if kind == "attr":
        if not found:
            return False, "nothing matches"
        name, value = check["name"], check["value"]
        bad = [node for node in found if name not in node.attrs or (value is not None and node.attrs[name] != value)]
        return not bad, "" if not bad else "%d of %d element(s) fail" % (len(bad), len(found))
    return False, "unknown check"


def run_checks(source, checks_text):
    page = parse_page(source)
    results = []
    for check in parse_checks(checks_text):
        ok, detail = run_check(page, check)
        results.append({"check": check["line"], "ok": bool(ok), "detail": detail})
    return results
'''

_MAIN = r'''
try:
    RESULTS = run_checks(ANSWER, sys.stdin.read())
except ValueError as exc:
    print("Bad checks: " + str(exc), file=sys.stderr)
    sys.exit(3)
print(json.dumps({"results": RESULTS}))
'''

# The same code, for checking tests on the server and in unit tests.
_NS: dict[str, Any] = {}
exec(compile(_LIB, "html_checks", "exec"), _NS)  # noqa: S102 - our own constant code, no user input
parse_checks = _NS["parse_checks"]
run_checks = _NS["run_checks"]


def harness(answer: str) -> str:
    """The Python program run in the sandbox for one student page."""
    return _LIB + "\nANSWER = " + repr(answer) + "\n" + _MAIN


class HtmlGrader(Grader):
    key = "html"
    description = "HTML pages: each test is a list of checks (elements, attributes, text) the page must pass."
    needs_expected = False  # checks are the test; there is no expected output
    can_generate_expected = False
    needs_python = True

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        out = _base_config(config, {"partial_credit"})
        return {**out, "partial_credit": bool(config.get("partial_credit", True))}

    def validate_language(self, language_key: str) -> None:
        if language_key != "html":
            raise ValueError("HTML questions must be answered in the 'html' language")

    def validate_test_input(self, text: str, config: dict[str, Any]) -> None:
        parse_checks(text)  # raises ValueError with the line number

    def prepare(self, target: RunTarget, test: TestSpec, config: dict[str, Any]) -> ExecRequest:
        if target.python is None:
            raise ValueError("the python3 language is needed to check HTML questions")
        return ExecRequest(
            language_key=target.python.key, judge0_id=target.python.judge0_id, file_name=target.python.file_name,
            source=harness(target.source), stdin=test.input, time_limit_ms=target.time_limit_ms,
            memory_limit_kb=target.memory_limit_kb,
        )

    def shown_stdout(self, result: ExecResult) -> str:
        """Readable check list instead of the harness's JSON."""
        return checklist_text(result.stdout)

    def check(self, test: TestSpec, result: ExecResult, config: dict[str, Any]) -> Check:
        return checklist_verdict(test, result.stdout, config["partial_credit"], what="the page")


register(HtmlGrader())
