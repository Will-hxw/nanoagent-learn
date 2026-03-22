import os
import shutil

if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("build"):
    shutil.rmtree("build")

os.system("pyinstaller --onefile --optimize=2 --clean --name Agent agent.py")

# Copy config.yaml to dist/ so the exe can find it at runtime
if os.path.exists("config.yaml"):
    shutil.copy2("config.yaml", "dist/config.yaml")
    print("config.yaml -> dist/config.yaml")
