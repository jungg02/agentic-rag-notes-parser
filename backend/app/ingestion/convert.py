import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

_LIBREOFFICE_LOCK = threading.Semaphore(1)


class ConversionError(Exception):
    pass


def convert_to_pdf(input_path: Path, output_dir: Path) -> Path:
    """Convert a DOCX/PPTX file to PDF via headless LibreOffice.

    Serialized with a process-wide semaphore because LibreOffice's shared
    user profile lock makes concurrent `soffice` invocations silently fail.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"

    with _LIBREOFFICE_LOCK:
        try:
            subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--norestore",
                    f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(input_path),
                ],
                timeout=120,
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ConversionError(f"LibreOffice conversion failed for {input_path}: {exc}") from exc

    result_path = output_dir / (input_path.stem + ".pdf")
    if not result_path.exists():
        raise ConversionError(f"Expected output {result_path} not found after conversion")
    return result_path
