import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.prad.logic.zones import (
    G11,
    TAURON_G12W,
    Zone,
    ZoneSchedule,
    add_months,
    day_key,
    days_in_month,
    is_free_day,
    make_schedule,
    month_key,
    month_start,
    next_month_key,
    next_zone_change,
    zone_at,
)

W = ZoneInfo("Europe/Warsaw")


def h(y, m, d, H):
    return datetime(y, m, d, H, tzinfo=W)


def test_weekday_peak_and_offpeak():
    assert zone_at(h(2026, 11, 23, 6), TAURON_G12W) is Zone.PEAK  # poniedzialek
    assert zone_at(h(2026, 11, 23, 5), TAURON_G12W) is Zone.OFFPEAK
    assert zone_at(h(2026, 11, 23, 12), TAURON_G12W) is Zone.PEAK
    assert zone_at(h(2026, 11, 23, 13), TAURON_G12W) is Zone.OFFPEAK
    assert zone_at(h(2026, 11, 23, 14), TAURON_G12W) is Zone.OFFPEAK
    assert zone_at(h(2026, 11, 23, 15), TAURON_G12W) is Zone.PEAK
    assert zone_at(h(2026, 11, 23, 21), TAURON_G12W) is Zone.PEAK
    assert zone_at(h(2026, 11, 23, 22), TAURON_G12W) is Zone.OFFPEAK


def test_weekend_all_offpeak():
    assert all(zone_at(h(2026, 11, 21, H), TAURON_G12W) is Zone.OFFPEAK for H in range(24))  # sobota


def test_holidays_all_offpeak():
    for d in [(2026, 11, 11), (2026, 12, 24), (2026, 12, 25), (2027, 1, 6), (2027, 3, 29), (2027, 5, 27)]:
        assert all(zone_at(h(*d, H), TAURON_G12W) is Zone.OFFPEAK for H in range(24)), d


def test_utc_input_converted():
    assert zone_at(datetime(2026, 11, 23, 5, tzinfo=timezone.utc), TAURON_G12W) is Zone.PEAK  # 6:00 w Warszawie


def test_next_zone_change():
    assert next_zone_change(h(2026, 11, 23, 10), lambda d: TAURON_G12W) == h(2026, 11, 23, 13)
    assert next_zone_change(h(2026, 11, 20, 23), lambda d: TAURON_G12W) == h(2026, 11, 23, 6)  # pt 23:00 -> pn 6:00


def test_month_helpers():
    assert month_key(h(2026, 12, 1, 0)) == "2026-12"
    assert next_month_key("2026-12") == "2027-01"
    assert add_months("2026-11", 12) == "2027-11"
    assert days_in_month("2027-02") == 28
    assert day_key(h(2026, 12, 1, 0)) == "2026-12-01"
    assert month_start("2026-12") == h(2026, 12, 1, 0)


def _old_zone_at(hour_start):          # kopia funkcji z v0.2 - wzorzec
    local = hour_start.astimezone(W)
    if is_free_day(local.date()):
        return Zone.OFFPEAK
    return Zone.PEAK if 6 <= local.hour < 13 or 15 <= local.hour < 22 else Zone.OFFPEAK


def test_tauron_g12w_equals_old_function_for_every_hour_of_2026():
    t = datetime(2025, 12, 31, 23, tzinfo=timezone.utc)      # 1.01.2026 00:00 lokalnie
    for _ in range(8760):
        assert zone_at(t, TAURON_G12W) is _old_zone_at(t), t
        t += timedelta(hours=1)


def test_range_over_midnight_and_merge():
    s = make_schedule("wlasne", "x", "G12", [(22, 2), (1, 6), (13, 15)], "robocze", "robocze")
    assert s.tanie_robocze == ((13, 15), (22, 6))
    assert s.offpeak_hours() == frozenset({13, 14, 22, 23, 0, 1, 2, 3, 4, 5})
    assert zone_at(h(2026, 11, 23, 23), s) is Zone.OFFPEAK and zone_at(h(2026, 11, 24, 5), s) is Zone.OFFPEAK
    assert zone_at(h(2026, 11, 24, 6), s) is Zone.PEAK


def test_g12_weekend_and_holiday_as_workday():
    s = make_schedule("wlasne", "x", "G12", [(13, 15), (22, 6)], "robocze", "robocze")
    assert zone_at(h(2026, 11, 21, 10), s) is Zone.PEAK       # sobota
    assert zone_at(h(2026, 11, 11, 10), s) is Zone.PEAK       # swieto w srode
    assert zone_at(h(2026, 11, 11, 13), s) is Zone.OFFPEAK


def test_holiday_as_weekend_with_weekend_as_workday():
    s = make_schedule("wlasne", "x", "G12", [(22, 6)], "robocze", "weekend")
    assert zone_at(h(2026, 11, 11, 10), s) is Zone.PEAK       # swieto jak weekend, a weekend jak robocze


def test_g11_all_peak():
    assert all(zone_at(h(2026, 11, d, H), G11) is Zone.PEAK for d in (21, 23) for H in range(24))
    assert next_zone_change(h(2026, 11, 23, 10), lambda d: G11) is None


def test_next_zone_change_over_long_holiday():
    assert next_zone_change(h(2026, 12, 23, 22), lambda d: TAURON_G12W) == h(2026, 12, 28, 6)


def test_next_zone_change_uses_schedule_of_the_day():
    assert next_zone_change(h(2026, 11, 30, 23), lambda d: G11 if d.month == 12 else TAURON_G12W) == h(2026, 12, 1, 0)


@pytest.mark.parametrize("args", [
    ("G12", [(5, 5)], "robocze", "robocze"), ("G12", [(0, 24)], "robocze", "robocze"),
    ("G12", [], "robocze", "robocze"), ("G12", [(0, 12), (12, 0)], "robocze", "robocze"),
    ("G11", [(22, 6)], "robocze", "robocze"), ("G11", [], "tania", "robocze"),
    ("G13", [(22, 6)], "robocze", "robocze"), ("G12", [(22, 6)], "zawsze", "robocze"),
])
def test_make_schedule_rejects(args):
    with pytest.raises(ValueError):
        make_schedule("wlasne", "x", *args)


def test_schedule_roundtrip_and_key():
    assert ZoneSchedule.from_dict(json.loads(json.dumps(TAURON_G12W.to_dict()))) == TAURON_G12W
    assert TAURON_G12W.to_dict()["tanie_robocze"] == [[13, 15], [22, 6]]
    other = make_schedule("wlasne", "inna nazwa", "G12w", [(22, 6), (13, 15)], "tania", "weekend")
    assert other.key() == TAURON_G12W.key() and other != TAURON_G12W
