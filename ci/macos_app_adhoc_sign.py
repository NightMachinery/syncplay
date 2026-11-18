import re
import subprocess

from py2app.util import codesign_adhoc, iter_platform_files

APPDIR = 'dist/SyncPlayU.app'
SYSTEM_PATHS = ("/usr/lib", "/System/Library")
RPATH = re.compile(r"^\s*cmd LC_RPATH\n\s*cmdsize \d+\n\s*path (.+) \(offset ", re.MULTILINE)

# A framework Python can leave the build machine's library directory in its
# search paths after py2app copies and relinks the bundled dependencies.
for filename in iter_platform_files(APPDIR):
    load_commands = subprocess.check_output(["otool", "-l", filename], text=True)
    for path in sorted(set(RPATH.findall(load_commands))):
        if path.startswith("/") and not any(path == root or path.startswith(root + "/") for root in SYSTEM_PATHS):
            subprocess.check_call(["install_name_tool", "-delete_rpath", path, filename])

codesign_adhoc(APPDIR)
