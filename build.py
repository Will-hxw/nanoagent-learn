import os
import shutil

if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("build"):
    shutil.rmtree("build")

os.system("pyinstaller --onefile --optimize=2 --clean --add-data \"config.yaml;.\" --hidden-import rich --hidden-import rich.console --hidden-import rich.markdown --hidden-import rich.theme --hidden-import rich.highlighter --exclude-module setuptools._vendor.packaging.licenses --exclude-module charset_normalizer.md__mypyc --name Agent agent.py")
