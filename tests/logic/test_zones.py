from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from custom_components.prad.logic.zones import (
    Zone,
    add_months,
    day_key,
    days_in_month,
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
    assert zone_at(h(2026, 11, 23, 6)) is Zone.PEAK  # poniedzialek
    assert zone_at(h(2026, 11, 23, 5)) is Zone.OFFPEAK
    assert zone_at(h(2026, 11, 23, 12)) is Zone.PEAK
    assert zone_at(h(2026, 11, 23, 13)) is Zone.OFFPEAK
    assert zone_at(h(2026, 11, 23, 14)) is Zone.OFFPEAK
    assert zone_at(h(2026, 11, 23, 15)) is Zone.PEAK
    assert zone_at(h(2026, 11, 23, 21)) is Zone.PEAK
    assert zone_at(h(2026, 11, 23, 22)) is Zone.OFFPEAK


def test_weekend_all_offpeak():
    assert all(zone_at(h(2026, 11, 21, H)) is Zone.OFFPEAK for H in range(24))  # sobota


def test_holidays_all_offpeak():
    for d in [(2026, 11, 11), (2026, 12, 24), (2026, 12, 25), (2027, 1, 6), (2027, 3, 29), (2027, 5, 27)]:
        assert all(zone_at(h(*d, H)) is Zone.OFFPEAK for H in range(24)), d


def test_utc_input_converted():
    assert zone_at(datetime(2026, 11, 23, 5, tzinfo=timezone.utc)) is Zone.PEAK  # 6:00 w Warszawie


def test_next_zone_change():
    assert next_zone_change(h(2026, 11, 23, 10)) == h(2026, 11, 23, 13)
    assert next_zone_change(h(2026, 11, 20, 23)) == h(2026, 11, 23, 6)  # pt 23:00 -> pn 6:00


def test_month_helpers():
    assert month_key(h(2026, 12, 1, 0)) == "2026-12"
    assert next_month_key("2026-12") == "2027-01"
    assert add_months("2026-11", 12) == "2027-11"
    assert days_in_month("2027-02") == 28
    assert day_key(h(2026, 12, 1, 0)) == "2026-12-01"
    assert month_start("2026-12") == h(2026, 12, 1, 0)
