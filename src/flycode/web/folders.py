"""Run native folder dialogs on the main thread of a separate process."""

import json
import subprocess
import sys
from pathlib import Path


def choose_folder(mode: str, name: str, initial_dir: Path) -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), mode, name, str(initial_dir)],
            capture_output=True, text=True, check=True, timeout=300,
        )
        selected = json.loads(result.stdout)
        if selected is not None and not isinstance(selected, str):
            raise ValueError("Invalid folder selection")
        return selected
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("Не удалось открыть системное окно. Введите путь к папке вручную.") from exc


def main() -> None:
    import tkinter as tk
    from tkinter import filedialog

    mode, name, initial_dir = sys.argv[1:]
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if mode == "create":
            selected = filedialog.asksaveasfilename(
                parent=root, title="Новая папка проекта", initialdir=initial_dir,
                initialfile=Path(name).name or "Новый проект",
            )
        else:
            selected = filedialog.askdirectory(
                parent=root, title="Выберите папку проекта", initialdir=initial_dir, mustexist=True,
            )
        print(json.dumps(selected or None))
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
