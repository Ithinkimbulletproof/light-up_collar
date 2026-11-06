# Логика ошейника без железа: реакция на кнопку и что показывать на ленте.
#
# Ошейник горит ровным светом выбранного цвета. Одна герметичная кнопка (docs/button.md):
#   одиночное нажатие — следующий цвет, двойное — следующий уровень яркости (лента мигает
#   номером уровня), удержание — показ заряда, нажатие во время показа — убрать показ.
# main.py читает железо (кнопку, АЦП) и передаёт сюда события и время, а отсюда берёт кадр
# и ток сцены (ref_ma) для leds.Output. Поэтому всё поведение проверяется тестами на ПК, включая правило
# «не мигать чаще 3 Гц» на итоговой картинке.

import battery
import button
import config
import leds
import modes

try:
    from time import ticks_add, ticks_diff
except ImportError:  # CPython: тесты на ПК

    def ticks_add(a, b):
        return a + b

    def ticks_diff(a, b):
        return a - b


LOW_FLASH_PERIOD_MS = 30000  # «низкий заряд»: вспышка раз в ~30 с
INDICATOR_MIN_BRIGHTNESS = 30  # индикаторы видно, даже если яркость убрана почти в ноль
_LOW_FLASH_REF_MA = leds.leds_ma(bytes((255, 0, 0)) * config.NUM_LEDS)  # вспышка «низкий заряд» на пике


