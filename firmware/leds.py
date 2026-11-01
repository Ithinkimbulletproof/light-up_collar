# Вывод на ленту: яркость → плавность → ограничение тока → лента.
#
# Порядок важен:
# 1. Яркость из настроек (0–100 %) и общий множитель (плавный старт) применяются к кадру эффекта.
# 2. Плавность: каждый канал каждого пикселя меняется не быстрее чем на 255 за TRANSITION_MS.
#    Это защищает от мигания чаще 3 Гц сразу всё: эффекты, индикаторы и быстрые нажатия кнопки.
# 3. Потолок тока: если оценка полного тока выше потолка, весь кадр масштабируется вниз.
#    Снижается множитель сразу, а растёт плавно — чтобы рост потолка тоже не дал вспышки.
# Модуль не трогает железо сам: объект NeoPixel передаётся снаружи (на ПК — None).

import config

_FIXED_MA = config.NUM_LEDS * config.MA_IDLE_PER_LED + config.I_ESP_MA  # покой ленты + плата


def leds_ma(frame):
    """Ток светодиодов кадра по формуле из ТЗ (docs/spec.md): (r + g + b) / 255 × MA_PER_CHANNEL."""
    return sum(frame) * config.MA_PER_CHANNEL // 255


def total_ma(frame, n=config.NUM_LEDS):
    """Оценка полного тока от батареи: светодиоды + покой пикселей + плата."""
    return leds_ma(frame) + n * config.MA_IDLE_PER_LED + config.I_ESP_MA


class Output:
    def __init__(self, n=config.NUM_LEDS, np=None):
        self.n = n
        self.np = np
        self.shown = bytearray(3 * n)  # после плавности, до ограничения тока
        self.out = bytearray(3 * n)  # что уходит на ленту
        self.k = 0  # множитель потолка тока, 0..256 (256 = без ограничения); с нуля — плавный старт
        self.est_ma = n * config.MA_IDLE_PER_LED + config.I_ESP_MA
        self._fixed = n * config.MA_IDLE_PER_LED + config.I_ESP_MA

    def step(self, target, brightness, gain, cap_ma, dt_ms):
        """target — кадр эффекта, brightness 0–100, gain 0–255, cap_ma — потолок полного тока."""
        level = brightness * gain // 100  # 0..255
        step = 255 * dt_ms // config.TRANSITION_MS
        if step < 1:
            step = 1
        shown = self.shown
        for i in range(3 * self.n):
            v = target[i] * level // 255
            s = shown[i]
            if v > s:
                s = v if v - s <= step else s + step
            elif v < s:
                s = v if s - v <= step else s - step
            shown[i] = s

        budget = cap_ma - self._fixed
        need = leds_ma(shown)
        if budget <= 0:
            k_target = 0
        elif need <= budget:
            k_target = 256
        else:
            k_target = budget * 256 // need
        if k_target < self.k:
            self.k = k_target
        else:
            k_step = 256 * dt_ms // config.TRANSITION_MS
            self.k = min(k_target, self.k + (k_step if k_step > 0 else 1))

        k = self.k
        out = self.out
        for i in range(3 * self.n):
            out[i] = shown[i] * k >> 8
        self.est_ma = self._fixed + leds_ma(out)
        return out

    def write(self):
        """Отправить self.out на ленту (в порядке цветов ленты, обычно GRB)."""
        np = self.np
        if np is None:
            return
        buf = np.buf
        o_r, o_g, o_b = np.ORDER[0], np.ORDER[1], np.ORDER[2]
        out = self.out
        for i in range(self.n):
            j = 3 * i
            buf[j + o_r] = out[j]
            buf[j + o_g] = out[j + 1]
            buf[j + o_b] = out[j + 2]
        np.write()

    def blank(self):
        """Погасить мгновенно (без плавности) — например, когда пропало питание ленты."""
        for i in range(3 * self.n):
            self.shown[i] = 0
            self.out[i] = 0
        self.k = 0
        self.est_ma = self._fixed
