# Проверка 3: замер напряжения (делитель 100к/100к на GPIO3).
#
# Запуск (модули на плате — tools\upload.py, см. docs/setup.md, шаг 3):
#   .venv\Scripts\mpremote connect COM5 run firmware/bringup/03_battery.py
#
# Раз в секунду печатает: сырой АЦП (0–4095), мкВ на входе АЦП, напряжение на узле питания,
# разброс замеров, проценты и что это за источник (батарея / USB / ничего).
#
# Что должно получиться:
#   - от USB (этап без батареи, верх делителя на 5V): ~4600–5100 мВ, источник «USB»;
#   - от батареи: 3300–4200 мВ, источник «батарея», проценты похожи на правду;
#   - разброс внутри секунды — десятки мВ, не сотни (если сотни — конденсатор 100 нФ с GPIO3 на землю);
#   - показания совпадают с мультиметром на узле питания ±50 мВ после калибровки.
# Калибровка: ADC_CORRECTION в config.py = (мВ по мультиметру) / (мВ здесь) × текущий ADC_CORRECTION.
#   От USB калибровка только предварительная (4,7 В — у края диапазона АЦП), окончательная — от батареи.
#
# Остановка: Ctrl-C закрывает mpremote; скрипт на плате прервёт следующая команда mpremote или RESET.

import time

from machine import ADC, Pin

import battery
import config

SOURCES = {battery.SOURCE_BATTERY: "батарея", battery.SOURCE_USB: "USB", battery.SOURCE_NONE: "НИЧЕГО (делитель не подключён?)"}


def main():
    # 11 дБ — максимальная аттенюация, на ESP32-C3 это диапазон 0–2,5 В на входе АЦП.
    adc = ADC(Pin(config.PIN_BATTERY_ADC), atten=ADC.ATTN_11DB)
    print("АЦП на GPIO{}, делитель ×{}, поправка {}".format(config.PIN_BATTERY_ADC, config.ADC_DIVIDER, config.ADC_CORRECTION))
    print("   сырой     мкВ АЦП   узел, мВ   разброс, мВ   %   источник")
    while True:
        raws = []
        uvs = []
        for _ in range(config.ADC_SAMPLES):
            try:
                raws.append(adc.read())
                uvs.append(adc.read_uv())
            except OSError as e:  # на ESP32 ошибка чтения АЦП — исключение, а не мусор
                print("Ошибка чтения АЦП:", e)
            time.sleep_ms(1000 // config.ADC_SAMPLES // 2)
        if uvs:
            uv = sum(uvs) // len(uvs)
            mv = battery.adc_uv_to_mv(uv)
            spread = battery.adc_uv_to_mv(max(uvs)) - battery.adc_uv_to_mv(min(uvs))
            src = battery.source(mv)
            pct = "{:3d}".format(battery.percent(mv)) if src == battery.SOURCE_BATTERY else "  -"
            print("{:8d}  {:10d}  {:9d}  {:12d}  {}   {}".format(sum(raws) // len(raws), uv, mv, spread, pct, SOURCES[src]))
        time.sleep_ms(500)


main()
