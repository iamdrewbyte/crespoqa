"""CrespoQA command line: single file or a folder of reports."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.config import load_settings
from .core.context import RunForm
from .core.logging_setup import setup_logging
from .core.pipeline import process_document
from .templates_store.db import Store, StoreError


def _infer_type(name: str, default: str) -> str:
    low = name.lower()
    if "exec" in low:
        return "executive"
    if "tech" in low:
        return "technical"
    return default


def _inspect(args) -> int:
    import json
    from .core.docx_validate import InvalidDocx
    from .core.inspect_doc import inspect_docx
    try:
        info = inspect_docx(args.path, args.label or ["Period:"], args.details or ["Document Details"], probe=args.probe)
    except InvalidDocx as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2))
    return 0


def _review(args) -> int:
    from .api.app import build_llm
    from .review import ReviewError, ReviewState, extract_for_review, run_review
    from .templates_store import models as m
    settings = load_settings()
    store = Store(settings.db_path)
    llm = None if args.no_llm else build_llm(settings)
    if llm is None and not args.no_llm:
        print("AI review is not enabled/configured (see AI_* in .env). Use --no-llm for deterministic checks.", file=sys.stderr)
        return 2
    docs = {}
    for rtype, path in (("technical", args.technical), ("executive", args.executive)):
        tpl = store.get_active(args.service, rtype)
        details = next((s.params for s in tpl.steps if isinstance(s, m.DocumentDetailsStep) and s.enabled), None)
        rs = next((x for x in tpl.steps if isinstance(x, m.AiRulesStep) and x.enabled), None)
        from .templates_store.ai_defaults import defaults_for
        docs[rtype] = extract_for_review(path, rtype, details, rs.params.rules if rs else defaults_for(rtype))
    state = ReviewState()
    try:
        run_review(docs, RunForm(consultant=args.consultant.strip()), llm, state, chunk_chars=settings.ai_chunk_chars,
                   max_chunks=settings.ai_max_chunks, progress=lambda p: print(f"... {p}", file=sys.stderr))
    except ReviewError as exc:
        print(exc, file=sys.stderr)
        return 1
    for n in state.notes:
        print(f"note: {n}")
    for f in state.findings:
        print(f"[{f.severity}] {f.check} ({f.doc}): {f.message}" + (f"\n    evidence: {f.evidence}" if f.evidence else "")
              + (f"\n    suggestion: {f.suggestion}" if f.suggestion else ""))
    print(f"{len(state.findings)} finding(s).")
    return 0


def _user(args) -> int:
    import getpass
    from .api.auth import hash_password
    store = Store(load_settings().db_path)
    try:
        if args.ucmd == "list":
            for u in store.list_users():
                print(f"{u['username']}\t{u['role']}\t{'disabled' if u['disabled'] else 'active'}")
        elif args.ucmd in ("add", "passwd"):
            pw = getpass.getpass("Password (min 10 chars): ")
            if len(pw) < 10 or pw != getpass.getpass("Repeat: "):
                print("Password too short or does not match.", file=sys.stderr)
                return 1
            if args.ucmd == "add":
                store.add_user(args.username, hash_password(pw), args.role)
            else:
                store.update_user(args.username, password_hash=hash_password(pw))
            print("OK")
        elif args.ucmd == "role":
            store.update_user(args.username, role=args.role); print("OK")
        else:
            store.update_user(args.username, disabled=1 if args.ucmd == "disable" else 0); print("OK")
    except StoreError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="crespoqa", description="QA-clean Word security reports.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Process one .docx or every .docx in a folder")
    run.add_argument("path", type=Path)
    run.add_argument("--service", required=True, help="e.g. WebApp, VA")
    run.add_argument("--type", choices=["technical", "executive"], help="Report type; inferred from file name if omitted")
    run.add_argument("--consultant", default="")
    run.add_argument("--reviewer", default="")
    run.add_argument("--client", default="")
    run.add_argument("--report-date", default="")
    run.add_argument("--period", default="")
    run.add_argument("--out", type=Path, default=Path("qa_output"))
    run.add_argument("--dry-run", action="store_true", help="Analyse only; write no processed file")
    run.add_argument("--report", action="store_true", help="Also write JSON and HTML change reports")
    run.add_argument("--no-layout", action="store_true", help="Skip the LibreOffice cross-check")
    ins = sub.add_parser("inspect", help="Print a structure-only summary of a .docx (no document text; safe to share)")
    ins.add_argument("path", type=Path)
    ins.add_argument("--label", action="append", default=None, help="Label to look for (default 'Period:')")
    ins.add_argument("--details", action="append", default=None, help="Document Details title text (default 'Document Details')")
    rv = sub.add_parser("review", help="Review-only checks on a Technical + Executive pair (never edits files)")
    rv.add_argument("technical", type=Path); rv.add_argument("executive", type=Path)
    rv.add_argument("--service", default="WebApp"); rv.add_argument("--consultant", default="")
    rv.add_argument("--no-llm", action="store_true", help="Deterministic checks only (no model call)")
    ins.add_argument("--probe", default="period", help="Word to locate case-insensitively (default 'period')")
    usr = sub.add_parser("user", help="Manage local accounts")
    usub = usr.add_subparsers(dest="ucmd", required=True)
    ua = usub.add_parser("add"); ua.add_argument("username"); ua.add_argument("--role", choices=["user", "editor"], default="user")
    usub.add_parser("list")
    up = usub.add_parser("passwd"); up.add_argument("username")
    ur = usub.add_parser("role"); ur.add_argument("username"); ur.add_argument("role", choices=["user", "editor"])
    ud = usub.add_parser("disable"); ud.add_argument("username")
    ue = usub.add_parser("enable"); ue.add_argument("username")
    args = ap.parse_args(argv)
    if args.cmd == "inspect":
        return _inspect(args)
    if args.cmd == "user":
        return _user(args)
    if args.cmd == "review":
        return _review(args)

    settings = load_settings()
    setup_logging(settings)
    store = Store(settings.db_path)
    form = RunForm(args.consultant.strip(), args.reviewer.strip(), args.client.strip(), args.report_date.strip(), args.period.strip())

    files = sorted(args.path.glob("*.docx")) if args.path.is_dir() else [args.path]
    files = [f for f in files if not f.name.startswith("~$") and not f.stem.endswith("_QA_Processed")]
    if args.path.is_dir() and args.out.resolve() == args.path.resolve():
        print("Output folder must differ from the input folder.", file=sys.stderr)
        return 2
    if not files:
        print("No .docx files found.", file=sys.stderr)
        return 2

    failed = 0
    for f in files:
        rtype = args.type or _infer_type(f.name, "technical")
        try:
            template = store.get_active(args.service, rtype)
        except KeyError as exc:
            print(f"{f.name}: {exc}", file=sys.stderr)
            failed += 1
            continue
        report = process_document(f, args.out, template, form, settings=settings, dry_run=args.dry_run, use_layout=not args.no_layout)
        if args.report:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"{f.stem}_change_report.json").write_text(report.to_json(), encoding="utf-8")
            (args.out / f"{f.stem}_change_report.html").write_text(report.to_html(), encoding="utf-8")
        print(f"{f.name}: {report.status}" + (f" - {report.error}" if report.error else ""))
        for t in report.tasks:
            print(f"  [{t.status}] {t.title}")
            for c in t.changes:
                print(f"      - {c}")
            for wmsg in t.warnings:
                print(f"      ! {wmsg}")
        failed += report.status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
