# Поведение ошейника: ровный свет, кнопка (одиночное, двойное, удержание), индикаторы.
import pytest

import battery
import button
import config
import modes
from app import App
from settings import Settings

FIXED = config.NUM_LEDS * config.MA_IDLE_PER_LED + config.I_ESP_MA
N = config.NUM_LEDS
LEVELS = len(config.BRIGHTNESS_LEVELS)


def make(tmp_path, mv=3900):
    s = Settings(str(tmp_path / "s.json"))
    mon = battery.Monitor()
    cap = battery.CapController()
    for k in range(battery.STARTUP_SAMPLES):
        mon.update(mv, FIXED, k * 250)
        cap.update(mon, k * 250)
    return App(s, mon, cap)


def pixels(f):
    return [tuple(f[3 * i : 3 * i + 3]) for i in range(N)]


def lit_count(f):
    return sum(1 for i in range(N) if any(f[3 * i : 3 * i + 3]))


def charge_frame(a):
    """Шкала заряда, когда она уже добежала до уровня заряда."""
    f = modes.new_frame()
    modes.charge_bar(f, a.mon.percent)
    return f


def single(a, t):
    a.on_button(button.PRESS, t)
    a.on_button(button.SINGLE, t + 100 + config.DOUBLE_PRESS_MS)


def double(a, t):
    a.on_button(button.PRESS, t)
    a.on_button(button.DOUBLE, t + 300)


def hold(a, t):
    a.on_button(button.PRESS, t)
    a.on_button(button.LONG, t + config.LONG_PRESS_MS)


# --- ровный свет и цвет ---


def test_solid_light_in_selected_color_full_brightness_by_default(tmp_path):
    a = make(tmp_path)
    f, b, _ = a.render(1000)
    assert f == bytes(config.PALETTE[0]) * N
    assert b == config.BRIGHTNESS_LEVELS[config.BRIGHTNESS_DEFAULT_INDEX] == 100
    f, _, _ = a.render(60_000)
    assert f == bytes(config.PALETTE[0]) * N


def test_single_press_next_color(tmp_path):
    a = make(tmp_path)
    single(a, 1000)
    assert a.s.palette == 1 and a.s.level == config.BRIGHTNESS_DEFAULT_INDEX


def test_color_wraps(tmp_path):
    a = make(tmp_path)
    for k in range(len(config.PALETTE)):
        single(a, k * 1000)
    assert a.s.palette == 0


