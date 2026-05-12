from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from .hooks import compact_test_output, estimate_tokens, gain, hook_response, record, rewrite_command
from .index import RepoIndex, extract_security_findings, extract_side_effects, is_test_file

SHIM_TOOLS = ["cat", "head", "tail", "rg", "grep", "find", "tree", "git", "pytest", "npm", "pnpm", "yarn", "cargo", "go"]

def fmt_list(title: str, items: list[str], empty: str = "none") -> list[str]:
    lines = [title + ":"]
    if items:
        lines += [f"- {x}" for x in items]
    else:
        lines.append(f"- {empty}")
    return lines


def precision_label(analyzer: str, precision: str, confidence: str) -> str:
    if analyzer == "python_ast" and confidence == "high":
        return "python_ast/high"
    if precision == "heuristic" or confidence == "low":
        return "heuristic/low"
    return f"{precision}/{confidence}"


def emit_tracked(lines: list[str], command_name: str, raw_token_estimate: int | None = None, *, payload: dict | None = None, json_mode: bool = False) -> None:
    """Print the rendered output and record a savings row.

    Two output paths:
    - Default: print the human-readable `lines` joined by newlines.
    - --json mode: print `payload` as a single JSON document (pretty-indented).
      Commands that haven't been wired for JSON yet wrap their text lines in a
      generic envelope so consumers always get parseable output.

    Two record paths:
    - Hook / PATH-shim: CODEWARD_ORIGINAL_COMMAND env var carries the literal
      raw command (e.g. 'cat foo.py'); we size that exactly.
    - Direct invocation (pure CLAUDE.md / Codex AGENTS.md mode): caller passes
      raw_token_estimate based on the agent's likely raw analogue.
    Commands without an honest raw analogue (symbol, callgraph, ...) pass nothing
    and are not recorded — they shouldn't inflate "saved" totals."""
    if json_mode:
        envelope = payload if payload is not None else {"command": command_name, "lines": list(lines)}
        text = json.dumps(envelope, indent=2, default=str)
    else:
        text = "\n".join(lines)
    print(text)
    original = os.environ.get("CODEWARD_ORIGINAL_COMMAND")
    if original:
        raw_tokens = estimate_raw_command_tokens(original)
        if raw_tokens > 0:
            record(Path.cwd(), f"hook: {original} -> codeward {command_name}", raw_tokens, estimate_tokens(text))
        return
    if raw_token_estimate is not None and raw_token_estimate > 0:
        record(Path.cwd(), f"direct: codeward {command_name}", raw_token_estimate, estimate_tokens(text))


def estimate_raw_command_tokens(command_text: str) -> int:
    try:
        parts = shlex.split(command_text)
    except ValueError:
        return 0
    if not parts:
        return 0
    head = Path(parts[0]).name
    if head in {"cat", "head", "tail"}:
        paths = [p for p in parts[1:] if not p.startswith("-")]
        if len(paths) == 1:
            try:
                return estimate_tokens(Path(paths[0]).read_text(errors="ignore"))
            except OSError:
                return 0
    if head in {"find", "tree", "git", "rg", "grep"}:
        code, out = run_capture_for_savings(command_text, rewritten=False)
        return estimate_tokens(out)
    return 0


def cmd_map(args) -> int:
    idx = RepoIndex(Path.cwd())
    langs = Counter(info.lang for info in idx.files.values() if not is_test_file(info.path))
    primary = langs.most_common(1)[0][0] if langs else "Unknown"
    scored = sorted(idx.files.values(), key=lambda i: (len(idx.dependents_of_file(i.path)), len(i.symbols), -i.lines), reverse=True)
    important = [
        {"path": info.path, "lang": info.lang, "lines": info.lines, "symbols": len(info.symbols), "role": role_for(info.path)}
        for info in scored[:20]
    ]
    payload = {
        "command": "map",
        "primary_language": primary,
        "root": str(idx.root),
        "counts": {"code_files": len(idx.code_files), "test_files": len(idx.test_files)},
        "languages": dict(langs.most_common()),
        "important_files": important,
        "suggested_next": ["codeward review --changed", "codeward impact --changed", "codeward symbol <Name>"],
    }
    lines = ["# Codeward semantic summary", f"{primary} repo", f"Root: {idx.root}", f"Files: {len(idx.code_files)} code, {len(idx.test_files)} tests"]
    lines.append("Languages: " + ", ".join(f"{k}={v}" for k, v in langs.most_common()) if langs else "Languages: none")
    lines.append("\nImportant files:")
    for f in important:
        lines.append(f"- {f['path']} — {f['lang']}, {f['lines']} lines, {f['symbols']} symbols, {f['role']}")
    lines.append("\nSuggested next commands:")
    lines += [f"- {c}" for c in payload["suggested_next"]]
    raw_estimate = sum(len(p) + 1 for p in idx.files) // 4
    emit_tracked(lines, "map", raw_token_estimate=raw_estimate, payload=payload, json_mode=getattr(args, "json_output", False))
    return 0


def role_for(path: str) -> str:
    if is_test_file(path):
        return "test coverage"
    p = path.lower()
    if "service" in p:
        return "domain/service logic"
    if "controller" in p or "route" in p:
        return "request/API flow"
    if "db" in p or "model" in p or "schema" in p:
        return "data/model layer"
    return "source"


def cmd_watch(args) -> int:
    """Run a foreground re-indexer that keeps the SQLite cache fresh.
    Subsequent `codeward <cmd>` calls in the same repo become much faster
    because they load from a hot cache instead of rebuilding from scratch.
    Different surface from RTK (RTK has no daemon); no clash."""
    from .watch import run_watch
    return run_watch(Path.cwd(), debounce=getattr(args, "debounce", 0.5))


