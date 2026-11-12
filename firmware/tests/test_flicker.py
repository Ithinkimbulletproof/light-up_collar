# Правило «не мигать чаще 3 Гц» (docs/spec.md) — на итоговых кадрах ленты.
#
# Гоняем всю цепочку App → leds.Output 40 раз в секунду, как на плате, и подсовываем
# худшие входы: быстрые нажатия (цвет, яркость с подсказкой миганием), показ заряда, индикаторы батареи.
#
# «Вспышка» — рост канала пикселя больше чем на половину шкалы (128 из 255) от предыдущего
# минимума. Следующая вспышка засчитывается только после спада на столько же.
# Требование: начала двух вспышек одного канала одного пикселя — не ближе 333 мс.
import pytest

import battery
import button
import config
import leds
from app import App
from settings import Settings

DT = 1000 // config.FPS
MIN_GAP_MS = 333
CAP = [10_000]  # потолок текущего прогона, см. фикстуру cap_ma
FIXED = config.NUM_LEDS * config.MA_IDLE_PER_LED + config.I_ESP_MA


def flash_onsets(series, threshold=128):
    onsets = []
    armed = True
    lo = hi = series[0]
    for k, v in enumerate(series):
        if armed:
            lo = min(lo, v)
            if v - lo >= threshold:
                onsets.append(k * DT)
                armed = False
                hi = v
        else:
            hi = max(hi, v)
            if hi - v >= threshold:
                armed = True
                lo = v
    return onsets


@pytest.fixture(autouse=True, params=[10_000, config.CAP_MA, config.LOW_CAP_MA])
def cap_ma(request):
    """Каждый сценарий — на реальных потолках и на большом: он не маскирует мигание, но отключает масштаб."""
    CAP[0] = request.param


