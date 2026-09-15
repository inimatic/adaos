"""Independent reading-list TEST acceptance through the live tools, not model input."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
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
    parser.add_argument("--cleanup-only", action="store_true", help="Remove only retained owned records, without claiming restart evidence")
    parser.add_argument("--observe-only", action="store_true", help="Read records/settings without mutations or exporting private values")
    parser.add_argument("--browser", action="store_true", help="Run independent desktop/mobile interactions")
    parser.add_argument("--discovery", action="store_true", help="Qualify an explicit public API query and import, without sending library contents")
    parser.add_argument("--rating-and-order", action="store_true", help="Qualify the accepted successor rating and shared default ordering")
    parser.add_argument("--trial", type=Path, help="Exact admitted local Trial receipt")
    parser.add_argument("--stable", action="store_true", help="Verify this Trial's retained writes after native Stable acceptance")
    args = parser.parse_args()
    if args.cleanup_only and (not args.resume or args.trial or args.browser):
        parser.error("Cleanup requires a retained DEV HTTP report only")
    if args.observe_only and (args.resume or args.cleanup_only or args.browser or args.discovery):
        parser.error("Read-only observation cannot be combined with mutation or browser checks")
    if args.stable and (not args.trial or not (args.resume or args.observe_only) or args.browser):
        parser.error("Stable review requires an exact Trial and either observation or retained HTTP writes")
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
    webspace = "desktop-dev-dev"
    trial = None
    dev_database = root / f".adaos/dev/sn_6acf0c01/skills/.runtime/{identifier}_skill/v0.1/data/reading_list.sqlite3"
    if args.trial:
        trial = json.loads(args.trial.read_text(encoding="utf-8"))
        assert trial["passed"] and trial["builder_placement"]["scenario_id"] == identifier
        assert trial["builder_placement"]["target"]["space_kind"] == "workspace"
        webspace = trial["builder_placement"]["target"]["webspace_id"]
        dev_before = hashlib.sha256(dev_database.read_bytes()).hexdigest()
        if args.stable:
            from adaos.services.applications.store import ApplicationStore
            selection = ApplicationStore(root / ".adaos/state").get_runtime_selection(webspace, identifier)
            assert selection.source == "stable_installation" and selection.runtime_root_ref == "workspace"
            assert selection.release_digest == trial["placement"]["runtime_selection"]["release_digest"]
    if args.browser:
        if args.resume:
            parser.error("Browser review and restart verification are separate phases")
        environment = {**os.environ, "ADAOS_E2E_HUB_URL": hub,
            "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=hub),
            "ADAOS_E2E_SCENARIO_ID": identifier, "ADAOS_E2E_TASK_ID": task,
            "ADAOS_E2E_SOURCE_SHA256": source_digest, "ADAOS_E2E_OUTPUT": str(output)}
        environment.update(ADAOS_E2E_WEBSPACE=webspace, ADAOS_E2E_TRIAL="1" if trial else "0")
        environment["ADAOS_E2E_RATING_AND_ORDER"] = "1" if args.rating_and_order else "0"
        if trial:
            environment["ADAOS_E2E_RELEASE_DIGEST"] = trial["placement"]["runtime_selection"]["release_digest"]
        script = Path(__file__).with_name("browser") / "workbench-test-automation.mjs"
        code = subprocess.run(["node", str(script)], env=environment).returncode
        if trial and output.is_file():
            browser_report = json.loads(output.read_text(encoding="utf-8"))
            browser_report["dev_database_before"] = dev_before
            browser_report["dev_database_after"] = hashlib.sha256(dev_database.read_bytes()).hexdigest()
            browser_report["dev_unchanged"] = browser_report["dev_database_after"] == dev_before
            browser_report["passed"] = bool(browser_report["passed"] and browser_report["dev_unchanged"])
            _write_json(output, browser_report)
            if not browser_report["passed"]:
                code = 1
        raise SystemExit(code)
    client = requests.Session()
    client.headers["X-AdaOS-Token"] = resolve_control_token(base_url=hub)
    report = {"scope": "Independent DEV-owner HTTP acceptance; no delegated-user or delivery claim",
              "scenario": identifier, "task": task, "source_sha256": source_digest,
              "checks": [], "calls": [], "records": [], "passed": False}
    report["rating_and_order"] = args.rating_and_order
    if args.cleanup_only:
        report["scope"] = "Cleanup of exactly retained DEV E2E records; not restart evidence"
    if trial:
        report.update(scope=("Native Stable adoption of retained Beta writes" if args.stable
                             else "Independent local Trial-owner HTTP acceptance; not external distribution"),
                      trial=trial["placement"]["runtime_selection"], dev_database_before=dev_before)
    if args.observe_only:
        report["scope"] = "Read-only runtime observation; not mutation acceptance or data-adoption proof"
    if args.stable:
        report["runtime_selection"] = selection.to_dict()

    def call(tool, *, rejected=False, rejection_probe=False, **values):
        started = perf_counter()
        response = client.post(hub + "/api/tools/call", json={"tool": identifier + "_skill:" + tool,
            "arguments": {"webspace_id": webspace, **values}}, timeout=30)
        body = response.json()
        # Installed reads may contain private records/settings. Retain outcomes,
        # not response bodies; report.records contains only this stand's writes.
        report["calls"].append({"tool": tool, "status": response.status_code,
                                "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                                "ok": response.ok and body.get("ok") is not False})
        if trial and response.ok:
            if args.stable:
                assert response.headers.get("X-AdaOS-Runtime-Source") != "trial"
            else:
                assert response.headers.get("X-AdaOS-Runtime-Source") == "trial"
                assert response.headers.get("X-AdaOS-Release-Digest") == report["trial"]["release_digest"]
        refused = response.status_code in (400, 409, 422) or (response.ok and (body.get("ok") is False or body.get("result", {}).get("ok") is False))
        if rejection_probe:
            assert refused or (response.ok and body.get("ok") is not False), "Rejection probe encountered an infrastructure error"
            return refused, None if refused else body.get("result", body)
        if rejected:
            assert refused, f"{tool}: expected rejection, HTTP {response.status_code}"
            return body
        assert response.ok and body.get("ok") is not False, f"{tool}: HTTP {response.status_code} rejected"
        result = body.get("result", body)
        assert result.get("ok") is not False, f"{tool}: rejected tool result"
        return result

    def check(name, condition):
        report["checks"].append({"id": name, "status": "passed" if condition else "failed"})
        print(name + ": " + report["checks"][-1]["status"], flush=True)

    def remember(book):
        assert book.get("id"), book
        report["records"] = [row for row in report["records"] if row["id"] != book["id"]] + [book]
        return book

    def form_values(book, **changes):
        return {key: changes.get(key, book.get(key)) for key in (
            "title", "author", "status", "note", "source_work_key", "publication_year", "cover_id", "rating")}

    try:
        if args.observe_only:
            items = call("list_books")["items"]
            settings = call("read_settings")["item"]
            check("read-records-without-export", isinstance(items, list))
            check("read-settings-without-export", isinstance(settings, dict))
            report["record_count"] = len(items)
        elif args.resume:
            previous = json.loads(args.resume.read_text(encoding="utf-8"))
            assert previous["scenario"] == identifier and previous["records"]
            if args.cleanup_only:
                marker = previous.get("marker", "")
                assert args.resume.resolve().is_relative_to(root / "e2e/artifacts/builder")
                assert not previous.get("trial") and marker.startswith("E2E-HTTP-")
                assert all(marker in row.get("title", "") for row in previous["records"])
                report["source_task"] = previous["task"]
            else:
                assert previous["task"] == task and previous["source_sha256"] == source_digest
            report["parent"] = str(args.resume.resolve())
            report["records"] = previous["records"]
            for expected in previous["records"]:
                actual = call("get_book", id=expected["id"])["item"]
                assert actual == expected, "Retained record changed; do not delete another actor's edit"
                check(("cleanup-verifies:" if args.cleanup_only else "restart-preserves:") + expected["id"], actual == expected)
            if previous.get("settings_written") and not args.cleanup_only:
                expected_settings = previous["settings_written"]
                actual_settings = call("read_settings")["item"]
                if args.stable:
                    # Adoption advances configuration identity but must keep the
                    # owner's Beta values, not require an obsolete revision.
                    expected_settings = {key: value for key, value in expected_settings.items() if key != "revision"}
                check("restart-preserves-settings", all(actual_settings.get(key) == value for key, value in expected_settings.items()))
            # Only records created by this review are removed, through public tools.
            for expected in previous["records"]:
                call("delete_book", id=expected["id"], revision=expected["revision"])
                check("cleanup:" + expected["id"], call("get_book", id=expected["id"])["item"] == {})
        else:
            initial = call("list_books")["items"]
            if trial:
                check("installed-read-succeeds-without-exporting-records", isinstance(initial, list))
            else:
                check("dev-read-succeeds-without-modifying-existing-records", isinstance(initial, list))
                report["initial_record_count"] = len(initial)
            denied = requests.post(hub + "/api/tools/call", json={"tool": identifier + "_skill:create_book",
                "arguments": {"webspace_id": webspace, "values": {"title": "Must not create"}}}, timeout=30)
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
            settings = call("read_settings")["item"]
            desired = {"preferred_view": "cards" if settings["preferred_view"] == "table" else "table", "discovery_count": 10}
            saved = call("update_settings", values=desired, revision=settings["revision"])["item"]
            report["settings_written"] = {**desired, "revision": saved["revision"]}
            check("settings-save-increments-revision", saved["revision"] > settings["revision"] and all(saved[key] == value for key, value in desired.items()))
            call("update_settings", rejected=True, values={"discovery_count": 20}, revision=settings["revision"])
            call("update_settings", rejected=True, values={"discovery_count": 20})
            check("stale-or-missing-settings-revision-refused", call("read_settings")["item"] == saved)
            if args.rating_and_order:
                check("legacy-record-has-no-rating", minimal.get("rating") is None)
                rated = remember(call("update_book", id=changed["id"], revision=changed["revision"], values=form_values(changed, rating=5)))
                check("rating-persists", rated.get("rating") == 5 and call("get_book", id=rated["id"])["item"] == rated)
                for invalid in (True, False, 0, 6, 1.5):
                    call("update_book", rejected=True, id=rated["id"], revision=rated["revision"], values=form_values(rated, rating=invalid))
                    call("create_book", rejected=True, values={"title": marker + " invalid rating", "rating": invalid})
                check("invalid-rating-does-not-mutate", call("get_book", id=rated["id"])["item"] == rated
                      and not any(row["title"] == marker + " invalid rating" for row in call("list_books", search=marker)["items"]))
                cleared = remember(call("update_book", id=rated["id"], revision=rated["revision"], values=form_values(rated, rating=None)))
                check("rating-clears-without-losing-other-fields", cleared.get("rating") is None
                      and all(cleared.get(key) == value for key, value in rated.items() if key not in {"rating", "revision", "updated_at"}))
                rated = remember(call("update_book", id=cleared["id"], revision=cleared["revision"], values=form_values(cleared, rating=3)))
                check("rating-does-not-change-another-record", call("get_book", id=minimal["id"])["item"] == minimal)
                alphabetical = call("update_settings", values={"default_order": "title_alpha"}, revision=saved["revision"])["item"]
                rows = call("list_books", search=marker)["items"]
                check("shared-title-order-affects-query", len(rows) >= 2 and [row["title"] for row in rows]
                      == sorted((row["title"] for row in rows), key=str.casefold))
                newest = call("update_settings", values={"default_order": "newest_first"}, revision=alphabetical["revision"])["item"]
                rows = call("list_books", search=marker)["items"]
                check("shared-newest-order-affects-query", [row["id"] for row in rows] == [rated["id"], minimal["id"]])
                check("default-order-is-repeatable", [row["id"] for row in rows]
                      == [row["id"] for row in call("list_books", search=marker)["items"]])
                check("adding-order-preserves-existing-settings", all(newest.get(key) == value for key, value in desired.items()))
                for invalid in ("unknown", "", True, False, None, 1, [], {}):
                    call("update_settings", rejected=True, values={"default_order": invalid}, revision=newest["revision"])
                call("update_settings", rejected=True, values={"default_order": "title_alpha"}, revision=saved["revision"])
                check("invalid-or-stale-order-preserves-settings", call("read_settings")["item"] == newest)
                report["settings_written"] = {**desired, "default_order": "newest_first", "revision": newest["revision"]}
                refused, unexpected = call("create_book", rejection_probe=True, values={"title": marker})
                check("preserves-published-title-only-duplicate-policy", refused)
                if unexpected:
                    # Remove only the extra synthetic record admitted by the regression.
                    call("delete_book", id=unexpected["id"], revision=unexpected["revision"])
                call("update_book", rejected=True, id=rated["id"], revision=rated["revision"],
                     values=form_values(rated, title=minimal["title"], author=None))
                check("duplicate-update-preserves-record", call("get_book", id=rated["id"])["item"] == rated)
            if args.discovery:
                discovered = call("search_open_library", query="The Time Machine", limit=10)["items"]
                check("public-discovery-is-bounded-and-nonempty", 0 < len(discovered) <= 10)
                assert discovered, "The explicit public query returned no usable result"
                selected = next((item for item in discovered if item.get("cover_id")), discovered[0])
                imported = remember(call("add_from_discovery", values={
                    "title": selected["title"] + " " + marker, "author": selected.get("author"),
                    "source_work_key": selected.get("source_work_key"),
                    "publication_year": selected.get("publication_year"), "cover_id": selected.get("cover_id")}))
                actual = call("get_book", id=imported["id"])["item"]
                check("discovery-import-persists-metadata", all(actual.get(key) == selected.get(key)
                      for key in ("source_work_key", "publication_year", "cover_id")))
        report["passed"] = bool(report["checks"]) and all(row["status"] == "passed" for row in report["checks"])
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if trial:
            report["dev_database_after"] = hashlib.sha256(dev_database.read_bytes()).hexdigest()
            report["dev_unchanged"] = report["dev_database_after"] == dev_before
            report["passed"] = report["passed"] and report["dev_unchanged"]
        client.close()
        _write_json(output, report)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
