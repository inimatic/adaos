"""Owned browser fixture for the real Retouch handlers, SQLite and image code.

Only the SDK Broker, blob transport and access/context boundary are substituted.
This is never a provider-quality or Core application-admission receipt.
"""
from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from PIL import Image

from adaos.services.builder.spend_ledger import BuilderSpendLedger


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class RetouchFixture:
    def __init__(self, root: Path, source: Path, token: str):
        self.root, self.source, self.token = root.resolve(), source.resolve(), token
        self.root.mkdir(parents=True, exist_ok=True)
        self.upload: ContextVar[tuple[bytes, str]] = ContextVar("retouch_fixture_upload")
        paths = sorted((source / "handlers").glob("*.py"))
        self.source_digest = digest(json.dumps({p.name: digest(p.read_bytes()) for p in paths}, sort_keys=True).encode())
        marker = self.root / "owned-fixture.json"
        owner = {"schema": "adaos.e2e.retouch_fixture.v1", "source": str(self.source),
                 "source_digest": self.source_digest, "test": True, "external_model_calls": 0}
        if marker.exists() and json.loads(marker.read_text(encoding="utf-8")) != owner:
            raise ValueError("Retouch fixture owner/source drift; use a new owned directory")
        marker.write_text(json.dumps(owner, indent=2), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("retouch_owned_browser_fixture", source / "handlers/retouch.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        (self.root / "data").mkdir(exist_ok=True)
        self.store = self.module.Store(self.root / "data")
        with self.store.connect() as db:
            for statement in self.module.SCHEMA: db.execute(statement)
            db.execute("CREATE TABLE IF NOT EXISTS fixture_jobs(request_id TEXT PRIMARY KEY,kind TEXT,input_digest TEXT,request TEXT,response TEXT,polls INTEGER DEFAULT 0,submits INTEGER DEFAULT 1)")
            db.execute("CREATE TABLE IF NOT EXISTS fixture_controls(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS fixture_calls(seq INTEGER PRIMARY KEY,operation TEXT,identity TEXT,at REAL)")
        self.ledger = BuilderSpendLedger(state_dir=self.root / "state")
        self.ledger.configure(self.module.pipeline.SPEND_SCOPE, limit_usd=10, default_reserve_usd=2,
                              authorization_ref="fixture:synthetic-only:no-provider-spend")
        self.module.runtime_store = lambda permission: self.store
        self.module.active_budget = lambda: self.ledger.snapshot(self.module.pipeline.SPEND_SCOPE)
        self.module.pipeline.SDKBroker = lambda: self

    def install_blob_boundary(self):
        """Install only inside the dedicated fixture server process."""
        from adaos.sdk.data import blob
        owner = self
        class Blob:
            def __init__(self, logical): self.logical = logical
            def put_bytes(self, filename, raw, *, media_type): return owner.publish(filename, raw, media_type, self.logical)
        blob.store = Blob
        blob.read_bytes = lambda ref, logical_name, **kwargs: self.read_blob(ref, logical_name)
        blob.put_upload = lambda logical: self.publish("fixture-upload", self.upload.get()[0], self.upload.get()[1], logical)

    def publish(self, filename, raw, mime, logical="exports"):
        identity = digest(raw)
        path = self.root / "blobs" / logical / identity[7:]
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != raw: raise ValueError("fixture_blob_conflict")
        path.write_bytes(raw)
        return {"schema": "adaos.storage.blob.object.v1", "ref": identity, "digest": identity,
                "size_bytes": len(raw), "owner_ref": "skill:natural_retouch_skill",
                "logical_name": logical, "media_type": mime}

    def read_blob(self, identity, logical):
        if logical not in {"exports", "originals"} or not isinstance(identity, str) or not __import__('re').fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise ValueError("invalid_fixture_blob_owner")
        raw = (self.root / "blobs" / logical / identity[7:]).read_bytes()
        if digest(raw) != identity: raise ValueError("fixture_blob_corrupt")
        return raw

    def control(self, key, default=None):
        with self.store.connect() as db:
            row = db.execute("SELECT value FROM fixture_controls WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def image_input(self, raw):
        return {"media_type": "image/jpeg", "detail": "high", "sha256": hashlib.sha256(raw).hexdigest(),
                "data_url": self.module.pipeline.data_url(raw, "image/jpeg")}

    def submit(self, kind, request):
        identity = request["request_id"]
        raw_request = self.module.dumps(request)
        try:
            self.ledger.reserve(request_id=identity, scope=request["spend_scope"], run_id=request["spend_run_id"],
                                input_digest=digest(raw_request.encode()), reserve_usd=request["spend_reserve_usd"])
        except ValueError as exc:
            if str(exc) != 'builder_usd_reservation_limit': raise
            from adaos.sdk.llm.errors import SubmissionNotAdmitted
            raise SubmissionNotAdmitted(str(exc), request_id=identity) from exc
        with self.store.connect() as db:
            previous = db.execute("SELECT input_digest,response FROM fixture_jobs WHERE request_id=?", (identity,)).fetchone()
            if previous:
                if previous[0] != digest(raw_request.encode()): raise ValueError("fixture_request_conflict")
                db.execute("UPDATE fixture_jobs SET submits=submits+1 WHERE request_id=?", (identity,))
                raise AssertionError("Application resubmitted an existing fixture job")
        response = {"request_id": identity, "status": "completed", "usage": {"input_tokens": 100, "output_tokens": 50}}
        if kind == "analysis":
            response["data"] = {"summary": "SYNTHETIC browser fixture: preserve texture; only the selected correction.",
                "operations": {key: {"applicability": "requires", "reason": "synthetic test blemish"} for key in self.module.OPS}}
        elif kind == "edit":
            raw = base64.b64decode(request["images"][0]["image_url"].split(",", 1)[1])
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            # The fixture blemish lies inside the native (top-left) crop,
            # rather than the padding added for an API canvas minimum.
            image.putpixel((min(100, image.width // 2), min(100, image.height // 2)), (161, 101, 91))
            raw = self.module.pipeline.encoded(image)
            self.publish(identity + ".png", raw, "image/png")
            response["media"] = {"sha256": digest(raw)[7:], "width": image.width, "height": image.height,
                                  "content_ref": "generated-image:" + digest(raw)[7:]}
        elif kind == "qa":
            response["data"] = {"passed": True, "issues": [], "checks": {
                key: {"verdict": "pass", "reason": "SYNTHETIC fixture verdict, no visual quality assertion"}
                for key in self.module.pipeline.QA_CHECKS}}
        else: raise ValueError("unknown_fixture_phase")
        with self.store.connect() as db:
            db.execute("INSERT INTO fixture_jobs(request_id,kind,input_digest,request,response) VALUES(?,?,?,?,?)",
                       (identity, kind, digest(raw_request.encode()), raw_request, self.module.dumps(response)))
            db.execute("INSERT INTO fixture_calls(operation,identity,at) VALUES(?,?,?)", ("submit:" + kind, identity, time.time()))
        if self.control("unknown_submit_phase") == kind: raise TimeoutError("synthetic unknown submit outcome")
        return {"request_id": identity, "status": "queued"}

    def poll(self, kind, identity):
        with self.store.connect() as db:
            row = db.execute("SELECT response,kind FROM fixture_jobs WHERE request_id=?", (identity,)).fetchone()
            if not row or row[1] != kind: raise FileNotFoundError("fixture_job_not_found")
            db.execute("UPDATE fixture_jobs SET polls=polls+1 WHERE request_id=?", (identity,))
            db.execute("INSERT INTO fixture_calls(operation,identity,at) VALUES(?,?,?)", ("poll:" + kind, identity, time.time()))
        if self.control("hold_phase") == kind: return {"request_id": identity, "status": "in_progress"}
        if self.control("fail_phase") == kind: return {"request_id": identity, "status": "failed"}
        response = json.loads(row[0])
        # Synthetic metering is isolated from the user's real USD account.
        self.ledger.observe(request_id=identity, receipt_ref="fixture-meter:" + identity,
                            usd=.01, basis="metered", complete=True)
        return response

    def read_image(self, identity):
        with self.store.connect() as db:
            row = db.execute("SELECT response FROM fixture_jobs WHERE request_id=? AND kind='edit'", (identity,)).fetchone()
        return self.read_blob("sha256:" + json.loads(row[0])["media"]["sha256"], "exports")

    async def call(self, name, arguments):
        if name not in {"query_records", "mutate_record", "save_mask", "run_command", "run_state", "comparison",
                        "adopt_result", "adoption_status", "upload_attachment", "read_attachment", "prepare_comparison",
                        "review_result", "recover_submission"}:
            raise ValueError("undeclared_fixture_tool")
        function = getattr(self.module, name)
        return await function(**arguments)

    def audit(self):
        with self.store.connect() as db:
            jobs = [dict(row) for row in db.execute("SELECT request_id,kind,input_digest,polls,submits FROM fixture_jobs ORDER BY rowid")]
            calls = [dict(row) for row in db.execute("SELECT * FROM fixture_calls ORDER BY seq")]
            runs = []
            for row in db.execute("SELECT id,revision,body FROM records WHERE kind='runs' ORDER BY rowid"):
                run = {**json.loads(row["body"]), "id": row["id"], "revision": row["revision"]}
                runs.append({**{key: run.get(key) for key in ("id", "photo_id", "revision", "status", "stage", "plan_revision", "plan_audit", "checks", "approved", "qa", "cost")},
                    "snapshot_digest": digest(self.module.dumps(run["snapshot"]).encode()),
                    "jobs": {phase: {key: job.get(key) for key in ("request_id", "status", "error")}
                             for phase, job in run.get("jobs", {}).items()}})
        return {"schema": "adaos.e2e.retouch_fixture_audit.v1", "source_digest": self.source_digest,
                "scope": "fixture handlers + SQLite + Client; Core authorization and provider quality excluded",
                "external_model_calls": 0, "jobs": jobs, "calls": calls, "runs": runs,
                "budget": self.ledger.snapshot(self.module.pipeline.SPEND_SCOPE)}


def fixture_app(owner: RetouchFixture) -> FastAPI:
    app = FastAPI()
    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.headers.get("X-Retouch-Fixture-Token") != owner.token:
            return Response(status_code=403)
        response = await call_next(request)
        response.headers["X-AdaOS-Runtime-Source"] = "fixture"
        response.headers["X-Retouch-Fixture-Source"] = owner.source_digest
        return response

    @app.post("/api/tools/call")
    async def tool_call(request: Request):
        body = await request.json()
        tool = str(body.get("tool") or "")
        if not tool.startswith("natural_retouch_skill:"): raise HTTPException(400, "fixture_tool_owner_mismatch")
        try:
            result = await owner.call(tool.split(":", 1)[1], body.get("arguments") or {})
            return {"ok": True, "result": result, "trace_id": "fixture:" + uuid.uuid4().hex}
        except (KeyError, ValueError) as exc: return {"ok": False, "error": str(exc)}

    @app.put("/api/tools/natural_retouch_skill/upload_attachment/attachments")
    async def upload(request: Request):
        raw = await request.body()
        token = owner.upload.set((raw, request.headers["Content-Type"].split(";")[0]))
        try:
            arguments = {"filename": request.query_params["filename"], "field_id": request.query_params["field_id"],
                         "media_type": request.headers["Content-Type"].split(";")[0], "size_bytes": len(raw), "digest": digest(raw)}
            receipt = await owner.call("upload_attachment", arguments)
            return {**receipt, "ref": f"/api/tools/natural_retouch_skill/read_attachment/attachments/originals/{digest(raw)[7:]}/{arguments['filename']}"}
        finally: owner.upload.reset(token)

    @app.get("/api/tools/natural_retouch_skill/read_attachment/attachments/{logical}/{identity}/{filename}")
    async def attachment(logical: str, identity: str, filename: str):
        await owner.call("read_attachment", {"ref": "sha256:" + identity})
        raw = owner.read_blob("sha256:" + identity, logical)
        mime = "image/png" if raw.startswith(b"\x89PNG") else "image/jpeg"
        return Response(raw, media_type=mime, headers={"Content-Disposition": f'inline; filename="{filename}"'})

    @app.post("/control")
    async def control(request: Request):
        values = await request.json()
        if set(values) - {"hold_phase", "fail_phase", "unknown_submit_phase", "exhaust_budget"}: raise HTTPException(400, "unknown_control")
        if "exhaust_budget" in values:
            value = values.pop("exhaust_budget")
            if type(value) is not bool: raise HTTPException(400, "invalid_budget_control")
            if value:
                remaining = owner.ledger.snapshot(owner.module.pipeline.SPEND_SCOPE)["available_usd"]
                owner.ledger.reserve(owner.module.pipeline.SPEND_SCOPE, request_id="fixture-budget-blocker",
                    run_id="fixture-budget-blocker", input_digest="synthetic-admission-fault", reserve_usd=remaining)
            else:
                owner.ledger.observe("fixture-budget-blocker", receipt_ref="fixture-budget-release", usd=0,
                                     basis="metered", complete=True)
        with owner.store.connect() as db:
            for key, value in values.items():
                if value not in {None, "analysis", "edit", "qa"}: raise HTTPException(400, "invalid_phase")
                db.execute("INSERT OR REPLACE INTO fixture_controls VALUES(?,?)", (key, json.dumps(value)))
        return {"ok": True}

    @app.get("/audit")
    async def audit(): return owner.audit()

    @app.get("/sample-image")
    async def sample_image():
        image = Image.new("RGB", (320, 240), (122, 100, 90))
        image.putpixel((100, 100), (190, 80, 70))
        stream = io.BytesIO(); image.save(stream, format="JPEG", quality=95)
        return Response(stream.getvalue(), media_type="image/jpeg")
    return app
