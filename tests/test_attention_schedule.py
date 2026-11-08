from datetime import datetime, timezone

import pytest

from adaos.services.attention_schedule import quiet_period
from adaos.services.conversation_attention import (
    ConversationAttentionError, default_attention_policy, plan_attention,
)


def schedule(**patch):
    return dict(enabled=True, start="22:00", end="08:00", timezone="Europe/Moscow", **patch)


@pytest.mark.parametrize("now,quiet,until", [
    ("2026-11-08T20:00:00Z", True, "2026-11-09T05:00:00+00:00"),
    ("2026-11-09T07:00:00Z", False, None),
    ("2026-11-08T19:00:00Z", True, "2026-11-09T05:00:00+00:00"),
    ("2026-11-09T05:00:00Z", False, None),
])
def test_moscow_quiet_interval_and_boundaries(now, quiet, until):
    result, due = quiet_period(schedule(), now=now)
    assert result is quiet
    assert (due.isoformat() if due else None) == until
    policy = default_attention_policy()
    policy["quiet_hours"].update(schedule())
    assert plan_attention("notification", policy=policy, now=now)["notify"] is not quiet


def test_cross_midnight_belongs_to_starting_weekday():
    quiet = schedule(weekdays=[6])  # Sunday night continues into Monday.
    assert quiet_period(quiet, now="2026-11-09T01:00:00Z")[0]
    assert not quiet_period(quiet, now="2026-11-09T20:00:00Z")[0]


def test_equal_endpoints_and_contiguous_all_day_quiet():
    quiet = {**schedule(), "start": "00:00", "end": "00:00"}
    assert quiet_period(quiet, now="2026-11-09T01:00:00Z") == (True, None)
    quiet["weekdays"] = [0, 1]
    assert quiet_period(quiet, now="2026-11-09T01:00:00Z")[1].isoformat() == "2026-11-10T21:00:00+00:00"


@pytest.mark.parametrize("start,end,now,until", [
    ("01:00", "02:30", "2026-03-08T06:30:00Z", "2026-03-08T07:00:00+00:00"),
    ("00:00", "01:30", "2026-11-01T05:00:00Z", "2026-11-01T05:30:00+00:00"),
])
def test_dst_gap_advances_and_fold_uses_first_occurrence(start, end, now, until):
    quiet = dict(enabled=True, start=start, end=end, timezone="America/New_York")
    assert quiet_period(quiet, now=now)[1].isoformat() == until
    assert not quiet_period(quiet, now=until)[0]


@pytest.mark.parametrize("patch", [
    {"start": "24:00"}, {"end": "29:00"}, {"timezone": "Not/A_Zone"},
    {"weekdays": []}, {"weekdays": [0, 0]}, {"weekdays": [True]},
])
def test_invalid_configuration_is_rejected(patch):
    policy = default_attention_policy()
    policy["quiet_hours"].update(patch)
    with pytest.raises(ConversationAttentionError):
        plan_attention("notification", policy=policy)


def test_clock_must_be_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        quiet_period(schedule(), now=datetime(2026, 11, 8, 23))
    assert quiet_period(schedule(), now=datetime(2026, 11, 8, 20, tzinfo=timezone.utc))[0]
