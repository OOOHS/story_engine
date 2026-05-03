import logging
import sys
from pathlib import Path
from typing import Optional

def setup_logger(name: str = "story_engine", log_level: int = logging.INFO, log_file: Optional[str] = None) -> logging.Logger:
    """
    Configures and returns a logger instance.
    
    Args:
        name: Name of the logger.
        log_level: Logging level (default: logging.INFO).
        log_file: Optional path to a log file. If provided, logs will be written to this file.
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    
    # Check if handlers already exist to avoid duplicate logs
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console Handler - Set level to WARNING to keep stdout clean for game UI
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING) 
    logger.addHandler(console_handler)

    # File Handler (Optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

# Default global logger
logger = setup_logger()
