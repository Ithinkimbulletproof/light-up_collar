# Ровный свет и индикаторы.
import pytest

import config
import modes

N = config.NUM_LEDS


def test_fill_solid():
    f = modes.new_frame()
    modes.fill(f, (1, 2, 3))
    assert f == bytes((1, 2, 3)) * N


@pytest.mark.parametrize("pct, lit", [(0, 0), (1, 1), (50, (N * 50 + 50) // 100), (100, N)])
def test_charge_bar_fraction(pct, lit):
    f = modes.new_frame()
    modes.charge_bar(f, pct)
    on = [i for i in range(N) if f[3 * i] or f[3 * i + 1] or f[3 * i + 2]]
    assert on == list(range(lit))


@pytest.mark.parametrize(
    "pct, color",
    [
        (100, config.PALETTE[3]),
        (80, config.PALETTE[3]),
        (75, config.PALETTE[3]),
        (74, config.PALETTE[2]),
        (50, config.PALETTE[2]),
        (49, config.PALETTE[1]),
        (25, config.PALETTE[1]),
        (24, config.PALETTE[0]),
        (3, config.PALETTE[0]),
    ],
)
def test_charge_bar_color_by_quarter(pct, color):
    f = modes.new_frame()
    modes.charge_bar(f, pct)
    lit = [tuple(f[3 * i : 3 * i + 3]) for i in range(N) if any(f[3 * i : 3 * i + 3])]
    assert lit and all(c == tuple(color) for c in lit)  # вся горящая часть — одного цвета


def test_charge_bar_80_percent_example():
    # пример из ТЗ: 80 % — горят 80 % ленты зелёным, остальные 20 % погашены совсем
    f = modes.new_frame()
    modes.charge_bar(f, 80)
    lit = sum(1 for i in range(N) if any(f[3 * i : 3 * i + 3]))
    assert lit == (N * 80 + 50) // 100
    assert all(f[3 * i : 3 * i + 3] == bytes(3) for i in range(lit, N))


def test_low_flash_is_one_soft_red_pulse():
    levels = []
    for t in range(-50, modes.LOW_FLASH_MS + 50, 10):
        f = modes.new_frame()
        modes.low_flash(f, t)
        assert f[1] == 0 and f[2] == 0
        levels.append(f[0])
    assert levels[0] == 0 and levels[-1] == 0 and max(levels) >= 250


def test_empty_blink_single_dim_pixel():
    seen = 0
    for t in range(0, modes.EMPTY_PERIOD_MS, 10):
        f = modes.new_frame()
        modes.empty_blink(f, t)
        assert all(v == 0 for v in f[1:])
        assert f[0] <= modes.EMPTY_LEVEL
        seen = max(seen, f[0])
    assert seen > 0


def test_charge_bar_fill_animation():
    def lit(pct, t):
        f = modes.new_frame()
        modes.charge_bar(f, pct, t)
        return sum(1 for i in range(N) if any(f[3 * i : 3 * i + 3]))

    assert lit(100, 0) == 1
    assert lit(100, config.CHARGE_FILL_MS // 2) == N // 2 + 1
    assert lit(100, config.CHARGE_FILL_MS) == N
    assert lit(40, config.CHARGE_FILL_MS) == (N * 40 + 50) // 100  # останавливается на уровне заряда
    assert lit(40, 10 * config.CHARGE_FILL_MS) == (N * 40 + 50) // 100
    assert lit(0, config.CHARGE_FILL_MS) == 0


def test_wipe():
    before = bytes((1, 2, 3)) * N
    for t, k in ((0, 1), (config.COLOR_WIPE_MS // 2, N // 2 + 1), (config.COLOR_WIPE_MS, N), (10_000, N)):
        f = modes.new_frame()
        modes.wipe(f, before, (9, 8, 7), t)
        assert f == bytes((9, 8, 7)) * min(k, N) + bytes((1, 2, 3)) * (N - min(k, N))