def cmd_preflight(args) -> int:
    """Compact 'what an editor should know before changing this file' summary.
    Used by Edit/Write PreToolUse hooks (and standalone). Output is intentionally
    short — it has to fit into the agent's context budget for every edit."""
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    rel = args.file.replace("\\", "/")
    if rel not in idx.files:
        msg = f"File not indexed: {rel}"
        if json_mode:
            print(json.dumps({"command": "preflight", "file": rel, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2
    info = idx.files[rel]
    deps = idx.dependents_of_file(rel)
    tests = idx.tests_for(rel)
    sec = extract_security_findings(idx.text(rel), info.lang)
    churn, hotspot_set = _recent_churn_and_hotspots(idx)
    commits_90d = churn.get(rel, 0)
    hotspot = rel in hotspot_set
    neighbors = _cochange_neighbors(idx, rel, top=3)
    blast = "HIGH" if len(deps) > 5 or any(k in rel.lower() for k in ["auth", "db", "session", "payment", "billing"]) else "MEDIUM" if deps else "LOW"
    if hotspot and blast != "HIGH":
        blast = "HIGH"
    recommended_checks = []
    if tests:
        pytests = [t for t in tests if t.endswith(".py")]
        recommended_checks.append("pytest " + " ".join(pytests[:3]) if pytests else "run likely related tests")
    if sec:
        recommended_checks.append("review security flags before editing")
    if blast == "HIGH":
        recommended_checks.append("inspect dependents and changed callers")
    if not recommended_checks:
        recommended_checks.append("run targeted project tests after editing")
    payload = {
        "command": "preflight", "file": rel, "language": info.lang, "lines": info.lines,
        "analyzer": info.analyzer, "precision": info.precision, "confidence": info.confidence,
        "symbols": len(info.symbols), "dependents": deps, "tests": tests,
        "side_effects": info.side_effects, "security_findings": sec, "blast_radius": blast,
        "hotspot": hotspot, "commits_90d": commits_90d, "neighbors": neighbors,
        "recommended_checks": recommended_checks,
    }
    out = [
        f"# Codeward preflight: {rel}",
        f"  language={info.lang}, lines={info.lines}, symbols={len(info.symbols)}, analyzer={precision_label(info.analyzer, info.precision, info.confidence)}, blast_radius={blast}",
    ]
    if deps:
        out.append(f"  dependents ({len(deps)}): {', '.join(deps[:6])}{', ...' if len(deps) > 6 else ''}")
    if tests:
        out.append(f"  likely tests: {', '.join(tests[:4])}{', ...' if len(tests) > 4 else ''}")
    if info.side_effects:
        out.append(f"  side effects: {', '.join(info.side_effects)}")
    if sec:
        out.append(f"  security flags: {', '.join(sec)}")
    if hotspot:
        out.append(f"  hotspot: yes ({commits_90d} commits in 90d)")
    if neighbors:
        out.append(f"  co-change neighbors: {', '.join(n['path'] for n in neighbors)}")
    out.append(f"  recommended next checks: {'; '.join(recommended_checks[:2])}")
    if json_mode:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("\n".join(out))
    return 0


def cmd_api(args) -> int:
    """Public API surface of a file or directory: top-level non-underscore
    symbols only. Skips test files. Useful for 'what does this module export?'
    without dumping its full contents."""
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    target = args.target.replace("\\", "/")
    matched = []
    if target in idx.files:
        matched = [target]
    else:
        # treat as directory prefix
        prefix = target.rstrip("/") + "/"
        matched = sorted(p for p in idx.files if p == target or p.startswith(prefix))
    if not matched:
        msg = f"No files matched: {target}"
        if json_mode:
            print(json.dumps({"command": "api", "target": target, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2
    file_rows: list[dict] = []
    for f in matched:
        if idx.is_test_file(f):
            continue
        info = idx.files[f]
        public = []
        for s in info.symbols:
            if s.kind == "method":
                continue  # methods covered under their class
            short = s.name.split(".")[-1]
            if short.startswith("_"):
                continue
            entry = {
                "name": s.name, "kind": s.kind, "line": s.line, "end_line": s.end_line,
                "signature": s.signature or f"{s.kind} {s.name}",
            }
            if s.methods:
                public_methods = [m for m in s.methods if not m.startswith("_")]
                entry["public_methods"] = public_methods
            public.append(entry)
        if public:
            file_rows.append({"file": f, "language": info.lang, "symbols": public})
    payload = {"command": "api", "target": target, "files": file_rows}
    out = [f"# Codeward API surface: {target}"]
    for r in file_rows:
        out.append(f"\n{r['file']}  ({r['language']})")
        for s in r["symbols"]:
            out.append(f"- {s['signature']}  @{s['line']}-{s['end_line']}")
            for pm in s.get("public_methods", []):
                out.append(f"    .{pm}")
    if not file_rows:
        out.append("No public symbols found")
    if json_mode:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("\n".join(out))
    return 0


def cmd_sdiff(args) -> int:
    """Semantic diff: list symbols added / removed / signature-changed between
    HEAD and a base ref. Replaces `git diff <base>` for symbol-level review.
    Use --base HEAD~1 for the last commit, or omit for unstaged working tree."""
    json_mode = getattr(args, "json_output", False)
    base = getattr(args, "base", None) or "HEAD"
    idx = RepoIndex(Path.cwd())
    file_rows = _semantic_diff_rows(idx, base)
    files = [row["file"] for row in file_rows]
    if not files:
        if json_mode:
            print(json.dumps({"command": "sdiff", "base": base, "files": []}, indent=2))
        else:
            print(f"No symbol-level changes vs {base}")
        return 0
    payload = {"command": "sdiff", "base": base, "files": file_rows}
    out = [f"# Codeward semantic diff vs {base}"]
    for r in file_rows:
        out.append(f"\n{r['file']}  ({r['status']})")
        for a in r.get("added", []):
            out.append(f"  + {a['signature'] or a['name']}")
        for d in r.get("removed", []):
            out.append(f"  - {d['signature'] or d['name']}")
        for c in r.get("changed", []):
            out.append(f"  ~ {c['name']}")
            out.append(f"      before: {c['before']}")
            out.append(f"      after:  {c['after']}")
    if not file_rows:
        out.append(f"No symbol-level changes vs {base}")
    if json_mode:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("\n".join(out))
    return 0


def cmd_blame(args) -> int:
    """Aggregate `git blame` output by author over a symbol's line range.
    Replaces `git blame -L X,Y -- file.py` with `codeward blame Foo.bar`.
    Output: per-author percentage, last-touched commit (sha + summary)."""
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    name = args.symbol
    syms = idx.find_symbol(re.sub(r"\(\*?([A-Za-z_]\w*)\)\.", r"\1.", name))
    if not syms:
        msg = f"Symbol not found: {name}"
        if json_mode:
            print(json.dumps({"command": "blame", "symbol": name, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2
    s = syms[0]
    if not s.end_line or s.end_line < s.line:
        msg = f"Symbol {s.name} has no recorded end_line"
        if json_mode:
            print(json.dumps({"command": "blame", "symbol": name, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2
    cp = subprocess.run(
        ["git", "blame", f"-L{s.line},{s.end_line}", "--line-porcelain", "--", s.file],
        cwd=Path.cwd(), text=True, capture_output=True,
    )
    if cp.returncode != 0:
        msg = f"git blame failed: {cp.stderr.strip()}"
        if json_mode:
            print(json.dumps({"command": "blame", "symbol": name, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return cp.returncode
    authors: dict[str, int] = {}
    last_commit = None
    last_summary = None
    current_author = None
    current_sha = None
    current_summary = None
    line_count = 0
    for ln in cp.stdout.splitlines():
        if ln.startswith("\t"):
            if current_author:
                authors[current_author] = authors.get(current_author, 0) + 1
                line_count += 1
                if last_commit != current_sha:
                    last_commit = current_sha
                    last_summary = current_summary
        elif ln.startswith("author "):
            current_author = ln[7:].strip()
        elif ln.startswith("summary "):
            current_summary = ln[8:].strip()
        elif re.match(r"^[0-9a-f]{40,}\s+\d+\s+\d+", ln):
            current_sha = ln.split()[0][:8]
    total = sum(authors.values()) or 1
    rows = sorted(((a, n, round(100*n/total, 1)) for a, n in authors.items()), key=lambda r: -r[1])
    payload = {
        "command": "blame", "symbol": s.name, "file": s.file, "line": s.line, "end_line": s.end_line,
        "total_lines": line_count,
        "authors": [{"author": a, "lines": n, "pct": p} for a, n, p in rows],
        "last_commit": {"sha": last_commit, "summary": last_summary} if last_commit else None,
    }
    out = ["# Codeward blame", f"{s.signature or s.name}  [{s.file}:{s.line}-{s.end_line}]", "Authors:"]
    for a, n, p in rows:
        out.append(f"  {p:5.1f}%  {n:>4} lines  {a}")
    if last_commit:
        out.append(f"Last touch: {last_commit}  {last_summary}")
    if json_mode:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("\n".join(out))
    return 0


def cmd_refs(args) -> int:
    """List every reference to a symbol with file:line + the matching line.
    Replaces `grep -rn <name> .`. Excludes the symbol's own definition by default.
    Use --include-defs to keep them."""
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    name = args.symbol
    syms = idx.find_symbol(name)
    bare = name.rsplit(".", 1)[-1]
    refs = idx.references_to(bare)
    def_files = {(s.file, s.line) for s in syms}
    include_defs = getattr(args, "include_defs", False)
    rows = [
        {"file": r.file, "line": r.line, "text": r.text[:160], "analyzer": r.analyzer, "precision": r.precision, "confidence": r.confidence, "kind": r.kind}
        for r in refs
        if include_defs or (r.file, r.line) not in def_files
    ]
    payload = {
        "command": "refs", "symbol": name,
        "definitions": [{"file": s.file, "line": s.line, "analyzer": s.analyzer, "precision": s.precision, "confidence": s.confidence} for s in syms],
        "references": rows, "total": len(rows),
    }
    out = ["# Codeward refs", f"Symbol: {name}  ({len(rows)} references, confidence-ranked)"]
    if syms:
        out.append("Defined:")
        for s in syms:
            out.append(f"  {s.file}:{s.line}  {s.signature or s.name}  [{precision_label(s.analyzer, s.precision, s.confidence)}]")
    out.append("References:")
    for r in rows[:80]:
        out.append(f"  {r['file']}:{r['line']}: {r['text']}  [{precision_label(r['analyzer'], r['precision'], r['confidence'])}]")
    if len(rows) > 80:
        out.append(f"  ... {len(rows) - 80} more")
    raw_estimate = sum(len(r["text"]) + len(r["file"]) + 8 for r in rows) // 4
    emit_tracked(out, f"refs {shlex.quote(name)}", raw_token_estimate=raw_estimate, payload=payload, json_mode=json_mode)
    return 0


def cmd_slice(args) -> int:
    """Print exact bytes of a symbol's body.
    Replaces `sed -n 'X,Yp' file` when you know what symbol you want but not
    where it is. Resolves dotted forms like `Engine.ServeHTTP`, `(*Engine).ServeHTTP`,
    `Console.print` against the index. Optional --no-comments strips comments
    and docstrings. Optional --signature-only emits just the signature line."""
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    name = args.symbol
    # Strip Go-style pointer receiver decoration: (*Engine).Foo -> Engine.Foo
    normalized = re.sub(r"\(\*?([A-Za-z_]\w*)\)\.", r"\1.", name)
    syms = idx.find_symbol(normalized)
    if not syms and "." in normalized:
        # Fallback: search by the trailing component
        syms = idx.find_symbol(normalized.rsplit(".", 1)[-1])
    if not syms:
        msg = f"Symbol not found: {name}"
        if json_mode:
            print(json.dumps({"command": "slice", "symbol": name, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2
    s = syms[0]
    if not s.end_line or s.end_line < s.line:
        msg = f"Symbol {s.name} has no recorded end_line; reindex required"
        if json_mode:
            print(json.dumps({"command": "slice", "symbol": name, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2
    text = idx.text(s.file)
    lines = text.splitlines()
    body = "\n".join(lines[s.line - 1:s.end_line])
    if getattr(args, "signature_only", False):
        body = s.signature or lines[s.line - 1]
    elif getattr(args, "no_comments", False):
        from .index import strip_comments_and_docstrings
        body = strip_comments_and_docstrings(body, idx.files[s.file].lang)
    payload = {
        "command": "slice", "symbol": s.name, "kind": s.kind, "file": s.file,
        "line": s.line, "end_line": s.end_line, "signature": s.signature, "body": body,
    }
    raw_estimate = estimate_tokens("\n".join(lines))  # `cat <file>` would have been the alternative
    if json_mode:
        print(json.dumps(payload, indent=2, default=str))
        # Track separately since we're not using emit_tracked
        original = os.environ.get("CODEWARD_ORIGINAL_COMMAND")
        if not original:
            record(Path.cwd(), f"direct: codeward slice {name}", raw_estimate, estimate_tokens(json.dumps(payload)))
    else:
        out = [
            f"# Codeward slice", f"{s.signature or s.name}  [{s.file}:{s.line}-{s.end_line}]", "", body,
        ]
        emit_tracked(out, f"slice {shlex.quote(name)}", raw_token_estimate=raw_estimate, payload=payload, json_mode=False)
    return 0


def cmd_read(args) -> int:
    idx = RepoIndex(Path.cwd())
    rel = args.file.replace("\\", "/")
    if rel not in idx.files:
        if getattr(args, "json_output", False):
            print(json.dumps({"command": "read", "error": f"File not indexed: {rel}"}, indent=2))
        else:
            print(f"File not indexed: {rel}", file=sys.stderr)
        return 2
    info = idx.files[rel]
    deps = idx.dependents_of_file(rel)
    tests = idx.tests_for(rel)
    methods_by_class: dict[str, list] = {}
    for s in info.symbols:
        if s.kind == "method" and "." in s.name:
            methods_by_class.setdefault(s.name.split(".", 1)[0], []).append(s)
    symbols_payload = []
    for s in info.symbols:
        if s.kind == "method":
            continue
        sym = {
            "name": s.name, "kind": s.kind, "line": s.line, "end_line": s.end_line,
            "signature": s.signature or f"{s.kind} {s.name}",
            "analyzer": s.analyzer, "precision": s.precision, "confidence": s.confidence,
        }
        if s.methods:
            sym["methods"] = [
                {"name": m.name.split(".", 1)[1], "line": m.line, "end_line": m.end_line, "signature": m.signature, "analyzer": m.analyzer, "precision": m.precision, "confidence": m.confidence}
                for m in methods_by_class.get(s.name, [])
            ] or [{"name": n} for n in s.methods]
        symbols_payload.append(sym)
    flow_payload = None
    if getattr(args, "flow", False):
        flow_slices = _flow_slices(idx, rel, info, getattr(args, "flow_count", 6))
        flow_payload = flow_slices
    payload = {
        "command": "read", "file": rel, "role": role_for(rel), "language": info.lang, "lines": info.lines,
        "analyzer": info.analyzer, "precision": info.precision, "confidence": info.confidence,
        "symbols": symbols_payload, "imports": info.imports[:12], "dependents": deps[:20], "tests": tests,
        "side_effects": info.side_effects, "raw_escape": f"!raw cat {shlex.quote(rel)}",
    }
    if flow_payload is not None:
        payload["flow"] = flow_payload
    out = ["# Codeward semantic summary", rel, f"Role: {payload['role']}", f"Language: {info.lang}", f"Lines: {info.lines}", f"Analyzer: {precision_label(info.analyzer, info.precision, info.confidence)}"]
    if symbols_payload:
        out.append("Exports/Symbols:")
        for s in symbols_payload:
            out.append(f"- {s['signature']}  @{s['line']}  [{precision_label(s['analyzer'], s['precision'], s['confidence'])}]")
            for m in s.get("methods", []):
                msig = m.get("signature") or f"def {m['name']}"
                line = m.get("line", "")
                if {"analyzer", "precision", "confidence"} <= set(m):
                    out.append(f"    - {msig}  @{line}  [{precision_label(m['analyzer'], m['precision'], m['confidence'])}]")
                else:
                    out.append(f"    - {msig}  @{line}")
    if info.imports:
        out += fmt_list("Imports", info.imports[:12])
    out += fmt_list("Used by", deps[:20])
    out += fmt_list("Tests", tests)
    if info.side_effects:
        out += fmt_list("Side effects", info.side_effects)
    if flow_payload:
        out.append("\nFlow (compact method bodies):")
        out.extend(flow_payload)
    elif getattr(args, "flow", False):
        out.append("\nFlow: no method bodies extracted (file may be empty or non-Python)")
    out.append("Raw escape: !raw cat " + shlex.quote(rel))
    raw_estimate = 0
    try:
        raw_estimate = estimate_tokens((Path.cwd() / rel).read_text(errors="ignore"))
    except OSError:
        pass
    emit_tracked(out, "read " + shlex.quote(rel), raw_token_estimate=raw_estimate, payload=payload, json_mode=getattr(args, "json_output", False))
    return 0


def _flow_slices(idx, rel: str, info, count: int) -> list[str]:
    """Return a compact dump of the file's most useful method bodies.
    Strips comments/docstrings and trims long bodies. Helps agents see
    implementation flow without raw `cat`."""
    from .index import strip_comments_and_docstrings
    text = idx.text(rel)
    if not text:
        return []
    lines = text.splitlines()
    candidates: list[tuple[int, str, int, int]] = []
    for s in info.symbols:
        # Only dump executable bodies — classes already contain their methods,
        # so dumping a class body would just re-include them at the wrong granularity.
        if s.kind not in ("method", "function"):
            continue
        if s.end_line and s.end_line > s.line:
            size = s.end_line - s.line + 1
            # Skip very small bodies (1-3 lines) — usually just `return x` or `pass`.
            if size < 4:
                continue
            label = s.signature or f"{s.kind} {s.name}"
            candidates.append((size, label, s.line, s.end_line))
    candidates.sort(key=lambda t: -t[0])
    if not candidates:
        return []
    out: list[str] = []
    for size, label, start, end in candidates[:count]:
        body = "\n".join(lines[start - 1:end])
        cleaned = strip_comments_and_docstrings(body, info.lang)
        compact_lines = [ln for ln in cleaned.splitlines() if ln.strip()]
        if len(compact_lines) > 40:
            compact_lines = compact_lines[:40] + [f"    # ... {len(cleaned.splitlines()) - 40} more lines truncated"]
        out.append(f"\n## {label}  @{start}-{end}")
        out.extend(compact_lines)
    return out


def cmd_search(args) -> int:
    idx = RepoIndex(Path.cwd())
    hits = idx.search(args.query)
    by_file: dict[str, list[tuple[int, str]]] = {}
    for rel, line, text in hits:
        by_file.setdefault(rel, []).append((line, text))
    files_payload = []
    for rel in sorted(by_file):
        shown = by_file[rel][: args.per_file]
        files_payload.append({
            "file": rel,
            "matches": [{"line": ln, "text": t} for ln, t in shown],
            "shown": len(shown),
            "total": len(by_file[rel]),
        })
    payload = {"command": "search", "query": args.query, "total_matches": len(hits), "files": files_payload}
    out = [f"{len(hits)} matches for {args.query!r} in {len(by_file)} files"]
    for f in files_payload:
        out.append(f"\n{f['file']}:")
        for m in f["matches"]:
            out.append(f"  {m['line']}: {m['text'][:180]}")
        if f["total"] > f["shown"]:
            out.append(f"  ... {f['total'] - f['shown']} more")
    raw_estimate = sum(len(t) + len(rel) + 8 for rel, _, t in hits) // 4
    emit_tracked(out, "search " + shlex.quote(args.query), raw_token_estimate=raw_estimate, payload=payload, json_mode=getattr(args, "json_output", False))
    return 0


def cmd_symbol(args) -> int:
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    syms = idx.find_symbol(args.name)
    if not syms:
        hits = idx.search(args.name)
        if json_mode:
            print(json.dumps({"command": "symbol", "name": args.name, "definitions": [], "text_matches": [{"file": r, "line": l, "text": t} for r, l, t in hits[:20]]}, indent=2))
        else:
            print(f"Symbol not found; text matches: {len(hits)}")
            for rel, line, text in hits[:20]:
                print(f"- {rel}:{line}: {text}")
        return 1 if not hits else 0
    defs = []
    for s in syms:
        scope = set(idx.dependents_of_file(s.file)) | {s.file}
        callers = [r for r in idx.references_to(s.name, scope=scope) if r.file != s.file or s.kind != "class"]
        defs.append({
            "name": s.name, "kind": s.kind, "file": s.file, "line": s.line, "end_line": s.end_line,
            "signature": s.signature or f"{s.kind} {s.name}",
            "analyzer": s.analyzer, "precision": s.precision, "confidence": s.confidence,
            "methods": list(s.methods),
            "callers": [{"file": r.file, "line": r.line, "text": r.text[:140], "analyzer": r.analyzer, "precision": r.precision, "confidence": r.confidence} for r in callers[:30]],
            "tests": idx.tests_for(s.file),
        })
    payload = {"command": "symbol", "name": args.name, "definitions": defs}
    out = ["# Codeward semantic summary", f"Symbol: {args.name}"]
    for d in defs:
        out.append(f"Defined: {d['file']}:{d['line']}  {d['signature']}  [{precision_label(d['analyzer'], d['precision'], d['confidence'])}]")
        if d["methods"]:
            out += ["Methods:"] + [f"- {m}" for m in d["methods"]]
        out.append("Callers:")
        if d["callers"]:
            for c in d["callers"]:
                out.append(f"- {c['file']}:{c['line']}: {c['text']}  [{precision_label(c['analyzer'], c['precision'], c['confidence'])}]")
        else:
            out.append("- none found")
        out += fmt_list("Tests", d["tests"])
    if json_mode:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("\n".join(out))
    return 0


def cmd_callgraph(args) -> int:
    idx = RepoIndex(Path.cwd())
    query = args.query
    out = [f"Callgraph: {query}", "Precision: confidence-ranked; Python AST calls are high confidence, syntax/regex fallbacks are labeled."]
    effects: list[str] = []
    chain: list[dict] = []
    handler = None
    route_file = None
    for info in idx.files.values():
        if query in info.routes:
            handler = info.routes[query]
            route_file = info.path
            out.append(f"{query}")
            out.append(f"→ {handler}()")
            chain.append({"step": query, "handler": handler, "kind": "route", "analyzer": info.analyzer, "precision": info.precision, "confidence": info.confidence})
            break
    if handler:
        if route_file and route_file in idx.files:
            effects.extend(idx.files[route_file].side_effects)
        handler_symbols = idx.find_symbol(handler)
        if handler_symbols:
            handler_sym = handler_symbols[0]
            effects.extend(idx.files[handler_sym.file].side_effects)
            for class_name, method_name, inferred, analyzer, precision, confidence in calls_from_symbol_body_metadata(idx, handler_sym):
                tag = " (inferred)" if inferred else ""
                out.append(f"  → {class_name}.{method_name}(){tag}")
                target_file = file_for_class_method(idx, class_name, method_name)
                chain.append({"caller": handler, "callee": f"{class_name}.{method_name}", "inferred": inferred, "target_file": target_file, "analyzer": analyzer, "precision": precision, "confidence": confidence})
                if target_file and target_file in idx.files:
                    effects.extend(idx.files[target_file].side_effects)
        else:
            for ref in idx.references_to(handler)[:10]:
                out.append(f"  ↳ referenced at {ref.file}:{ref.line}: {ref.text[:100]}  [{precision_label(ref.analyzer, ref.precision, ref.confidence)}]")
                chain.append({"reference": {"file": ref.file, "line": ref.line, "text": ref.text[:100], "analyzer": ref.analyzer, "precision": ref.precision, "confidence": ref.confidence}})
    else:
        syms = idx.find_symbol(query)
        if syms:
            out.append(f"{query} defined at {syms[0].file}:{syms[0].line}")
            for ref in idx.references_to(query)[:20]:
                out.append(f"→ {ref.file}:{ref.line}: {ref.text[:140]}  [{precision_label(ref.analyzer, ref.precision, ref.confidence)}]")
                chain.append({"caller": {"file": ref.file, "line": ref.line, "text": ref.text[:140], "analyzer": ref.analyzer, "precision": ref.precision, "confidence": ref.confidence}, "callee": query})
            effects = idx.files[syms[0].file].side_effects
        else:
            effects = []
            out.append("No route/symbol match found. Try `codeward search`.")
    side = sorted(set(effects))
    out += fmt_list("Side effects", side)
    if getattr(args, "json_output", False):
        print(json.dumps({"command": "callgraph", "query": query, "chain": chain, "side_effects": side}, indent=2, default=str))
    else:
        print("\n".join(out))
    return 0


def calls_from_symbol_body(idx: RepoIndex, sym) -> list[tuple[str, str, bool]]:
    """Backward-compatible helper returning (class_name, method_name, inferred)."""
    return [(cls, method, inferred) for cls, method, inferred, _, _, _ in calls_from_symbol_body_metadata(idx, sym)]


def calls_from_symbol_body_metadata(idx: RepoIndex, sym) -> list[tuple[str, str, bool, str, str, str]]:
    """Returns list of (class_name, method_name, inferred, analyzer, precision, confidence). `inferred` is True
    when class_name was resolved from an earlier instance assignment rather than
    appearing literally at the call site."""
    text = idx.text(sym.file)
    if idx.files.get(sym.file) and idx.files[sym.file].analyzer == "python_ast":
        ast_calls = ast_calls_from_symbol_body(idx, sym)
        if ast_calls:
            return ast_calls
    lines = text.splitlines()
    if sym.line <= 0 or sym.line > len(lines):
        return []
    start_index = sym.line - 1
    def_line = lines[start_index]
    indent = len(def_line) - len(def_line.lstrip())
    body: list[str] = []
    for line in lines[start_index + 1:]:
        stripped = line.strip()
        line_indent = len(line) - len(line.lstrip())
        if stripped and line_indent <= indent and re.match(r"(?:async\s+def|def|class)\s+", stripped):
            break
        body.append(line)
    instance_to_class: dict[str, str] = {}
    for line in lines[:start_index]:
        for m in re.finditer(r"\b([a-z_][\w]*)\s*=\s*([A-Z][\w]*)\s*\(", line):
            instance_to_class[m.group(1)] = m.group(2)
    for line in body:
        for m in re.finditer(r"\b([a-z_][\w]*)\s*=\s*([A-Z][\w]*)\s*\(", line):
            instance_to_class[m.group(1)] = m.group(2)
    found: list[tuple[str, str, bool, str, str, str]] = []
    for line in body:
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\s*\([^)]*\)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", line):
            found.append((m.group(1), m.group(2), False, "regex", "heuristic", "low"))
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", line):
            found.append((m.group(1), m.group(2), False, "regex", "heuristic", "low"))
        for m in re.finditer(r"\b([a-z_][\w]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", line):
            instance, method = m.group(1), m.group(2)
            cls = instance_to_class.get(instance)
            if cls:
                found.append((cls, method, True, "regex", "heuristic", "low"))
    seen = set()
    out = []
    for cls, method, inferred, analyzer, precision, confidence in found:
        key = (cls, method)
        if key in seen:
            continue
        seen.add(key)
        out.append((cls, method, inferred, analyzer, precision, confidence))
    return sorted(out, key=lambda t: (t[0], t[1]))


def ast_calls_from_symbol_body(idx: RepoIndex, sym) -> list[tuple[str, str, bool, str, str, str]]:
    import ast
    try:
        tree = ast.parse(idx.text(sym.file))
    except SyntaxError:
        return []
    target_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == sym.line:
            target_node = node
            break
    if target_node is None:
        return []
    instance_to_class: dict[str, str] = {}
    found: list[tuple[str, str, bool, str, str, str]] = []
    for node in ast.walk(target_node):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            class_name = node.value.func.id
            if class_name[:1].isupper():
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        instance_to_class[target.id] = class_name
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            value = node.func.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id[:1].isupper():
                found.append((value.func.id, method, False, "python_ast", "exact_range", "high"))
            elif isinstance(value, ast.Name) and value.id in instance_to_class:
                found.append((instance_to_class[value.id], method, True, "python_ast", "exact_range", "high"))
            elif isinstance(value, ast.Name) and value.id[:1].isupper():
                found.append((value.id, method, False, "python_ast", "exact_range", "high"))
    seen = set()
    out = []
    for row in found:
        key = row[:2]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return sorted(out, key=lambda t: (t[0], t[1]))


def file_for_class_method(idx: RepoIndex, class_name: str, method_name: str) -> str | None:
    for info in idx.files.values():
        for sym in info.symbols:
            if sym.kind == "class" and sym.name == class_name and method_name in sym.methods:
                return sym.file
    return None


def selected_files(idx: RepoIndex, args) -> list[str]:
    if getattr(args, "changed", False):
        return idx.changed_files(getattr(args, "base", None))
    if getattr(args, "target", None):
        return [args.target]
    return idx.changed_files(getattr(args, "base", None))


def cmd_tests_for(args) -> int:
    idx = RepoIndex(Path.cwd())
    tests = idx.tests_for(args.target)
    pytests = [t for t in tests if t.endswith(".py")]
    suggested = "pytest " + " ".join(shlex.quote(t) for t in pytests) if pytests else "run project test suite"
    out = [f"Likely tests for {args.target}:"]
    out += [f"- {t}" for t in tests] or ["- none found"]
    out.append(f"Suggested command: {suggested}")
    if getattr(args, "json_output", False):
        print(json.dumps({"command": "tests-for", "target": args.target, "tests": tests, "suggested_command": suggested}, indent=2))
    else:
        print("\n".join(out))
    return 0


def cmd_impact(args) -> int:
    idx = RepoIndex(Path.cwd())
    files = selected_files(idx, args)
    churn, hotspot_set = _recent_churn_and_hotspots(idx)
    rows = []
    out = ["Impact analysis:"]
    if not files:
        out.append("No changed files found.")
    for f in files:
        deps = idx.dependents_of_file(f) if f in idx.files else []
        tests = idx.tests_for(f)
        risk = "HIGH" if len(deps) > 3 or any(k in f.lower() for k in ["auth", "db", "session", "payment"]) else "MEDIUM" if deps else "LOW"
        is_hot = f in hotspot_set
        if is_hot and risk != "HIGH":
            risk = "HIGH"
        commits_90d = churn.get(f, 0)
        rows.append({
            "file": f,
            "dependents": deps,
            "tests": tests,
            "risk": risk,
            "hotspot": is_hot,
            "commits_90d": commits_90d,
        })
        out.append(f"\nChanged: {f}")
        out += fmt_list("Direct dependents", deps)
        out += fmt_list("Likely affected tests", tests)
        out.append(f"Risk: {risk}")
        if is_hot:
            out.append(f"  (hotspot: yes — {commits_90d} commits in 90d)")
    if getattr(args, "json_output", False):
        print(json.dumps({"command": "impact", "files": rows}, indent=2))
    else:
        print("\n".join(out))
    return 0


def cmd_review(args) -> int:
    idx = RepoIndex(Path.cwd())
    files = selected_files(idx, args)
    base = getattr(args, "base", None) or "HEAD"
    changed_by_file = {row["file"]: row for row in _semantic_diff_rows(idx, base)}
    churn, hotspot_set = _recent_churn_and_hotspots(idx)
    rows = []
    out = ["Review summary:"]
    if not files:
        out.append("No changed files found.")
    all_tests = []
    all_security: list[dict] = []
    for f in files:
        row: dict = {"file": f}
        out.append(f"\nChanged file: {f}")
        if f in idx.files:
            info = idx.files[f]
            row["analyzer"] = info.analyzer
            row["precision"] = info.precision
            row["confidence"] = info.confidence
            row["symbols"] = [{"name": s.name, "kind": s.kind, "analyzer": s.analyzer, "precision": s.precision, "confidence": s.confidence} for s in info.symbols]
            diff_row = changed_by_file.get(f, {"added": [], "removed": [], "changed": []})
            changed_symbols = (
                [{"name": s["name"], "change": "added"} for s in diff_row.get("added", [])]
                + [{"name": s["name"], "change": "removed"} for s in diff_row.get("removed", [])]
                + [{"name": s["name"], "change": "signature"} for s in diff_row.get("changed", [])]
            )
            row["changed_symbols"] = changed_symbols
            if changed_symbols:
                out.append(f"Actually changed symbols ({precision_label(info.analyzer, info.precision, info.confidence)}):")
                for s in changed_symbols:
                    out.append(f"- {s['change']} {s['name']}")
            elif info.symbols:
                out.append(f"File symbol inventory ({precision_label(info.analyzer, info.precision, info.confidence)}):")
                for s in info.symbols:
                    out.append(f"- {s.kind} {s.name}  [{precision_label(s.analyzer, s.precision, s.confidence)}]")
            effects = info.side_effects or extract_side_effects(idx.text(f))
            row["risks"] = effects
            if effects:
                out.append("Risks:")
                for e in effects:
                    out.append(f"- {e}: verify transactional behavior, errors, and tests")
            row["security_findings"] = []
            if getattr(args, "security", False):
                for finding in extract_security_findings(idx.text(f), info.lang):
                    all_security.append({"file": f, "finding": finding})
                    row["security_findings"].append(finding)
            tests = idx.tests_for(f)
            row["tests"] = tests
            all_tests.extend(tests)
            out += fmt_list("Missing/related tests to inspect", tests)
            row["hotspot"] = f in hotspot_set
            row["commits_90d"] = churn.get(f, 0)
            summary_bits = []
            if changed_symbols:
                summary_bits.append(f"{len(changed_symbols)} changed symbols")
            if effects:
                summary_bits.append(f"side effects: {', '.join(effects[:2])}")
            if row["hotspot"]:
                summary_bits.append(f"hotspot ({row['commits_90d']} commits/90d)")
            if not tests:
                summary_bits.append("no likely tests found")
            row["semantic_risk_summary"] = "; ".join(summary_bits) or "no elevated semantic risk detected"
        rows.append(row)
    pytests = sorted(set(t for t in all_tests if t.endswith(".py")))
    suggested = "pytest " + " ".join(pytests) if pytests else "run targeted project tests"
    semantic_risk_summary = [
        {"file": row["file"], "summary": row.get("semantic_risk_summary", "not indexed")}
        for row in rows
    ]
    if getattr(args, "security", False):
        out.append("\nSecurity findings:")
        if all_security:
            for s in all_security:
                out.append(f"- {s['file']}: {s['finding']}")
        else:
            out.append("- none found")
    out.append("\nSuggested commands:")
    out.append(f"- {suggested}")
    out.append("- codeward impact --changed")
    if getattr(args, "json_output", False):
        print(json.dumps({
            "command": "review",
            "files": rows,
            "security_findings": all_security,
            "suggested_command": suggested,
            "semantic_risk_summary": semantic_risk_summary,
        }, indent=2))
    else:
        print("\n".join(out))
    return 0


def _defer_to_rtk(cmd: str) -> bool:
    """When RTK is on PATH, recommend its equivalent for commands where RTK's
    Bash output compression is the better-suited tool. Returns True if the
    caller should print a deferral notice and exit early."""
    if shutil.which("rtk") is None:
        return False
    print(f"# Codeward deferring to RTK")
    print(f"RTK is installed and handles `{cmd}` output compression natively.")
    print(f"Use `rtk {cmd}` instead — it's faster and Codeward adds nothing here.")
    print(f"Re-run with `codeward {cmd} --force` to use Codeward anyway.")
    return True


def cmd_status(args) -> int:
    if not getattr(args, "force", False) and _defer_to_rtk("git status"):
        return 0
    cp = subprocess.run(["git", "status", "--short"], text=True, capture_output=True)
    lines = cp.stdout.splitlines()
    counts = Counter(l[:2].strip() or "?" for l in lines)
    payload = {"command": "status", "changed_files": len(lines), "counts": dict(counts), "files": lines[:20]}
    out = [f"Git status: {len(lines)} changed files"]
    out += [f"- {k}: {v}" for k, v in counts.items()]
    out += ["- " + l for l in lines[:20]]
    emit_tracked(out, "status", payload=payload, json_mode=getattr(args, "json_output", False))
    return cp.returncode


def cmd_diff(args) -> int:
    if not getattr(args, "force", False) and _defer_to_rtk("git diff"):
        return 0
    cp = subprocess.run(["git", "diff", "--stat"], text=True, capture_output=True)
    payload = {"command": "diff", "stat": cp.stdout or ""}
    out = ["Git diff summary:", cp.stdout or "No unstaged diff."]
    emit_tracked(out, "diff", payload=payload, json_mode=getattr(args, "json_output", False))
    return cp.returncode


def cmd_test(args) -> int:
    if not getattr(args, "force", False) and _defer_to_rtk("test"):
        return 0
    command = args.command
    code, raw, summary = compact_test_output(command, Path.cwd(), env=clean_shim_env())
    print(summary)
    record(Path.cwd(), " ".join(command), estimate_tokens(raw), estimate_tokens(summary))
    return code


def cmd_gain(args) -> int:
    from .hooks import HISTORY, _read_history, global_history_path
    # Default is global so users see aggregated savings across every repo
    # they've used Codeward in, not just the cwd. --repo opts back into
    # the per-repo view; --all combines both (deduped).
    scope = "global"
    if getattr(args, "repo_scope", False):
        scope = "repo"
    elif getattr(args, "all_scope", False):
        scope = "all"
    elif getattr(args, "global_scope", False):
        scope = "global"

    if getattr(args, "json_output", False):
        if scope == "global":
            rows = _read_history(global_history_path())
        elif scope == "all":
            local = _read_history(Path.cwd() / HISTORY)
            global_rows = _read_history(global_history_path())
            seen = set(); rows = []
            for r in local + global_rows:
                k = (r.get("ts"), r.get("command"))
                if k in seen: continue
                seen.add(k); rows.append(r)
        else:
            rows = _read_history(Path.cwd() / HISTORY)
        saved = sum(r.get("saved_tokens", 0) for r in rows)
        total_raw = sum(r.get("raw_tokens", 0) for r in rows)
        out_total = sum(r.get("output_tokens", 0) for r in rows)
        pct = (saved / total_raw * 100) if total_raw else 0
        print(json.dumps({
            "command": "gain",
            "scope": scope,
            "rows": rows,
            "summary": {
                "commands_tracked": len(rows),
                "raw_tokens": total_raw,
                "output_tokens": out_total,
                "tokens_saved": saved,
                "pct_saved": round(pct, 2),
            },
            "rtk_active": shutil.which("rtk") is not None,
        }, indent=2))
        return 0

    print(gain(Path.cwd(), scope=scope))
    if shutil.which("rtk") is not None:
        print("\nRTK is active — also see `rtk gain` for the cat/grep/find output-compression layer.")
    return 0


def run_capture_for_savings(command_text: str, rewritten: bool) -> tuple[int, str]:
    final = rewrite_command(command_text) if rewritten else None
    effective = final or command_text
    try:
        parts = shlex.split(effective)
    except ValueError as e:
        return 2, str(e)
    if rewritten and parts and parts[0] == "codeward":
        parts = [sys.executable, "-m", "codeward.cli", *parts[1:]]
    try:
        cp = subprocess.run(parts, cwd=Path.cwd(), text=True, capture_output=True, timeout=30, env=clean_shim_env())
    except FileNotFoundError as e:
        return 127, str(e)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        return 124, out + "\n[Codeward savings: command timed out]"
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def _git_log_commits(since: str = "90d", max_commits: int = 2000) -> list[list[str]]:
    """Return a list of commits; each commit is a list of repo-relative paths
    touched. Empty list on: non-git dir, missing git binary, timeout, or no
    commits in the window.

    Uses `--name-only -z --no-renames` plus a NUL-delimited commit sentinel
    so unusual paths and commit messages can't break parsing.
    """
    try:
        cp = subprocess.run(
            [
                "git", "log",
                f"--since={since}",
                "--name-only",
                "--no-renames",
                "-z",
                "--format=%x00COMMIT%x00",
                "-n", str(max_commits),
            ],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if cp.returncode != 0:
        return []
    commits: list[list[str]] = []
    for chunk in cp.stdout.split("\x00COMMIT\x00"):
        paths = [p.strip() for p in chunk.split("\x00") if p.strip()]
        if paths:
            commits.append(paths)
    return commits


def _recent_churn_and_hotspots(idx: RepoIndex) -> tuple[Counter[str], set[str]]:
    commits = _git_log_commits("90d", 2000)
    churn: Counter[str] = Counter()
    for paths in commits:
        for path in paths:
            if path in idx.files:
                churn[path] += 1
    if not churn:
        return churn, set()
    values = sorted(churn.values(), reverse=True)
    pivot = values[len(values) // 10] if len(values) >= 10 else values[0]
    threshold = max(3, pivot)
    return churn, {path for path, commits_90d in churn.items() if commits_90d >= threshold}


def _cochange_neighbors(idx: RepoIndex, target: str, top: int = 3) -> list[dict]:
    co: Counter[str] = Counter()
    for paths in _git_log_commits("90d", 2000):
        if target not in paths:
            continue
        for path in paths:
            if path != target and path in idx.files:
                co[path] += 1
    return [{"path": path, "co_changes": count} for path, count in co.most_common(top)]


def _semantic_diff_rows(idx: RepoIndex, base: str) -> list[dict]:
    from .index import analyze_file

    cp = subprocess.run(["git", "diff", "--name-only", base], cwd=Path.cwd(), text=True, capture_output=True)
    files = [f.strip() for f in cp.stdout.splitlines() if f.strip()]
    rows: list[dict] = []
    for rel in files:
        if rel not in idx.files:
            continue
        cur_info = idx.files[rel]
        cur_sig = {sym.name: (sym.kind, sym.signature or "") for sym in cur_info.symbols}
        base_cp = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=Path.cwd(), text=True, capture_output=True)
        if base_cp.returncode != 0:
            rows.append({
                "file": rel,
                "status": "new",
                "added": [{"name": name, "signature": sig[1]} for name, sig in cur_sig.items()],
                "removed": [],
                "changed": [],
            })
            continue
        try:
            base_info = analyze_file(rel, base_cp.stdout)
        except Exception:
            base_info = None
        if not base_info:
            rows.append({"file": rel, "status": "modified", "added": [], "removed": [], "changed": []})
            continue
        base_sig = {sym.name: (sym.kind, sym.signature or "") for sym in base_info.symbols}
        added = [{"name": name, "signature": cur_sig[name][1]} for name in cur_sig if name not in base_sig]
        removed = [{"name": name, "signature": base_sig[name][1]} for name in base_sig if name not in cur_sig]
        changed = [
            {"name": name, "before": base_sig[name][1], "after": cur_sig[name][1]}
            for name in cur_sig if name in base_sig and cur_sig[name][1] != base_sig[name][1]
        ]
        if added or removed or changed:
            rows.append({"file": rel, "status": "modified", "added": added, "removed": removed, "changed": changed})
    return rows


def _file_raw_tokens(idx: RepoIndex, rel: str) -> int:
    try:
        return estimate_tokens(idx.text(rel))
    except OSError:
        return 0


def _recommended_file_command(rel: str) -> str:
    return "codeward read " + shlex.quote(rel)


def cmd_budget(args) -> int:
    """Estimate raw repo/file token cost and point agents at cheaper commands.

    This is intentionally approximate (same 4 chars/token heuristic used by gain),
    but good enough to stop agents from blindly dumping the most expensive files.
    """
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    target = (getattr(args, "target", None) or "").replace("\\", "/").strip("/")
    top_n = max(1, int(getattr(args, "top", 10) or 10))
    candidates = list(idx.code_files)
    if target:
        if target in idx.files:
            candidates = [target]
        else:
            prefix = target.rstrip("/") + "/"
            candidates = [p for p in candidates if p == target or p.startswith(prefix)]
    rows = []
    total_raw = 0
    for rel in candidates:
        if rel not in idx.files:
            continue
        tokens = _file_raw_tokens(idx, rel)
        total_raw += tokens
        info = idx.files[rel]
        rows.append({
            "path": rel,
            "language": info.lang,
            "lines": info.lines,
            "raw_tokens": tokens,
            "symbols": len(info.symbols),
            "dependents": len(idx.dependents_of_file(rel)),
            "tests": len(idx.tests_for(rel)),
            "recommended_command": _recommended_file_command(rel),
        })
    rows.sort(key=lambda r: (r["raw_tokens"], r["dependents"], r["symbols"]), reverse=True)
    shown = rows[:top_n]
    payload = {
        "command": "budget",
        "target": target or ".",
        "estimated_raw_code_tokens": total_raw,
        "files_analyzed": len(rows),
        "files": shown,
        "tips": [
            "Use codeward read <file> before raw cat/head/tail.",
            "Use codeward pack <target> for a budgeted multi-file context bundle.",
            "Use codeward slice <symbol> when only one function/class matters.",
        ],
    }
    lines = [
        "# Codeward token budget",
        f"Target: {target or '.'}",
        f"Files analyzed: {len(rows)}",
        f"Estimated raw code tokens: {total_raw}",
        "",
        f"Top {len(shown)} token hotspots:",
    ]
    for row in shown:
        lines.append(f"- {row['path']} — ~{row['raw_tokens']} tokens, {row['lines']} lines, {row['symbols']} symbols")
        lines.append(f"  cheaper: {row['recommended_command']}")
        if row["tests"]:
            lines.append(f"  tests: codeward tests-for {shlex.quote(row['path'])}")
        if row["dependents"]:
            lines.append(f"  impact: codeward impact {shlex.quote(row['path'])}")
    lines.append("")
    lines += [f"Tip: {tip}" for tip in payload["tips"]]
    emit_tracked(lines, "budget", raw_token_estimate=total_raw, payload=payload, json_mode=json_mode)
    return 0


def _pack_target_files(idx: RepoIndex, target: str) -> list[str]:
    target = target.replace("\\", "/").strip("/")
    if target in idx.files:
        return [target]
    prefix = target.rstrip("/") + "/"
    dir_matches = sorted(p for p in idx.code_files if p.startswith(prefix))
    if dir_matches:
        return dir_matches[:12]
    syms = idx.find_symbol(target)
    if syms:
        return sorted({s.file for s in syms})
    hits = idx.search(target, include_tests=False)
    return sorted({p for p, _, _ in hits})[:12]


def _pack_file_row(idx: RepoIndex, rel: str, relation: str, top_symbols: int) -> dict:
    info = idx.files[rel]
    symbols = [s.signature or f"{s.kind} {s.name}" for s in info.symbols[:top_symbols]]
    return {
        "path": rel,
        "relation": relation,
        "language": info.lang,
        "lines": info.lines,
        "raw_tokens": _file_raw_tokens(idx, rel),
        "symbols": symbols,
        "side_effects": list(info.side_effects),
        "dependents": idx.dependents_of_file(rel),
        "tests": idx.tests_for(rel),
    }


def _render_pack_lines(target: str, rows: list[dict], max_tokens: int) -> list[str]:
    lines = ["# Codeward context pack", f"Target: {target}", f"Budget: ~{max_tokens} tokens", "", "Included files:"]
    for row in rows:
        lines.append(f"- {row['path']} ({row['relation']}) — {row['language']}, {row['lines']} lines, raw~{row['raw_tokens']} tokens")
        if row["symbols"]:
            lines.append("  symbols: " + "; ".join(row["symbols"][:6]))
        if row["side_effects"]:
            lines.append("  side effects: " + ", ".join(row["side_effects"][:4]))
        if row["tests"]:
            lines.append("  likely tests: " + ", ".join(row["tests"][:4]))
        if row["dependents"]:
            lines.append("  direct dependents: " + ", ".join(row["dependents"][:4]))
    lines.append("")
    lines.append("Next commands:")
    lines.append(f"- codeward budget {shlex.quote(target)}")
    for row in rows[:3]:
        lines.append(f"- codeward read {shlex.quote(row['path'])}")
    lines.append(f"Estimated pack tokens: {estimate_tokens(chr(10).join(lines))}")
    return lines


def cmd_pack(args) -> int:
    """Build a compact context bundle for a file, directory, symbol, or search query.

    It includes semantic summaries only, never full bodies, and stops before the
    requested approximate budget when possible.
    """
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    target = args.target.replace("\\", "/")
    max_tokens = max(80, int(getattr(args, "max_tokens", 800) or 800))
    top_symbols = max(1, int(getattr(args, "top_symbols", 6) or 6))
    targets = _pack_target_files(idx, target)
    if not targets:
        msg = f"No indexed files, symbols, or search hits matched: {target}"
        if json_mode:
            print(json.dumps({"command": "pack", "target": target, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    def add(rel: str, relation: str) -> None:
        if rel in idx.files and rel not in seen:
            seen.add(rel)
            ordered.append((rel, relation))

    for rel in targets:
        add(rel, "target")
    for rel in targets:
        for tf in idx.tests_for(rel):
            add(tf, "likely-test")
    for rel in targets:
        for dep in idx.dependents_of_file(rel):
            add(dep, "dependent")
    # Co-change neighbors — files that historically move together with the
    # target(s). Guarded so a git stall or non-repo never breaks pack.
    try:
        nb_commits = _git_log_commits("90d", 2000)
        co: Counter[str] = Counter()
        target_set = set(targets)
        for paths in nb_commits:
            if not target_set.intersection(paths):
                continue
            for p in paths:
                if p in idx.files and p not in target_set:
                    co[p] += 1
        for rel, _ in co.most_common(3):
            add(rel, "co-change")
    except Exception:
        pass
    # Search hits supply nearby context when target is a free-text query.
    for rel, _, _ in idx.search(target, include_tests=False)[:12]:
        add(rel, "search-hit")

    included: list[dict] = []
    for rel, relation in ordered:
        row = _pack_file_row(idx, rel, relation, top_symbols)
        trial = included + [row]
        trial_lines = _render_pack_lines(target, trial, max_tokens)
        if included and estimate_tokens("\n".join(trial_lines)) > max_tokens:
            continue
        included = trial
    lines = _render_pack_lines(target, included, max_tokens)
    payload = {
        "command": "pack",
        "target": target,
        "max_tokens": max_tokens,
        "estimated_pack_tokens": estimate_tokens("\n".join(lines)),
        "included_files": [r["path"] for r in included],
        "files": included,
    }
    raw_estimate = sum(r["raw_tokens"] for r in included)
    emit_tracked(lines, "pack", raw_token_estimate=raw_estimate, payload=payload, json_mode=json_mode)
    return 0


def _diff_pack_file_row(
    idx: RepoIndex,
    rel: str,
    semantic_rows: dict[str, dict],
    churn: Counter[str],
    hotspot_set: set[str],
    top_symbols: int,
    security: bool,
) -> dict:
    info = idx.files.get(rel)
    diff_row = semantic_rows.get(rel, {"added": [], "removed": [], "changed": []})
    changed_symbols = (
        [{"name": row["name"], "change": "added"} for row in diff_row.get("added", [])]
        + [{"name": row["name"], "change": "removed"} for row in diff_row.get("removed", [])]
        + [{"name": row["name"], "change": "signature"} for row in diff_row.get("changed", [])]
    )[:top_symbols]
    dependents = idx.dependents_of_file(rel) if info else []
    tests = idx.tests_for(rel)
    side_effects = list(info.side_effects) if info else []
    neighbors = _cochange_neighbors(idx, rel, top=3) if info else []
    security_findings = extract_security_findings(idx.text(rel), info.lang) if security and info else []
    hotspot = rel in hotspot_set
    commits_90d = churn.get(rel, 0)
    risk_points = (
        3 * int(hotspot)
        + 2 * int(bool(side_effects))
        + 2 * int(bool(security_findings))
        + min(3, len(dependents))
        + int(bool(changed_symbols))
        + int(not tests)
    )
    risk = "HIGH" if risk_points >= 5 else "MEDIUM" if risk_points >= 2 else "LOW"
    return {
        "path": rel,
        "risk": risk,
        "risk_points": risk_points,
        "hotspot": hotspot,
        "commits_90d": commits_90d,
        "changed_symbols": changed_symbols,
        "likely_tests": tests,
        "dependents": dependents,
        "neighbors": neighbors,
        "side_effects": side_effects,
        "security_findings": security_findings,
    }


def _render_diff_pack_lines(base: str, rows: list[dict], max_tokens: int, security: bool) -> list[str]:
    lines = [
        "# Codeward diff pack",
        f"Base: {base}",
        f"Budget: ~{max_tokens} tokens",
        f"Security scan: {'on' if security else 'off'}",
        "",
        "Included changed files:",
    ]
    for row in rows:
        lines.append(f"- {row['path']} — risk={row['risk']}, changed_symbols={len(row['changed_symbols'])}, hotspot={'yes' if row['hotspot'] else 'no'}")
        if row["changed_symbols"]:
            lines.append("  symbols: " + ", ".join(f"{sym['change']} {sym['name']}" for sym in row["changed_symbols"]))
        if row["likely_tests"]:
            lines.append("  likely tests: " + ", ".join(row["likely_tests"][:4]))
        if row["dependents"]:
            lines.append("  dependents: " + ", ".join(row["dependents"][:4]))
        if row["neighbors"]:
            lines.append("  co-change neighbors: " + ", ".join(n["path"] for n in row["neighbors"]))
        if row["side_effects"]:
            lines.append("  side effects: " + ", ".join(row["side_effects"][:4]))
        if row["security_findings"]:
            lines.append("  security findings: " + ", ".join(row["security_findings"][:4]))
    if not rows:
        lines.append("- none found")
    lines.append("")
    lines.append(f"Estimated pack tokens: {estimate_tokens(chr(10).join(lines))}")
    return lines


def cmd_diff_pack(args) -> int:
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    base = getattr(args, "base", None) or "HEAD"
    max_tokens = max(80, int(getattr(args, "max_tokens", 800) or 800))
    top_symbols = max(1, int(getattr(args, "top_symbols", 6) or 6))
    files = idx.changed_files(base if getattr(args, "base", None) else None)
    semantic_rows = {row["file"]: row for row in _semantic_diff_rows(idx, base)}
    churn, hotspot_set = _recent_churn_and_hotspots(idx)
    candidates = [
        _diff_pack_file_row(
            idx,
            rel,
            semantic_rows,
            churn,
            hotspot_set,
            top_symbols,
            getattr(args, "security", False),
        )
        for rel in files
        if rel in idx.files
    ]
    candidates.sort(key=lambda row: (row["risk_points"], len(row["changed_symbols"]), len(row["dependents"])), reverse=True)
    included: list[dict] = []
    for row in candidates:
        trial = included + [row]
        trial_lines = _render_diff_pack_lines(base, trial, max_tokens, getattr(args, "security", False))
        if included and estimate_tokens("\n".join(trial_lines)) > max_tokens:
            continue
        included = trial
    lines = _render_diff_pack_lines(base, included, max_tokens, getattr(args, "security", False))
    payload = {
        "command": "diff-pack",
        "base": base,
        "changed": bool(getattr(args, "changed", False)),
        "max_tokens": max_tokens,
        "top_symbols": top_symbols,
        "security": bool(getattr(args, "security", False)),
        "estimated_pack_tokens": estimate_tokens("\n".join(lines)),
        "included_files": [row["path"] for row in included],
        "files": included,
    }
    emit_tracked(lines, "diff-pack", raw_token_estimate=None, payload=payload, json_mode=json_mode)
    return 0


def cmd_hotspots(args) -> int:
    """Rank files by risk = churn (recent commits) x (1 + dependents).

    Surfaces the files most likely to break under a nearby edit, blending git
    history with the static dependency graph the index already maintains.
    """
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    since = getattr(args, "since", "90d") or "90d"
    top_n = max(1, int(getattr(args, "top", 10) or 10))
    max_commits = max(1, int(getattr(args, "max_commits", 2000) or 2000))
    commits = _git_log_commits(since, max_commits)
    churn: Counter[str] = Counter()
    for paths in commits:
        for p in paths:
            if p in idx.files:
                churn[p] += 1
    rows: list[dict] = []
    for path, n in churn.most_common():
        deps = len(idx.dependents_of_file(path))
        score = n * (1 + deps)
        rows.append({
            "path": path,
            "commits": n,
            "dependents": deps,
            "risk_score": score,
            "rationale": f"{n} commits in {since} x {deps} dependents = {score}",
        })
    rows.sort(key=lambda r: (r["risk_score"], r["commits"], r["dependents"]), reverse=True)
    shown = rows[:top_n]
    payload = {
        "command": "hotspots",
        "since": since,
        "top": top_n,
        "files_analyzed": len(rows),
        "files": shown,
    }
    lines = [
        "# Codeward hotspots",
        f"Window: {since}",
        f"Files with churn: {len(rows)}",
    ]
    if not rows:
        lines.append("No git history in window (or not a git repo).")
    else:
        lines.append("")
        lines.append(f"Top {len(shown)} risk files:")
        for row in shown:
            lines.append(
                f"- {row['path']} — {row['commits']} commits, {row['dependents']} dependents, score {row['risk_score']}"
            )
            lines.append(f"  {row['rationale']}")
    emit_tracked(lines, "hotspots", raw_token_estimate=None, payload=payload, json_mode=json_mode)
    return 0


def cmd_neighbors(args) -> int:
    """Files that historically change together with <file>.

    Aggregates co-occurrences from `git log` commits that touched the target
    file. Useful for `pack` and for agents to know which adjacent files
    usually need to move together with an edit.
    """
    idx = RepoIndex(Path.cwd())
    json_mode = getattr(args, "json_output", False)
    target = args.file.replace("\\", "/").strip("/")
    since = getattr(args, "since", "90d") or "90d"
    top_n = max(1, int(getattr(args, "top", 10) or 10))
    max_commits = max(1, int(getattr(args, "max_commits", 2000) or 2000))
    if target not in idx.files:
        msg = f"Not indexed: {target}"
        if json_mode:
            print(json.dumps({"command": "neighbors", "file": target, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 2
    commits = _git_log_commits(since, max_commits)
    co: Counter[str] = Counter()
    for paths in commits:
        if target not in paths:
            continue
        for p in paths:
            if p != target and p in idx.files:
                co[p] += 1
    neighbors = [{"path": p, "co_changes": c} for p, c in co.most_common(top_n)]
    payload = {
        "command": "neighbors",
        "file": target,
        "since": since,
        "top": top_n,
        "neighbors": neighbors,
    }
    lines = [
        "# Codeward co-change neighbors",
        f"Target: {target}",
        f"Window: {since}",
    ]
    if not neighbors:
        lines.append("No co-change neighbors found (or not a git repo).")
    else:
        lines.append("")
        lines.append(f"Top {len(neighbors)} neighbors:")
        for row in neighbors:
            lines.append(f"- {row['path']} — {row['co_changes']} co-changes")
    emit_tracked(lines, "neighbors", raw_token_estimate=None, payload=payload, json_mode=json_mode)
    return 0


def cmd_savings(args) -> int:
    commands = args.command or [
        "find . -maxdepth 3 -type f",
        "git status",
        "git diff",
    ]
    rows = []
    total_raw = 0
    total_out = 0
    for command_text in commands:
        rewritten = rewrite_command(command_text)
        if not rewritten:
            rows.append((command_text, "not rewritten", 0, 0, 0, 0.0))
            continue
        raw_code, raw = run_capture_for_savings(command_text, rewritten=False)
        out_code, compact = run_capture_for_savings(command_text, rewritten=True)
        raw_tokens = estimate_tokens(raw)
        out_tokens = estimate_tokens(compact)
        saved = max(raw_tokens - out_tokens, 0)
        pct = (saved / raw_tokens * 100) if raw_tokens else 0.0
        total_raw += raw_tokens
        total_out += out_tokens
        status = f"raw={raw_code}, codeward={out_code}"
        rows.append((command_text, rewritten, raw_tokens, out_tokens, saved, pct, status))
        if not args.no_history:
            record(Path.cwd(), f"savings: {command_text} -> {rewritten}", raw_tokens, out_tokens)
    total_saved = max(total_raw - total_out, 0)
    total_pct = (total_saved / total_raw * 100) if total_raw else 0.0
    lines = ["Codeward savings analysis", f"Commands analyzed: {len(rows)}", f"Total raw tokens: {total_raw}", f"Total Codeward tokens: {total_out}", f"Total saved: {total_saved} ({total_pct:.1f}%)", ""]
    for row in rows:
        if len(row) == 6:
            command_text, rewritten, *_ = row
            lines.append(f"- {command_text}: not rewritten")
            continue
        command_text, rewritten, raw_tokens, out_tokens, saved, pct, status = row
        lines.append(f"- {command_text}")
        lines.append(f"  rewrite: {rewritten}")
        lines.append(f"  tokens: raw={raw_tokens}, codeward={out_tokens}, saved={saved} ({pct:.1f}%)")
        lines.append(f"  status: {status}")
    print("\n".join(lines))
    return 0


def cmd_index(args) -> int:
    idx = RepoIndex(Path.cwd())
    path = idx.write_sqlite(Path(args.output) if args.output else None)
    print(f"Indexed {len(idx.code_files)} code files and {len(idx.test_files)} test files into {path}")
    return 0


def cmd_mcp(args) -> int:
    """Start the Codeward MCP server on stdio.

    Exposes every read-only semantic command as an MCP tool so any
    MCP-compatible client (Claude Desktop, Cursor, Continue, Zed, Cline,
    Goose, Windsurf, ChatGPT Desktop) can use Codeward natively — no
    bespoke hook config. Requires `pip install 'codeward[mcp]'`."""
    from . import mcp_server
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else None
    return mcp_server.run(cwd=cwd)


def clean_shim_env() -> dict[str, str]:
    env = os.environ.copy()
    shim_dir = env.get("CODEWARD_SHIM_DIR")
    if shim_dir:
        paths = [p for p in env.get("PATH", "").split(os.pathsep) if Path(p).resolve() != Path(shim_dir).resolve()]
        env["PATH"] = os.pathsep.join(paths)
    return env


def cmd_run(args) -> int:
    if args.shell_command:
        original = args.shell_command
        original_parts = shlex.split(original)
    else:
        command_args = list(args.command)
        if command_args and command_args[0] == "--":
            command_args = command_args[1:]
        original_parts = ([args.tool] if args.tool else []) + command_args
        original = shlex.join(original_parts)
    if not original_parts:
        print("codeward run: missing command", file=sys.stderr)
        return 2
    rewritten = rewrite_command(original)
    final_text = rewritten or original
    final_parts = shlex.split(final_text)
    if args.dry_run:
        print(final_text)
        return 0
    if rewritten:
        env = clean_shim_env()
        env["CODEWARD_ORIGINAL_COMMAND"] = original
        return subprocess.run(final_parts, env=env).returncode
    if args.shell_command:
        return subprocess.run(final_text, shell=True, env=clean_shim_env()).returncode
    return subprocess.run(original_parts, env=clean_shim_env()).returncode


def cmd_init_agent(args) -> int:
    root = Path.cwd()
    if shutil.which("rtk") is not None and not args.force:
        print("RTK is active on this system and already handles the cat/grep/find layer.", file=sys.stderr)
        print("Installing Codeward PATH shims would intercept `rtk`'s child lookups and double-transform commands.", file=sys.stderr)
        print("Re-run with --force to install anyway, or use `codeward init` (no hook) for the semantic-only setup.", file=sys.stderr)
        return 1
    bin_dir = root / args.bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in SHIM_TOOLS:
        shim = bin_dir / tool
        shim.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "export CODEWARD_SHIM_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            f"exec codeward run --tool {shlex.quote(tool)} -- \"$@\"\n"
        )
        shim.chmod(0o755)

    if args.agents_md:
        agents = root / "AGENTS.md"
        block = agent_instructions_block(args.bin_dir)
        existing = agents.read_text() if agents.exists() else ""
        start = "<!-- codeward-shims:start -->"
        end = "<!-- codeward-shims:end -->"
        if start in existing and end in existing:
            before, rest = existing.split(start, 1)
            _, after = rest.split(end, 1)
            agents.write_text(before.rstrip() + "\n\n" + block + after.lstrip())
        elif "## Codeward agent shell integration" not in existing:
            agents.write_text(existing.rstrip() + "\n\n" + block + "\n")
    print(f"Installed Codeward agent shims: {bin_dir}")
    print(f"Enable for Codex/Gemini/other agents with: export PATH=\"$PWD/{args.bin_dir}:$PATH\"")
    return 0


def agent_instructions_block(bin_dir: str) -> str:
    return f"""<!-- codeward-shims:start -->
## Codeward agent shell integration

Codeward is installed for coding agents that execute shell commands through this repository, including Codex, Gemini CLI, Claude Code, OpenCode, and other RTK-style agents.

Before running agent tasks from this repo, prepend the local shim directory:

```bash
export PATH=\"$PWD/{bin_dir}:$PATH\"
```

The shims forward common noisy tools through `codeward run --tool <name> ...`, so commands like `cat`, `rg`, `find`, `git diff`, and test commands become compact semantic Codeward output when appropriate. Commands Codeward should not handle pass through to the real tool with the shim directory removed from PATH to avoid recursion.

Use `!raw <command>` or invoke the real command outside `{bin_dir}` when exact raw output is required.
<!-- codeward-shims:end -->
"""


def cmd_coach(args) -> int:
    original = " ".join(args.command)
    better = rewrite_command(original)
    if better and better != original:
        print(f"Original command: {original}")
        print(f"Better command: {better}")
        print("Why: semantic Codeward commands return compact repo-aware output and avoid dumping raw context.")
        print(f"Bypass: !raw {original}")
    else:
        print(f"Command looks acceptable: {original}")
        print("Tip: use codeward map/read/symbol/impact/review when exploring code structure.")
    return 0


def cmd_hook(args) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as e:
        print(f"Codeward: Invalid hook JSON: {e}", file=sys.stderr)
        return 0
    try:
        response = hook_response(payload, agent=args.agent)
    except Exception as e:
        print(f"Codeward: hook failed: {e}", file=sys.stderr)
        return 0
    if response is not None:
        print(json.dumps(response))
    return 0


def _doctor_newest_source_mtime(root: Path) -> float:
    """Lightweight stat-only walk used by doctor — does not parse or analyze files."""
    from .index import IGNORE_DIRS, CODE_EXTS, MAX_INDEXABLE_BYTES, TEST_PATTERNS
    import fnmatch
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix not in CODE_EXTS and not any(fnmatch.fnmatch(name, pat) for pat in TEST_PATTERNS):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_INDEXABLE_BYTES:
                continue
            if st.st_mtime > newest:
                newest = st.st_mtime
    return newest


def _doctor_sqlite_file_count(db: Path) -> int | None:
    import sqlite3
    try:
        con = sqlite3.connect(db)
        try:
            return con.execute("select count(*) from files").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None


def cmd_doctor(args) -> int:
    issues: list[str] = []
    lines: list[str] = ["Codeward doctor"]

    rtk = shutil.which("rtk")
    if rtk:
        version = "unknown"
        try:
            cp = subprocess.run([rtk, "--version"], text=True, capture_output=True, timeout=5)
            version = (cp.stdout or cp.stderr).strip().splitlines()[0] if (cp.stdout or cp.stderr) else "unknown"
        except (OSError, subprocess.TimeoutExpired):
            pass
        lines.append(f"RTK: present at {rtk} ({version})")
    else:
        lines.append("RTK: not detected on PATH")

    def hook_position(settings_path: Path) -> tuple[str, int | None, int | None]:
        if not settings_path.exists():
            return ("absent", None, None)
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            return ("malformed", None, None)
        if not isinstance(data, dict):
            return ("malformed", None, None)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return ("absent", None, None)
        pre = hooks.get("PreToolUse")
        if not isinstance(pre, list):
            return ("absent", None, None)
        cs = rtk_idx = None
        for i, entry in enumerate(pre):
            if not isinstance(entry, dict) or entry.get("matcher") != "Bash":
                continue
            for h in entry.get("hooks") or []:
                if not isinstance(h, dict):
                    continue
                cmd = str(h.get("command", ""))
                first = _command_first_token(cmd)
                if cs is None and (first == "codeward" or "codeward" in cmd):
                    cs = i
                if rtk_idx is None and first == "rtk":
                    rtk_idx = i
        if cs is None:
            return ("absent", None, rtk_idx)
        return ("present", cs, rtk_idx)

    global_settings = Path.home() / ".claude" / "settings.json"
    state, cs_i, rtk_i = hook_position(global_settings)
    if state == "absent":
        lines.append(f"Global hook ({global_settings}): not installed")
    elif state == "malformed":
        lines.append(f"Global hook ({global_settings}): malformed JSON")
        issues.append("global settings.json is malformed")
    else:
        if rtk_i is not None and cs_i is not None and cs_i > rtk_i:
            lines.append(f"Global hook: present at index {cs_i}, BUT rtk runs first at index {rtk_i}")
            issues.append("Codeward hook should run before rtk; use `codeward init --hook --global` to fix")
        else:
            ordering = "before rtk" if rtk_i is not None else "rtk absent"
            lines.append(f"Global hook: present (ordering: {ordering})")

    project_settings = Path.cwd() / ".claude" / "settings.local.json"
    state, _, _ = hook_position(project_settings)
    lines.append(f"Project hook ({project_settings}): {state}")

    shim_dir = Path.cwd() / ".codeward" / "bin"
    on_path = any(Path(p).resolve() == shim_dir.resolve() for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    if shim_dir.exists():
        lines.append(f"PATH shims: {shim_dir} ({'on PATH' if on_path else 'NOT on PATH'})")
        if rtk and on_path:
            issues.append("PATH shims are active alongside RTK; rtk-invoked tools will route through Codeward shims")
            lines.append("WARNING: shims may double-transform commands invoked via rtk")
    else:
        lines.append("PATH shims: not installed")

    db = Path.cwd() / ".codeward" / "index.sqlite"
    if db.exists():
        try:
            db_mtime = db.stat().st_mtime
            newest = _doctor_newest_source_mtime(Path.cwd())
            stale = newest > db_mtime
            file_count = _doctor_sqlite_file_count(db)
            count_str = f", {file_count} code files" if file_count is not None else ""
            lines.append(f"Index: {db} ({'stale (will rebuild)' if stale else 'fresh'}{count_str})")
        except Exception as e:
            lines.append(f"Index: {db} (error: {e})")
    else:
        lines.append("Index: not built (run `codeward index` to create)")

    cfg_path = Path.cwd() / ".codeward" / "config.toml"
    if cfg_path.exists():
        from .index import load_repo_config
        cfg = load_repo_config(Path.cwd())
        if "_error" in cfg:
            lines.append(f"Config: {cfg_path} (malformed: {cfg['_error']})")
            issues.append("config.toml is malformed; fix or remove it")
        else:
            keys = [k for k in cfg if not k.startswith("_")]
            lines.append(f"Config: {cfg_path} (loaded: {', '.join(keys) or 'no overrides'})")
    else:
        lines.append("Config: no .codeward/config.toml (using defaults)")

    history = Path.cwd() / ".codeward" / "history.jsonl"
    if history.exists():
        try:
            rows = [json.loads(line) for line in history.read_text().splitlines() if line.strip()]
            saved = sum(r.get("saved_tokens", 0) for r in rows)
            lines.append(f"History: {len(rows)} entries, {saved} tokens saved")
            for r in rows[-3:]:
                lines.append(f"  - {r.get('command', '<no command>')[:100]} (saved {r.get('saved_tokens', 0)})")
        except (OSError, json.JSONDecodeError) as e:
            lines.append(f"History: error reading {history}: {e}")
    else:
        lines.append("History: empty")

    if rtk:
        lines.append("Tip: also check `rtk gain` for the cat/grep/find compression layer.")

    if issues:
        lines.append("\nIssues:")
        for i in issues:
            lines.append(f"- {i}")

    if getattr(args, "json_output", False):
        print(json.dumps({"command": "doctor", "lines": lines, "issues": issues, "ok": not issues}, indent=2))
    else:
        print("\n".join(lines))
    return 0 if not issues else 1


SEMANTIC_AGENTS_BLOCK_START = "<!-- codeward-semantic:start -->"
SEMANTIC_AGENTS_BLOCK_END = "<!-- codeward-semantic:end -->"


def semantic_agents_block(rtk_present: bool) -> str:
    rtk_line = (
        "RTK is also active on this system, and owns the Bash output-compression layer "
        "(`cat`, `rg`, `grep`, `find`, `git status`, etc.). Codeward does not auto-rewrite those — "
        "use them normally and RTK will compress the output. Reach for the commands below when you "
        "want a semantic answer instead of compressed text.\n\n"
    ) if rtk_present else ""
    return (
        f"{SEMANTIC_AGENTS_BLOCK_START}\n"
        "## Codeward semantic commands\n\n"
        f"{rtk_line}"
        "Prefer these for navigating and reasoning about the codebase:\n\n"
        "- `codeward map` — repo overview (primary language, important files, suggested next steps).\n"
        "- `codeward read <file>` — symbols, imports, dependents, likely tests, side effects.\n"
        "- `codeward search <query>` — grouped search hits across the repo.\n"
        "- `codeward symbol <Name>` — definition + confidence-ranked callers + tests for a symbol.\n"
        "- `codeward callgraph <route-or-symbol>` — confidence-ranked flow summary, with side effects.\n"
        "- `codeward tests-for <file-or-symbol>` — likely covering tests.\n"
        "- `codeward impact --changed` — risk + dependents for changed files.\n"
        "- `codeward review --changed [--security]` — pre-commit review pre-pass.\n"
        "- `codeward slice <Class.method>` — exact bytes when AST/tree-sitter ranges exist (replaces `sed -n 'X,Yp'`).\n"
        "- `codeward refs <symbol>` — confidence-ranked reference sites as file:line (replaces `grep -rn`).\n"
        "- `codeward blame <symbol>` — git blame aggregated by author over the symbol's range.\n"
        "- `codeward sdiff [--base ref]` — semantic diff: which symbols changed, not raw lines.\n"
        "- `codeward api <file-or-dir>` — public API surface (top-level non-underscore symbols).\n\n"
        "Use plain shell (or `!raw <cmd>` if a hook is configured) when you need exact byte-for-byte output.\n"
        f"{SEMANTIC_AGENTS_BLOCK_END}\n"
    )


def upsert_semantic_block(agents_path: Path, rtk_present: bool) -> None:
    block = semantic_agents_block(rtk_present)
    existing = agents_path.read_text() if agents_path.exists() else ""
    if SEMANTIC_AGENTS_BLOCK_START in existing and SEMANTIC_AGENTS_BLOCK_END in existing:
        before, rest = existing.split(SEMANTIC_AGENTS_BLOCK_START, 1)
        _, after = rest.split(SEMANTIC_AGENTS_BLOCK_END, 1)
        agents_path.write_text(before.rstrip() + "\n\n" + block + after.lstrip())
    else:
        sep = "\n\n" if existing.strip() else ""
        agents_path.write_text(existing.rstrip() + sep + block)


def _command_first_token(command: str) -> str:
    """Return the basename of the first shell token. Handles absolute paths
    like '/usr/local/bin/rtk hook claude'. Falls back to plain split on parse error."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return ""
    return Path(parts[0]).name


def insert_gemini_hook_entry(settings_path: Path, hook_command: str = "codeward hook --agent gemini") -> str:
    """Idempotently install a Gemini CLI BeforeTool/run_shell_command hook.
    Writes to the same `~/.gemini/settings.json` file Gemini already uses for
    auth/general/agents config — adds a sibling `hooks` section. Returns
    'added' or 'noop'. Raises RuntimeError on malformed JSON, never overwrites
    silently."""
    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            backup = settings_path.with_suffix(settings_path.suffix + ".broken")
            backup.write_text(settings_path.read_text())
            raise RuntimeError(
                f"{settings_path} is malformed JSON. Saved a copy at {backup} and refused to overwrite."
            )
        if not isinstance(data, dict):
            raise RuntimeError(f"{settings_path} top-level value is not a JSON object; refusing to mutate.")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RuntimeError(f"{settings_path}: 'hooks' is not an object; refusing to mutate.")
    before = hooks.setdefault("BeforeTool", [])
    if not isinstance(before, list):
        raise RuntimeError(f"{settings_path}: 'hooks.BeforeTool' is not a list; refusing to mutate.")

    def is_codeward(entry) -> bool:
        if not isinstance(entry, dict) or entry.get("matcher") != "run_shell_command":
            return False
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and "codeward" in str(h.get("command", "")):
                return True
        return False

    if any(is_codeward(e) for e in before):
        return "noop"
    before.append({
        "matcher": "run_shell_command",
        "hooks": [{"type": "command", "name": "codeward-rewrite", "command": hook_command}],
    })
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(settings_path)
    return "added"


def insert_edit_hook_entry(settings_path: Path, hook_command: str, matcher: str = "Edit|Write|MultiEdit") -> str:
    """Idempotently install a PreToolUse hook for Edit/Write tools that injects
    `codeward preflight` info via additionalContext. Doesn't clash with RTK
    (RTK only matches Bash). Returns 'added' or 'noop'."""
    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            backup = settings_path.with_suffix(settings_path.suffix + ".broken")
            backup.write_text(settings_path.read_text())
            raise RuntimeError(
                f"{settings_path} is malformed JSON. Saved a copy at {backup} and refused to overwrite. "
                "Fix or remove the file and rerun."
            )
        if not isinstance(data, dict):
            raise RuntimeError(f"{settings_path} top-level value is not a JSON object; refusing to mutate.")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RuntimeError(f"{settings_path}: 'hooks' is not an object; refusing to mutate.")
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        raise RuntimeError(f"{settings_path}: 'hooks.PreToolUse' is not a list; refusing to mutate.")

    def is_codeward_edit(entry) -> bool:
        if not isinstance(entry, dict) or entry.get("matcher") != matcher:
            return False
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and "codeward" in str(h.get("command", "")):
                return True
        return False

    if any(is_codeward_edit(e) for e in pre):
        return "noop"
    pre.append({"matcher": matcher, "hooks": [{"type": "command", "command": hook_command}]})
    tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(settings_path)
    return "added"


def insert_hook_entry(settings_path: Path, hook_command: str) -> str:
    """Idempotently insert a PreToolUse Bash hook before any existing rtk entry.
    Returns 'added', 'noop', or 'reordered'.
    Raises RuntimeError if the existing settings file is malformed and a backup
    cannot be safely taken — never silently overwrites user state."""
    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            backup = settings_path.with_suffix(settings_path.suffix + ".broken")
            backup.write_text(settings_path.read_text())
            raise RuntimeError(
                f"{settings_path} is malformed JSON. Saved a copy at {backup} and refused to overwrite. "
                "Fix or remove the file and rerun."
            )
        if not isinstance(data, dict):
            raise RuntimeError(f"{settings_path} top-level value is not a JSON object; refusing to mutate.")
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise RuntimeError(f"{settings_path}: 'hooks' is not an object; refusing to mutate.")
    pre = hooks.get("PreToolUse")
    if pre is None:
        pre = []
        hooks["PreToolUse"] = pre
    if not isinstance(pre, list):
        raise RuntimeError(f"{settings_path}: 'hooks.PreToolUse' is not a list; refusing to mutate.")

    def is_codeward(entry) -> bool:
        if not isinstance(entry, dict) or entry.get("matcher") != "Bash":
            return False
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and _command_first_token(str(h.get("command", ""))) == "codeward":
                return True
            if isinstance(h, dict) and "codeward" in str(h.get("command", "")):
                return True
        return False

    def is_rtk(entry) -> bool:
        if not isinstance(entry, dict) or entry.get("matcher") != "Bash":
            return False
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and _command_first_token(str(h.get("command", ""))) == "rtk":
                return True
        return False

    new_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": hook_command}]}
    rtk_indices = [i for i, e in enumerate(pre) if is_rtk(e)]
    cs_indices = [i for i, e in enumerate(pre) if is_codeward(e)]

    result = "noop"
    if cs_indices:
        if rtk_indices and min(cs_indices) > min(rtk_indices):
            entry = pre.pop(min(cs_indices))
            pre.insert(min(rtk_indices), entry)
            result = "reordered"
    else:
        target = min(rtk_indices) if rtk_indices else len(pre)
        pre.insert(target, new_entry)
        result = "added"

    tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(settings_path)
    return result


GLOBAL_MEMORY_TARGETS = [
    # (path, agent display name, parent-dir-must-exist hint).
    # If the agent's parent dir doesn't exist (i.e. the agent isn't installed),
    # we skip silently so we don't pollute home with empty config dirs for
    # agents the user doesn't use.
    (Path.home() / ".claude" / "CLAUDE.md", "Claude Code"),
    (Path.home() / ".codex"  / "AGENTS.md", "Codex"),
    (Path.home() / ".gemini" / "GEMINI.md", "Gemini CLI"),
]


def cmd_init(args) -> int:
    root = Path.cwd()
    rtk_present = shutil.which("rtk") is not None
    # Write to BOTH CLAUDE.md (Claude Code's auto-discovered memory file) and
    # AGENTS.md (the Codex/Cursor/generic-agent convention). Without this,
    # Claude Code agents would not see the semantic-command vocabulary at all
    # and would fall back to grep/sed for navigation.
    written = []
    for memfile in ("CLAUDE.md", "AGENTS.md"):
        upsert_semantic_block(root / memfile, rtk_present)
        written.append(str(root / memfile))
    print("Wrote project semantic command guide to:")
    for w in written:
        print(f"  - {w}")

    # --global writes the same vocabulary into each agent's GLOBAL memory file
    # so the teaching applies to every repo the user opens, not just this one.
    if args.global_install:
        global_written = []
        global_skipped = []
        for path, agent in GLOBAL_MEMORY_TARGETS:
            if not path.parent.exists():
                global_skipped.append((path, agent))
                continue
            upsert_semantic_block(path, rtk_present)
            global_written.append((path, agent))
        if global_written:
            print("\nWrote global semantic command guide to:")
            for p, a in global_written:
                print(f"  - {p}  ({a})")
        if global_skipped:
            print("\nSkipped (agent config dir not present — agent likely not installed):")
            for p, a in global_skipped:
                print(f"  - {p}  ({a})")

    if rtk_present:
        print("\nRTK detected — Codeward will not install a Bash hook by default. Re-run with --hook to opt in.")
    if not args.hook:
        return 0

    no_bash = getattr(args, "no_hook_bash", False)
    no_edit = getattr(args, "no_hook_edit", False)
    if no_bash and no_edit:
        print("error: --no-hook-bash and --no-hook-edit cannot both be set "
              "(would install nothing). Drop one or omit --hook.", file=sys.stderr)
        return 2

    hook = root / ".claude" / "hooks" / "codeward-hook.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\ncodeward hook --agent claude\n")
    hook.chmod(0o755)

    project_settings = root / ".claude" / "settings.local.json"
    project_settings.parent.mkdir(parents=True, exist_ok=True)

    # Bash rewrite hook: rewrites cat/grep/etc. to codeward equivalents and
    # tracks savings. Skipped with --no-hook-bash (e.g., when RTK already
    # owns the Bash surface and you only want preflight context for edits).
    if not no_bash:
        insert_hook_entry(project_settings, str(hook))
        print(f"Installed Codeward Bash rewrite hook: {hook}")
        print(f"  Wired into: {project_settings}")

    # Edit/Write preflight hook: different tool surface from RTK (RTK only fires
    # on Bash). Injects `codeward preflight` info before any file edit so the
    # agent sees dependents+tests+side-effects up-front. Skipped with --no-hook-edit.
    if not no_edit:
        insert_edit_hook_entry(project_settings, str(hook))
        print(f"Installed Codeward Edit/Write preflight hook  (different matcher from RTK; no clash).")

    if args.global_install:
        global_settings = Path.home() / ".claude" / "settings.json"
        global_settings.parent.mkdir(parents=True, exist_ok=True)
        if not no_bash:
            outcome = insert_hook_entry(global_settings, "codeward hook --agent claude")
            if outcome == "added":
                print(f"Added global Bash hook entry to {global_settings} (placed before any rtk entry).")
            elif outcome == "reordered":
                print(f"Re-ordered existing Codeward Bash hook in {global_settings} to run before rtk.")
            else:
                print(f"Global Bash hook already present in {global_settings} (no change).")
        if not no_edit:
            insert_edit_hook_entry(global_settings, "codeward hook --agent claude")
            print(f"Installed global Edit/Write preflight hook into {global_settings}.")

    # Gemini CLI hook: same shape (run_shell_command BeforeTool) — different
    # config file. Off by default unless --gemini is passed.
    if getattr(args, "install_gemini_hook", False):
        gemini_settings = Path.home() / ".gemini" / "settings.json"
        if not gemini_settings.parent.exists():
            print(f"Skipped Gemini hook: {gemini_settings.parent} doesn't exist (Gemini CLI not installed?)")
        else:
            try:
                outcome = insert_gemini_hook_entry(gemini_settings)
                if outcome == "added":
                    print(f"Installed Gemini BeforeTool/run_shell_command hook into {gemini_settings}.")
                else:
                    print(f"Gemini hook already present in {gemini_settings} (no change).")
            except RuntimeError as e:
                print(f"Gemini hook install failed: {e}", file=sys.stderr)

    if rtk_present and not no_bash:
        print("RTK is active. Codeward will rewrite to `codeward ...` first; RTK passes those through unchanged.")
    if not no_bash:
        print("Use !raw <command> to bypass Bash rewrites.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codeward", description="Semantic codebase intelligence for coding agents")
    # Parent parser provides --json to every read-only command. Stable schema
    # documented in docs/JSON_SCHEMA.md. Programmatic clients (CI tools, MCP
    # servers, IDE plugins) should prefer this over scraping text output.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", dest="json_output", action="store_true",
                        help="Emit structured JSON instead of human-readable text")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("map", parents=[common]).set_defaults(func=cmd_map)
    r = sub.add_parser("read", parents=[common]); r.add_argument("file")
    r.add_argument("--flow", action="store_true", help="Also dump compact bodies of the file's largest methods")
    r.add_argument("--flow-count", type=int, default=6, help="Number of methods to include with --flow (default: 6)")
    r.set_defaults(func=cmd_read)
    s = sub.add_parser("search", parents=[common]); s.add_argument("query"); s.add_argument("--per-file", type=int, default=5); s.set_defaults(func=cmd_search)
    sy = sub.add_parser("symbol", parents=[common]); sy.add_argument("name"); sy.set_defaults(func=cmd_symbol)
    cg = sub.add_parser("callgraph", parents=[common]); cg.add_argument("query"); cg.set_defaults(func=cmd_callgraph)
    tf = sub.add_parser("tests-for", parents=[common]); tf.add_argument("target"); tf.set_defaults(func=cmd_tests_for)
    im = sub.add_parser("impact", parents=[common]); im.add_argument("target", nargs="?"); im.add_argument("--changed", action="store_true"); im.add_argument("--base"); im.set_defaults(func=cmd_impact)
    rv = sub.add_parser("review", parents=[common]); rv.add_argument("target", nargs="?"); rv.add_argument("--changed", action="store_true"); rv.add_argument("--base"); rv.add_argument("--security", action="store_true"); rv.set_defaults(func=cmd_review)
    st = sub.add_parser("status", parents=[common]); st.add_argument("--force", action="store_true", help="Run even when RTK is installed (default defers to RTK)")
    st.set_defaults(func=cmd_status)
    df = sub.add_parser("diff", parents=[common]); df.add_argument("--force", action="store_true", help="Run even when RTK is installed (default defers to RTK)")
    df.set_defaults(func=cmd_diff)
    sl = sub.add_parser("slice", parents=[common]); sl.add_argument("symbol")
    sl.add_argument("--no-comments", action="store_true", help="Strip comments and docstrings from the body")
    sl.add_argument("--signature-only", action="store_true", help="Print only the signature line")
    sl.set_defaults(func=cmd_slice)
    rf = sub.add_parser("refs", parents=[common]); rf.add_argument("symbol")
    rf.add_argument("--include-defs", action="store_true", help="Also show the definition site(s)")
    rf.set_defaults(func=cmd_refs)
    bl = sub.add_parser("blame", parents=[common]); bl.add_argument("symbol")
    bl.set_defaults(func=cmd_blame)
    sd = sub.add_parser("sdiff", parents=[common]); sd.add_argument("--base", default="HEAD")
    sd.set_defaults(func=cmd_sdiff)
    ap = sub.add_parser("api", parents=[common]); ap.add_argument("target")
    ap.set_defaults(func=cmd_api)
    pf = sub.add_parser("preflight", parents=[common]); pf.add_argument("file")
    pf.set_defaults(func=cmd_preflight)
    wt = sub.add_parser("watch")
    wt.add_argument("--debounce", type=float, default=0.5, help="Coalesce file events within this many seconds (default: 0.5)")
    wt.set_defaults(func=cmd_watch)
    te = sub.add_parser("test"); te.add_argument("--force", action="store_true", help="Run even when RTK is installed"); te.add_argument("command", nargs=argparse.REMAINDER); te.set_defaults(func=cmd_test)
    ix = sub.add_parser("index"); ix.add_argument("--output"); ix.set_defaults(func=cmd_index)
    rn = sub.add_parser("run"); rn.add_argument("--dry-run", action="store_true"); rn.add_argument("--tool"); rn.add_argument("--shell-command"); rn.add_argument("command", nargs=argparse.REMAINDER); rn.set_defaults(func=cmd_run)
    ia = sub.add_parser("init-agent"); ia.add_argument("--bin-dir", default=".codeward/bin"); ia.add_argument("--no-agents-md", dest="agents_md", action="store_false"); ia.add_argument("--force", action="store_true", help="Install shims even if RTK is active"); ia.set_defaults(func=cmd_init_agent, agents_md=True)
    gp = sub.add_parser("gain", parents=[common])
    gp.add_argument("--repo", dest="repo_scope", action="store_true",
                    help="Show only this repo's savings (default is global across all repos)")
    gp.add_argument("--global", dest="global_scope", action="store_true",
                    help="Show aggregated savings across all repos (this is the default)")
    gp.add_argument("--all", dest="all_scope", action="store_true",
                    help="Aggregate per-repo + global history (deduplicated)")
    gp.set_defaults(func=cmd_gain)
    bd = sub.add_parser("budget", parents=[common]); bd.add_argument("target", nargs="?"); bd.add_argument("--top", type=int, default=10); bd.set_defaults(func=cmd_budget)
    pk = sub.add_parser("pack", parents=[common]); pk.add_argument("target"); pk.add_argument("--max-tokens", type=int, default=800); pk.add_argument("--top-symbols", type=int, default=6); pk.set_defaults(func=cmd_pack)
    dp = sub.add_parser("diff-pack", parents=[common], help="Budgeted semantic context bundle for changed files")
    dp.add_argument("--changed", action="store_true", help="Use working-tree changes")
    dp.add_argument("--base", help="Diff against this ref instead of the working tree")
    dp.add_argument("--max-tokens", type=int, default=800)
    dp.add_argument("--top-symbols", type=int, default=6)
    dp.add_argument("--security", action="store_true")
    dp.set_defaults(func=cmd_diff_pack)
    hs = sub.add_parser("hotspots", parents=[common], help="Rank files by risk = recent churn x dependents")
    hs.add_argument("--since", default="90d", help="Git log window, e.g. 30d, 6.months, 2024-01-01 (default: 90d)")
    hs.add_argument("--top", type=int, default=10, help="Number of files to return (default: 10)")
    hs.add_argument("--max-commits", type=int, default=2000, help="Hard cap on commits scanned (default: 2000)")
    hs.set_defaults(func=cmd_hotspots)
    nb = sub.add_parser("neighbors", parents=[common], help="Files that historically change together with <file>")
    nb.add_argument("file")
    nb.add_argument("--since", default="90d", help="Git log window (default: 90d)")
    nb.add_argument("--top", type=int, default=10, help="Number of neighbors to return (default: 10)")
    nb.add_argument("--max-commits", type=int, default=2000, help="Hard cap on commits scanned (default: 2000)")
    nb.set_defaults(func=cmd_neighbors)
    sv = sub.add_parser("savings"); sv.add_argument("--command", action="append"); sv.add_argument("--no-history", action="store_true"); sv.set_defaults(func=cmd_savings)
    co = sub.add_parser("coach"); co.add_argument("command", nargs=argparse.REMAINDER); co.set_defaults(func=cmd_coach)
    hk = sub.add_parser("hook"); hk.add_argument("--agent", choices=["claude", "cursor", "gemini", "generic"], default="claude"); hk.set_defaults(func=cmd_hook)
    init = sub.add_parser("init")
    init.add_argument("--hook", action="store_true", help="Also install Claude Code Bash hook (opt-in; orders before any RTK entry)")
    init.add_argument("--global", dest="global_install", action="store_true",
                      help="Also write semantic vocabulary to each agent's GLOBAL memory file "
                      "(~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ~/.gemini/GEMINI.md). "
                      "When combined with --hook, additionally wires ~/.claude/settings.json.")
    init.add_argument("--gemini", dest="install_gemini_hook", action="store_true",
                      help="Also install the Gemini CLI BeforeTool/run_shell_command hook in ~/.gemini/settings.json")
    init.add_argument("--no-hook-bash", action="store_true",
                      help="Skip the Bash rewrite hook (install only the Edit/Write preflight). "
                      "Useful when RTK already owns the Bash surface and you just want pre-edit context.")
    init.add_argument("--no-hook-edit", action="store_true",
                      help="Skip the Edit/Write preflight hook (install only the Bash rewrite hook).")
    init.set_defaults(func=cmd_init)
    doc = sub.add_parser("doctor", parents=[common])
    doc.set_defaults(func=cmd_doctor)
    mp = sub.add_parser("mcp", help="Run the Codeward MCP server on stdio (needs `pip install codeward[mcp]`)")
    mp.add_argument("--cwd", help="Pin the server to this directory instead of inheriting the launcher's cwd")
    mp.set_defaults(func=cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
