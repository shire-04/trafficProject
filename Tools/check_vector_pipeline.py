from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vectorDB import ChromaDBVectorStore, TextFileLoader  # noqa: E402


def main() -> None:
    data_raw_path = PROJECT_ROOT / "data_raw"
    temp_db_path = PROJECT_ROOT / "chroma_data" / "test_rebuild"
    sample_file = data_raw_path / "国家交通应急预案.txt"

    chunks = TextFileLoader.load_text_file(str(sample_file), chunk_size=300, semantic_chunking=True)
    print(f"loaded_chunks={len(chunks)}")
    if chunks:
        print(f"sample_metadata={chunks[0].get('metadata')}")

    store = ChromaDBVectorStore(db_path=str(temp_db_path), collection_name="traffic_documents_test")
    stats = store.offline_ingest(
        source_directory=str(data_raw_path),
        chunk_size=300,
        file_patterns=['国家交通应急预案.txt'],
        semantic_chunking=True,
    )
    print(f"stats={stats}")

    results = store.search_evidence("特别重大交通事件预警", n_results=2)
    print(f"result_count={len(results)}")
    if results:
        print(f"first_result_keys={sorted(results[0].keys())}")
        print(f"first_result_metadata={{'accident_type': '{results[0]['accident_type']}', 'weather': '{results[0]['weather']}', 'severity': '{results[0]['severity']}'}}")

    severity_filter = results[0]['severity'] if results else "特别重大"
    filtered_results = store.search_evidence(
        "特别重大交通事件预警",
        n_results=2,
        severity=severity_filter,
    )
    print(f"filtered_severity={severity_filter}")
    print(f"filtered_result_count={len(filtered_results)}")
    if filtered_results:
        print(f"filtered_first_severity={filtered_results[0]['severity']}")


if __name__ == "__main__":
    main()