from copy import deepcopy

from adaos.services.site_studio.chat_authoring import (
    chat_context_size, chat_execution_limits, merge_chat_observations,
)


def observation(kind="material", target="prototype", offset=0, text="first page"):
    return {"request": {"kind": kind, "target": target, "offset": offset}, "result": {"content": text}}


def test_inspection_deduplicates_only_the_same_range_and_keeps_other_namespaces():
    original = [observation(), observation(offset=24000), observation(kind="component")]
    before = deepcopy(original)
    incoming = [observation(text="updated first page")]
    merged = merge_chat_observations(original, incoming)
    assert len(merged) == 3
    assert {item["request"]["offset"] for item in merged} == {0, 24000}
    assert merged[-1]["result"]["content"] == "updated first page"
    merged[-1]["result"]["content"] = "local modification"
    assert original == before and incoming[0]["result"]["content"] == "updated first page"


def test_more_than_24_distinct_reads_keep_the_early_reference():
    reads = [observation(offset=offset) for offset in range(30)]
    assert merge_chat_observations(reads[:24], reads[24:]) == reads


def test_run_policy_is_independent_and_counts_utf8_bytes():
    policy = chat_execution_limits()
    assert policy == {"max_steps": 16, "max_context_bytes": 256 * 1024, "max_repairs": 3}
    policy["max_steps"] = 1
    assert chat_execution_limits()["max_steps"] == 16
    assert chat_context_size({"text": "я"}) > chat_context_size({"text": "a"})