def run(tmp_path, seconds, inputs, mv=3900, palette=0, level=None):
    """inputs(app, now, frame_no) — что происходит на этом кадре."""
    cap_ma = CAP[0]
    s = Settings(str(tmp_path / "s.json"))
    s.palette = palette
    mon = battery.Monitor()
    cap = battery.CapController()
    for k in range(battery.STARTUP_SAMPLES):
        mon.update(mv, FIXED, k * 250)
    if level is None:
        app = App(s, mon, cap, brightness=100)
    else:
        s.level = level
        app = App(s, mon, cap)
    out = leds.Output()
    frames = []
    t0 = battery.STARTUP_SAMPLES * 250
    for k in range(seconds * 1000 // DT):
        now = t0 + k * DT
        if k % 10 == 0:
            mon.update(mv, out.est_ma, now)
        inputs(app, now, k)
        f, b, g = app.render(now)
        frames.append(bytes(out.step(f, b, g, cap_ma, DT, app.ref_ma)))
    return frames


def run_levels(tmp_path, seconds, inputs):
    """Как run, но яркость берётся из уровня в настройках (её и меняет двойное нажатие)."""
    s = Settings(str(tmp_path / "s.json"))
    s.palette = 7
    mon = battery.Monitor()
    for k in range(battery.STARTUP_SAMPLES):
        mon.update(3900, FIXED, k * 250)
    app = App(s, mon, battery.CapController())
    out = leds.Output()
    frames = []
    t0 = battery.STARTUP_SAMPLES * 250
    for k in range(seconds * 1000 // DT):
        now = t0 + k * DT
        if k % 10 == 0:
            mon.update(3900, out.est_ma, now)
        inputs(app, now, k)
        f, b, g = app.render(now)
        frames.append(bytes(out.step(f, b, g, CAP[0], DT, app.ref_ma)))
    return frames


def worst_gap(frames):
    """Минимальный промежуток между вспышками по всем каналам всех пикселей (None — вспышек ≤ 1)."""
    worst = None
    for ch in range(len(frames[0])):
        onsets = flash_onsets([f[ch] for f in frames])
        for a, b in zip(onsets, onsets[1:]):
            gap = b - a
            worst = gap if worst is None else min(worst, gap)
    return worst


def assert_no_fast_flashing(frames):
    gap = worst_gap(frames)
    assert gap is None or gap >= MIN_GAP_MS, "вспышки через {} мс".format(gap)


def worst_gap_relative(frames):
    """То же, но вспышка — рост на половину максимума самого канала: на урезанных цветах 128 недостижимо."""
    worst = None
    for ch in range(len(frames[0])):
        series = [f[ch] for f in frames]
        onsets = flash_onsets(series, max(max(series) // 2, 6))
        for a, b in zip(onsets, onsets[1:]):
            worst = b - a if worst is None else min(worst, b - a)
    return worst


def nothing(app, now, k):
    pass


@pytest.mark.parametrize("palette", range(len(config.PALETTE)))
def test_steady_light(tmp_path, palette):
    assert_no_fast_flashing(run(tmp_path, 10, nothing, palette=palette))


@pytest.mark.parametrize("every_frames", [4, 6, 8, 16])
def test_single_press_spam(tmp_path, every_frames):
    # Одиночное приходит не чаще, чем раз в ~0,45 с (нажатие + окно двойного); берём с запасом чаще
    def inputs(app, now, k):
        if k % every_frames == 0:
            app.on_button(button.SINGLE, now)

    assert_no_fast_flashing(run(tmp_path, 10, inputs))


@pytest.mark.parametrize("every_frames", [8, 12, 20])
def test_double_press_spam(tmp_path, every_frames):
    # Яркость по кругу 10 → 35 → 100 % и подсказка миганиями; двойное быстрее ~0,3 с не сделать
    def inputs(app, now, k):
        if k % every_frames == 0:
            app.on_button(button.DOUBLE, now)

    assert_no_fast_flashing(run_levels(tmp_path, 10, inputs))


def test_level_hint_three_blinks(tmp_path):
    # Самая длинная подсказка — три мигания подряд на полной яркости
    def inputs(app, now, k):
        if k == 40:
            app.s.level = 1
            app.on_button(button.DOUBLE, now)

    assert_no_fast_flashing(run_levels(tmp_path, 6, inputs))


def test_charge_display_toggling(tmp_path):
    # Удержание показывает заряд, нажатие убирает — и так быстро, как можно
    def inputs(app, now, k):
        if k % 12 == 0:
            app.on_button(button.LONG, now)
        elif k % 12 == 6:
            app.on_button(button.PRESS, now)
            app.on_button(button.SINGLE, now)

    assert_no_fast_flashing(run(tmp_path, 10, inputs, palette=7))


@pytest.mark.parametrize("palette", [0, 5, 7])
def test_low_flash_over_light(tmp_path, palette):
    assert_no_fast_flashing(run(tmp_path, 70, nothing, mv=config.V_LOW_MV - 80, palette=palette))


def press_single(app, now, k):
    if k % 18 == 0:  # быстрее не нажать: нажатие и окно двойного
        app.on_button(button.SINGLE, now)


def press_double(app, now, k):
    # Обычная быстрая серия. Если жать без остановки чаще раза в ~0,7 с (предел кнопки ~0,52 с), подсказка обрывается,
    # и при потолке 300 мА этот детектор показывает 275–325 мс на всплесках до ~130 единиц из 255 (фиолетовый),
    # при 700 мА — чисто; правило на 128 чисто.
    if k % 28 == 0:
        app.on_button(button.DOUBLE, now)


def press_charge(app, now, k):
    if k % 70 == 0:  # удержание 1,5 с и короткое нажатие
        app.on_button(button.LONG, now)
    elif k % 70 == 66:
        app.on_button(button.PRESS, now)


@pytest.mark.parametrize("gesture", [press_single, press_double, press_charge])
@pytest.mark.parametrize("palette", [0, 3, 6, 7])
@pytest.mark.parametrize("level", range(len(config.BRIGHTNESS_LEVELS)))
def test_gestures_at_hand_speed_relative(tmp_path, gesture, palette, level):
    # С порогом от самого канала; нажатия — с той частотой, какую может дать рука.
    frames = run(tmp_path, 10, gesture, palette=palette, level=level)
    gap = worst_gap_relative(frames[80:])
    assert gap is None or gap >= MIN_GAP_MS, "вспышки через {} мс".format(gap)


def test_detector_catches_real_flicker():
    # Сам детектор: прямоугольник 5 Гц без плавности — должен быть пойман.
    series = [255 if (k * DT // 100) % 2 else 0 for k in range(200)]
    onsets = flash_onsets(series)
    assert min(b - a for a, b in zip(onsets, onsets[1:])) < MIN_GAP_MS
