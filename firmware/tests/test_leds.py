# Яркость, плавность и ограничение тока.
import pytest

import config
import leds

N = config.NUM_LEDS
DT = 1000 // config.FPS
FIXED = N * config.MA_IDLE_PER_LED + config.I_ESP_MA


def run(out, target, frames, brightness=100, gain=255, cap=config.CAP_MA):
    for _ in range(frames):
        r = out.step(target, brightness, gain, cap, DT)
    return r


def test_estimate_formula():
    white = bytes((255,)) * (3 * N)
    assert leds.total_ma(white) == N * 3 * config.MA_PER_CHANNEL + FIXED
    assert leds.total_ma(bytes(3 * N)) == FIXED


def test_white_full_is_capped():
    out = leds.Output()
    white = bytes((255,)) * (3 * N)
    run(out, white, 200)
    assert out.est_ma <= config.CAP_MA
    assert out.est_ma >= config.CAP_MA - 20  # бюджет используется почти полностью (шаг масштаба 1/256)


def test_cap_never_exceeded_from_first_frame():
    out = leds.Output()
    white = bytes((255,)) * (3 * N)
    for _ in range(100):
        out.step(white, 100, 255, config.CAP_MA, DT)
        assert out.est_ma <= config.CAP_MA


def test_low_cap_drops_immediately():
    out = leds.Output()
    white = bytes((255,)) * (3 * N)
    run(out, white, 100)
    out.step(white, 100, 255, config.LOW_CAP_MA, DT)
    assert out.est_ma <= config.LOW_CAP_MA


def test_cap_rise_is_gradual():
    # Потолок вырос скачком (например, USB → батарея) — лента светлеет плавно, а не вспышкой.
    out = leds.Output()
    white = bytes((255,)) * (3 * N)
    run(out, white, 100, cap=config.CAP_MIN_MA)
    k_before = out.k
    out.step(white, 100, 255, config.CAP_MA, DT)
    assert out.k - k_before <= 256 * DT // config.TRANSITION_MS + 1
    run(out, white, config.TRANSITION_MS // DT + 2)
    assert out.est_ma >= config.CAP_MA - 20


def test_brightness_without_cap_is_plain_share():
    out = leds.Output()
    red = bytes((255, 0, 0)) * N
    r = run(out, red, 100, brightness=50, cap=10_000)
    assert r[0] == 127 and r[1] == 0


@pytest.mark.parametrize("rgb", config.PALETTE)
def test_brightness_is_share_of_capped_max(rgb):
    # Уровень считается от того, что пропускает потолок: на любом цвете 35 % — это 35 % от 100 %.
    frame = bytes(rgb) * N
    top = max(run(leds.Output(), frame, 100, brightness=100))
    for level in config.BRIGHTNESS_LEVELS:
        r = run(leds.Output(), frame, 100, brightness=level)
        assert abs(max(r) - top * level // 100) <= 2


def test_levels_differ_on_white():
    white = bytes((255,)) * (3 * N)
    seen = [max(run(leds.Output(), white, 100, brightness=b)) for b in config.BRIGHTNESS_LEVELS]
    assert seen == sorted(seen) and len(set(seen)) == len(seen)


def test_cap_holds_while_color_changes():
    # Красный на 100 % почти влезает в потолок, белый — нет: пока плавность переводит один в другой,
    # сумма каналов на полпути больше обоих — второй, страховочный потолок должен это поймать.
    out = leds.Output()
    run(out, bytes((255, 0, 0)) * N, 100)
    for frame in (bytes((255,)) * (3 * N), bytes((0, 0, 255)) * N, bytes((255, 170, 0)) * N):
        for _ in range(30):
            out.step(frame, 100, 255, config.CAP_MA, DT)
            assert out.est_ma <= config.CAP_MA


def test_slew_limits_change_speed():
    out = leds.Output()
    white = bytes((255,)) * (3 * N)
    run(out, bytes((10,)) * (3 * N), 50, cap=10_000)
    r = out.step(white, 100, 255, 10_000, DT)
    assert r[0] <= 10 + 255 * DT // config.TRANSITION_MS + 1
    frames_to_full = config.TRANSITION_MS // DT + 2
    r = run(out, white, frames_to_full, cap=10_000)
    assert r[0] == 255


def steady(frame, brightness, cap):
    out = leds.Output()
    r = bytes(run(out, frame, 100, brightness=brightness, cap=cap))
    return r, out.est_ma


@pytest.mark.parametrize("rgb", config.PALETTE)
def test_low_cap_keeps_levels_that_fit(rgb):
    # Масштаб считается от номинального потолка: при «низком заряде» и на USB (300 мА) уровни, которые
    # и так влезают, не тускнеют и не теряют оттенок — срезается только то, что не влезает.
    frame = bytes(rgb) * N
    for level in config.BRIGHTNESS_LEVELS:
        nominal, est = steady(frame, level, config.CAP_MA)
        if est <= config.LOW_CAP_MA:
            assert steady(frame, level, config.LOW_CAP_MA)[0] == nominal


def test_level_change_is_gradual_on_capped_colour():
    # Белый урезан масштабом до ~82 из 255; смена уровня всё равно должна идти так же плавно,
    # как на цвете без урезания, а не скачком за кадр-два.
    out = leds.Output()
    white = bytes((255,)) * (3 * N)
    top = max(run(out, white, 100))
    allowed = top * (255 * DT // config.TRANSITION_MS) // 255 + 2
    for level in (10, 35, 100, 10):
        prev = max(out.out)
        for _ in range(30):
            cur = max(out.step(white, level, 255, config.CAP_MA, DT))
            assert abs(cur - prev) <= allowed
            prev = cur


def test_cap_recovery_after_drop_is_gradual():
    # Потолок упал на кадр (CAP_MIN) и вернулся: лента светлеет обратно плавно, а не за один кадр.
    out = leds.Output()
    white = bytes((255,)) * (3 * N)
    run(out, white, 100)
    out.step(white, 100, 255, config.CAP_MIN_MA, DT)
    prev = max(out.out)
    for _ in range(config.TRANSITION_MS // DT + 2):
        cur = max(out.step(white, 100, 255, config.CAP_MA, DT))
        assert cur - prev <= 255 * DT // config.TRANSITION_MS + 1
        prev = cur
    assert out.est_ma >= config.CAP_MA - 20


def test_blank():
    out = leds.Output()
    run(out, bytes((255,)) * (3 * N), 50)
    out.blank()
    assert out.out == bytes(3 * N)
    assert out._pre == bytes(3 * N)
    assert out.est_ma == FIXED


class FakeNeoPixel:
    ORDER = (1, 0, 2, 3)  # как в micropython-lib neopixel: GRB

    def __init__(self, n):
        self.buf = bytearray(3 * n)
        self.writes = 0

    def write(self):
        self.writes += 1


def test_write_uses_strip_color_order():
    np = FakeNeoPixel(N)
    out = leds.Output(np=np)
    run(out, bytes((10, 20, 30)) * N, 50, cap=10_000)
    out.write()
    assert np.writes == 1
    assert np.buf[:3] == bytes((20, 10, 30))  # G, R, B
