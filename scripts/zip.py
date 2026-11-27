"""zip -j without the zip tool: deflated, file names only. Usage: zip.py out.zip file..."""

import sys
import zipfile
from pathlib import Path

with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    for name in sys.argv[2:]:
        z.write(name, Path(name).name)
