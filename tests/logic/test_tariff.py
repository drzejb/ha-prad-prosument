import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from custom_components.prad.logic.tariff import (
    DEFAULT_PRICING, DEFAULT_TARIFF, EMPTY_PRICE_NAME, EXAMPLE_PRICE_NAME, OpustEntry, PriceListError,
    PriceVersion, Pricing, empty_price, example_price, next_change, price_at, ratio_at, validate_prices,
    with_opust, with_version, zones_differ,
)
from custom_components.prad.logic.zones import G11, TAURON_G12W, TZ, make_schedule

A = example_price(date(2026, 9, 1))
G12 = make_schedule("wlasne", "G12 własne", "G12", [(13, 15), (22, 6)], "robocze", "robocze")


def test_price_at_before_first_and_on_boundary():
    b = replace(A, od=date(2026, 11, 15), nazwa="B")
    assert price_at([A, b], date(2026, 1, 1)) is A            # przed pierwszą wersją → pierwsza
    assert price_at([A, b], date(2026, 11, 14)) is A and price_at([A, b], date(2026, 11, 15)) is b
    assert next_change([A, b], date(2026, 11, 14)) == date(2026, 11, 15) and next_change([A, b], date(2026, 11, 15)) is None


@pytest.mark.parametrize("day, hours", [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)])
def test_schedule_at_on_dst_days(day, hours):
    base = replace(A, od=date(2026, 1, 1))                      # lista musi być posortowana po `od`
    b = replace(A, od=day, nazwa="B", taryfa="G11", strefy=G11)
    pricing = Pricing((base, b), (OpustEntry(date(2026, 1, 1), D("0.8")),))
    t = datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(timezone.utc)
    assert pricing.schedule_at(t - timedelta(hours=1)) == TAURON_G12W
    for n in range(hours):
        assert pricing.schedule_at(t + timedelta(hours=n)) == G11, n
    assert (t + timedelta(hours=hours)).astimezone(TZ).date() == day + timedelta(days=1)


def test_ratio_at():
    entries = [OpustEntry(date(2026, 9, 1), D("0.8")), OpustEntry(date(2026, 11, 15), D("0.7"))]
    assert ratio_at(entries, date(2026, 1, 1)) == D("0.8") and ratio_at(entries, date(2026, 11, 15)) == D("0.7")


def test_roundtrip():
    assert PriceVersion.from_dict(json.loads(json.dumps(A.to_dict()))) == A
    assert A.to_dict()["od"] == "2026-09-01" and A.to_dict()["energy_peak"] == "0.627"
    e = OpustEntry(date(2026, 9, 1), D("0.8"))
    assert OpustEntry.from_dict(json.loads(json.dumps(e.to_dict()))) == e


def test_zone_change_only_on_first_day():                       # R-a
    with pytest.raises(PriceListError) as err:
        with_version([A], None, replace(A, od=date(2026, 12, 15), nazwa="G11", taryfa="G11", strefy=G11))
    assert err.value.code == "zone_change_not_first"
    assert len(with_version([A], None, replace(A, od=date(2026, 12, 1), nazwa="G11", taryfa="G11", strefy=G11))) == 2
    assert len(with_version([A], None, replace(A, od=date(2026, 12, 15), nazwa="ceny"))) == 2   # same strefy – dowolny dzień


def test_removing_middle_version_creating_zone_change_is_rejected():   # R-a po usunięciu
    mid = replace(A, od=date(2026, 12, 1), nazwa="G12", taryfa="G12", strefy=G12)
    last = replace(mid, od=date(2026, 12, 15), nazwa="G12 nowe ceny")
    versions = with_version(with_version([A], None, mid), None, last)
    with pytest.raises(PriceListError) as err:
        with_version(versions, date(2026, 12, 1), None)
    assert err.value.code == "zone_change_not_first"


def test_moving_od_creating_zone_change_is_rejected():
    g11 = replace(A, od=date(2026, 12, 1), nazwa="G11", taryfa="G11", strefy=G11)
    with pytest.raises(PriceListError):
        with_version([A, g11], date(2026, 12, 1), replace(g11, od=date(2026, 12, 2)))


def test_list_errors():
    for args, code in [(([A], date(2026, 9, 1), None), "last_version"), (([A], date(2020, 1, 1), None), "unknown_version"),
                       (([A], None, replace(A, nazwa="dup")), "duplicate_date")]:
        with pytest.raises(PriceListError) as err:
            with_version(*args)
        assert err.value.code == code
    with pytest.raises(PriceListError) as err:
        validate_prices([])
    assert err.value.code == "empty"
    with pytest.raises(PriceListError):
        with_opust([OpustEntry(date(2026, 9, 1), D("0.8"))], date(2026, 9, 1), None)


def test_with_version_sorts_and_replaces():
    early = replace(A, od=date(2026, 1, 1), nazwa="wcześniejszy")
    assert [v.nazwa for v in with_version([A], None, early)] == ["wcześniejszy", EXAMPLE_PRICE_NAME]
    assert with_version([A], date(2026, 9, 1), replace(A, energy_peak=D("1")))[0].energy_peak == D("1")


def test_zones_differ():
    g11 = replace(A, od=date(2026, 12, 1), nazwa="G11", taryfa="G11", strefy=G11)
    assert zones_differ([A], [A, g11], date(2026, 11, 1), date(2026, 11, 30)) is False
    assert zones_differ([A], [A, g11], date(2026, 11, 1), date(2026, 12, 1)) is True
    assert zones_differ([A], [replace(A, energy_peak=D("9"))], date(2026, 1, 1), date(2026, 12, 31)) is False
    assert zones_differ([A], [A, g11], date(2026, 12, 5), date(2026, 12, 1)) is False


@pytest.mark.parametrize("changes", [dict(energy_peak=D("-1")), dict(vat_percent=D("101")), dict(nazwa="  "),
                                     dict(taryfa="G11"), dict(oze=D("NaN"))])
def test_price_version_rejects(changes):
    with pytest.raises(ValueError):
        replace(A, **changes)


def test_opust_range_and_defaults():
    for bad in ("-0.1", "1.01", "NaN"):
        with pytest.raises(ValueError):
            OpustEntry(date(2026, 1, 1), D(bad))
    e = empty_price(date(2026, 11, 1), G11)
    assert e.taryfa == "G11" and e.energy_peak == D("0") and e.vat_percent == D("23") and e.nazwa == EMPTY_PRICE_NAME
    assert DEFAULT_PRICING.prices[0].energy_peak == DEFAULT_TARIFF.energy_peak and DEFAULT_PRICING.ratio_at(date(2030, 1, 1)) == D("0.8")
