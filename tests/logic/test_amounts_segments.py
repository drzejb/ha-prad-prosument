from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from custom_components.prad.logic.amounts import (
    SegmentAmounts,
    compute_amounts,
    compute_amounts_segments,
    split_kwh,
    split_largest_remainder,
    split_proportional,
)
from custom_components.prad.logic.tariff import DEFAULT_TARIFF, example_price
from custom_components.prad.logic.zones import Zone

P, O = Zone.PEAK, Zone.OFFPEAK
A = example_price(date(2026, 1, 1))
B = replace(A, od=date(2026, 11, 15), nazwa="B", energy_peak=D("0.70"), energy_offpeak=D("0.45"),
            oze=D("0.008"), fixed_network=D("12.00"))


def test_split_largest_remainder():
    assert split_largest_remainder(10, [D(1), D(1), D(1)]) == [4, 3, 3]      # tie -> earlier
    assert split_largest_remainder(3, [D(1), D(1)]) == [2, 1]
    assert split_largest_remainder(5, [D(0), D(0)]) == [5, 0]                 # zero weights -> first
    assert split_largest_remainder(7, [D(0), D(3), D(1)]) == [0, 5, 2]        # zero weight -> 0
    assert split_largest_remainder(0, [D(2), D(3)]) == [0, 0]
    assert split_largest_remainder(241, [D("140"), D("100.8")]) == [140, 101]
    assert split_largest_remainder(219, [D("140"), D("100.8")]) == [127, 92]
    assert split_largest_remainder(139, [D("70"), D("80")]) == [65, 74]
    for bad in ((-1, [D(1)]), (1, []), (1, [D(-1), D(2)])):
        with pytest.raises(ValueError):
            split_largest_remainder(*bad)


def test_split_kwh_keeps_fraction():
    assert split_kwh(D("7.5"), [D(1), D(3)]) == [D("2"), D("5.5")]
    assert split_kwh(D("7.5"), [D(0), D(0)]) == [D("7.5"), D("0")]
    assert split_kwh(D("7.5"), [D(4)]) == [D("7.5")]
    assert sum(split_kwh(D("206.5"), [D("140"), D("100.8")])) == D("206.5")
    with pytest.raises(ValueError):
        split_kwh(D("-1"), [D(1)])


def test_split_proportional():
    assert split_proportional(D("149.1"), [D("140"), D("31.5")]) == [D("121.714"), D("27.386")]
    assert split_proportional(D("83.8"), [D("70"), D("25")]) == [D("61.747"), D("22.053")]
    assert split_proportional(D("5.5"), [D("3")]) == [D("5.5")]
    assert split_proportional(D("5.5"), [D(0), D(0)]) == [D("5.5"), D("0")]


def test_one_segment_equals_compute_amounts():
    c, b = {P: D("195"), O: D("280")}, {P: D("0"), O: D("2")}
    assert compute_amounts_segments([SegmentAmounts(A, 30, c, b)], 30) == compute_amounts(c, b, DEFAULT_TARIFF)
    assert compute_amounts(c, b, A).gross == D("55.81")


def test_price_change_on_15_november():
    t = compute_amounts_segments([
        SegmentAmounts(A, 14, {P: D("140"), O: D("70")}, {P: D("127"), O: D("65")}),
        SegmentAmounts(B, 16, {P: D("101"), O: D("80")}, {P: D("92"), O: D("74")}),
    ], 30)
    assert (t.net, t.vat, t.gross) == (D("339.96"), D("78.21"), D("418.17"))
    assert (t.peak_gross, t.offpeak_gross, t.fixed_gross) == (D("278.06"), D("90.81"), D("49.30"))
    line = {(l.segment, l.key, l.zone): l for l in t.lines}
    assert line[(0, "energy", P)].net == D("79.63") and line[(1, "energy", P)].net == D("64.40")
    assert line[(0, "oze", P)].net == D("1.02") and line[(1, "oze", P)].net == D("0.81")
    assert line[(0, "fixed_network", None)].net == D("5.07")
    assert line[(1, "fixed_network", None)].net == D("6.40")
    assert line[(0, "capacity", None)].net == D("11.22") and line[(1, "capacity", None)].net == D("12.83")
    assert line[(0, "subscription", None)].net == D("2.13") and line[(1, "subscription", None)].net == D("2.43")
    assert line[(1, "capacity", None)].vat == D("2.95")