class App:
    def __init__(self, settings, monitor, cap, brightness=None):
        self.s = settings
        self.mon = monitor
        self.cap = cap
        self._brightness = brightness  # только для тестов: яркость вместо уровня из настроек
        self.frame = modes.new_frame()
        # Смена цвета бежит по ленте: _wipe_from — что было на ленте в момент нажатия.
        # Второй буфер — чтобы снимать «что сейчас» без новых выделений памяти.
        self._wipe_from = modes.new_frame()
        self._wipe_snap = modes.new_frame()
        self._wipe_t0 = None
        self._wipe_ref = 0  # ток перекраски на полной яркости: больший из старого и нового цвета и тока прерванной перекраски
        self._ref_frame = modes.new_frame()  # для расчёта тока сцены
        # Ток светодиодов всей сцены на полной яркости — по нему leds.Output подбирает масштаб под потолок.
        # None — хватит тока самого кадра (ровный свет).
        self.ref_ma = None
        self._ramp_t0 = None  # начало плавного старта
        self._charge_until = None
        self._charge_t0 = None
        self._charge_ref = 0  # ток всей шкалы заряда на полной яркости
        self._swallow = False  # серия нажатий, которой убрали показ заряда, больше ничего не делает
        self._hint_t0 = None  # подсказка после смены яркости: мигнуть номером уровня
        self._fade_from = 0  # смена уровня идёт за TRANSITION_MS, какой бы маленькой она ни была
        self._fade_t0 = None
        self._hint_count = 0
        self._low_next = None
        self._low_t0 = None

    @property
    def brightness(self):
        return self.s.brightness if self._brightness is None else self._brightness

    # --- кнопка ---

    def on_button(self, ev, now):
        if self.mon.empty:
            self._swallow = False  # серия оборвалась — не тащить «съесть нажатие» в следующую
            return  # ТЗ: в состоянии «разряжен» кнопка игнорируется
        if ev == button.PRESS:
            # Показ заряда считаем идущим, только если его время ещё не вышло: пока лента без питания,
            # render() не доходит до его сброса, и устаревший показ съел бы нажатие.
            if self._charge_until is not None and ticks_diff(self._charge_until, now) > 0:
                self._swallow = True
            self._charge_until = None
            return
        if self._swallow:
            self._swallow = False  # SINGLE, DOUBLE или LONG той же серии — ничего не делаем
            return
        if ev == button.SINGLE:
            # Запоминаем, что сейчас светит (вместе с недобежавшей перекраской), и пускаем новый цвет поверх.
            self._light(self._wipe_snap, now)
            prev_ref = self._wipe_ref if self._wipe_t0 is not None else 0  # перекраска ещё бежит: помним её ток
            self._wipe_from, self._wipe_snap = self._wipe_snap, self._wipe_from
            self._wipe_t0 = now
            self.s.next_palette(now)
            modes.fill(self._ref_frame, self.s.color)
            self._wipe_ref = max(prev_ref, leds.leds_ma(self._wipe_from), leds.leds_ma(self._ref_frame))
        elif ev == button.DOUBLE:
            self._fade_from = self._level(now)
            self._fade_t0 = now
            self._hint_count = self.s.next_level(now) + 1  # 1, 2 или 3 мигания — номер уровня
            # Мигать начинаем, когда яркость доехала до нового уровня: иначе первое мигание
            # сольётся с плавным переходом (особенно 100 % → 10 %) и его не будет видно.
            self._hint_t0 = ticks_add(now, config.TRANSITION_MS)
        elif ev == button.LONG:
            self._charge_t0 = now
            self._charge_until = ticks_add(now, config.CHARGE_DISPLAY_MS)
            modes.charge_bar(self._ref_frame, self.mon.percent)  # шкала целиком: масштаб не прыгает, пока она бежит
            self._charge_ref = leds.leds_ma(self._ref_frame)

    # --- лента ---

    @property
    def strip_powered(self):
        """Узел питания на нуле: лента без питания, данные на неё не шлём (main держит DIN в 0)."""
        return self.mon.source != battery.SOURCE_NONE

    def _light(self, out, now):
        """Обычный свет: выбранный цвет, а сразу после нажатия — перекраска диод за диодом."""
        if self._wipe_t0 is not None:
            t = ticks_diff(now, self._wipe_t0)
            if t < config.COLOR_WIPE_MS:
                modes.wipe(out, self._wipe_from, self.s.color, t)
                return
            self._wipe_t0 = None
        modes.fill(out, self.s.color)

    def _level(self, now):
        """Яркость на сейчас: после двойного нажатия едет к новому уровню за TRANSITION_MS."""
        if self._fade_t0 is not None:
            t = ticks_diff(now, self._fade_t0)
            if t < config.TRANSITION_MS:
                return self._fade_from + (self.brightness - self._fade_from) * t // config.TRANSITION_MS
            self._fade_t0 = None
        return self.brightness

    def _hint_gain(self, now):
        """Множитель 0–255 для подсказки уровня: N мягких миганий до нуля, потом 255."""
        if self._hint_t0 is None:
            return 255
        t = ticks_diff(now, self._hint_t0)
        if t < 0:
            return 255  # яркость ещё переходит на новый уровень
        if t >= self._hint_count * config.LEVEL_HINT_PERIOD_MS:
            self._hint_t0 = None
            return 255
        return 255 - modes.pulse(t % config.LEVEL_HINT_PERIOD_MS, config.LEVEL_HINT_PULSE_MS)

    def render(self, now):
        """Кадр на сейчас. Возвращает (кадр, яркость 0–100, множитель плавного старта 0–255)."""
        f = self.frame
        self.ref_ma = None
        if not self.mon.ready or not self.strip_powered:
            self._ramp_t0 = None  # когда питание появится — снова плавный старт
            modes.fill(f, (0, 0, 0))
            return f, 0, 0

        if self.mon.empty:
            modes.empty_blink(f, now)
            return f, 100, 255

        if self._ramp_t0 is None:
            self._ramp_t0 = now
        ramp = ticks_diff(now, self._ramp_t0)
        gain = 255 if ramp >= config.STARTUP_RAMP_MS else ramp * 255 // config.STARTUP_RAMP_MS
        bright = self._level(now)
        indicator = max(bright, INDICATOR_MIN_BRIGHTNESS)

        if self._charge_until is not None:
            if ticks_diff(self._charge_until, now) > 0:
                modes.charge_bar(f, self.mon.percent, ticks_diff(now, self._charge_t0))
                self.ref_ma = self._charge_ref
                return f, indicator, gain
            self._charge_until = None

        if self.mon.low:
            if self._low_next is None or ticks_diff(now, self._low_next) >= 0:
                self._low_t0 = now
                self._low_next = ticks_add(now, LOW_FLASH_PERIOD_MS)
            if self._low_t0 is not None and ticks_diff(now, self._low_t0) < modes.LOW_FLASH_MS:
                modes.low_flash(f, ticks_diff(now, self._low_t0))
                self.ref_ma = _LOW_FLASH_REF_MA
                return f, indicator, gain
        else:
            self._low_next = None
            self._low_t0 = None

        self._light(f, now)
        if self._wipe_t0 is not None:
            self.ref_ma = self._wipe_ref
        return f, bright, gain * self._hint_gain(now) // 255
