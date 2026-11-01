# Проверка 1: лента WS2812B.
#
# Запуск (из корня проекта, COM5 замените на свой порт; модули — см. docs/setup.md, шаг 3):
#   .venv\Scripts\python tools\upload.py COM5
#   .venv\Scripts\mpremote connect COM5 run firmware/bringup/01_leds.py
#
# Что должно получиться:
#   1) каждый пиксель по очереди загорается красным, зелёным, синим — по одному, тускло;
#      порядок слева направо от входа ленты, без пропусков и «мусора» на соседних пикселях;
#   2) вся лента тускло-белая, потом тускло красная, зелёная, синяя;
#   3) лента гаснет, в консоли — время вывода кадра.
# Если цвета перепутаны (вместо красного — зелёный) — у ленты другой порядок цветов, сообщите.
# Если лента не реагирует или мигает мусором при питании от 5 В — см. docs/wiring.md, «Логический уровень».
#
# Яркость низкая специально: от USB доступно ~500 мА на всё.

import time

from machine import Pin
from neopixel import NeoPixel

import config

LEVEL = 16  # из 255: ~1,3 мА на канал
STEP_MS = 60


def estimate_ma(np):
    """Оценка тока ленты по формуле из ТЗ (без платы). Делим один раз, иначе тусклые пиксели «теряются»."""
    channels = 0
    for i in range(np.n):
        r, g, b = np[i]
        channels += r + g + b
    return channels * config.MA_PER_CHANNEL // 255 + np.n * config.MA_IDLE_PER_LED


def show(np):
    t0 = time.ticks_us()
    np.write()
    return time.ticks_diff(time.ticks_us(), t0)


def main():
    np = NeoPixel(Pin(config.PIN_LED_DATA, Pin.OUT), config.NUM_LEDS)
    np.fill((0, 0, 0))
    show(np)
    print("Лента: {} пикселей на GPIO{}".format(config.NUM_LEDS, config.PIN_LED_DATA))

    try:
        print("Шаг 1: пиксели по очереди — красный, зелёный, синий")
        for name, color in (("красный", (LEVEL, 0, 0)), ("зелёный", (0, LEVEL, 0)), ("синий", (0, 0, LEVEL))):
            print("  " + name)
            for i in range(config.NUM_LEDS):
                np.fill((0, 0, 0))
                np[i] = color
                show(np)
                time.sleep_ms(STEP_MS)

        print("Шаг 2: вся лента")
        worst_us = 0
        for name, color in (
            ("белый", (LEVEL, LEVEL, LEVEL)),
            ("красный", (LEVEL, 0, 0)),
            ("зелёный", (0, LEVEL, 0)),
            ("синий", (0, 0, LEVEL)),
        ):
            np.fill(color)
            us = show(np)
            worst_us = max(worst_us, us)
            print("  {}: оценка тока ленты ~{} мА".format(name, estimate_ma(np)))
            time.sleep_ms(1500)

        # Время вывода кадра: важно для частоты кадров (~40 fps = 25 мс на кадр).
        samples = []
        for _ in range(50):
            samples.append(show(np))
        samples.sort()
        print("Вывод кадра: медиана {} мкс, худший {} мкс".format(samples[len(samples) // 2], max(samples[-1], worst_us)))
    finally:
        np.fill((0, 0, 0))
        np.write()
        print("Лента погашена. Проверка 1 закончена.")


main()
