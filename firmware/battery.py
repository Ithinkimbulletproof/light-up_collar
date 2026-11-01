# Батарея: напряжение, проценты, флаги «низкий заряд» / «разряжен», подстройка потолка тока.
#
# Модуль не трогает железо: на вход — замеры (мВ), оценка тока и время, на выходе — состояние.
# Поэтому всё проверяется тестами на ПК. Чтение АЦП — в main.py.

import config

try:
    from time import ticks_diff
except ImportError:  # CPython: тесты на ПК

    def ticks_diff(a, b):
        return a - b

SOURCE_BATTERY = "battery"
SOURCE_USB = "usb"  # на узле питания больше, чем бывает у Li-Po: это 5 В от USB
SOURCE_NONE = "none"  # почти ноль: делитель не подключён или питание отрезано


def adc_uv_to_mv(uv):
    """Микровольты на входе АЦП → милливольты батареи (с делителем и поправкой)."""
    return int(uv * config.ADC_DIVIDER * config.ADC_CORRECTION / 1000 + 0.5)


def rest_mv(loaded_mv, current_ma):
    """Напряжение без нагрузки: V_rest ≈ V + I × R_int."""
    return loaded_mv + current_ma * config.R_INT_MOHM // 1000


def source(mv, previous=None):
    """Что питает узел «ПИТ». previous — прошлый ответ: у порога USB есть гистерезис,
    чтобы просадка USB под нагрузкой не выдала его за батарею (и не подняла потолок тока)."""
    usb_from = config.V_USB_EXIT_MV if previous == SOURCE_USB else config.V_NO_BATTERY_HIGH_MV
    if mv > usb_from:
        return SOURCE_USB
    if mv < config.V_NO_BATTERY_LOW_MV:
        return SOURCE_NONE
    return SOURCE_BATTERY


def percent(mv_rest):
    """Проценты по таблице разряда (линейно между точками), 0–100."""
    table = config.LIPO_TABLE
    if mv_rest >= table[0][0]:
        return 100
    if mv_rest <= table[-1][0]:
        return 0
    for i in range(1, len(table)):
        hi_mv, hi_pct = table[i - 1]
        lo_mv, lo_pct = table[i]
        if mv_rest >= lo_mv:
            return lo_pct + (hi_pct - lo_pct) * (mv_rest - lo_mv) // (hi_mv - lo_mv)
    return 0


EMPTY_CONFIRM_MS = 2000  # «разряжен» — если держится столько, а не от одного выброса
LOW_CONFIRM_MS = 2000
STARTUP_SAMPLES = 8  # замеров при погашенной ленте перед первым кадром


class _Ema:
    """Сглаживание: новое = старое + (замер − старое) / 2^shift. Целые числа, ×16 для точности."""

    def __init__(self, shift):
        self.shift = shift
        self.acc = None

    def add(self, v):
        if self.acc is None:
            self.acc = v * 16
        else:
            self.acc += (v * 16 - self.acc) >> self.shift
        return (self.acc + 8) >> 4

    @property
    def value(self):
        return None if self.acc is None else (self.acc + 8) >> 4


