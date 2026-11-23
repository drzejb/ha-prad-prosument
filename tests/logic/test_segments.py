from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from custom_components.prad.logic.segments import freeze_segments, price_runs, ratio_runs, run_energy
from custom_components.prad.logic.state import Counters
from custom_components.prad.logic.tariff import DEFAULT_PRICING, OpustEntry, Pricing, example_price
from custom_components.prad.logic.zones import Zone

P, O = Zone.PEAK, Zone.OFFPEAK
A = example_price(date(2026, 1, 1))
B = replace(A, od=date(2026, 11, 15), nazwa="B", energy_peak=D("0.70"), energy_offpeak=D("0.45"),
            oze=D("0.008"), fixed_network=D("12.00"))
PR = Pricing((A, B), (OpustEntry(date(2026, 1, 1), D("0.8")),))


def test_runs():
    assert price_runs("2026-11", PR) == [(date(2026, 11, 1), date(2026, 11, 14), A), (date(2026, 11, 15), date(2026, 11, 30), B)]
    assert price_runs("2026-10", PR) == [(date(2026, 10, 1), date(2026, 10, 31), A)]
    same = Pricing((A,), (OpustEntry(date(2026, 1, 1), D("0.8")), OpustEntry(date(2026, 11, 15), D("0.8"))))
    assert ratio_runs("2026-11", same) == [(date(2026, 11, 1), date(2026, 11, 30), D("0.8"))]      # same value = one run
    two = Pricing((A,), (OpustEntry(date(2026, 1, 1), D("0.8")), OpustEntry(date(2026, 11, 15), D("0.7"))))
    assert [r[2] for r in ratio_runs("2026-11", two)] == [D("0.8"), D("0.7")]


RUNS = [(date(2026, 11, 1), date(2026, 11, 14)), (date(2026, 11, 15), date(2026, 11, 30))]


def test_run_energy_from_days():
    days = {f"2026-11-{d:02d}": Counters(import_peak=10.0 if d <= 14 else 6.3, import_offpeak=5.0,
                                         export_peak=2.0 if d <= 14 else 0.0) for d in range(1, 31)}
    month = Counters(import_peak=240.8, import_offpeak=150.0, export_peak=28.0)
    e = run_energy(month, days, RUNS)
    assert e[0] == {"import_peak": D("140"), "import_offpeak": D("70"), "export_peak": D("28"), "export_offpeak": D("0")}
    assert e[1]["import_peak"] == D("100.8") and e[1]["import_offpeak"] == D("80") and e[1]["export_peak"] == D("0")


def test_run_energy_days_missing_split_by_missing_day_count():
    days = {f"2026-11-{d:02d}": Counters(import_peak=10.0) for d in range(20, 31)}      # 11 days, all in the second run
    e = run_energy(Counters(import_peak=300.0), days, RUNS)
    assert e[0]["import_peak"] == D("140")      # (300 - 110) * 14 / 19
    assert e[1]["import_peak"] == D("160")      # 110 + 190 * 5 / 19


def test_run_energy_rest_never_negative_and_extra():
    days = {f"2026-11-{d:02d}": Counters(import_peak=10.0) for d in range(1, 10)}
    assert run_energy(Counters(import_peak=50.0), days, RUNS)[0]["import_peak"] == D("90")
    extra = {date(2026, 11, d): {"import_peak": 10.0, "import_offpeak": 0.0, "export_peak": 0.0, "export_offpeak": 0.0}
             for d in range(10, 31)}
    e = run_energy(Counters(import_peak=90.0), days, RUNS, extra)
    assert e[0]["import_peak"] == D("140") and e[1]["import_peak"] == D("160")


def test_freeze_segments_without_days_gives_one_full_segment():
    c = Counters(import_peak=195.0, import_offpeak=280.0, export_peak=279.0, export_offpeak=312.0)
    price, ratio = freeze_segments("2026-09", c, {}, DEFAULT_PRICING)
    assert len(price) == 1 and (price[0].od, price[0].do) == (date(2026, 9, 1), date(2026, 9, 30))
    assert price[0].pobor == {P: D("195"), O: D("280")} and price[0].oddanie == {P: D("279"), O: D("312")}
    assert ratio[0].wspolczynnik == D("0.8") and ratio[0].oddanie == {P: D("279"), O: D("312")}
