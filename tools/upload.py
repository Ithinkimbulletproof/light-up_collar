"""Скопировать модули прошивки (firmware/*.py) в корень файловой системы платы.

Примеры:
  .venv\\Scripts\\python tools\\upload.py COM5          модули для скриптов проверки (без main.py)
  .venv\\Scripts\\python tools\\upload.py COM5 --main   вместе с main.py — прошивка будет запускаться сама

Скрипты проверки (firmware/bringup) импортируют эти модули, поэтому после изменения
любого файла в firmware/ копирование надо повторить. Неизменённые файлы mpremote пропускает.
Папки bringup/ и tests/ на плату не копируются: скрипты проверки запускаются с ПК через `mpremote run`.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRMWARE = os.path.join(ROOT, "firmware")
MPREMOTE = os.path.join(os.path.dirname(sys.executable), "mpremote")


def main():
    args = sys.argv[1:]
    with_main = "--main" in args
    args = [a for a in args if a != "--main"]
    if len(args) != 1:
        print(__doc__)
        return 2
    port = args[0]
    files = sorted(
        os.path.join("firmware", f)
        for f in os.listdir(FIRMWARE)
        if f.endswith(".py") and (with_main or f != "main.py")
    )
    print("Копирую на {}: {}".format(port, ", ".join(os.path.basename(f) for f in files)))
    return subprocess.call([MPREMOTE, "connect", port, "cp", *files, ":"], cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
