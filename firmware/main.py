# Прошивка ошейника: запускается сама при включении платы.
#
# Здесь только работа с железом и расписание; вся логика — в app.py, leds.py, battery.py,
# и она проверена тестами на ПК. Один простой цикл без asyncio:
#   кнопка — каждые ~5 мс, кадр — каждые 25 мс, батарея — каждые 250 мс, строка состояния — раз в 5 с.
#
# Безопасный старт: если при включении зажата кнопка, прошивка не запускается
# и плата остаётся в REPL (пригодится, если прошивка мешает подключиться).

from machine import Pin

import config

# Первым делом — линия данных ленты в 0: пока остальное грузится, лента не должна ловить помехи.
_data_pin = Pin(config.PIN_LED_DATA, Pin.OUT, value=0)

import time  # noqa: E402

from machine import ADC  # noqa: E402
from neopixel import NeoPixel  # noqa: E402

import app  # noqa: E402
import battery  # noqa: E402
import button  # noqa: E402
import leds  # noqa: E402
import settings  # noqa: E402

FRAME_MS = 1000 // config.FPS
BATTERY_PERIOD_MS = 250
LOG_PERIOD_MS = 5000
SOURCES = {battery.SOURCE_BATTERY: "батарея", battery.SOURCE_USB: "USB", battery.SOURCE_NONE: "нет питания ленты"}


def read_mv(adc):
    """Среднее из ADC_SAMPLES замеров, мВ на узле питания; None, если АЦП не ответил."""
    total = 0
    n = 0
    for _ in range(config.ADC_SAMPLES):
        try:
            total += adc.read_uv()
            n += 1
        except OSError:  # на ESP32 ошибка чтения АЦП — исключение; один сбой не повод падать
            pass
    return battery.adc_uv_to_mv(total // n) if n else None


def safe_boot(btn):
    if btn.value():
        return False
    time.sleep_ms(50)  # не путать с дребезгом
    return btn.value() == 0


def status_line(s, mon, cap, out, worst_frame_us):
    flags = []
    if mon.low:
        flags.append("НИЗКИЙ ЗАРЯД")
    if mon.empty:
        flags.append("РАЗРЯЖЕН")
    if cap.limited:
        flags.append("потолок снижен")
    return "{} | V {} мВ, без нагрузки {} мВ, {}% | потолок {} мА, ток ~{} мА | цвет {}, яркость {}% | кадр до {} мкс{}".format(
        SOURCES[mon.source],
        mon.v_loaded,
        mon.v_rest,
        mon.percent,
        cap.cap,
        out.est_ma,
        config.PALETTE_NAMES[s.palette],
        s.brightness,
        worst_frame_us,
        " | " + ", ".join(flags) if flags else "",
    )


def run():
    btn = Pin(config.PIN_BUTTON, Pin.IN, Pin.PULL_UP)
    if safe_boot(btn):
        print("Безопасный старт: кнопка зажата при включении — прошивка не запущена, REPL свободен.")
        return

    np = NeoPixel(_data_pin, config.NUM_LEDS)
    out = leds.Output(np=np)
    s = settings.Settings()
    defaulted = s.load()
    mon = battery.Monitor()
    cap = battery.CapController()
    a = app.App(s, mon, cap)
    b = button.Button()
    adc = ADC(Pin(config.PIN_BATTERY_ADC), atten=ADC.ATTN_11DB)
    print(
        "Ошейник запущен: цвет {}, яркость {}%{}".format(
            config.PALETTE_NAMES[s.palette], s.brightness, " (по умолчанию: {})".format(", ".join(defaulted)) if defaulted else ""
        )
    )

    now = time.ticks_ms()
    t_frame = now
    t_batt = time.ticks_add(now, -BATTERY_PERIOD_MS)  # первый замер — сразу
    t_log = now
    worst_frame_us = 0
    strip_was_on = False
    try:
        while True:
            now = time.ticks_ms()

            ev = b.update(btn.value() == 0, now)  # подтяжка вверх: нажата = 0
            if ev:
                a.on_button(ev, now)

            if time.ticks_diff(now, t_batt) >= BATTERY_PERIOD_MS:
                t_batt = now
                mv = read_mv(adc)
                if mv is not None:
                    mon.update(mv, out.est_ma, now)
                    cap.update(mon, now)
                if s.maybe_save(now, mon.flash_write_allowed()):
                    print("сохранено: цвет {}, яркость {}%".format(config.PALETTE_NAMES[s.palette], s.brightness))

            dt = time.ticks_diff(now, t_frame)
            if dt >= FRAME_MS:
                t_frame = now
                t0 = time.ticks_us()
                frame, bright, gain = a.render(now)
                out.step(frame, bright, gain, cap.cap, min(dt, 100), a.ref_ma)
                if a.strip_powered:
                    out.write()
                    strip_was_on = True
                elif strip_was_on:
                    # Питание ленты пропало: данные не шлём, линия остаётся в 0 после последнего кадра.
                    out.blank()
                    strip_was_on = False
                worst_frame_us = max(worst_frame_us, time.ticks_diff(time.ticks_us(), t0))

            if time.ticks_diff(now, t_log) >= LOG_PERIOD_MS:
                t_log = now
                print(status_line(s, mon, cap, out, worst_frame_us))
                worst_frame_us = 0

            time.sleep_ms(config.BUTTON_POLL_MS)
    finally:
        # Ctrl-C из mpremote или ошибка в коде: погасить ленту, если у неё есть питание.
        if a.strip_powered:
            np.fill((0, 0, 0))
            np.write()


run()
