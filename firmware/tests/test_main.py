# Запуск main.py на ПК с поддельным железом: поддельные machine, neopixel и часы.
#
# Проверяет то, что не видно по отдельным модулям: прошивка стартует, лента гаснет до замеров,
# кнопка доходит до логики, настройки сохраняются, безопасный старт и гашение при выходе работают.
import os
import sys
import types

import pytest

import config

MAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


class Stop(Exception):
    pass


class Clock(types.ModuleType):
    """Поддельный модуль time с ручными часами; sleep_ms двигает время."""

    def __init__(self, limit_ms, on_tick=None):
        super().__init__("time")
        self.ms = 0
        self.limit_ms = limit_ms
        self.on_tick = on_tick

    def ticks_ms(self):
        return self.ms

    def ticks_us(self):
        return self.ms * 1000

    def ticks_diff(self, a, b):
        return a - b

    def ticks_add(self, a, b):
        return a + b

    def sleep_ms(self, n):
        self.ms += n
        if self.on_tick:
            self.on_tick(self.ms)
        if self.ms >= self.limit_ms:
            raise Stop



class Board:
    """Состояние поддельной платы: уровень кнопки и напряжение на узле питания."""

    def __init__(self, mv, pressed=lambda t: False):
        self.mv = mv
        self.pressed = pressed
        self.clock = None
        self.strip = None


def fake_machine(board):
    m = types.ModuleType("machine")

    class Pin:
        IN, OUT, PULL_UP = 1, 3, 2

        def __init__(self, num, mode=None, pull=None, value=None):
            self.num = num
            self.level = value

        def value(self, v=None):
            if v is not None:
                self.level = v
                return None
            if self.num == config.PIN_BUTTON:
                return 0 if board.pressed(board.clock.ms) else 1
            return self.level

    class ADC:
        ATTN_11DB = 3

        def __init__(self, pin, atten=None):
            pass

        def read_uv(self):
            return board.mv * 1000 // config.ADC_DIVIDER

    m.Pin = Pin
    m.ADC = ADC
    return m


def fake_neopixel(board):
    m = types.ModuleType("neopixel")

    class NeoPixel:
        ORDER = (1, 0, 2, 3)

        def __init__(self, pin, n):
            self.n = n
            self.buf = bytearray(3 * n)
            self.writes = []
            board.strip = self

        def fill(self, c):
            for i in range(self.n):
                self.buf[3 * i : 3 * i + 3] = bytes((c[1], c[0], c[2]))

        def write(self):
            self.writes.append(bytes(self.buf))

    m.NeoPixel = NeoPixel
    return m


def boot(monkeypatch, tmp_path, board, seconds, stop=Stop):
    monkeypatch.chdir(tmp_path)  # settings.json пишется в текущую папку
    board.clock = Clock(seconds * 1000)
    if stop is not Stop:

        def on_tick(ms, limit=seconds * 1000):
            if ms >= limit:
                raise stop

        board.clock.on_tick = on_tick
        board.clock.limit_ms = 10**12
    monkeypatch.setitem(sys.modules, "time", board.clock)
    monkeypatch.setitem(sys.modules, "machine", fake_machine(board))
    monkeypatch.setitem(sys.modules, "neopixel", fake_neopixel(board))
    with open(MAIN, encoding="utf-8") as f:
        code = compile(f.read(), MAIN, "exec")
    try:
        exec(code, {"__name__": "__main__"})
    except stop:
        pass
    return board


def lit(frame):
    return any(frame)


def test_boots_on_usb_and_lights_up(monkeypatch, tmp_path):
    b = boot(monkeypatch, tmp_path, Board(mv=4800), seconds=5)
    w = b.strip.writes
    assert len(w) > 100  # ~40 кадров в секунду
    assert lit(w[-2])  # последний кадр — гашение в finally при остановке теста
    assert max(sum(f) for f in w) * config.MA_PER_CHANNEL // 255 + 63 <= config.USB_CAP_MA + 5


def test_dark_until_battery_measured(monkeypatch, tmp_path):
    b = boot(monkeypatch, tmp_path, Board(mv=3900), seconds=3)
    first = b.strip.writes[0]
    assert not lit(first)  # первый кадр — тёмный: сначала замеры батареи
    assert lit(b.strip.writes[-2])


