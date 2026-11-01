# Настройки: проверка полей, цвет и яркость, отложенная атомарная запись.
import json

import pytest

import config
from settings import Settings

LEVELS = len(config.BRIGHTNESS_LEVELS)


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "settings.json")


def write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


def test_defaults_when_no_file(path):
    s = Settings(path)
    assert set(s.load()) == {"palette", "level"}
    assert s.palette == config.DEFAULT_PALETTE_INDEX and s.color == config.PALETTE[0]
    assert s.brightness == config.BRIGHTNESS_LEVELS[config.BRIGHTNESS_DEFAULT_INDEX]


@pytest.mark.parametrize("content", ["", "{", "[]", "null", "{}"])
def test_bad_file_gives_defaults(path, content):
    write(path, content)
    s = Settings(path)
    assert set(s.load()) == {"palette", "level"}
    assert (s.palette, s.level) == (config.DEFAULT_PALETTE_INDEX, config.BRIGHTNESS_DEFAULT_INDEX)


@pytest.mark.parametrize(
    "data, defaulted, palette, level",
    [
        ({"palette": 3, "level": 9}, {"level"}, 3, config.BRIGHTNESS_DEFAULT_INDEX),
        ({"palette": 8, "level": 0}, {"palette"}, config.DEFAULT_PALETTE_INDEX, 0),
        ({"palette": True, "level": 1.5}, {"palette", "level"}, 0, config.BRIGHTNESS_DEFAULT_INDEX),  # true — не номер
        ({"palette": 5}, {"level"}, 5, config.BRIGHTNESS_DEFAULT_INDEX),  # файл прошлой версии без яркости
        ({"palette": 5, "mode": 3}, {"level"}, 5, config.BRIGHTNESS_DEFAULT_INDEX),  # лишние поля не мешают
    ],
)
def test_each_field_checked_separately(path, data, defaulted, palette, level):
    write(path, data)
    s = Settings(path)
    assert set(s.load()) == defaulted
    assert (s.palette, s.level) == (palette, level)


def test_roundtrip(path):
    s = Settings(path)
    for _ in range(4):
        s.next_palette(0)
    s.next_level(0)
    s.save()
    t = Settings(path)
    assert t.load() == []
    assert (t.palette, t.color) == (4, config.PALETTE[4])
    assert t.level == (config.BRIGHTNESS_DEFAULT_INDEX + 1) % LEVELS


def test_save_is_atomic_and_leaves_no_tmp(path, tmp_path):
    s = Settings(path)
    s.save()
    s.next_palette(0)
    s.save()  # перезапись существующего файла
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_deferred_save(path):
    s = Settings(path)
    s.next_palette(now=1000)
    assert not s.maybe_save(1000 + config.SAVE_DELAY_MS - 1)
    s.next_level(now=3000)  # ещё одно изменение — отсчёт заново
    assert not s.maybe_save(1000 + config.SAVE_DELAY_MS + 100)
    assert s.maybe_save(3000 + config.SAVE_DELAY_MS)
    assert not s.dirty
    assert not s.maybe_save(3000 + config.SAVE_DELAY_MS * 5)  # без изменений не пишем


def test_save_postponed_while_not_allowed(path):
    s = Settings(path)
    s.next_palette(0)
    assert not s.maybe_save(config.SAVE_DELAY_MS * 3, allowed=False)
    assert s.dirty  # изменение ждёт
    assert s.maybe_save(config.SAVE_DELAY_MS * 4, allowed=True)


def test_palette_cycle_and_wrap(path):
    s = Settings(path)
    seen = [s.next_palette(0) for _ in range(len(config.PALETTE))]
    assert seen == list(config.PALETTE[1:]) + [config.PALETTE[0]]


def test_level_cycles_after_max_to_min(path):
    s = Settings(path)
    assert s.level == config.BRIGHTNESS_DEFAULT_INDEX == LEVELS - 1  # по умолчанию — максимум
    assert s.next_level(0) == 0  # после максимума — минимум
    assert s.brightness == config.BRIGHTNESS_LEVELS[0]
    assert [s.next_level(0) for _ in range(LEVELS)] == list(range(1, LEVELS)) + [0]
    assert s.dirty


def test_flash_error_does_not_crash_and_retries_later(path, monkeypatch):
    import settings as settings_mod

    s = Settings(path)
    s.next_palette(0)

    def broken(src, dst):
        raise OSError(28, "ENOSPC")

    monkeypatch.setattr(settings_mod, "_replace", broken)
    assert not s.maybe_save(config.SAVE_DELAY_MS)  # не падает
    assert s.dirty  # изменение ждёт
    assert not s.maybe_save(config.SAVE_DELAY_MS + 100)  # не долбит флеш каждый цикл
    monkeypatch.setattr(settings_mod, "_replace", __import__("os").replace)
    assert s.maybe_save(config.SAVE_DELAY_MS * 2 + 10)
