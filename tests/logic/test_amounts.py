from decimal import Decimal as D

from custom_components.prad.logic.amounts import compute_amounts, price_now_gross
from custom_components.prad.logic.tariff import DEFAULT_TARIFF, Tariff
from custom_components.prad.logic.zones import Zone

P, O = Zone.PEAK, Zone.OFFPEAK


def test_september_invoice_exact():
    t = compute_amounts({P: D("195"), O: D("280")}, {P: D("0"), O: D("2")}, DEFAULT_TARIFF)
    assert (t.net, t.vat, t.gross) == (D("45.37"), D("10.44"), D("55.81"))
    assert t.energy_gross == D("1.03") and t.distribution_gross == D("54.78")
    line = {(l.key, l.zone): l for l in t.lines}
    assert line[("oze", P)].net == D("1.42") and line[("oze", P)].vat == D("0.33")
    assert line[("cogeneration", P)].net == D("0.59")  # 0.585 -> 0.59 (HALF_UP)
    assert line[("capacity", None)].gross == D("29.58")
    assert t.peak_gross + t.offpeak_gross + t.fixed_gross == t.gross


def test_october_model_about_451():
    t = compute_amounts({P: D("370"), O: D("431")}, {P: D("202.8"), O: D("235")}, DEFAULT_TARIFF)
    assert D("449") <= t.gross <= D("453")


def test_price_now():
    assert price_now_gross(P, DEFAULT_TARIFF) == D("1.2304")
    assert price_now_gross(O, DEFAULT_TARIFF) == D("0.6306")


def test_tariff_roundtrip():
    assert Tariff.from_dict(DEFAULT_TARIFF.to_dict()) == DEFAULT_TARIFF
    assert Tariff.from_dict({}) == DEFAULT_TARIFF
