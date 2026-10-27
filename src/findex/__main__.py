"""python -m findex ... — те саме, що команда `findex`."""

from findex.cli import app

# Під `spawn` (Windows, macOS) воркер ProcessPoolExecutor імпортує головний модуль заново.
# multiprocessing пропускає модулі з іменем *.__main__, але guard робить це явним і не
# залежить від цієї деталі: запуск CLI — лише коли модуль справді головний.
if __name__ == "__main__":
    app(prog_name="findex")
