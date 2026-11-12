"""Анимации ленты для README: настоящая логика прошивки, прогнанная на ПК.

Работают те же модули, что на плате (button, app, leds, battery), в том же порядке, что в main.py:
кнопка каждые 5 мс, батарея раз в 250 мс, кадр раз в 25 мс. Подменено только железо: нажатия
идут по сценарию, батарея — простая модель (напряжение без нагрузки минус ток × R_int), а кадр
вместо ленты рисуется в GIF.

    .venv\\Scripts\\python tools\\render_demo.py

Картинки появятся в docs/images/. Нужен Pillow (есть в tools/requirements.txt).
"""

import os
import sys
import tempfile

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "firmware"))

import app  # noqa: E402
import battery  # noqa: E402
import button  # noqa: E402
import config  # noqa: E402
import leds  # noqa: E402
import settings  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "images")
FRAME_MS = 1000 // config.FPS
GIF_EVERY = 2  # в GIF каждый второй кадр прошивки (50 мс): глазу хватает, а файл вдвое легче
BATTERY_PERIOD_MS = 250
BATTERY_OCV_MV = 3990  # батарея без нагрузки: ~80 %, шкала заряда зелёная

N = config.NUM_LEDS
PITCH = max(20, 640 // N)  # шаг светодиодов на картинке, px: до 32 светодиодов лента занимает ~640 px, дальше — по 20 px на каждый
CHIP = PITCH // 4  # половина размера светодиода
GLOW = PITCH // 2  # радиус свечения
PAD = 28
W = PAD * 2 + PITCH * N
H = 104
STRIP_Y = 40  # центр ленты по вертикали
BG = (14, 16, 19)
BAND = (30, 32, 37)
CHIP_OFF = (44, 46, 52)
TEXT = (186, 191, 198)


def _font(size):
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return None  # без шрифта с кириллицей — без подписей


FONT = _font(14)


def to_screen(v):
    """Яркость светодиода (ШИМ, линейная) → значение для экрана (sRGB, гамма ~2,2)."""
    return round(255 * (v / 255) ** (1 / 2.2))


def draw(rgb_out, pressed, caption):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((PAD - 14, STRIP_Y - CHIP - 8, W - PAD + 14, STRIP_Y + CHIP + 8), radius=CHIP + 8, fill=BAND)

    glow = Image.new("RGB", (W, H), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    colors = []
    for i in range(N):
        c = tuple(to_screen(v) for v in rgb_out[3 * i : 3 * i + 3])
        colors.append(c)
        x = PAD + PITCH * i + PITCH // 2
        if any(c):
            g.ellipse((x - GLOW, STRIP_Y - GLOW, x + GLOW, STRIP_Y + GLOW), fill=tuple(v * 3 // 4 for v in c))
    img = ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(9)))

    d = ImageDraw.Draw(img)
    for i, c in enumerate(colors):
        x = PAD + PITCH * i + PITCH // 2
        chip = tuple(max(a, b) for a, b in zip(c, CHIP_OFF))
        d.rounded_rectangle((x - CHIP, STRIP_Y - CHIP, x + CHIP, STRIP_Y + CHIP), radius=2, fill=chip)
        if any(c):
            lum = max(c) / 255
            core = tuple(round(v + (255 - v) * 0.55 * lum) for v in c)
            d.rounded_rectangle((x - CHIP // 2, STRIP_Y - CHIP // 2, x + CHIP // 2, STRIP_Y + CHIP // 2), radius=1, fill=core)

    # Кнопка и подпись под лентой.
    bx, by = PAD + 6, H - 24
    d.ellipse((bx - 7, by - 7, bx + 7, by + 7), outline=TEXT, width=2, fill=TEXT if pressed else BG)
    if FONT and caption:
        d.text((bx + 16, by), caption, font=FONT, fill=TEXT, anchor="lm")
    return img


def simulate(name, presses, captions, duration_ms, skip_ms=0, palette=0, level=None):
    """presses — [(начало, длительность)] нажатий кнопки, мс от момента, когда прошивка готова светить.
    captions — [(с какого мс, текст)]. Первые skip_ms считаются, но в GIF не попадают."""
    s = settings.Settings(os.path.join(tempfile.gettempdir(), "collar_demo_settings.json"))  # во флеш не пишем
    s.palette = palette
    if level is not None:
        s.level = level
    mon = battery.Monitor()
    cap = battery.CapController()
    a = app.App(s, mon, cap)
    b = button.Button()
    out = leds.Output()

    frames = []
    now = 0
    t_frame = 0
    t_batt = -BATTERY_PERIOD_MS
    t_ready = None  # на плате это первые ~2 с после включения: замеры батареи при тёмной ленте
    frame_no = 0
    while t_ready is None or now - t_ready < duration_ms:
        rel = None if t_ready is None else now - t_ready
        pressed = rel is not None and any(t0 <= rel < t0 + length for t0, length in presses)

        ev = b.update(pressed, now)
        if ev:
            a.on_button(ev, now)

        if now - t_batt >= BATTERY_PERIOD_MS:
            t_batt = now
            mon.update(BATTERY_OCV_MV - out.est_ma * config.R_INT_MOHM // 1000, out.est_ma, now)
            cap.update(mon, now)
            if t_ready is None and mon.ready:
                t_ready = now

        if now - t_frame >= FRAME_MS:
            dt = now - t_frame
            t_frame = now
            frame, bright, gain = a.render(now)
            rgb = out.step(frame, bright, gain, cap.cap, dt, a.ref_ma)
            frame_no += 1
            if rel is not None and rel >= skip_ms and frame_no % GIF_EVERY == 0:
                caption = ""
                for t0, text in captions:
                    if rel >= t0:
                        caption = text
                frames.append(draw(rgb, b.pressed, caption))

        now += config.BUTTON_POLL_MS

    path = os.path.join(OUT_DIR, name)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=FRAME_MS * GIF_EVERY, loop=0, optimize=True)
    print("{}: {} кадров, {} КБ".format(path, len(frames), os.path.getsize(path) // 1024))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    tap = 110  # короткое нажатие, мс
    gap = 120  # пауза между нажатиями двойного

    # Включение и смена цвета одиночными нажатиями.
    singles = [2200, 3700, 5200]
    simulate(
        "colors.gif",
        [(t, tap) for t in singles],
        [(0, "включение: вся лента разом, плавно")] + [(t, "одно нажатие: следующий цвет") for t in singles],
        6900,
    )

    # Яркость двойными нажатиями: 100 → 10 → 35 → 100 %, лента мигает номером уровня.
    doubles = [2000, 4300, 7200]
    names = ["яркость 10 %, одно мигание", "яркость 35 %, два мигания", "яркость 100 %, три мигания"]
    simulate(
        "brightness.gif",
        [p for t in doubles for p in ((t, tap), (t + tap + gap, tap))],
        [(1700, "ровный свет, яркость 100 %")] + [(t, "двойное нажатие: " + n) for t, n in zip(doubles, names)],
        10_200,
        skip_ms=1700,
        palette=4,
    )

    # Заряд: удержание 1,5 с, показ 5 с, нажатие во время показа его убирает.
    hold_at = 2200
    dismiss_at = hold_at + config.LONG_PRESS_MS + 3200
    simulate(
        "charge.gif",
        [(hold_at, config.LONG_PRESS_MS + 300), (dismiss_at, tap)],
        [
            (1700, "ровный свет"),
            (hold_at, "удержание 1,5 с: показ заряда"),
            (hold_at + config.LONG_PRESS_MS, "заряд ~80 %: горит 80 % ленты, зелёным"),
            (dismiss_at, "нажатие во время показа: убрать показ"),
        ],
        dismiss_at + 1300,
        skip_ms=1700,
        palette=6,
    )


if __name__ == "__main__":
    main()
