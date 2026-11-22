from datetime import datetime, timedelta

import pytest

from custom_components.prad.logic.tracker import TrackerRef, advance
from custom_components.prad.logic.zones import TZ as W


def h(y, mo, d, hh=0, mm=0, ss=0):
    return datetime(y, mo, d, hh, mm, ss, tzinfo=W)


def ref(time, values):
    return TrackerRef(time=time, values=values)


T0 = h(2026, 11, 23, 10)
HOUR = timedelta(hours=1)


def test_first_reading_sets_reference():
    r = advance(None, h(2026, 11, 23, 10, 0, 10), (100.0, 50.0), 30)
    assert r.hours == () and r.new_ref.time == h(2026, 11, 23, 10)


def test_one_hour():
    r = advance(ref(h(2026, 11, 23, 10), (100.0, 50.0)), h(2026, 11, 23, 11, 0, 10), (101.5, 50.2), 30)
    assert [x.start for x in r.hours] == [h(2026, 11, 23, 10)]
    assert r.hours[0].deltas == pytest.approx((1.5, 0.2))


def test_gap_split_equally():
    r = advance(ref(h(2026, 11, 23, 10), (100.0, 0.0)), h(2026, 11, 23, 13, 0, 10), (103.0, 0.0), 30)
    assert [x.start.hour for x in r.hours] == [10, 11, 12]
    assert all(x.deltas[0] == pytest.approx(1.0) for x in r.hours)


def test_unavailable_keeps_reference():
    rf = ref(T0, (100.0, 5.0))
    r = advance(rf, T0 + HOUR, None, 30)
    assert r.new_ref is rf and r.hours == () and not r.rejected


def test_same_hour_twice_keeps_reference():
    rf = ref(T0, (100.0, 5.0))
    r = advance(rf, T0 + timedelta(minutes=30), (101.0, 5.0), 30)
    assert r.new_ref is rf and r.hours == () and not r.rejected


def test_negative_delta_resets_reference():
    r = advance(ref(T0, (100.0, 5.0)), T0 + HOUR, (3.0, 5.0), 30)
    assert r.rejected and r.reason == "negative" and r.new_ref.values == (3.0, 5.0) and r.hours == ()


def test_over_limit_resets_reference():
    r = advance(ref(T0, (100.0, 0.0)), T0 + 2 * HOUR, (161.0, 0.0), 30)
    assert r.rejected and r.reason == "over_limit"


def test_limit_scales_with_gap():
    r = advance(ref(T0, (100.0, 0.0)), T0 + 3 * HOUR, (189.0, 0.0), 30)
    assert not r.rejected and len(r.hours) == 3


def test_dst_autumn():
    t0 = datetime(2026, 10, 25, 0, tzinfo=W)
    t1 = datetime(2026, 10, 25, 4, tzinfo=W)
    r = advance(ref(t0, (0.0, 0.0)), t1, (5.0, 0.0), 30)
    assert len(r.hours) == 5


def test_dst_spring():
    r = advance(ref(datetime(2026, 3, 29, 0, tzinfo=W), (0.0, 0.0)), datetime(2026, 3, 29, 4, tzinfo=W), (3.0, 0.0), 30)
    assert len(r.hours) == 3
