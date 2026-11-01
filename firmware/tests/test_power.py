# Слежение за батареей и адаптивный потолок — на модели батареи с внутренним сопротивлением.
import math

import config
from battery import SOURCE_BATTERY, SOURCE_NONE, SOURCE_USB, STARTUP_SAMPLES, CapController, Monitor

FIXED = config.NUM_LEDS * config.MA_IDLE_PER_LED + config.I_ESP_MA
FULL_WHITE_MA = config.NUM_LEDS * 3 * config.MA_PER_CHANNEL
SAMPLE_MS = 250


def feed(mon, mv, ms, t0=0, current=FIXED):
    t = t0
    while t < t0 + ms:
        mon.update(mv, current, t)
        t += SAMPLE_MS
    return t


def simulate(ocv, r_mohm, demand, seconds, mon=None, cap=None):
    """ocv(t) — напряжение без нагрузки, demand(t) — сколько хотят светодиоды, мА.

    Ток ограничен потолком; напряжение под нагрузкой = ocv − I × R. Возвращает (mon, cap, история потолка).
    """
    mon = mon or Monitor()
    cap = cap or CapController()
    history = []
    for k in range(seconds * 1000 // SAMPLE_MS):
        t = k * SAMPLE_MS
        leds = demand(t) if mon.ready else 0
        i = FIXED + min(leds, max(0, cap.cap - FIXED))
        mon.update(ocv(t) - i * r_mohm // 1000, i, t)
        cap.update(mon, t)
        history.append(cap.cap)
    return mon, cap, history


def reversals(history):
    """Сколько раз потолок менял направление (снижался ↔ рос)."""
    dirs = [1 if b > a else -1 for a, b in zip(history, history[1:]) if b != a]
    return sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)


def breathing_white(t):
    """Нагрузка, которая плавно гуляет 15–100 % каждые 4 с (показ заряда, вспышки, смена цвета)."""
    level = 0.15 + 0.85 * (0.5 - 0.5 * math.cos(2 * math.pi * (t % 4000) / 4000))
    return int(FULL_WHITE_MA * level)


# --- Monitor ---


def test_startup_on_dead_battery_never_lights():
    mon = Monitor()
    feed(mon, config.V_EMPTY_MV - 80, SAMPLE_MS * STARTUP_SAMPLES)
    assert mon.ready and mon.empty


def test_startup_on_good_battery():
    mon = Monitor()
    feed(mon, 3900, SAMPLE_MS * STARTUP_SAMPLES)
    assert mon.ready and not mon.empty and not mon.low
    assert mon.source == SOURCE_BATTERY


def test_low_needs_confirmation_and_has_hysteresis():
    # Замеры под током покоя: V_rest = V + 113 мА × 0,25 Ом ≈ V + 28 мВ, поэтому берём с запасом.
    mon = Monitor()
    t = feed(mon, 3900, 3000)
    t = feed(mon, config.V_LOW_MV - 80, 1000, t)
    assert not mon.low  # ещё не подтверждено
    t = feed(mon, config.V_LOW_MV - 80, 10_000, t)
    assert mon.low
    t = feed(mon, (config.V_LOW_MV + config.V_LOW_CLEAR_MV) // 2, 60_000, t)
    assert mon.low  # между порогами — держится
    t = feed(mon, config.V_LOW_CLEAR_MV + 30, config.LOW_CLEAR_HOLD_MS // 2, t)
    assert mon.low  # мало времени
    feed(mon, config.V_LOW_CLEAR_MV + 30, config.LOW_CLEAR_HOLD_MS + 3000, t)
    assert not mon.low


def test_empty_latches_even_if_voltage_recovers():
    mon = Monitor()
    t = feed(mon, 3700, 3000)
    t = feed(mon, config.V_EMPTY_MV - 80, 3000, t)
    assert not mon.empty  # сглаживание и подтверждение: не от первого же замера
    t = feed(mon, config.V_EMPTY_MV - 80, 10_000, t)
    assert mon.empty
    feed(mon, 3700, 60_000, t)  # лента погасла — напряжение подскочило
    assert mon.empty
    assert mon.percent == 0


def test_single_glitch_is_not_empty():
    mon = Monitor()
    t = feed(mon, 3800, 3000)
    mon.update(2600, FIXED, t)
    feed(mon, 3800, 5000, t + SAMPLE_MS)
    assert not mon.empty and not mon.low


def test_compensation_uses_current():
    # 3,45 В под током 700 мА — это ~3,63 В без нагрузки: не «низкий заряд»
    mon = Monitor()
    feed(mon, 3450, 10_000, current=700)
    assert not mon.low
    assert abs(mon.v_rest - (3450 + 700 * config.R_INT_MOHM // 1000)) <= 2


def test_usb_and_no_divider():
    mon = Monitor()
    feed(mon, 4750, 3000)
    assert mon.source == SOURCE_USB
    assert mon.percent == 100 and not mon.low and not mon.empty
    cap = CapController()
    assert cap.update(mon, 3000) == config.USB_CAP_MA and not cap.limited
    mon2 = Monitor()
    feed(mon2, 0, 3000)
    assert mon2.source == SOURCE_NONE


def test_flash_write_allowed():
    mon = Monitor()
    feed(mon, 3900, 3000)
    assert mon.flash_write_allowed()
    mon = Monitor()
    feed(mon, config.V_FLASH_MIN_MV - 30, 3000, current=600)  # под нагрузкой ниже порога
    assert not mon.flash_write_allowed()


# --- CapController ---


def test_healthy_battery_keeps_full_cap():
    mon, cap, hist = simulate(lambda t: 3900, 250, lambda t: FULL_WHITE_MA, 120)
    assert cap.cap == config.CAP_MA and not cap.limited


def test_sag_reduces_cap_and_settles():
    # 3,62 В без нагрузки: на 700 мА проседает ниже 3,45 В
    mon, cap, hist = simulate(lambda t: 3620, 250, lambda t: FULL_WHITE_MA, 300)
    assert config.CAP_MIN_MA <= cap.cap < config.CAP_MA
    assert cap.limited
    assert mon.v_loaded >= config.V_SAG_MV - 20
    assert reversals(hist) == 0  # без раскачки


def test_breathing_does_not_make_cap_hunt():
    mon, cap, hist = simulate(lambda t: 3620, 250, breathing_white, 600)
    assert reversals(hist) <= 1


def test_wrong_r_int_still_no_hunting():
    # Реальное сопротивление больше заложенного — компенсация неточна, но потолок не качается.
    for r_true in (150, 350):
        mon, cap, hist = simulate(lambda t: 3620, r_true, breathing_white, 600)
        assert reversals(hist) <= 2, r_true


def test_cap_recovers_when_battery_allows():
    # Просадка была из-за холода/нагрузки, потом напряжение без нагрузки выросло
    ocv = lambda t: 3600 if t < 120_000 else 3950  # noqa: E731
    mon, cap, hist = simulate(ocv, 250, lambda t: FULL_WHITE_MA, 900)
    assert min(hist) < config.CAP_MA
    assert cap.cap == config.CAP_MA and not cap.limited


def test_cap_never_below_minimum():
    mon, cap, hist = simulate(lambda t: 3500, 600, lambda t: FULL_WHITE_MA, 300)
    assert min(hist) >= config.CAP_MIN_MA


def test_low_limits_cap():
    mon, cap, hist = simulate(lambda t: config.V_LOW_MV - 40, 100, lambda t: 50, 60)
    assert mon.low
    assert cap.cap <= config.LOW_CAP_MA and cap.limited


def test_switch_on_while_board_on_usb_no_false_empty():
    # Плата уже работает от USB-C, выключатель ошейника выключен (на узле ~0 В), потом его включают.
    for v in (3700, 3900, 4100):
        mon = Monitor()
        t = feed(mon, 0, 10_000)
        assert mon.source == SOURCE_NONE
        t = feed(mon, v, 60_000, t)
        assert mon.source == SOURCE_BATTERY
        assert not mon.empty and not mon.low, v
        assert abs(mon.v_rest - (v + FIXED * config.R_INT_MOHM // 1000)) <= 3


def test_switch_on_with_dead_battery_still_caught():
    # После смены источника заново проходит проверка до первого кадра
    mon = Monitor()
    t = feed(mon, 0, 5000)
    feed(mon, config.V_EMPTY_MV - 100, SAMPLE_MS * STARTUP_SAMPLES, t)
    assert mon.ready and mon.empty


def test_usb_hysteresis():
    from battery import source

    assert source(config.V_NO_BATTERY_HIGH_MV + 50) == SOURCE_USB
    assert source(config.V_NO_BATTERY_HIGH_MV - 50) == SOURCE_BATTERY
    # USB под нагрузкой просел ниже порога входа — всё ещё USB
    assert source(config.V_NO_BATTERY_HIGH_MV - 50, SOURCE_USB) == SOURCE_USB
    assert source(config.V_USB_EXIT_MV - 50, SOURCE_USB) == SOURCE_BATTERY
    assert config.V_USB_EXIT_MV < config.V_NO_BATTERY_HIGH_MV


def test_sagging_usb_keeps_usb_cap():
    mon = Monitor()
    cap = CapController()
    t = feed(mon, 4900, 3000)
    t = feed(mon, config.V_NO_BATTERY_HIGH_MV - 60, 10_000, t, current=300)
    assert mon.source == SOURCE_USB
    assert cap.update(mon, t) == config.USB_CAP_MA


def test_no_flash_write_when_empty_even_if_voltage_bounces():
    mon = Monitor()
    t = feed(mon, 3800, 3000)
    t = feed(mon, config.V_EMPTY_MV - 100, 10_000, t)
    assert mon.empty
    feed(mon, 3650, 10_000, t)  # лента погасла — напряжение подскочило выше порога записи
    assert mon.v_loaded >= config.V_FLASH_MIN_MV
    assert not mon.flash_write_allowed()
