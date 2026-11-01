# Кадры для ленты: ровный свет и индикаторы (заряд, «низкий заряд», «разряжен»).
#
# Кадр — bytearray длиной 3 × N в порядке R, G, B (перевод в GRB ленты — в leds.py).
# Яркость, плавность переходов и ограничение тока применяются потом, в leds.py.
# Индикаторы медленные: свет рядом с глазами собаки, никаких вспышек чаще 3 Гц.
# Модуль без железа — его проверяют тесты на ПК.

import math

import config

LOW_FLASH_MS = 600
EMPTY_PERIOD_MS = 5000
EMPTY_PULSE_MS = 600
EMPTY_LEVEL = 24

# Волна 0 → 255 → 0 (приподнятый косинус), 256 шагов на период.
_WAVE = bytes(int(127.5 - 127.5 * math.cos(2 * math.pi * i / 256) + 0.5) for i in range(256))


def pulse(t, length):
    """Одиночный плавный импульс 0 → 255 → 0 длиной length мс; вне [0, length) — 0."""
    if t < 0 or t >= length:
        return 0
    return _WAVE[t * 256 // length]


def new_frame(n=config.NUM_LEDS):
    return bytearray(3 * n)


def fill(out, rgb, level=255):
    """Вся лента одним цветом — это и есть обычный режим ошейника."""
    r = rgb[0] * level // 255
    g = rgb[1] * level // 255
    b = rgb[2] * level // 255
    for i in range(0, len(out), 3):
        out[i] = r
        out[i + 1] = g
        out[i + 2] = b


def wipe(out, before, rgb, t):
    """Смена цвета: новый цвет rgb бежит от первого пикселя диод за диодом поверх кадра before.

    t — мс от нажатия; вся лента перекрашивается за COLOR_WIPE_MS. Первый пиксель — сразу.
    """
    n = len(out) // 3
    reached = (t * n) // config.COLOR_WIPE_MS + 1
    for i in range(n):
        j = 3 * i
        if i < reached:
            out[j] = rgb[0]
            out[j + 1] = rgb[1]
            out[j + 2] = rgb[2]
        else:
            out[j] = before[j]
            out[j + 1] = before[j + 1]
            out[j + 2] = before[j + 2]


def charge_color(percent):
    """Цвет шкалы заряда по четвертям: 75–100 % зелёный, 50–75 % жёлтый, 25–50 % оранжевый, ниже — красный."""
    if percent >= 75:
        return config.PALETTE[3]  # зелёный
    if percent >= 50:
        return config.PALETTE[2]  # жёлтый
    if percent >= 25:
        return config.PALETTE[1]  # оранжевый
    return config.PALETTE[0]  # красный


def charge_bar(out, percent, t=None):
    """Заряд: горит такая доля ленты, сколько процентов заряда (80 % — 80 % пикселей), остальное погашено.

    Цвет всей горящей части — по четверти, в которой заряд (charge_color).
    t — мс от начала показа: шкала бежит от первого пикселя диод за диодом, вся лента — за CHARGE_FILL_MS,
    и останавливается на уровне заряда. t=None — сразу целиком.
    """
    n = len(out) // 3
    lit = (n * percent + 50) // 100
    if percent > 0 and lit == 0:
        lit = 1
    if t is not None:
        reached = (t * n) // config.CHARGE_FILL_MS + 1  # первый пиксель — сразу
        if reached < lit:
            lit = reached
    rgb = charge_color(percent)
    for i in range(n):
        on = i < lit
        out[3 * i] = rgb[0] if on else 0
        out[3 * i + 1] = rgb[1] if on else 0
        out[3 * i + 2] = rgb[2] if on else 0


def low_flash(out, t):
    """Предупреждение «низкий заряд»: одна плавная красная вспышка всей ленты, t — от начала вспышки."""
    fill(out, (255, 0, 0), pulse(t, LOW_FLASH_MS))


def empty_blink(out, t):
    """«Разряжен»: лента погашена, раз в 5 с тусклая вспышка одного пикселя."""
    for i in range(len(out)):
        out[i] = 0
    out[0] = EMPTY_LEVEL * pulse(t % EMPTY_PERIOD_MS, EMPTY_PULSE_MS) // 255
