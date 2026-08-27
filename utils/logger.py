import logging
import sys
from pathlib import Path

# ANSI color codes for Windows Terminal / PowerShell / Linux
COLORS = {
    "DEBUG": "\033[36m",    # Cyan
    "INFO": "\033[32m",     # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",    # Red
    "CRITICAL": "\033[35m", # Magenta
    "RESET": "\033[0m"
}

class ColoredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = COLORS.get(record.levelname, COLORS["RESET"])
        reset = COLORS["RESET"]
        time_str = self.formatTime(record, "%H:%M:%S")
        prefix = f"{color}[{time_str}] [{record.levelname:<7}]{reset}"
        return f"{prefix} {record.getMessage()}"

def setup_logger(name: str = "ACDownloader", log_file: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Configures a pure standard library logger with colored terminal output."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(ColoredFormatter())
        logger.addHandler(console_handler)
    
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(h, logging.FileHandler) and h.baseFilename == str(log_file.resolve()) for h in logger.handlers):
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d): %(message)s"
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        
    return logger

logger = setup_logger()
