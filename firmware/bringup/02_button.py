# Проверка 2: герметичная кнопка (GPIO5 на землю; подтяжка 4,7 кОм на 3V3, 330 Ом последовательно, 100 нФ).
#
# Запуск (модули на плате — tools\upload.py, см. docs/setup.md, шаг 3; лента не нужна, плата от USB):
#   .venv\Scripts\mpremote connect COM5 run firmware/bringup/02_button.py
#
# Логика распознавания — та же, что в прошивке (firmware/button.py).
# Что должно получиться (по 10 раз каждый жест):
#   не трогаем          → в начале «SW=1», событий нет;
#   одно нажатие        → «НАЖАТИЕ», через ~0,35 с «ОДИНОЧНОЕ» — ровно одно;
#   два быстрых         → «НАЖАТИЕ», «ДВОЙНОЕ» — без «ОДИНОЧНОЕ»;
#   держать ~1,5 с      → «НАЖАТИЕ», «УДЕРЖАНИЕ» (не отпуская) — без «ОДИНОЧНОЕ».
# Строка «дребезг» показывает, сколько лишних переключений отфильтровано.
# Если жесты путаются — подберите DEBOUNCE_MS / DOUBLE_PRESS_MS / LONG_PRESS_MS в config.py.
#
# Остановка: Ctrl-C в окне запуска закрывает только mpremote на ПК, скрипт на плате работает дальше.
# Его прервёт любая следующая команда mpremote или кнопка RESET на плате.

import time

from machine import Pin

import button
import config

NAMES = {
    button.PRESS: "НАЖАТИЕ",
    button.SINGLE: "ОДИНОЧНОЕ → следующий цвет",
    button.DOUBLE: "ДВОЙНОЕ   → следующая яркость",
    button.LONG: "УДЕРЖАНИЕ → показать заряд",
}


def main():
    pin = Pin(config.PIN_BUTTON, Pin.IN, Pin.PULL_UP)
    b = button.Button()
    print(
        "Кнопка на GPIO{}. Антидребезг {} мс, окно двойного {} мс, удержание {} мс.".format(
            config.PIN_BUTTON, config.DEBOUNCE_MS, config.DOUBLE_PRESS_MS, config.LONG_PRESS_MS
        )
    )
    print("SW={} ({}).".format(pin.value(), "НАЖАТА — проверьте провода!" if pin.value() == 0 else "отпущена, так и надо"))

    t_start = time.ticks_ms()
    t_press = t_start
    reported_bounces = 0
    while True:
        now = time.ticks_ms()
        ev = b.update(pin.value() == 0, now)  # подтяжка вверх: нажата = 0
        if ev:
            t = time.ticks_diff(now, t_start) / 1000
            if ev == button.PRESS:
                t_press = now
                print("{:8.2f} с  {}".format(t, NAMES[ev]))
            else:
                print("{:8.2f} с  {}  (от начала серии {} мс)".format(t, NAMES[ev], time.ticks_diff(now, t_press)))
        if b.idle and b.bounces != reported_bounces:
            print("           дребезг: отфильтровано {} лишних переключений".format(b.bounces - reported_bounces))
            reported_bounces = b.bounces
        time.sleep_ms(config.BUTTON_POLL_MS)


main()
