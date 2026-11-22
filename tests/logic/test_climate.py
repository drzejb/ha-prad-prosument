import pytest

from custom_components.prad.logic.climate import degree_days, kwh_per_degree_day


def test_degree_days_sums_only_days_below_base():
    assert degree_days([10, 20, 18.5], 18) == pytest.approx(8)


def test_kwh_per_degree_day_is_none_below_ten_degree_days():
    assert kwh_per_degree_day(500, 9.9) is None


def test_kwh_per_degree_day_divides_kwh_by_degree_days():
    assert kwh_per_degree_day(500, 250) == pytest.approx(2.0)
