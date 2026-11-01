# Яркость, плавность и ограничение тока.
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
    assert out.est_ma >= config.CAP_MA - 20  # бюджет используется почти полностью (шаг множителя 1/256)


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


def test_brightness_applied_before_cap():
    out = leds.Output()
    red = bytes((255, 0, 0)) * N
    r = run(out, red, 100, brightness=50)
    assert r[0] == 127 and r[1] == 0


def test_slew_limits_change_speed():
    out = leds.Output()
    white = bytes((255,)) * (3 * N)
    run(out, bytes((10,)) * (3 * N), 50, cap=10_000)
    r = out.step(white, 100, 255, 10_000, DT)
    assert r[0] <= 10 + 255 * DT // config.TRANSITION_MS + 1
    frames_to_full = config.TRANSITION_MS // DT + 2
    r = run(out, white, frames_to_full, cap=10_000)
    assert r[0] == 255


def test_blank():
    out = leds.Output()
    run(out, bytes((255,)) * (3 * N), 50)
    out.blank()
    assert out.out == bytes(3 * N)
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
