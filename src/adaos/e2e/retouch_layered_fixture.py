"""Layered application test boundary; real handlers/queue, synthetic pixels.

No model weights, network providers, user budget or production data are used.
The CPU matte and provider are explicit doubles; layer algebra is unchanged.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image

from .retouch_fixture import RetouchFixture, digest


def synthetic_native_qa(request):
    """Typed provider double: permit extraction, keep final QA advisory.

    This is an execution test, never evidence of segmentation/photo quality.
    Reject schema drift so a green fixture cannot hide a changed contract.
    """
    from jsonschema import validate
    stage = request.get('data', {}).get('stage')
    if not isinstance(stage, str) or not stage:
        raise ValueError('synthetic_native_qa_stage_required')
    result = {'issues': [], 'preference': 'candidate' if stage.startswith('segment') else 'uncertain',
              'complete': True}
    for field in ('resolved_issue_ids', 'requested_evidence', 'reassessed_view_ids'):
        if field in request['schema'].get('properties', {}):
            result[field] = []
    validate(result, request['schema'])
    return result


class LayeredRetouchFixture(RetouchFixture):
    def __init__(self, root, source, token):
        super().__init__(root, source, token)
        self.workspace = self.load_handler('workspace')
        self.assistance = self.load_handler('assistance')
        self.assistance.workspace = self.workspace
        self.workspace.legacy.runtime_store = lambda permission: self.store
        self.workspace.legacy.pipeline.SDKBroker = lambda: self
        self.owned = self.workspace.LayerStore(self.store.path.parent)
        self.owned.migrate()
        if not self.owned.budget()['configured']:
            self.owned.configure_budget(30, 0)
        original = self.workspace.engine.sibling
        def segment(rgb, root, progress=None):
            if progress: progress('native_alpha_tiles', 1, 1)
            # A deterministic opaque interior with a soft contour. Native
            # resolution is retained; this is not a segmentation quality test.
            alpha = np.zeros(rgb.shape[:2], dtype=float)
            alpha[8:-8, 8:-8] = 1
            alpha[8:-8, 8] = .5
            return alpha, rgb.copy(), rgb.copy(), {'synthetic_test_only': True}
        matting = SimpleNamespace(segment=segment, REVISION='synthetic-owned-test', WEIGHTS_SHA256='synthetic-owned-test')
        self.workspace.engine.sibling = lambda name: matting if name == 'native_matting' else original(name)

    def load_handler(self, name):
        spec = importlib.util.spec_from_file_location('layered_fixture_' + name, self.source / ('handlers/' + name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def function_for(self, name):
        if name in {'workspace_query', 'workspace_command', 'workspace_run', 'workspace_draft'}:
            return getattr(self.workspace, name)
        if name == 'workspace_assist':
            return self.assistance.workspace_assist
        return super().function_for(name)

    def validate_edit(self, request):
        from adaos.sdk.llm import images
        return images.validate_edit(**{k: v for k, v in request.items() if k not in {'request_id', 'context'}})

    def submit(self, kind, request):
        if kind == 'edit':
            self.validate_edit(request)
        else:
            import inspect
            from adaos.sdk.llm import content
            inspect.signature(content.generate).bind(**request)
        identity = request['request_id']
        raw_request = self.module.dumps(request)
        with self.store.connect() as db:
            if db.execute('SELECT 1 FROM fixture_jobs WHERE request_id=?', (identity,)).fetchone():
                raise AssertionError('Application resubmitted a retained layered job')
        response = {'request_id': identity, 'status': 'completed'}
        if self.control('quota_phase') == kind:
            # Persist a genuine terminal failure. Clearing the injected fault
            # must not turn this retained attempt into a successful response.
            response.update(status='failed',error={'code':'credit_balance_exhausted',
                'type':'insufficient_quota','message':'Synthetic provider balance exhausted'})
        elif kind == 'edit':
            raw = base64.b64decode(request['images'][0]['image_url'].split(',', 1)[1])
            image = Image.open(io.BytesIO(raw))
            alpha_input = 'A' in image.getbands()
            image = image.convert('RGBA')
            assert image.size == tuple(map(int, request['size'].split('x')))
            pixels = np.asarray(image).copy()
            if request['background'] == 'transparent':
                # Simulated retouch plus changed silhouette exercises repair.
                # The controlled photo is320x240 and native padding is appended
                # at right/bottom. Change visible foreground, not padded pixels.
                pixels[min(100,image.height-1), min(160,image.width-1), :3] = [161, 101, 91]
                pixels[:, :max(1, image.width // (10 if alpha_input else 20)), 3] = 0
            else:
                pixels[..., :3] = [122, 100, 90]
                pixels[..., 3] = 255
            stream = io.BytesIO(); Image.fromarray(pixels).save(stream, format='PNG')
            raw = stream.getvalue()
            self.publish(identity + '.png', raw, 'image/png')
            response['media'] = {'sha256': digest(raw)[7:], 'width': image.width, 'height': image.height}
        else:
            # Negative QA must remain advisory and preserve deliverable pixels.
            response['data'] = (synthetic_native_qa(request)
                if request.get('purpose') == 'natural_retouch.native_qa' else
                {'verdict': 'requires_review', 'advice': 'SYNTHETIC advisory; result must remain available'})
        with self.store.connect() as db:
            db.execute('INSERT INTO fixture_jobs(request_id,kind,input_digest,request,response) VALUES(?,?,?,?,?)',
                       (identity, kind, digest(raw_request.encode()), raw_request, self.module.dumps(response)))
            db.execute('INSERT INTO fixture_calls(operation,identity,at) VALUES(?,?,?)', ('submit:' + kind, identity, time.time()))
        if self.control('unknown_submit_phase') == kind:
            raise TimeoutError('synthetic unknown submit outcome')
        return {'request_id': identity, 'status': 'queued'}

    def poll(self, kind, identity):
        with self.store.connect() as db:
            row = db.execute('SELECT kind,response FROM fixture_jobs WHERE request_id=?', (identity,)).fetchone()
            if row is None or row['kind'] != kind:
                raise ValueError('fixture_job_not_found')
            db.execute('UPDATE fixture_jobs SET polls=polls+1 WHERE request_id=?', (identity,))
            db.execute('INSERT INTO fixture_calls(operation,identity,at) VALUES(?,?,?)', ('poll:' + kind, identity, time.time()))
        if self.control('hold_phase') == kind: return {'request_id': identity, 'status': 'in_progress'}
        if self.control('fail_phase') == kind: return {'request_id': identity, 'status': 'failed'}
        return json.loads(row['response'])

    def audit(self):
        value = super().audit()
        value.update(profile='natural-retouch.v6', request_budget=self.owned.budget(),
                     photos=self.owned.query({'kind': 'photo'})['items'],
                     groups=self.owned.query({'kind': 'group'})['items'],
                     series=self.owned.query({'kind': 'series'})['items'])
        return value
