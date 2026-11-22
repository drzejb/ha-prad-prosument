from decimal import Decimal

from custom_components.prad.logic.settlement import Inflow, settle
from custom_components.prad.logic.zones import Zone

P, O = Zone.PEAK, Zone.OFFPEAK
D = Decimal


def test_september_invoice():
    s = settle("2026-09", {P: D("195"), O: D("280")}, {P: D("279"), O: D("312")}, (), D("0.8"), False, True)
    assert s.credit == {P: D("223"), O: D("250")} and s.billable == {P: D("0"), O: D("2")}
    assert s.cross_moved == D("28") and s.new_inflows == () and s.storage_after == 0


def test_september_from_ha_data_rounds_to_invoice():
    s = settle("2026-09", {P: D("194.2"), O: D("280.2")}, {P: D("279.0"), O: D("312.2")}, (), D("0.8"), False, True)
    assert s.consumption == {P: D("194"), O: D("280")} and s.billable[O] == D("1")  # +-1 kWh vs invoice - expected


def test_october_running_not_rounded():
    s = settle("2026-10", {P: D("370"), O: D("431")}, {P: D("209"), O: D("245")}, (), D("0.8"), False, False)
    assert s.billable == {P: D("202.8"), O: D("235.0")}


def test_surplus_becomes_inflow_valid_12_months():
    s = settle("2027-06", {P: D("100"), O: D("50")}, {P: D("300"), O: D("100")}, (), D("0.8"), False, True)
    # credit P=240, O=80; own P=100 (surplus 140), own O=50 (surplus 30)
    assert s.new_inflows == (Inflow("2027-06", P, D("140"), "2028-06"), Inflow("2027-06", O, D("30"), "2028-06"))


def test_storage_fifo_oldest_first_peak_first():
    inflows = (Inflow("2026-12", O, D("5"), "2027-12"), Inflow("2026-11", P, D("10"), "2027-11"))
    s = settle("2027-01", {P: D("8"), O: D("4")}, {P: D("0"), O: D("0")}, inflows, D("0.8"), False, True)
    assert s.storage_used == D("12") and s.billable == {P: D("0"), O: D("0")}
    assert s.inflows_after == (Inflow("2026-12", O, D("3"), "2027-12"),)


def test_expiry_after_valid_until_month():
    inflows = (Inflow("2026-11", P, D("10"), "2027-11"),)
    s = settle("2027-11", {P: D("0"), O: D("0")}, {P: D("0"), O: D("0")}, inflows, D("0.8"), False, True)
    assert s.expired == D("10") and s.storage_after == 0
    s2 = settle("2027-11", {P: D("4"), O: D("0")}, {P: D("0"), O: D("0")}, inflows, D("0.8"), False, True)
    assert s2.storage_used == D("4") and s2.expired == D("6")


def test_running_does_not_expire():
    s = settle("2027-11", {P: D("0"), O: D("0")}, {P: D("0"), O: D("0")},
               (Inflow("2026-11", P, D("10"), "2027-11"),), D("0.8"), False, False)
    assert s.expired == 0 and s.storage_after == D("10")


def test_storage_before_cross_flag():
    inflows = (Inflow("2026-11", P, D("10"), "2027-11"),)
    a = settle("2027-01", {P: D("0"), O: D("10")}, {P: D("10"), O: D("0")}, inflows, D("0.8"), False, True)
    b = settle("2027-01", {P: D("0"), O: D("10")}, {P: D("10"), O: D("0")}, inflows, D("0.8"), True, True)
    assert a.cross_moved == D("8") and a.storage_used == D("2")
    assert b.storage_used == D("10") and b.cross_moved == D("0") and b.new_inflows[0].amount == D("8")


def test_zero_everything():
    s = settle("2026-11", {P: D(0), O: D(0)}, {P: D(0), O: D(0)}, (), D("0.8"), False, True)
    assert s.billable == {P: 0, O: 0} and s.new_inflows == ()
