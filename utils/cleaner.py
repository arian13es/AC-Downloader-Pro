import shutil
from pathlib import Path
from utils.logger import logger

class WorkspaceCleaner:
    @staticmethod
    def clean_directory(dir_path: Path, preserve_dir: bool = True) -> None:
        """Removes all files and subdirectories inside a directory."""
        if not dir_path.exists():
            return
        for item in dir_path.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Could not delete {item}: {e}")
        if not preserve_dir:
            try:
                dir_path.rmdir()
            except Exception:
                pass

    @staticmethod
    def remove_path(path: Path) -> None:
        """Removes a single file or directory recursively."""
        if not path.exists():
            return
        try:
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error removing {path}: {e}")
