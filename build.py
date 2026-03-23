import os
import shutil
import subprocess
import sys

if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("build"):
    shutil.rmtree("build")

subprocess.check_call([
    sys.executable,
    "-m",
    "PyInstaller",
    "Agent.spec",
    "--clean",
    "--noconfirm",
])
