"""兼容入口：旧脚本路径已迁移至 Tools/importData。

为避免现有命令中断，保留该文件并提示使用新路径：
`python Tools/importData/import_case_triples_to_neo4j.py`
"""

from pathlib import Path
import runpy


TARGET_SCRIPT = Path(__file__).resolve().parents[1] / "Tools" / "importData" / "import_case_triples_to_neo4j.py"


if __name__ == "__main__":
    runpy.run_path(str(TARGET_SCRIPT), run_name="__main__")