class Monitor:
    """Состояние батареи по замерам. update() вызывать несколько раз в секунду."""

    def __init__(self):
        self._loaded = _Ema(2)  # под нагрузкой: быстро, для подстройки потолка
        self._rest = _Ema(3)  # с поправкой на нагрузку: для флагов
        self._slow = _Ema(5)  # для процентов, чтобы не прыгали
        self.source = None  # ещё не мерили
        self._restart()

    def _restart(self):
        """Всё с нуля: при старте и при смене источника питания.

        Нужно, например, когда плата уже работает от USB, а выключатель ошейника включают потом:
        иначе сглаживание ползёт от нуля, и батарея на пару секунд выглядит «разряженной».
        Заодно заново проходит проверка батареи до первого кадра.
        """
        self._loaded.acc = None
        self._rest.acc = None
        self._slow.acc = None
        self.samples = 0
        self.low = False
        self.empty = False  # защёлка до выключения питания (или до смены источника — см. выше)
        self._low_since = None
        self._low_clear_since = None
        self._empty_since = None

    @property
    def v_loaded(self):
        return self._loaded.value

    @property
    def v_rest(self):
        return self._rest.value

    @property
    def percent(self):
        if self.source != SOURCE_BATTERY:
            return 100  # без батареи (питание от USB) показываем полную шкалу
        v = self._slow.value
        return 0 if v is None or self.empty else percent(v)

    @property
    def ready(self):
        """Набрано достаточно замеров, чтобы решить, можно ли зажигать ленту."""
        return self.samples >= STARTUP_SAMPLES

    def update(self, mv, current_ma, now):
        """mv — среднее из нескольких замеров АЦП (уже гладкое), current_ma — оценка тока в этот момент."""
        src = source(mv, self.source)
        if src != self.source:
            self._restart()
            self.source = src
        self.samples += 1
        self._loaded.add(mv)
        rest = self._rest.add(rest_mv(mv, current_ma))
        self._slow.add(rest_mv(mv, current_ma))
        if self.source != SOURCE_BATTERY:
            return  # USB или питание ленты отрезано: флагов батареи нет

        if not self.ready:
            return
        if self.samples == STARTUP_SAMPLES and rest < config.V_EMPTY_MV:
            # Замеры при погашенной ленте: батарея уже севшая — не зажигаемся вовсе.
            self.empty = True
            return

        if rest < config.V_EMPTY_MV:
            if self._empty_since is None:
                self._empty_since = now
            elif ticks_diff(now, self._empty_since) >= EMPTY_CONFIRM_MS:
                self.empty = True
        else:
            self._empty_since = None

        if not self.low:
            if rest < config.V_LOW_MV:
                if self._low_since is None:
                    self._low_since = now
                elif ticks_diff(now, self._low_since) >= LOW_CONFIRM_MS:
                    self.low = True
                    self._low_clear_since = None
            else:
                self._low_since = None
        else:
            if rest > config.V_LOW_CLEAR_MV:
                if self._low_clear_since is None:
                    self._low_clear_since = now
                elif ticks_diff(now, self._low_clear_since) >= config.LOW_CLEAR_HOLD_MS:
                    self.low = False
                    self._low_since = None
            else:
                self._low_clear_since = None

    def flash_write_allowed(self):
        """Можно ли писать во флеш: при просадке запись опаснее потери настройки."""
        if self.source != SOURCE_BATTERY:
            # USB или узел батареи на нуле: плата тогда питается от USB, просадки нет
            return True
        v = self.v_loaded
        return v is not None and v >= config.V_FLASH_MIN_MV and not self.low and not self.empty


class CapController:
    """Адаптивный потолок полного тока (docs/spec.md, «Подстройка под батарею»)."""

    def __init__(self):
        self.cap = config.CAP_MA
        self.limited = False
        self._t_down = None
        self._t_up = None

    def update(self, mon, now):
        if mon.source != SOURCE_BATTERY:
            self.cap = config.USB_CAP_MA
            self.limited = False
            self._t_down = None
            self._t_up = now
            return self.cap

        ceiling = config.LOW_CAP_MA if mon.low else config.CAP_MA
        if self.cap > ceiling:
            self.cap = ceiling
        if self._t_up is None:
            self._t_up = now

        v = mon.v_loaded
        if v is not None and v < config.V_SAG_MV:
            # Просадка под нагрузкой: −10 % раз в CAP_DOWN_PERIOD_MS, не ниже минимума.
            if self._t_down is None or ticks_diff(now, self._t_down) >= config.CAP_DOWN_PERIOD_MS:
                self.cap = max(config.CAP_MIN_MA, self.cap - self.cap * config.CAP_DOWN_PERCENT // 100)
                self._t_down = now
            self._t_up = now  # возврат отсчитываем заново
        else:
            self._t_down = None
            if self.cap < ceiling and ticks_diff(now, self._t_up) >= config.CAP_UP_PERIOD_MS:
                self._t_up = now
                new = min(ceiling, self.cap + max(10, self.cap * config.CAP_UP_PERCENT // 100))
                # Возврат — только если и при новом потолке напряжение под нагрузкой не уйдёт ниже V_RECOVER.
                predicted = mon.v_rest - new * config.R_INT_MOHM // 1000
                if predicted >= config.V_RECOVER_MV:
                    self.cap = new

        self.limited = self.cap < config.CAP_MA
        return self.cap
