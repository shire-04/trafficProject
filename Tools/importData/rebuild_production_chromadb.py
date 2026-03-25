from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vectorDB import (  # noqa: E402
    ChromaDBVectorStore,
    PRODUCTION_COLLECTION_NAME,
)


TEXT_SOURCE_FILES = [
    "国家交通应急预案.txt",
]


def resolve_file_list(args: list[str]) -> list[str]:
    custom_files = [str(value or "").strip() for value in args if str(value or "").strip()]
    return custom_files or list(TEXT_SOURCE_FILES)


def main() -> None:
    data_raw_path = PROJECT_ROOT / "data_raw"
    db_path = PROJECT_ROOT / "chroma_data"
    text_source_files = resolve_file_list(sys.argv[1:])

    store = ChromaDBVectorStore(
        db_path=str(db_path),
        collection_name=PRODUCTION_COLLECTION_NAME,
    )

    stats = store.offline_ingest(
        source_directory=str(data_raw_path),
        chunk_size=500,
        file_patterns=text_source_files,
        semantic_chunking=True,
    )
    quality_report = store.get_quality_report()

    print(json.dumps({
        'source_files': text_source_files,
        'stats': stats,
        'quality_report': quality_report,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
