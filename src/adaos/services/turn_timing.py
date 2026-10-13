"""Lightweight correlation receipts for Builder turns; no domain package imports."""
from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from typing import Any


def record_turn_timing(*, meta: Mapping[str, Any], stage: str, timings: Mapping[str, Any]) -> dict[str, Any]:
    values = {}
    for key, value in timings.items():
        if (not re.fullmatch(r'[a-z][a-z0-9_]{0,79}_ms', key)
                or isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            continue
        values[key] = round(value, 2)
    receipt = {'schema': 'adaos.builder.turn_timing.v1', 'stage': stage[:80], 'timings_ms': values}
    for key in ('turn_trace_id', 'source_message_id', 'webspace_id'):
        value = str(meta.get(key) or '')
        if re.fullmatch(r'[A-Za-z0-9_.:-]{1,240}', value):
            receipt[key] = value
    logging.getLogger('adaos.builder.timing').info('%s', json.dumps(receipt, separators=(',', ':')))
    return receipt