def test_color_change_runs_pixel_by_pixel(tmp_path):
    a = make(tmp_path)
    old, new = tuple(config.PALETTE[0]), tuple(config.PALETTE[1])
    a.render(1000)
    a.on_button(button.SINGLE, 1000)
    seen = []
    for t in range(0, config.COLOR_WIPE_MS + 50, 25):
        px = pixels(a.render(1000 + t)[0])
        k = sum(1 for p in px if p == new)
        assert px == [new] * k + [old] * (N - k)  # новый цвет — сплошной кусок от начала ленты
        seen.append(k)
    assert seen[0] == 1
    assert seen == sorted(seen) and seen[-1] == N
    assert abs(seen[(config.COLOR_WIPE_MS // 2) // 25] - N // 2) <= 2


def test_second_color_mid_wipe_runs_over_current_picture(tmp_path):
    a = make(tmp_path)
    c2 = tuple(config.PALETTE[2])
    a.on_button(button.SINGLE, 1000)
    t2 = 1000 + config.COLOR_WIPE_MS // 2
    before = pixels(a.render(t2)[0])
    a.on_button(button.SINGLE, t2)
    px = pixels(a.render(t2)[0])
    assert px[0] == c2 and px[1:] == before[1:]  # без скачков
    assert pixels(a.render(t2 + config.COLOR_WIPE_MS)[0]) == [c2] * N


# --- яркость: двойное нажатие ---


def test_double_press_cycles_brightness_and_keeps_color(tmp_path):
    a = make(tmp_path)
    seen = []
    for k in range(LEVELS + 1):
        double(a, k * 5000)
        _, b, _ = a.render(k * 5000 + 4000)  # после подсказки
        seen.append(b)
    assert seen == [config.BRIGHTNESS_LEVELS[(config.BRIGHTNESS_DEFAULT_INDEX + 1 + k) % LEVELS] for k in range(LEVELS + 1)]
    assert seen[0] == config.BRIGHTNESS_LEVELS[0]  # с максимума — на минимум
    assert a.s.palette == 0


def strip_brightness_after_double(tmp_path, level_before):
    """Яркость пикселя 0 на ленте (после плавности и потолка тока) каждые 25 мс после двойного нажатия."""
    import leds

    a = make(tmp_path)
    a.s.level = level_before
    out = leds.Output()
    t = 0
    for _ in range(120):  # 3 с: старт и установившийся свет
        f, b, g = a.render(t)
        out.step(f, b, g, 10_000, 25)
        t += 25
    double(a, t - 300)
    seen = []
    for _ in range((config.TRANSITION_MS + (LEVELS + 1) * config.LEVEL_HINT_PERIOD_MS) // 25 + 20):
        f, b, g = a.render(t)
        seen.append(max(out.step(f, b, g, 10_000, 25)[:3]))
        t += 25
    return seen


def count_blinks(series):
    """Сколько раз яркость заметно проседала (больше чем на треть) и возвращалась.

    Считаем после того, как лента впервые дошла до нового уровня: сам переход — не мигание.
    """
    top = max(series[-10:])  # установившийся новый уровень
    settled = next(i for i, v in enumerate(series) if v >= top * 9 // 10)
    blinks = 0
    down = False
    for v in series[settled:]:
        if not down and v < top * 2 // 3:
            down = True
            blinks += 1
        elif down and v >= top * 9 // 10:
            down = False
    return blinks


@pytest.mark.parametrize("level_after", range(LEVELS))
def test_level_hint_visible_on_strip(tmp_path, level_after):
    # 1, 2 или 3 мигания — номер уровня — должны быть видны на ленте, а не только в логике:
    # в том числе при переходе 100 % → 10 %, когда лента сама плавно темнеет.
    seen = strip_brightness_after_double(tmp_path, (level_after - 1) % LEVELS)
    assert count_blinks(seen) == level_after + 1
    top = max(seen[-10:])
    settled = next(i for i, v in enumerate(seen) if v >= top * 9 // 10)
    assert min(seen[settled:]) <= top // 2  # мигание глубокое — хорошо заметно


def test_level_hint_waits_for_brightness_to_settle(tmp_path):
    a = make(tmp_path)
    a.render(0)
    a.render(config.STARTUP_RAMP_MS)  # плавный старт закончился
    double(a, 10_000)
    _, _, g = a.render(10_300 + config.TRANSITION_MS // 2)
    assert g == 255  # пока яркость переходит на новый уровень — не мигаем
    _, _, g = a.render(10_300 + config.TRANSITION_MS + config.LEVEL_HINT_PULSE_MS // 2)
    assert g < 50  # потом мигаем


# --- заряд: удержание ---


def test_hold_shows_charge_for_5_s(tmp_path):
    a = make(tmp_path)
    hold(a, 1000 - config.LONG_PRESS_MS)
    t0 = 1000
    f, b, _ = a.render(t0 + config.CHARGE_FILL_MS)
    assert f == charge_frame(a) and b >= 30
    f, _, _ = a.render(t0 + config.CHARGE_DISPLAY_MS - 10)
    assert f == charge_frame(a)
    f, _, _ = a.render(t0 + config.CHARGE_DISPLAY_MS + 10)
    assert f == bytes(config.PALETTE[0]) * N
    assert a.s.palette == 0


def test_charge_bar_runs_pixel_by_pixel(tmp_path):
    a = make(tmp_path, mv=4300 - 10)
    full = lit_count(charge_frame(a))
    hold(a, 10_000 - config.LONG_PRESS_MS)
    counts = [lit_count(a.render(10_000 + t)[0]) for t in range(0, config.CHARGE_FILL_MS + 100, 25)]
    assert counts[0] == 1 and counts == sorted(counts) and counts[-1] == full


@pytest.mark.parametrize("finish", [button.SINGLE, button.DOUBLE, button.LONG])
def test_press_during_charge_only_dismisses(tmp_path, finish):
    a = make(tmp_path)
    hold(a, 0)
    a.on_button(button.PRESS, 3000)
    a.on_button(finish, 3400)  # конец той же серии — ничего не меняет и показ заново не начинает
    assert (a.s.palette, a.s.level) == (0, config.BRIGHTNESS_DEFAULT_INDEX)
    f, _, _ = a.render(3500)
    assert f == bytes(config.PALETTE[0]) * N
    single(a, 6000)  # следующая серия работает как обычно
    assert a.s.palette == 1


def test_stale_charge_display_does_not_eat_press(tmp_path):
    a = make(tmp_path)
    hold(a, 0)
    for k in range(10):  # питание ленты пропало (выключатель выкл., плата на USB)
        a.mon.update(0, FIXED, 2000 + k * 250)
        a.render(2000 + k * 250)
    single(a, config.LONG_PRESS_MS + config.CHARGE_DISPLAY_MS + 5000)
    assert a.s.palette == 1  # нажатие не съедено устаревшим показом


# --- состояния батареи и питания ---


def test_swallow_does_not_leak_through_empty(tmp_path):
    # Нажатие во время показа заряда, а до конца серии батарея села; после смены источника
    # (плату подключили к USB) первое настоящее нажатие не должно пропасть.
    a = make(tmp_path)
    hold(a, 0)
    a.on_button(button.PRESS, 2000)
    a.mon.empty = True
    a.on_button(button.SINGLE, 2500)
    a.mon.empty = False
    single(a, 5000)
    assert a.s.palette == 1


def test_button_ignored_when_empty(tmp_path):
    a = make(tmp_path, mv=config.V_EMPTY_MV - 100)
    assert a.mon.empty
    single(a, 0)
    double(a, 1000)
    hold(a, 2000)
    assert (a.s.palette, a.s.level) == (0, config.BRIGHTNESS_DEFAULT_INDEX)
    f, b, _ = a.render(5000)
    assert all(v == 0 for v in f[1:]) and b == 100


def test_dark_until_ready(tmp_path):
    s = Settings(str(tmp_path / "s.json"))
    a = App(s, battery.Monitor(), battery.CapController())
    f, b, g = a.render(0)
    assert f == bytes(3 * N) and g == 0


def test_startup_ramp(tmp_path):
    a = make(tmp_path)
    _, _, g0 = a.render(10_000)
    _, _, g1 = a.render(10_000 + config.STARTUP_RAMP_MS // 2)
    _, _, g2 = a.render(10_000 + config.STARTUP_RAMP_MS)
    assert g0 == 0 and 100 < g1 < 155 and g2 == 255


def test_no_strip_power_means_dark(tmp_path):
    a = make(tmp_path, mv=0)
    assert not a.strip_powered
    _, _, g = a.render(1000)
    assert g == 0


def test_soft_start_again_after_power_returns(tmp_path):
    a = make(tmp_path)
    a.render(1000)
    _, _, g = a.render(1000 + config.STARTUP_RAMP_MS + 10)
    assert g == 255
    for k in range(10):
        a.mon.update(0, FIXED, 3000 + k * 250)
    a.render(6000)
    for k in range(battery.STARTUP_SAMPLES):
        a.mon.update(3900, FIXED, 7000 + k * 250)
    _, _, g = a.render(10_000)
    assert g == 0


@pytest.mark.parametrize("level", range(LEVELS))
def test_charge_visible_even_at_dim_level(tmp_path, level):
    a = make(tmp_path)
    a.s.level = level
    hold(a, 1000)
    _, b, _ = a.render(1000 + config.LONG_PRESS_MS + 100)
    assert b >= 30


def test_low_flash_every_30_s(tmp_path):
    a = make(tmp_path, mv=config.V_LOW_MV - 120)
    t = 2000
    while not a.mon.low:
        a.mon.update(config.V_LOW_MV - 120, FIXED, t)
        t += 250
    starts = set()
    for k in range(0, 65_000, 25):
        a.render(t + k)
        if a._low_t0 is not None:
            starts.add(a._low_t0)
    assert len(starts) == 3  # сразу, через 30 и через 60 с
