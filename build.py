import os
import shutil

if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("build"):
    shutil.rmtree("build")

os.system("pyinstaller --onefile --optimize=2 --clean --name Agent agent.py")

