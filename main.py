from pathlib import Path

from birthday_bot.bot import run
from birthday_bot.config import Settings


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent
    settings = Settings.from_env(base_dir)
    run(settings)
