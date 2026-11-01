# Пересчёт напряжения и процентов батареи.
import config
from battery import SOURCE_BATTERY, SOURCE_NONE, SOURCE_USB, adc_uv_to_mv, percent, rest_mv, source


def test_adc_divider():
    assert adc_uv_to_mv(1_850_000) == 3700


def test_rest_compensation():
    # 700 мА × 0,25 Ом = 175 мВ; R_INT_MOHM — именно миллиомы
    assert config.R_INT_MOHM == 250
    assert rest_mv(3450, 700) == 3625
    assert isinstance(rest_mv(3450, 700), int)


def test_percent_ends_and_monotonic():
    assert percent(4300) == 100
    assert percent(3000) == 0
    last = 101
    for mv in range(4250, 3250, -5):
        p = percent(mv)
        assert 0 <= p <= 100
        assert p <= last
        last = p


def test_percent_table_points():
    for mv, pct in config.LIPO_TABLE:
        assert percent(mv) == pct


def test_low_threshold_is_a_few_percent():
    assert 4 <= percent(config.V_LOW_MV) <= 7


def test_source():
    assert source(4750) == SOURCE_USB
    assert source(3700) == SOURCE_BATTERY
    assert source(4200) == SOURCE_BATTERY
    assert source(100) == SOURCE_NONE
