"""Lightweight frontend/backend API contract drift checker.

Scans:
  - Backend routes  : rpg/app.py, rpg/platform_app/api.py,
                      rpg/platform_app/frontend_routes.py
  - Frontend calls  : frontend/src/api-client.js, frontend/src/data-loader.js,
                      frontend/src/*.jsx (looking for direct `fetch("/api/...")`)
  - Docs            : rpg/BACKEND_API_CONTRACT.md, rpg/CLAUDE_CODE_HANDOFF.md

Produces a Markdown report at  rpg/docs/api_contract_drift.md  and prints a
summary to stdout.

Read-only:  only writes the report file. No code is modified, no tests touched.

Usage (from project root):
    python -m tools.contract_check
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (resolved relative to this file → repo layout)
# ---------------------------------------------------------------------------

THIS_FILE = Path(__file__).resolve()
RPG_DIR = THIS_FILE.parent.parent                          # …/rpg
PROJECT_ROOT = RPG_DIR.parent                              # …/我蕾穆丽娜不爱你
FRONTEND_SRC = PROJECT_ROOT / "frontend" / "src"
REPORT_PATH = RPG_DIR / "docs" / "api_contract_drift.md"

BACKEND_FILES: list[Path] = [
    RPG_DIR / "app.py",
    RPG_DIR / "platform_app" / "api.py",
    RPG_DIR / "platform_app" / "frontend_routes.py",
]

FRONTEND_CORE_FILES: list[Path] = [
    FRONTEND_SRC / "api-client.js",
    FRONTEND_SRC / "data-loader.js",
]

DOC_FILES: list[Path] = [
    RPG_DIR / "BACKEND_API_CONTRACT.md",
    RPG_DIR / "CLAUDE_CODE_HANDOFF.md",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Endpoint:
    method: str          # "GET" | "POST" | ... | "ANY"
    path: str            # normalised (placeholders → {arg})


@dataclass
class Hit:
    endpoint: Endpoint
    source: str          # relative file path
    line: int
    confidence: str = "high"   # "high" | "low"
    raw: str = ""


@dataclass
class Scan:
    routes: dict[Endpoint, list[Hit]] = field(default_factory=lambda: defaultdict(list))
    calls: dict[Endpoint, list[Hit]] = field(default_factory=lambda: defaultdict(list))
    doc_mentions: dict[Endpoint, list[Hit]] = field(default_factory=lambda: defaultdict(list))
    doc_cookies: dict[str, list[Hit]] = field(default_factory=lambda: defaultdict(list))
    code_cookies: dict[str, list[Hit]] = field(default_factory=lambda: defaultdict(list))
    files_scanned: list[Path] = field(default_factory=list)
    files_missing: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PLACEHOLDER_RE = re.compile(r"\{[^/}]+\}")
PYTHON_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]+)?\}")

# ${id}  ${userId}  ${'foo'}  → {arg}
JS_INTERP_RE = re.compile(r"\$\{[^}]+\}")
# encodeURIComponent(x) inside string concat  → {arg}
JS_ENCODE_CONCAT_RE = re.compile(r'"\s*\+\s*encodeURIComponent\([^)]*\)\s*\+\s*"')
# generic "+ varName +" concat
JS_VAR_CONCAT_RE = re.compile(r'"\s*\+\s*[a-zA-Z_$][\w$.]*\s*\+\s*"')
JS_VAR_CONCAT_TAIL_RE = re.compile(r'"\s*\+\s*[a-zA-Z_$][\w$.]*\s*$')


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


def _read(p: Path) -> list[str] | None:
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None


def _norm_path(raw: str) -> tuple[str, bool]:
    """Normalise a path string. Returns (canonical, low_confidence)."""
    s = raw.strip()
    low = False

    # JS template interpolations / concats → {arg}
    if "${" in s or "+" in s:
        low = True
    s = JS_INTERP_RE.sub("{arg}", s)
    s = JS_ENCODE_CONCAT_RE.sub("{arg}", s)
    s = JS_VAR_CONCAT_RE.sub("{arg}", s)
    s = JS_VAR_CONCAT_TAIL_RE.sub("{arg}", s)
    # Remove stray closing quote left after a tail concat
    s = s.replace('"', "").replace("'", "").replace("`", "")
    # Drop query string (we only compare paths)
    s = s.split("?", 1)[0]
    s = s.split("#", 1)[0]
    # Strip a trailing slash unless root
    if len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    # Python FastAPI `{name:converter}` → `{name}` to align with JS templates
    s = PYTHON_PATH_PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", s)
    # Canonicalise: all `{...}` → `{arg}` for matching
    s_canonical = PLACEHOLDER_RE.sub("{arg}", s)
    return s_canonical, low


# ---------------------------------------------------------------------------
# Backend route scanner
# ---------------------------------------------------------------------------

# Matches:  @app.get("/api/foo")     @router.post('/api/bar', response_model=...)
#           @app.websocket("/ws")    @frontend_router.delete("/x")
ROUTE_DECORATOR_RE = re.compile(
    r'^\s*@\w+\.(get|post|put|delete|patch|head|options|websocket|api_route)\s*\(\s*'
    r'(["\'])([^"\']+)\2',
    re.IGNORECASE,
)

# api_route(methods=[...]) is rare here; we treat it as ANY when seen.

def scan_backend_routes(scan: Scan) -> None:
    for f in BACKEND_FILES:
        lines = _read(f)
        if lines is None:
            scan.files_missing.append(f)
            continue
        scan.files_scanned.append(f)
        for i, line in enumerate(lines, 1):
            m = ROUTE_DECORATOR_RE.match(line)
            if not m:
                continue
            method = m.group(1).upper()
            if method == "API_ROUTE":
                method = "ANY"
            raw_path = m.group(3)
            path, low = _norm_path(raw_path)
            if not path.startswith("/"):
                continue
            ep = Endpoint(method, path)
            scan.routes[ep].append(
                Hit(ep, _rel(f), i, "low" if low else "high", line.strip())
            )
        _scan_cookies(scan.code_cookies, f, lines)


# ---------------------------------------------------------------------------
# Frontend call scanner
# ---------------------------------------------------------------------------

# Locate the START of a wrapper/fetch/SSE call.
#   GET( POST( PUT( PATCH( DEL( DELETE( _send( fetch( sseStream( openEventSource(
# `sseStream` is the project's internal SSE helper used for /api/chat and
# /api/opening; treat its first arg as a POST endpoint.
FE_WRAPPER_START_RE = re.compile(
    r'\b(GET|POST|PUT|PATCH|DEL|DELETE|_send|fetch|sseStream|openEventSource)\s*\('
)

# Any string literal anchored to BASE +/= that points at /api/* — used to
# pick up `BASE + "/api/saves/" + sid + "/export"` style URL builders.
FE_BASE_CONCAT_RE = re.compile(
    r'BASE\s*\+\s*("[^"]*"(?:\s*\+\s*[\w$.()]+\s*\+\s*"[^"]*")*)'
)
FE_API_BASE_CONCAT_RE = re.compile(
    r'api\.base\s*\+\s*("[^"]*"(?:\s*\+\s*[\w$.()]+\s*\+\s*"[^"]*")*)'
)

# Match method:"POST" inside the same line as _send/fetch to refine the method
FE_METHOD_KV_RE = re.compile(r'method\s*:\s*["\'](GET|POST|PUT|PATCH|DELETE)["\']', re.IGNORECASE)


def _extract_first_arg(text: str) -> str | None:
    """Given text starting right after the opening '(' of a call, return the
    first argument's source verbatim (handles nested parens, ignores commas
    inside parens/brackets/strings). Returns None if no balanced first arg.
    """
    depth_paren = 0
    depth_brack = 0
    depth_brace = 0
    in_str: str | None = None
    escape = False
    out: list[str] = []
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if in_str:
            out.append(ch)
            if ch == "\\":
                escape = True
                continue
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            out.append(ch)
            continue
        if ch == "(":
            depth_paren += 1
            out.append(ch)
            continue
        if ch == "[":
            depth_brack += 1
            out.append(ch)
            continue
        if ch == "{":
            depth_brace += 1
            out.append(ch)
            continue
        if ch == ")":
            if depth_paren == 0:
                # End of outer call → arg ends here
                return "".join(out)
            depth_paren -= 1
            out.append(ch)
            continue
        if ch == "]":
            depth_brack = max(0, depth_brack - 1)
            out.append(ch)
            continue
        if ch == "}":
            depth_brace = max(0, depth_brace - 1)
            out.append(ch)
            continue
        if ch == "," and depth_paren == 0 and depth_brack == 0 and depth_brace == 0:
            return "".join(out)
        out.append(ch)
    return "".join(out)


def _normalize_arg_expression(expr: str) -> tuple[str | None, bool]:
    """Take a JS expression like '"/api/saves/" + sid + "/delete"' and return
    a normalised path string and a low-confidence flag.

    Returns (None, _) if the expression has no /api/ string literal."""
    low = False
    if "+" in expr or "${" in expr or "`" in expr or "(" in expr:
        low = True

    # First: collapse balanced function calls e.g. encodeURIComponent(id) → {arg}
    expr_clean = _strip_calls(expr)
    # Backticks → double quotes so the literal walker treats them uniformly
    expr_clean = expr_clean.replace("`", '"')
    # Template interpolations ${...} → {arg}
    expr_clean = JS_INTERP_RE.sub("{arg}", expr_clean)

    # Walk: alternate quoted strings and outer code.
    parts = re.findall(r'(["\'])([^"\']*)\1|([^"\']+)', expr_clean)
    pieces: list[str] = []
    saw_api = False
    last_was_string = False
    for q, lit, mid in parts:
        if q:
            pieces.append(lit)
            if "/api/" in lit:
                saw_api = True
            last_was_string = True
        else:
            # mid is the connective code between strings.
            # If it contains a + on either side bridging a variable, that's a
            # dynamic insertion → {arg}.
            if "+" in mid and re.search(r"[a-zA-Z_${]", mid):
                pieces.append("{arg}")
                low = True
            last_was_string = False
    if not saw_api:
        return None, low
    full = "".join(pieces)
    idx = full.find("/api/")
    if idx < 0:
        return None, low
    full = full[idx:]
    full = re.split(r"[\s,)]", full, maxsplit=1)[0]
    return full, low


def _strip_calls(expr: str) -> str:
    """Replace every `IDENT(...)` (balanced parens) with `{arg}`. String literals
    inside calls are NOT preserved — the goal is to collapse function-call
    dynamic segments to a single placeholder so the outer concat walker only
    sees real path literals."""
    out: list[str] = []
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        # Detect IDENT(...) start
        if ch.isalpha() or ch == "_" or ch == "$":
            j = i
            while j < n and (expr[j].isalnum() or expr[j] in "_$."):
                j += 1
            # Skip whitespace
            k = j
            while k < n and expr[k] == " ":
                k += 1
            if k < n and expr[k] == "(":
                # find matching close
                depth = 1
                m = k + 1
                in_str: str | None = None
                escape = False
                while m < n and depth > 0:
                    c = expr[m]
                    if escape:
                        escape = False
                    elif in_str:
                        if c == "\\":
                            escape = True
                        elif c == in_str:
                            in_str = None
                    elif c in ('"', "'", "`"):
                        in_str = c
                    elif c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                    m += 1
                if depth == 0:
                    out.append("{arg}")
                    i = m
                    continue
            # Not a call — emit identifier as-is
            out.append(expr[i:j])
            i = j
            continue
        # Pass strings through verbatim so the literal walker can find them
        if ch in ('"', "'", "`"):
            quote = ch
            out.append(ch)
            i += 1
            escape = False
            while i < n:
                c = expr[i]
                out.append(c)
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)

# String literal that LOOKS like an /api endpoint inside any code:
FE_INLINE_API_RE = re.compile(r'(["\'`])(/api/[A-Za-z0-9_\-./{}$:+]*)\1')

# EventSource("/api/.../stream")  → GET
FE_EVENTSOURCE_RE = re.compile(
    r'\bEventSource\s*\(\s*(["\'`])([^"\'`]*)\1'
)


def _record_call(scan: Scan, method: str, raw_path: str, src: Path, line_no: int, raw_line: str, force_low: bool = False) -> None:
    if not raw_path or "/api/" not in raw_path:
        return
    path, low = _norm_path(raw_path)
    if not path.startswith("/api"):
        return
    conf = "low" if (low or force_low) else "high"
    ep = Endpoint(method.upper(), path)
    scan.calls[ep].append(Hit(ep, _rel(src), line_no, conf, raw_line.strip()))


def scan_frontend_calls(scan: Scan) -> None:
    files: list[Path] = list(FRONTEND_CORE_FILES)
    if FRONTEND_SRC.exists():
        files.extend(sorted(p for p in FRONTEND_SRC.glob("*.jsx")))
    seen: set[Path] = set()
    for f in files:
        if f in seen:
            continue
        seen.add(f)
        lines = _read(f)
        if lines is None:
            scan.files_missing.append(f)
            continue
        scan.files_scanned.append(f)

        for i, line in enumerate(lines, 1):
            # Wrapper helpers + fetch() + sseStream() — balanced-paren extraction
            for m in FE_WRAPPER_START_RE.finditer(line):
                fn = m.group(1).upper()
                expr = _extract_first_arg(line[m.end():])
                if expr is None:
                    continue
                path, low = _normalize_arg_expression(expr)
                if not path:
                    continue
                if fn == "_SEND":
                    mm = FE_METHOD_KV_RE.search(line)
                    method = mm.group(1).upper() if mm else "ANY"
                elif fn == "FETCH":
                    mm = FE_METHOD_KV_RE.search(line)
                    method = mm.group(1).upper() if mm else "GET"
                elif fn == "SSESTREAM":
                    method = "POST"
                elif fn == "OPENEVENTSOURCE":
                    method = "GET"
                    low = True
                else:
                    method = "DELETE" if fn == "DEL" else fn
                _record_call(scan, method, path, f, i, line, force_low=low)

            # BASE + "/api/..." URL builders (e.g. exportUrl, CSV download links)
            for rx in (FE_BASE_CONCAT_RE, FE_API_BASE_CONCAT_RE):
                for m in rx.finditer(line):
                    expr = m.group(1)
                    path, low = _normalize_arg_expression(expr)
                    if path:
                        _record_call(scan, "GET", path, f, i, line, force_low=True)

            # EventSource("/api/...") direct (back-compat)
            for m in FE_EVENTSOURCE_RE.finditer(line):
                raw_path = m.group(2)
                if "/api/" in raw_path:
                    idx = raw_path.find("/api/")
                    raw_path = raw_path[idx:]
                    _record_call(scan, "GET", raw_path, f, i, line, force_low=True)


# ---------------------------------------------------------------------------
# Docs scanner
# ---------------------------------------------------------------------------

# Pull METHOD `/path` or | METHOD | `/path` style mentions
DOC_TABLE_RE = re.compile(
    r'\b(GET|POST|PUT|DELETE|PATCH)\b[^\n`|]{0,40}?[`]([/][A-Za-z0-9_\-./{}:]+)[`]',
)
# Loose: any /api/... in backticks regardless of method
DOC_INLINE_RE = re.compile(r'`([/][A-Za-z0-9_\-./{}:]*\/api[A-Za-z0-9_\-./{}:]*)`')

COOKIE_NAME_RE = re.compile(
    r'\b(rpg_session(?:_[A-Za-z0-9]+)?)\b'
)
COOKIE_KW_RE = re.compile(r'\b(?:[Cc]ookie|SESSION_COOKIE|set_cookie|delete_cookie)\b')


def scan_docs(scan: Scan) -> None:
    for f in DOC_FILES:
        lines = _read(f)
        if lines is None:
            scan.files_missing.append(f)
            continue
        scan.files_scanned.append(f)
        for i, line in enumerate(lines, 1):
            for m in DOC_TABLE_RE.finditer(line):
                method = m.group(1).upper()
                path, low = _norm_path(m.group(2))
                if not path.startswith("/api"):
                    continue
                ep = Endpoint(method, path)
                scan.doc_mentions[ep].append(
                    Hit(ep, _rel(f), i, "low" if low else "high", line.strip())
                )
            # Cookie mentions in docs
            if COOKIE_KW_RE.search(line):
                for cm in COOKIE_NAME_RE.finditer(line):
                    name = cm.group(1)
                    scan.doc_cookies[name].append(
                        Hit(Endpoint("", ""), _rel(f), i, "high", line.strip())
                    )


def _scan_cookies(target: dict[str, list[Hit]], f: Path, lines: list[str]) -> None:
    for i, line in enumerate(lines, 1):
        if not COOKIE_KW_RE.search(line) and "rpg_session" not in line:
            continue
        for cm in COOKIE_NAME_RE.finditer(line):
            name = cm.group(1)
            target[name].append(Hit(Endpoint("", ""), _rel(f), i, "high", line.strip()))


# ---------------------------------------------------------------------------
# Drift analysis
# ---------------------------------------------------------------------------

@dataclass
class Drift:
    kind: str            # human-readable category
    severity: str        # "high" | "medium" | "low" | "info"
    endpoint: Endpoint | None
    summary: str
    details: list[str] = field(default_factory=list)


def _match_endpoint(target: Endpoint, pool: Iterable[Endpoint]) -> Endpoint | None:
    """Find a matching endpoint in pool. Method ANY/GET fallback allowed."""
    for ep in pool:
        if ep.path != target.path:
            continue
        if ep.method == target.method:
            return ep
        if "ANY" in (ep.method, target.method):
            return ep
    return None


def analyse(scan: Scan) -> list[Drift]:
    drifts: list[Drift] = []

    # 1) Frontend calls without a backend route → wishlist (real problem)
    for ep, hits in sorted(scan.calls.items(), key=lambda kv: (kv[0].path, kv[0].method)):
        if _match_endpoint(ep, scan.routes.keys()):
            continue
        any_low = any(h.confidence == "low" for h in hits)
        # Wrapper-only files (api-client.js) likely declare wishlist endpoints
        sev = "high" if not any_low else "medium"
        callers = ", ".join(sorted({f"{h.source}:{h.line}" for h in hits}))
        drifts.append(Drift(
            kind="frontend_only",
            severity=sev,
            endpoint=ep,
            summary=f"Frontend calls {ep.method} {ep.path} but no backend route matches",
            details=[f"callers: {callers}",
                     f"confidence: {'low' if any_low else 'high'}"],
        ))

    # 2) Backend routes never called from frontend → dead endpoint (info)
    for ep, hits in sorted(scan.routes.items(), key=lambda kv: (kv[0].path, kv[0].method)):
        if _match_endpoint(ep, scan.calls.keys()):
            continue
        # Skip non-/api routes (e.g. "/" or "/app"): they're page handlers, not API.
        if not ep.path.startswith("/api"):
            continue
        defs = ", ".join(sorted({f"{h.source}:{h.line}" for h in hits}))
        drifts.append(Drift(
            kind="backend_only",
            severity="info",
            endpoint=ep,
            summary=f"Backend defines {ep.method} {ep.path} but no frontend caller found",
            details=[f"defined at: {defs}"],
        ))

    # 3) Doc-mentioned endpoint not in backend
    for ep in sorted(scan.doc_mentions.keys(), key=lambda e: (e.path, e.method)):
        if _match_endpoint(ep, scan.routes.keys()):
            continue
        srcs = ", ".join(sorted({f"{h.source}:{h.line}" for h in scan.doc_mentions[ep]}))
        drifts.append(Drift(
            kind="doc_orphan",
            severity="medium",
            endpoint=ep,
            summary=f"Docs reference {ep.method} {ep.path} but backend has no such route",
            details=[f"mentioned at: {srcs}"],
        ))

    # 4) Cookie name drift between docs and code
    code_names = set(scan.code_cookies.keys())
    doc_names = set(scan.doc_cookies.keys())
    if code_names and doc_names and code_names != doc_names:
        doc_only = sorted(doc_names - code_names)
        code_only = sorted(code_names - doc_names)
        if doc_only or code_only:
            details = []
            if doc_only:
                doc_locs = []
                for name in doc_only:
                    for h in scan.doc_cookies[name]:
                        doc_locs.append(f"{name} @ {h.source}:{h.line}")
                details.append("doc-only: " + "; ".join(doc_locs))
            if code_only:
                code_locs = []
                for name in code_only:
                    for h in scan.code_cookies[name]:
                        code_locs.append(f"{name} @ {h.source}:{h.line}")
                details.append("code-only: " + "; ".join(code_locs))
            drifts.append(Drift(
                kind="cookie_drift",
                severity="high",
                endpoint=None,
                summary=(
                    f"Cookie name mismatch — docs say {sorted(doc_names)}, "
                    f"code uses {sorted(code_names)}"
                ),
                details=details,
            ))

    return drifts


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

SEV_RANK = {"high": 0, "medium": 1, "info": 2, "low": 3}


def _group_for_report(drifts: list[Drift]) -> dict[str, list[Drift]]:
    g: dict[str, list[Drift]] = defaultdict(list)
    for d in drifts:
        g[d.kind].append(d)
    return g


def render_report(scan: Scan, drifts: list[Drift]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out: list[str] = []
    out.append("# API 契约漂移报告")
    out.append("")
    out.append(f"_生成时间_: {now}")
    out.append("")
    out.append("由 `python -m tools.contract_check` 自动生成。只读扫描，不修改任何源文件。")
    out.append("")

    # --- coverage --------------------------------------------------------
    out.append("## 1. 扫描覆盖")
    out.append("")
    out.append("| 类别 | 文件 | 状态 |")
    out.append("|---|---|---|")
    for f in BACKEND_FILES:
        ok = "找到" if f.exists() else "**缺失**"
        out.append(f"| backend | `{_rel(f)}` | {ok} |")
    for f in FRONTEND_CORE_FILES:
        ok = "找到" if f.exists() else "**缺失**"
        out.append(f"| frontend (core) | `{_rel(f)}` | {ok} |")
    if FRONTEND_SRC.exists():
        jsx_count = len(list(FRONTEND_SRC.glob("*.jsx")))
        out.append(f"| frontend (jsx 直接 fetch) | `{_rel(FRONTEND_SRC)}/*.jsx` | 扫描 {jsx_count} 个文件 |")
    for f in DOC_FILES:
        ok = "找到" if f.exists() else "**缺失**"
        out.append(f"| docs | `{_rel(f)}` | {ok} |")
    out.append("")

    routes_total = sum(1 for e in scan.routes if e.path.startswith("/api"))
    calls_total = len(scan.calls)
    out.append("| 指标 | 数量 |")
    out.append("|---|---|")
    out.append(f"| 后端路由 (`/api/*`) | {routes_total} |")
    out.append(f"| 后端路由 (含非 `/api`) | {len(scan.routes)} |")
    out.append(f"| 前端调用 endpoint | {calls_total} |")
    out.append(f"| 文档 endpoint 提及 | {len(scan.doc_mentions)} |")
    out.append(f"| 文档 cookie 名 | {sorted(scan.doc_cookies.keys())} |")
    out.append(f"| 代码 cookie 名 | {sorted(scan.code_cookies.keys())} |")
    out.append("")

    # --- drift summary ---------------------------------------------------
    out.append("## 2. 漂移汇总")
    out.append("")
    if not drifts:
        out.append("> 未发现漂移。前后端契约与文档一致。")
        out.append("")
    else:
        by_sev: dict[str, int] = defaultdict(int)
        by_kind: dict[str, int] = defaultdict(int)
        for d in drifts:
            by_sev[d.severity] += 1
            by_kind[d.kind] += 1
        out.append("| 严重性 | 数量 |")
        out.append("|---|---|")
        for sev in ("high", "medium", "info", "low"):
            if by_sev.get(sev):
                out.append(f"| {sev} | {by_sev[sev]} |")
        out.append("")
        out.append("| 类别 | 数量 | 含义 |")
        out.append("|---|---|---|")
        meanings = {
            "frontend_only": "前端调但后端没 (wishlist / 真问题)",
            "backend_only": "后端有但前端无 (dead endpoint / 信息性)",
            "doc_orphan": "文档提及但代码没实现",
            "cookie_drift": "cookie 名文档 vs 代码不一致",
        }
        for kind in ("frontend_only", "cookie_drift", "doc_orphan", "backend_only"):
            if by_kind.get(kind):
                out.append(f"| {kind} | {by_kind[kind]} | {meanings.get(kind, '')} |")
        out.append("")

    grouped = _group_for_report(drifts)

    # --- section per kind, ordered by severity ---------------------------
    sections = [
        ("frontend_only",
         "## 3. 前端调用但后端缺失 (wishlist / 真问题)",
         "前端代码引用了一个 endpoint，但后端没有对应路由。多数是 wishlist。"),
        ("cookie_drift",
         "## 4. Cookie 名不一致",
         "文档与代码使用了不同的 cookie 名。这是协议级 drift，必须对齐。"),
        ("doc_orphan",
         "## 5. 文档提及但代码缺失",
         "文档/契约里写了某 endpoint，但后端代码里搜不到。可能是文档过期。"),
        ("backend_only",
         "## 6. 后端有但前端没调 (信息性)",
         "可能是 dead endpoint、admin-only、SSE 直接订阅，或 curl 测试用。请人工判断。"),
    ]
    for kind, title, blurb in sections:
        out.append(title)
        out.append("")
        out.append(blurb)
        out.append("")
        items = grouped.get(kind) or []
        if not items:
            out.append("_无_")
            out.append("")
            continue
        if kind == "cookie_drift":
            for d in items:
                out.append(f"- **{d.summary}**")
                for line in d.details:
                    out.append(f"  - {line}")
            out.append("")
            continue
        # Endpoint table
        out.append("| 严重 | Method | Path | 信息 |")
        out.append("|---|---|---|---|")
        items_sorted = sorted(items, key=lambda d: (
            SEV_RANK.get(d.severity, 9),
            d.endpoint.path if d.endpoint else "",
            d.endpoint.method if d.endpoint else "",
        ))
        for d in items_sorted:
            method = d.endpoint.method if d.endpoint else "—"
            path = d.endpoint.path if d.endpoint else "—"
            info = "; ".join(d.details)
            out.append(f"| {d.severity} | `{method}` | `{path}` | {info} |")
        out.append("")

    # --- legend ----------------------------------------------------------
    out.append("## 7. 说明")
    out.append("")
    out.append("- 路径匹配以 path 字符串为准（FastAPI 占位 `{name:converter}` 与 JS 模板 "
               "`${var}` 都被规范化为 `{arg}`）。")
    out.append("- 信心 `low` 表示路径里有动态拼接（`${...}` 或 `\" + var + \"`），可能误报。")
    out.append("- 仅前后端各跑一个 grep pass，没有做完整 AST 分析；如果对路径有疑问，"
               "请翻到列出的行号亲眼核对。")
    out.append("- 本工具只读 — 报告自动覆盖 `rpg/docs/api_contract_drift.md`，不改任何源代码。")
    out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Stdout summary
# ---------------------------------------------------------------------------

def print_summary(scan: Scan, drifts: list[Drift]) -> None:
    print("=" * 70)
    print("API CONTRACT DRIFT CHECK")
    print("=" * 70)
    print(f"backend routes scanned    : {len(scan.routes)}")
    print(f"frontend calls scanned    : {len(scan.calls)}")
    print(f"doc endpoint mentions     : {len(scan.doc_mentions)}")
    print(f"doc  cookie names         : {sorted(scan.doc_cookies.keys())}")
    print(f"code cookie names         : {sorted(scan.code_cookies.keys())}")
    print(f"files scanned             : {len(scan.files_scanned)}")
    if scan.files_missing:
        print(f"files MISSING             : {[_rel(p) for p in scan.files_missing]}")
    print("-" * 70)
    by_sev: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    for d in drifts:
        by_sev[d.severity] += 1
        by_kind[d.kind] += 1
    print(f"drifts total              : {len(drifts)}")
    for sev in ("high", "medium", "info", "low"):
        if by_sev.get(sev):
            print(f"  {sev:<8}: {by_sev[sev]}")
    print()
    print("by kind:")
    for kind in ("frontend_only", "cookie_drift", "doc_orphan", "backend_only"):
        if by_kind.get(kind):
            print(f"  {kind:<16}: {by_kind[kind]}")
    print("-" * 70)
    # Top 5 by severity
    top = sorted(
        drifts,
        key=lambda d: (
            SEV_RANK.get(d.severity, 9),
            0 if d.kind == "frontend_only" else 1,
            d.endpoint.path if d.endpoint else "",
        ),
    )[:5]
    if top:
        print("top 5 (by severity):")
        for d in top:
            ep = f"{d.endpoint.method} {d.endpoint.path}" if d.endpoint else "—"
            print(f"  [{d.severity:<6}] {d.kind:<14} {ep:<50} {d.summary}")
    print("-" * 70)
    print(f"report written → {_rel(REPORT_PATH)}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    scan = Scan()
    scan_backend_routes(scan)
    scan_frontend_calls(scan)
    scan_docs(scan)
    drifts = analyse(scan)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(scan, drifts), encoding="utf-8")

    print_summary(scan, drifts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
