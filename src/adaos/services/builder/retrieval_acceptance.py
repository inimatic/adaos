"""Materialized platform facts, not model claims, close a retrieval dependency."""
import json

from adaos.services.builder_context import digest
from .prototype_mcp import library_snapshot, OPENAI_TOOLS, SERVER_LABEL


class ContextDeliveryRejected(ValueError):
    def __init__(self, receipt):
        self.receipt = receipt
        self.findings = [{'code': 'context.required_retrieval_not_delivered', 'gate': 'G7',
            'owner': 'context_delivery', 'detail': 'Required guidance was not materialized: ' + ', '.join(receipt['missing_fragment_ids'])}]
        super().__init__(self.findings[0]['detail'])


def verify_materialized_guidance(response, admission):
    required = set(admission.get('required_retrieval_refs') or [])
    if not required:
        return None
    snapshot = library_snapshot()
    expected = {row['id']: row for row in snapshot['fragments']}
    retrieved = set()
    if admission['transport'] == 'personal':
        # These are produced by Core's registered executor only after checking
        # exact public fragment contents; assistant/model text is never read.
        for call in (response.get('tool_telemetry') or {}).get('tool_calls', []):
            if (call.get('name') == OPENAI_TOOLS[1] and call.get('status') == 'completed'
                    and call.get('library_digest') == snapshot['library_digest']):
                retrieved.update(ref for ref in call.get('fragment_ids', []) if ref in expected)
    else:
        for item in (response.get('response') or {}).get('output', []):
            if (item.get('type') != 'mcp_call' or item.get('name') != OPENAI_TOOLS[1]
                    or item.get('server_label') != SERVER_LABEL or item.get('status') != 'completed' or item.get('error')):
                continue
            value = item.get('output')
            try:
                if isinstance(value, str):
                    if len(value.encode('utf-8')) > 65536: continue
                    value = json.loads(value)
                if not isinstance(value, dict) or value.get('isError') or value.get('ok') is False: continue
                if 'structuredContent' in value: value = value['structuredContent']
                elif isinstance(value.get('content'), list): value = json.loads(value['content'][0]['text'])
                if isinstance(value.get('result'), dict): value = value['result']
                if value.get('library_digest') != snapshot['library_digest'] or value.get('side_effects') != 'none': continue
                fragments = value.get('fragments') or ([value['fragment']] if value.get('fragment') else [])
                for fragment in fragments:
                    known = expected.get(fragment.get('id'))
                    if (known and fragment.get('content') == known['content']
                            and fragment.get('content_digest') == known['content_digest']
                            and digest(fragment['content']) == known['content_digest']):
                        retrieved.add(fragment['id'])
            except (ValueError, KeyError, TypeError, IndexError):
                continue
    missing = sorted(required - retrieved)
    receipt = {'schema': 'adaos.builder.retrieval_acceptance.v1', 'status': 'failed' if missing else 'passed',
        'generation_plan_ref': admission.get('generation_plan_ref'), 'transport': admission['transport'],
        'library_digest': snapshot['library_digest'], 'required_fragment_ids': sorted(required),
        'materialized_fragment_ids': sorted(retrieved), 'missing_fragment_ids': missing,
        'authority': 'core_registered_executor' if admission['transport'] == 'personal' else 'provider_mcp_tool_output_with_exact_content_check'}
    if admission.get('guidance_library_digest') != snapshot['library_digest']:
        receipt.update(status='failed', missing_fragment_ids=sorted(required))
    if receipt['status'] == 'failed':
        raise ContextDeliveryRejected(receipt)
    return receipt
