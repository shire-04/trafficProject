from pathlib import Path
import runpy


TARGET_SCRIPT = Path(__file__).resolve().parent / "importData" / "import_national_plan_to_neo4j.py"


if __name__ == "__main__":
    runpy.run_path(str(TARGET_SCRIPT), run_name="__main__")
