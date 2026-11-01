# Настройки: номер цвета в палитре и уровень яркости — с сохранением во флеш.
#
# Запись отложенная (через SAVE_DELAY_MS после последнего изменения — при быстром перелистывании
# флеш не изнашивается) и атомарная (временный файл + переименование: выключение питания
# посреди записи не портит настройки). Каждое поле при загрузке проверяется отдельно.
# Модуль не трогает железо, кроме файловой системы, — тесты на ПК пишут во временную папку.

import json
import os

import config

try:
    from time import ticks_diff
except ImportError:  # CPython: тесты на ПК

    def ticks_diff(a, b):
        return a - b


# На ПК (Windows) os.rename не перезаписывает файл, нужен os.replace; в MicroPython rename перезаписывает.
_replace = getattr(os, "replace", os.rename)


def _int_in(value, lo, hi):
    # bool в Python — тоже int, а true/false в файле не должны превращаться в 1/0
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value < hi


class Settings:
    def __init__(self, path=config.SETTINGS_FILE):
        self.path = path
        self.palette = config.DEFAULT_PALETTE_INDEX
        self.level = config.BRIGHTNESS_DEFAULT_INDEX  # номер уровня в BRIGHTNESS_LEVELS
        self._dirty_at = None

    @property
    def color(self):
        return config.PALETTE[self.palette]

    @property
    def brightness(self):
        """Яркость, %."""
        return config.BRIGHTNESS_LEVELS[self.level]

    # --- загрузка и сохранение ---

    def load(self):
        """Прочитать файл. Возвращает список полей, взятых по умолчанию (для журнала)."""
        defaulted = ["palette", "level"]
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return defaulted
        if not isinstance(data, dict):
            return defaulted
        p = data.get("palette")
        if _int_in(p, 0, len(config.PALETTE)):
            self.palette = p
            defaulted.remove("palette")
        lv = data.get("level")
        if _int_in(lv, 0, len(config.BRIGHTNESS_LEVELS)):
            self.level = lv
            defaulted.remove("level")
        return defaulted

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"palette": self.palette, "level": self.level}, f)
        _replace(tmp, self.path)
        self._dirty_at = None

    @property
    def dirty(self):
        return self._dirty_at is not None

    def maybe_save(self, now, allowed=True):
        """Сохранить, если прошло SAVE_DELAY_MS после последнего изменения и запись разрешена.

        Если запись запрещена (низкое напряжение) или не удалась (ошибка файловой системы),
        изменение ждёт в памяти — ошейник продолжает светить. Возвращает True, если записали.
        """
        if self._dirty_at is None or not allowed:
            return False
        if ticks_diff(now, self._dirty_at) < config.SAVE_DELAY_MS:
            return False
        try:
            self.save()
        except OSError as e:
            print("не удалось сохранить настройки:", e)
            self._dirty_at = now  # следующая попытка — не раньше чем через SAVE_DELAY_MS
            return False
        return True

    def next_palette(self, now):
        """Одиночное нажатие: следующий цвет палитры, после последнего — первый."""
        self.palette = (self.palette + 1) % len(config.PALETTE)
        self._dirty_at = now
        return self.color

    def next_level(self, now):
        """Двойное нажатие: следующий уровень яркости, после максимума — минимум. Возвращает номер уровня."""
        self.level = (self.level + 1) % len(config.BRIGHTNESS_LEVELS)
        self._dirty_at = now
        return self.level
