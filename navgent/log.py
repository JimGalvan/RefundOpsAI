from datetime import datetime
from pathlib import Path


class Log:
    path = Path(__file__).parent.parent / "logs" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.log"

    @staticmethod
    def write(*parts: object) -> None:
        Log.path.parent.mkdir(exist_ok=True)
        with Log.path.open("a", encoding="utf-8") as file:
            file.write(" ".join(map(str, parts)) + "\n")
