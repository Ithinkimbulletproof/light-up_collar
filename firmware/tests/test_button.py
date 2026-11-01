# Проверка автомата кнопки на сценариях нажатий, без железа.
import pytest

import config
from button import DOUBLE, LONG, PRESS, SINGLE, Button

STEP = config.BUTTON_POLL_MS
W = config.DOUBLE_PRESS_MS


def run(script, bounce_ms=0, tail_ms=2500):
    """script — список (нажата, сколько мс). Возвращает [(время, событие)].

    bounce_ms — в начале каждого участка уровень «дребезжит» столько миллисекунд.
    """
    b = Button()
    t = 0
    events = []
    for pressed, duration in list(script) + [(False, tail_ms)]:
        start = t
        while t < start + duration:
            level = pressed
            if bounce_ms and t - start < bounce_ms:
                level = pressed if ((t - start) // STEP) % 2 else not pressed
            ev = b.update(level, t)
            if ev:
                events.append((t, ev))
            t += STEP
    return events


def kinds(events):
    return [e for _, e in events]


def test_single_after_window():
    ev = run([(True, 100)])
    assert kinds(ev) == [PRESS, SINGLE]
    assert ev[1][0] > 100 + W  # одиночное — только когда окно двойного закрылось


def test_double_fires_on_second_release():
    ev = run([(True, 100), (False, 150), (True, 100)])
    assert kinds(ev) == [PRESS, DOUBLE]
    assert ev[1][0] == 100 + 150 + 100 + config.DEBOUNCE_MS


def test_two_singles_when_pause_is_long():
    assert kinds(run([(True, 100), (False, W + 100), (True, 100)])) == [PRESS, SINGLE, PRESS, SINGLE]


def test_gap_just_inside_window_is_double():
    assert kinds(run([(True, 100), (False, W - 10), (True, 100)])) == [PRESS, DOUBLE]


def test_gap_just_outside_window_is_two_singles():
    assert kinds(run([(True, 100), (False, W + 20), (True, 100)])) == [PRESS, SINGLE, PRESS, SINGLE]


def test_long_fires_while_held():
    ev = run([(True, config.LONG_PRESS_MS + 500)])
    assert kinds(ev) == [PRESS, LONG]
    assert ev[1][0] == config.LONG_PRESS_MS


def test_release_after_long_does_nothing():
    assert kinds(run([(True, 4000)])) == [PRESS, LONG]


def test_press_just_under_long_is_single():
    assert kinds(run([(True, config.LONG_PRESS_MS - 100)])) == [PRESS, SINGLE]
    assert kinds(run([(True, config.LONG_PRESS_MS - 25)])) == [PRESS, SINGLE]  # отпускание ещё в антидребезге


def test_hold_is_never_part_of_double():
    ev = run([(True, 100), (False, 150), (True, config.LONG_PRESS_MS + 200)])
    assert kinds(ev) == [PRESS, LONG]


def test_third_press_inside_window_ignored():
    ev = run([(True, 80), (False, 120), (True, 80), (False, 120), (True, 80)])
    assert kinds(ev) == [PRESS, DOUBLE]


def test_press_after_window_works_again():
    ev = run([(True, 80), (False, 120), (True, 80), (False, W + 50), (True, 80)])
    assert kinds(ev) == [PRESS, DOUBLE, PRESS, SINGLE]


def test_held_third_press_ignored_too():
    ev = run([(True, 80), (False, 120), (True, 80), (False, 120), (True, config.LONG_PRESS_MS + 300)])
    assert kinds(ev) == [PRESS, DOUBLE]


@pytest.mark.parametrize("bounce", [5, 15, 25])
def test_bounce_does_not_create_extra_events(bounce):
    assert kinds(run([(True, 200)], bounce_ms=bounce)) == [PRESS, SINGLE]
    assert kinds(run([(True, 100), (False, 150), (True, 100)], bounce_ms=bounce)) == [PRESS, DOUBLE]
    assert kinds(run([(True, config.LONG_PRESS_MS + 300)], bounce_ms=bounce)) == [PRESS, LONG]


def test_glitch_shorter_than_debounce_is_ignored():
    assert kinds(run([(False, 100), (True, 10)])) == []


def test_bounces_are_counted():
    b = Button()
    t = 0
    for level in (True, False, True, False, True):
        b.update(level, t)
        t += STEP
    assert b.bounces >= 2


@pytest.mark.parametrize(
    "before_wrap, script, expected",
    [
        (500, [(True, 2500)], [PRESS, LONG]),  # переполнение посреди удержания
        (200, [(True, 100)], [PRESS, SINGLE]),  # переполнение посреди окна двойного
        (15, [(True, 100)], [PRESS, SINGLE]),  # переполнение посреди антидребезга
        (300, [(True, 100), (False, 150), (True, 100)], [PRESS, DOUBLE]),
    ],
)
def test_ticks_wraparound(before_wrap, script, expected):
    # На плате ticks_ms переполняется; автомат должен сравнивать время только через ticks_diff.
    import button

    period = 1 << 30
    orig = button.ticks_diff
    button.ticks_diff = lambda a, b: ((a - b + period // 2) % period) - period // 2
    try:
        b = Button()
        t0 = period - before_wrap
        events = []
        i = 0
        for pressed, duration in list(script) + [(False, 2000)]:
            end = i + duration
            while i < end:
                ev = b.update(pressed, (t0 + i) % period)
                if ev:
                    events.append(ev)
                i += STEP
        assert events == expected
    finally:
        button.ticks_diff = orig
