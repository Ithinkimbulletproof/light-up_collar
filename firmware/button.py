# Распознавание жестов кнопки: одиночное, двойное нажатие и удержание (docs/button.md).
#
# Модуль не трогает железо: на вход update() подаются время и уровень кнопки,
# на выходе — событие. Поэтому логику можно проверить на ПК (firmware/tests).
# Опрашивать надо часто и регулярно (config.BUTTON_POLL_MS): прерывания на ESP32
# в MicroPython всё равно «мягкие», а при дребезге часть из них теряется.
#
# События:
#   PRESS  — первое нажатие новой серии (нужно, чтобы сразу убрать показ заряда);
#   SINGLE — одно короткое нажатие (приходит через DOUBLE_PRESS_MS после отпускания:
#            ждём, не будет ли второго);
#   DOUBLE — два коротких (приходит при отпускании второго);
#   LONG   — кнопку держат LONG_PRESS_MS (приходит, не дожидаясь отпускания).
# Каждая серия нажатий заканчивается ровно одним из SINGLE / DOUBLE / LONG.
#   - «Короткое» — отпущено раньше LONG_PRESS_MS.
#   - Удержание никогда не часть двойного: если второе нажатие удержали — это LONG, первое отбрасывается.
#   - Третье нажатие подряд внутри окна после двойного игнорируется (вместе с остальной серией).

import config

try:
    from time import ticks_diff
except ImportError:  # CPython: тесты на ПК, время там не переполняется

    def ticks_diff(a, b):
        return a - b


PRESS = "press"
SINGLE = "single"
DOUBLE = "double"
LONG = "long"

# Состояния автомата
_IDLE = 0  # ничего не происходит
_DOWN1 = 1  # первое нажатие, кнопка зажата
_WAIT2 = 2  # первое отпустили, ждём второе
_DOWN2 = 3  # второе нажатие, кнопка зажата
_HELD = 4  # удержание уже сработало, ждём отпускания
_MUTE = 5  # после двойного: нажатия внутри окна игнорируются


class Button:
    def __init__(
        self,
        debounce_ms=config.DEBOUNCE_MS,
        double_ms=config.DOUBLE_PRESS_MS,
        long_ms=config.LONG_PRESS_MS,
    ):
        self.debounce_ms = debounce_ms
        self.double_ms = double_ms
        self.long_ms = long_ms
        self._state = _IDLE
        self._stable = False  # уровень после антидребезга: True — нажата
        self._raw = False  # последний прочитанный уровень
        self._raw_since = None  # когда _raw последний раз поменялся
        self._edge_at = 0  # время последнего нажатия/отпускания после антидребезга
        self.bounces = 0  # сколько раз дребезжал уровень (для отладки, 02_button.py)

    def update(self, pressed, now):
        """pressed — кнопка нажата сейчас (сырое чтение), now — ticks_ms(). Возвращает событие или None."""
        if self._raw_since is None:
            self._raw_since = now
        if pressed != self._raw:
            if pressed == self._stable:
                self.bounces += 1  # вернулся к прежнему уровню раньше, чем истёк антидребезг
            self._raw = pressed
            self._raw_since = now

        if self._raw != self._stable and ticks_diff(now, self._raw_since) >= self.debounce_ms:
            self._stable = self._raw
            self._edge_at = self._raw_since  # время настоящего фронта, без задержки антидребезга
            return self._on_edge(self._stable)

        return self._on_tick(now)

    def _on_edge(self, down):
        state = self._state
        if down:
            if state == _IDLE:
                self._state = _DOWN1
                return PRESS
            if state == _WAIT2:
                self._state = _DOWN2
            # в _MUTE нажатие игнорируется; отпускание его продлит окно (см. ниже)
            return None
        if state == _DOWN1:
            self._state = _WAIT2
            return None
        if state == _DOWN2:
            self._state = _MUTE
            return DOUBLE
        if state == _HELD:
            self._state = _IDLE
        # _MUTE: отпустили проигнорированное нажатие — окно отсчитывается от этого отпускания
        return None

    def _on_tick(self, now):
        state = self._state
        settled = self._raw == self._stable  # нет непроверенного фронта (дребезг/антидребезг)
        held = ticks_diff(now, self._edge_at)
        # Если отпускание уже началось и только ждёт антидребезга, это не удержание.
        if state in (_DOWN1, _DOWN2) and settled and held >= self.long_ms:
            self._state = _HELD
            return LONG
        # Окно двойного считается от отпускания до начала второго нажатия: пока нажатие не проверено — ждём.
        if state == _WAIT2 and settled and held > self.double_ms:
            self._state = _IDLE
            return SINGLE
        if state == _MUTE and settled and not self._stable and held > self.double_ms:
            self._state = _IDLE
        return None

    @property
    def pressed(self):
        """Кнопка нажата сейчас (после антидребезга)."""
        return self._stable

    @property
    def idle(self):
        """Серия закончилась и кнопка отпущена."""
        return self._state == _IDLE and not self._stable
