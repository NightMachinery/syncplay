"""Build the per-user NSIS installer from the same payload as the portable ZIP."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def nsis_path(path):
    return str(path).replace("$", "$$").replace('"', '$\\"')


def build_installer(compiler, payload, installer):
    version = json.loads((payload / "build-info.json").read_text(encoding="utf-8"))
    upstream = version["upstream_version"]
    night = version["night_version"]

    # Preserve the settings INI and any files not shipped with the package.
    files = sorted(path for path in payload.rglob("*") if path.is_file())
    dirs = sorted({parent for path in files for parent in path.parents if payload in parent.parents},
                  key=lambda path: (-len(path.parts), path))
    settings = payload / "syncplay.ini"
    with tempfile.TemporaryDirectory() as scratch:
        manifest = Path(scratch) / "remove-files.nsh"
        commands = [f'Delete "$INSTDIR\\{nsis_path(path.relative_to(payload))}"'
                    for path in files if path != settings]
        commands += [f'RMDir "$INSTDIR\\{nsis_path(path.relative_to(payload))}"' for path in dirs]
        manifest.write_text("\n".join(commands) + "\n", encoding="utf-8")

        subprocess.run([
            compiler,
            "/WX",
            f"/DPAYLOAD={nsis_path(payload)}",
            f"/DINSTALLER={nsis_path(installer)}",
            f"/DFILE_VERSION={upstream}.{night}",
            f"/DDISPLAY_VERSION={upstream} (Night {night})",
            f"/DREMOVE_FILES={nsis_path(manifest)}",
            str(Path(__file__).with_name("syncplayu-windows.nsi")),
        ], check=True)


if __name__ == "__main__":
    makensis, app_dir, output = (Path(arg).resolve() for arg in sys.argv[1:])
    build_installer(makensis, app_dir, output)
