"""Independent reading-list TEST acceptance through the live tools, not model input."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.e2e.builder import _write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creation", type=Path, required=True)
    parser.add_argument("--start", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path, help="Verify retained records after an independently performed restart")
    args = parser.parse_args()
    load_dotenv()
    root = Path.cwd()
    output = args.output.resolve()
    if os.getenv("ENV_TYPE") != "dev" or output.exists() or not output.is_relative_to(root / "e2e/artifacts/builder"):
        parser.error("Requires DEV and new evidence inside e2e/artifacts/builder")
    created = json.loads(args.creation.read_text(encoding="utf-8"))["created_test"]
    identifier = created["id"]
    if not identifier.startswith("workbench_test_") or created["result"]["project"]["created_by"] != "builder.user":
        parser.error("Owned native UI TEST receipt required")
    admitted = json.loads(args.start.read_text(encoding="utf-8"))["result"]["session"]
    session = json.loads((root / f".adaos/state/builder/automation/scenario.{identifier}.json").read_text(encoding="utf-8"))
    task = admitted["current_task_id"]
    if admitted["object_id"] != identifier or session["current_task_id"] != task or session["status"] != "completed":
        parser.error("Exact completed TEST task required; worker completion alone is not acceptance")
    webui = root / f".adaos/dev/sn_6acf0c01/scenarios/{identifier}/webui.json"
    source_digest = hashlib.sha256(webui.read_bytes()).hexdigest()
    hub = "http://127.0.0.1:8778"
    client = requests.Session()
    client.headers["X-AdaOS-Token"] = resolve_control_token(base_url=hub)
    report = {"scope": "Independent DEV-owner HTTP acceptance; no delegated-user or delivery claim",
              "scenario": identifier, "task": task, "source_sha256": source_digest,
              "checks": [], "calls": [], "records": [], "passed": False}

    def call(tool, *, rejected=False, **values):
        started = perf_counter()
        response = client.post(hub + "/api/tools/call", json={"tool": identifier + "_skill:" + tool,
            "arguments": {"webspace_id": "desktop-dev-dev", **values}}, timeout=30)
        body = response.json()
        report["calls"].append({"tool": tool, "arguments": values, "status": response.status_code,
                                "elapsed_ms": round((perf_counter() - started) * 1000, 2), "response": body})
        if rejected:
            assert response.status_code in (400, 422) or (response.ok and (body.get("ok") is False or body.get("result", {}).get("ok") is False)), body
            return body
        assert response.ok and body.get("ok") is not False, body
        result = body.get("result", body)
        assert result.get("ok") is not False, result
        return result

    def check(name, condition):
        report["checks"].append({"id": name, "status": "passed" if condition else "failed"})
        print(name + ": " + report["checks"][-1]["status"], flush=True)

    def remember(book):
        assert book.get("id"), book
        report["records"] = [row for row in report["records"] if row["id"] != book["id"]] + [book]
        return book

    try:
        if args.resume:
            previous = json.loads(args.resume.read_text(encoding="utf-8"))
            assert previous["scenario"] == identifier and previous["task"] == task
            assert previous["source_sha256"] == source_digest and previous["records"]
            report["parent"] = str(args.resume.resolve())
            report["records"] = previous["records"]
            for expected in previous["records"]:
                actual = call("get_book", id=expected["id"])["item"]
                check("restart-preserves:" + expected["id"], actual == expected)
            # Only records created by this review are removed, through public tools.
            for expected in previous["records"]:
                call("delete_book", id=expected["id"], revision=expected["revision"])
                check("cleanup:" + expected["id"], call("get_book", id=expected["id"])["item"] == {})
        else:
            initial = call("list_books")["items"]
            check("fresh-runtime-has-no-prototype-seeds", initial == [])
            denied = requests.post(hub + "/api/tools/call", json={"tool": identifier + "_skill:create_book",
                "arguments": {"webspace_id": "desktop-dev-dev", "values": {"title": "Must not create"}}}, timeout=30)
            check("unauthenticated-ingress-denied", denied.status_code in (401, 403))
            marker = "E2E-HTTP-" + uuid4().hex[:8]
            report["marker"] = marker
            minimal = remember(call("create_book", values={"title": marker}))
            check("optional-fields-absent", all(minimal.get(key) is None for key in ("author", "status", "note")))
            book = remember(call("create_book", values={"title": "Северный ветер " + marker, "author": "Иван Северин",
                "status": "reading", "note": "Первая строка\nВторая строка"}))
            check("created-record-readable", call("get_book", id=book["id"])["item"] == book)
            for query, label in (("Северный ветер", "title"), ("Иван Северин", "author"), ("СЕВЕРНЫЙ", "uppercase")):
                rows = call("list_books", search=query, status="reading")["items"]
                check("search-and-filter:" + label, any(row["id"] == book["id"] for row in rows))
            check("filter-excludes-record", not any(row["id"] == book["id"] for row in call("list_books", search=marker, status="done")["items"]))
            check("empty-search", call("list_books", search=uuid4().hex)["items"] == [])
            before = call("list_books")["items"]
            for title in ("", "   "):
                call("create_book", rejected=True, values={"title": title})
            call("create_book", rejected=True, values={"title": marker, "status": "unknown"})
            check("invalid-create-does-not-mutate", call("list_books")["items"] == before)
            call("update_book", rejected=True, id=book["id"], revision=book["revision"], values={"title": " "})
            check("invalid-update-preserves-record", call("get_book", id=book["id"])["item"] == book)
            changed = remember(call("update_book", id=book["id"], revision=book["revision"], values={
                "title": "Новое название " + marker, "author": None, "status": "done", "note": "Обновлено\nЕще строка"}))
            check("update-persists-and-increments-revision", changed["revision"] > book["revision"] and
                  changed["author"] is None and changed["note"] == "Обновлено\nЕще строка" and
                  call("get_book", id=book["id"])["item"] == changed)
            call("update_book", rejected=True, id=book["id"], revision=book["revision"], values={"title": "Stale"})
            check("stale-revision-does-not-overwrite", call("get_book", id=book["id"])["item"] == changed)
            missing = "missing-" + uuid4().hex
            check("missing-read-is-empty", call("get_book", id=missing)["item"] == {})
            call("update_book", rejected=True, id=missing, values={"title": "Missing"})
            call("delete_book", rejected=True, id=missing)
            disposable = call("create_book", values={"title": marker + " delete"})
            call("delete_book", id=disposable["id"], revision=disposable["revision"])
            check("delete-removes-only-selected", call("get_book", id=disposable["id"])["item"] == {} and
                  call("get_book", id=minimal["id"])["item"] == minimal and call("get_book", id=book["id"])["item"] == changed)
        report["passed"] = bool(report["checks"]) and all(row["status"] == "passed" for row in report["checks"])
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        client.close()
        _write_json(output, report)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