def test_single_press_changes_color_and_saves(monkeypatch, tmp_path):
    board = Board(mv=3900, pressed=lambda t: 2000 <= t < 2100)
    b = boot(monkeypatch, tmp_path, board, seconds=2 + 1 + config.SAVE_DELAY_MS // 1000 + 1)
    with open(tmp_path / "settings.json") as f:
        assert '"palette": 1' in f.read()
    g, r, bl = b.strip.writes[-2][:3]  # в буфере ленты порядок G, R, B
    assert (r > 0, g > 0, bl) == (True, True, 0)  # оранжевый: красный + немного зелёного


def test_double_press_changes_brightness(monkeypatch, tmp_path):
    board = Board(mv=3900, pressed=lambda t: 2000 <= t < 2100 or 2250 <= t < 2350)
    boot(monkeypatch, tmp_path, board, seconds=2 + 1 + config.SAVE_DELAY_MS // 1000 + 2)
    with open(tmp_path / "settings.json") as f:
        text = f.read()
    level = (config.BRIGHTNESS_DEFAULT_INDEX + 1) % len(config.BRIGHTNESS_LEVELS)
    assert '"level": {}'.format(level) in text and '"palette": 0' in text  # цвет не тронут


def test_hold_shows_charge(monkeypatch, tmp_path):
    hold_end = 2000 + config.LONG_PRESS_MS + 200
    board = Board(mv=3900, pressed=lambda t: 2000 <= t < hold_end)
    b = boot(monkeypatch, tmp_path, board, seconds=(hold_end + 2000) // 1000 + 1)
    f = b.strip.writes[-2]
    lit_px = sum(1 for i in range(config.NUM_LEDS) if any(f[3 * i : 3 * i + 3]))
    assert 0 < lit_px < config.NUM_LEDS  # горит доля ленты по заряду, а не вся
    assert not os.path.exists(tmp_path / "settings.json")  # показ заряда ничего не сохраняет


def test_no_save_at_low_voltage(monkeypatch, tmp_path):
    board = Board(mv=config.V_LOW_MV - 100, pressed=lambda t: 2000 <= t < 2100)
    boot(monkeypatch, tmp_path, board, seconds=10)
    assert not os.path.exists(tmp_path / "settings.json")


def test_safe_boot(monkeypatch, tmp_path):
    b = boot(monkeypatch, tmp_path, Board(mv=3900, pressed=lambda t: True), seconds=3)
    assert b.strip is None  # до ленты дело не дошло


def test_dead_battery_never_lights_strip(monkeypatch, tmp_path):
    b = boot(monkeypatch, tmp_path, Board(mv=config.V_EMPTY_MV - 120), seconds=8)
    for f in b.strip.writes:
        assert sum(f) <= 24  # только тусклый пиксель «разряжен»


def test_no_strip_power_no_data(monkeypatch, tmp_path):
    b = boot(monkeypatch, tmp_path, Board(mv=0), seconds=3)
    assert b.strip.writes == []  # ленте без питания данные не шлём


def test_ctrl_c_blanks_strip(monkeypatch, tmp_path):
    b = boot(monkeypatch, tmp_path, Board(mv=3900), seconds=4, stop=KeyboardInterrupt)
    assert lit(b.strip.writes[-2])
    assert not lit(b.strip.writes[-1])


def test_adc_errors_do_not_crash(monkeypatch, tmp_path):
    # Каждый второй замер АЦП — ошибка: прошивка работает и всё равно меряет
    board = Board(mv=3900)
    calls = {"n": 0}
    m = fake_machine(board)
    good_read = m.ADC.read_uv

    def flaky(self):
        calls["n"] += 1
        if calls["n"] % 2:
            raise OSError(116, "ETIMEDOUT")
        return good_read(self)

    m.ADC.read_uv = flaky
    monkeypatch.setattr(sys.modules[__name__], "fake_machine", lambda b: m)
    b = boot(monkeypatch, tmp_path, board, seconds=5)
    assert lit(b.strip.writes[-2])


def test_adc_always_failing_keeps_strip_dark(monkeypatch, tmp_path):
    board = Board(mv=3900)
    m = fake_machine(board)

    def dead(self):
        raise OSError(116, "ETIMEDOUT")

    m.ADC.read_uv = dead
    monkeypatch.setattr(sys.modules[__name__], "fake_machine", lambda b: m)
    b = boot(monkeypatch, tmp_path, board, seconds=5)
    assert all(not lit(f) for f in b.strip.writes)


def test_main_passes_scene_current_to_output(monkeypatch, tmp_path):
    # Без тока сцены белый хвост разгорался бы, пока перекраска до него не дошла.
    (tmp_path / "settings.json").write_text('{"palette": 7, "level": 2}')
    board = Board(mv=3900, pressed=lambda t: 5000 <= t < 5060)
    b = boot(monkeypatch, tmp_path, board, seconds=7)
    w = b.strip.writes
    last = 3 * (config.NUM_LEDS - 1)
    i = 5000 // 26  # заведомо до нажатия
    base = w[i][last]
    during = [f[last] for f in w[i:]][: 860 // 26]  # перекраска идёт примерно с 5,41 до 5,91 с
    assert base > 0 and max(during) <= base + 1
