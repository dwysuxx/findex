"""findex — маленький пошуковий рушій."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("findex")
except PackageNotFoundError:  # запуск із сирців без встановлення
    __version__ = "0.0.0"
