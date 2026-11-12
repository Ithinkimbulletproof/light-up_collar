# Вывод на ленту: яркость → плавность → масштаб под потолок → страховочный потолок → лента.
#
# Порядок важен:
# 1. Яркость из настроек (0–100 %) и общий множитель (плавный старт, подсказка уровня) применяются к кадру эффекта.
# 2. Плавность: каждый канал каждого пикселя меняется не быстрее чем на 255 за TRANSITION_MS.
#    Это защищает от мигания чаще 3 Гц сразу всё: эффекты, индикаторы и быстрые нажатия кнопки.
# 3. Масштаб под номинальный потолок CAP_MA: если сцена на полной яркости в него не влезает
#    (белому нужно ~1 А), весь кадр уменьшается так, чтобы 100 % как раз влезали. Поэтому уровень
#    яркости — доля от максимума под потолком, и шаги одинаковы на любом цвете. Масштаб считается
#    от тока всей сцены (ref_ma), а не от кадра анимации: иначе во время перекраски ещё не перекрашенные
#    пиксели разгорались бы. К новому значению он едет примерно за TRANSITION_MS: шаг целый и не меньше 1,
#    поэтому при кадре 25 мс разница в 3 единицы проходит за 75 мс, а в 25 единиц — за 625 мс. Растущий
#    канал не обгоняет своё итоговое значение: иначе после показа заряда цвет вспыхивал бы, пока масштаб
#    ещё не опустился.
# 4. Страховочный потолок по фактическому кадру и текущему потолку: он срезает кадр, пока потолок
#    снижен (низкий заряд, USB, просадка), и ловит переходы, когда сглаженный кадр ненадолго
#    превышает потолок. Снижается сразу, а растёт плавно — чтобы рост потолка не дал вспышки.
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
        self.shown = bytearray(3 * n)  # после плавности, до масштаба и потолка
        self.out = bytearray(3 * n)  # что уходит на ленту
        self.scale = None  # масштаб под номинальный потолок, 0..256; None — подобрать с первого кадра
        self._scale_goal = None  # куда масштаб едет сейчас
        self._scale_step = 1  # с какой скоростью: разница, делённая на TRANSITION_MS
        self._pre = bytearray(3 * n)  # кадр после масштаба на прошлом шаге: растущему каналу нужна точка отсчёта
        self.k = 0  # страховочный множитель потолка, 0..256 (256 = без ограничения); с нуля — плавный старт
        self.est_ma = n * config.MA_IDLE_PER_LED + config.I_ESP_MA
        self._fixed = n * config.MA_IDLE_PER_LED + config.I_ESP_MA

    def step(self, target, brightness, gain, cap_ma, dt_ms, ref_ma=None):
        """target — кадр эффекта, brightness 0–100, gain 0–255, cap_ma — потолок полного тока.

        ref_ma — ток светодиодов всей сцены на полной яркости, под него подбирается масштаб
        (app.App.ref_ma; во время перекраски — самый тяжёлый цвет с начала серии нажатий). None — ток самого кадра.
        """
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

        k_step = 256 * dt_ms // config.TRANSITION_MS
        if k_step < 1:
            k_step = 1

        full = leds_ma(target)
        if ref_ma is not None and ref_ma > full:
            full = ref_ma
        nominal = (cap_ma if cap_ma > config.CAP_MA else config.CAP_MA) - self._fixed
        if nominal <= 0:
            scale_target = 0
        elif full > nominal:
            scale_target = nominal * 256 // full
        else:
            scale_target = 256
        scale = self.scale
        if scale is None:
            scale = self._scale_goal = scale_target
        else:
            if scale_target != self._scale_goal:
                self._scale_goal = scale_target
                self._scale_step = abs(scale_target - scale) * dt_ms // config.TRANSITION_MS
                if self._scale_step < 1:
                    self._scale_step = 1
            if scale_target > scale:
                scale = min(scale_target, scale + self._scale_step)
            else:
                scale = max(scale_target, scale - self._scale_step)
        self.scale = scale

        out = self.out
        pre = self._pre
        rise = (step * scale_target >> 8) + 1  # плавность в единицах итогового масштаба
        for i in range(3 * self.n):
            o = (shown[i] * scale + 128) >> 8
            p = pre[i]
            if o > p:
                top = ((target[i] * level // 255) * scale_target + 128) >> 8
                if top > p + rise:
                    top = p + rise
                if o > top:
                    o = top if top > p else p
            pre[i] = o
            out[i] = o

        budget = cap_ma - self._fixed
        need = leds_ma(out)
        if budget <= 0:
            k_target = 0
        elif need <= budget:
            k_target = 256
        else:
            k_target = budget * 256 // need
        if k_target < self.k:
            self.k = k_target
        else:
            self.k = min(k_target, self.k + k_step)

        k = self.k
        for i in range(3 * self.n):
            out[i] = out[i] * k >> 8
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
            self._pre[i] = 0
            self.out[i] = 0
        self.scale = None
        self.k = 0
        self.est_ma = self._fixed
