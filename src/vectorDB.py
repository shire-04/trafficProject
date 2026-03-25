"""
ChromaDB Vector Database module for Traffic Emergency Response RAG

This module handles:
1. Loading and chunking text files from data_raw/
2. Embedding text chunks into ChromaDB
3. Providing semantic search interface for LLM RAG (Retrieval-Augmented Generation)

The knowledge graph (Action-Resource triples and event consequences) is managed in Neo4j.
This module focuses exclusively on storing regulatory documents, emergency plans, and case studies
as searchable embeddings for context retrieval during LLM generation.
"""

import os
import hashlib
import re
from pathlib import Path
from typing import List, Dict, Optional, Iterable
import logging
from collections import Counter

import chromadb

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


DEFAULT_COLLECTION_NAME = "traffic_documents"
PRODUCTION_COLLECTION_NAME = "traffic_documents_v2"


class TextFileLoader:
    """Load and chunk text files for vector embedding"""

    @staticmethod
    def extract_metadata(text: str, file_name: str = "") -> Dict[str, str]:
        """构建最小文档 metadata，仅用于定位来源，不做业务语义推断。"""
        return {
            'file_name': file_name or '',
        }
    
    @staticmethod
    def _find_sentence_boundary(text: str, pos: int, direction: str = 'backward') -> int:
        """
        Find the nearest sentence boundary (。！？) from a given position
        
        Args:
            text: Full text
            pos: Current position
            direction: 'backward' to find boundary before pos, 'forward' to find after
            
        Returns:
            Position of sentence boundary, or original pos if not found
        """
        sentence_end_marks = {'。', '！', '？', '\n'}
        
        if direction == 'backward':
            # Search backward for sentence end
            for i in range(pos - 1, max(-1, pos - 100), -1):
                if i >= 0 and text[i] in sentence_end_marks:
                    return i + 1
        else:
            # Search forward for sentence end
            for i in range(pos, min(len(text), pos + 100)):
                if text[i] in sentence_end_marks:
                    return i + 1
        
        return pos

    @staticmethod
    def _is_case_document(file_name: str) -> bool:
        normalized = str(file_name or "").strip()
        if not normalized:
            return False
        return normalized.startswith("交通应急处理案例") and normalized.endswith(".txt")

    @staticmethod
    def _is_document_heading(paragraph: str) -> bool:
        cleaned = str(paragraph or "").strip()
        if not cleaned:
            return False
        if cleaned in {"经典交通应急处置案例", "交通应急处理案例", "经典交通事故案例"}:
            return True
        if re.match(r"^经典交通.*案例", cleaned):
            return True
        if re.match(r"^交通应急处理案例\s*[（(].*[）)]\s*$", cleaned):
            return True
        return False

    @staticmethod
    def _build_case_chunks(content: str, file_name: str, file_path: str) -> List[Dict]:
        raw_blocks = [block.strip() for block in re.split(r"\n\s*\n+", str(content or "")) if block.strip()]
        chunks: List[Dict] = []
        index = 0
        chunk_id = 0

        while index < len(raw_blocks):
            current = raw_blocks[index].strip()
            if TextFileLoader._is_document_heading(current):
                index += 1
                continue

            next_accident = raw_blocks[index + 1].strip() if index + 1 < len(raw_blocks) else ""
            next_consequence = raw_blocks[index + 2].strip() if index + 2 < len(raw_blocks) else ""
            next_measure = raw_blocks[index + 3].strip() if index + 3 < len(raw_blocks) else ""

            if next_accident.startswith("特定事故：") and next_consequence.startswith("后果：") and next_measure.startswith("措施："):
                case_text = "\n\n".join([current, next_accident, next_consequence, next_measure]).strip()
                if case_text:
                    chunks.append({
                        'content': case_text,
                        'chunk_id': chunk_id,
                        'file_name': file_name,
                        'source': file_path,
                        'metadata': TextFileLoader.extract_metadata(case_text, file_name)
                    })
                    chunk_id += 1
                index += 4
                continue

            if current:
                chunks.append({
                    'content': current,
                    'chunk_id': chunk_id,
                    'file_name': file_name,
                    'source': file_path,
                    'metadata': TextFileLoader.extract_metadata(current, file_name)
                })
                chunk_id += 1
            index += 1

        return chunks
    
    @staticmethod
    def load_text_file(file_path: str, chunk_size: int = 500, 
                      semantic_chunking: bool = True) -> List[Dict]:
        """
        Load and chunk a text file with optional semantic-aware chunking
        
        Args:
            file_path: Path to text file
            chunk_size: Target number of characters per chunk (default: 500)
            semantic_chunking: If True, snap chunks to sentence boundaries
                              If False, use simple fixed-size chunking
            
        Returns:
            List of dictionaries with keys: 
                - content: Text chunk content
                - chunk_id: Sequential chunk number
                - file_name: Source file name
                - source: Full source path
        """
        chunks = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read().strip()
            
            if not content:
                logger.warning(f"File is empty: {file_path}")
                return chunks
            
            file_name = os.path.basename(file_path)

            if TextFileLoader._is_case_document(file_name):
                case_chunks = TextFileLoader._build_case_chunks(content, file_name, file_path)
                logger.info(
                    f"Loaded {len(case_chunks)} case-level chunks from {file_name} "
                    f"(total: {len(content)} chars, case_chunking=True)"
                )
                return case_chunks

            chunk_id = 0
            
            if semantic_chunking:
                # Semantic-aware chunking: snap to sentence boundaries
                i = 0
                while i < len(content):
                    # Calculate chunk end position
                    end_pos = min(i + chunk_size, len(content))
                    
                    # If not at end of text, find nearest sentence boundary
                    if end_pos < len(content):
                        # Try to find boundary within reasonable range
                        boundary = TextFileLoader._find_sentence_boundary(
                            content, end_pos, direction='backward'
                        )
                        # Only use boundary if it's not too close to start (min 200 chars)
                        if boundary > i + 200:
                            end_pos = boundary
                        else:
                            # Boundary too close, try forward
                            boundary_forward = TextFileLoader._find_sentence_boundary(
                                content, end_pos, direction='forward'
                            )
                            if boundary_forward < len(content):
                                end_pos = boundary_forward
                    
                    chunk_text = content[i:end_pos].strip()
                    if chunk_text and len(chunk_text) > 50:  # Filter out very small chunks
                        chunks.append({
                            'content': chunk_text,
                            'chunk_id': chunk_id,
                            'file_name': file_name,
                            'source': file_path,
                            'metadata': TextFileLoader.extract_metadata(chunk_text, file_name)
                        })
                        chunk_id += 1
                    
                    i = end_pos
            else:
                # Simple fixed-size chunking (original behavior)
                for i in range(0, len(content), chunk_size):
                    chunk_text = content[i:i+chunk_size].strip()
                    if chunk_text:
                        chunks.append({
                            'content': chunk_text,
                            'chunk_id': chunk_id,
                            'file_name': file_name,
                            'source': file_path,
                            'metadata': TextFileLoader.extract_metadata(chunk_text, file_name)
                        })
                        chunk_id += 1
            
            logger.info(f"Loaded {len(chunks)} chunks from {file_name} (total: {len(content)} chars, semantic_chunking={semantic_chunking})")
        except Exception as e:
            logger.error(f"Error loading text file {file_path}: {e}")
        
        return chunks
    
    @staticmethod
    def load_all_text_files(directory: str, chunk_size: int = 500, 
                            file_patterns: Optional[List[str]] = None,
                            semantic_chunking: bool = True) -> List[Dict]:
        """
        Load and chunk all text files from a directory
        
        Args:
            directory: Directory path containing text files
            chunk_size: Target number of characters per chunk
            file_patterns: List of file patterns to load (e.g., ['*.txt'])
                          If None, loads all .txt files by default
            semantic_chunking: If True, snap chunks to sentence boundaries
            
        Returns:
            Combined list of text chunks from all files
        """
        if file_patterns is None:
            file_patterns = ['*.txt']
        
        all_chunks = []
        directory_path = Path(directory)
        
        for pattern in file_patterns:
            for file_path in directory_path.glob(pattern):
                chunks = TextFileLoader.load_text_file(
                    str(file_path), 
                    chunk_size,
                    semantic_chunking=semantic_chunking
                )
                all_chunks.extend(chunks)
        
        logger.info(f"Total chunks loaded: {len(all_chunks)}")
        return all_chunks


class ChromaDBVectorStore:
    """ChromaDB vector store for RAG context retrieval"""
    
    def __init__(self, db_path: str = "./chroma_data", collection_name: str = DEFAULT_COLLECTION_NAME):
        """
        Initialize ChromaDB vector store for RAG
        
        Args:
            db_path: Path to persist ChromaDB data
            collection_name: Name of the document collection for storing text embeddings
        """
        self.db_path = db_path
        self.collection_name = collection_name
        os.makedirs(db_path, exist_ok=True)
        
        # Initialize ChromaDB client with persistent storage
        # Using the new ChromaDB API (0.4+)
        try:
            # Try new API first (chromadb >= 0.4.0)
            self.client = chromadb.PersistentClient(path=db_path)
        except (AttributeError, TypeError):
            # Fallback to old API for compatibility
            try:
                from chromadb.config import Settings
                settings = Settings(
                    chroma_db_impl="duckdb+parquet",
                    persist_directory=db_path,
                    anonymized_telemetry=False
                )
                self.client = chromadb.Client(settings)
            except Exception as e:
                logger.warning(f"Failed to initialize with Settings, using simple Client: {e}")
                self.client = chromadb.Client()
        
        # Create or get collection for storing regulatory/emergency documents
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={
                "description": "Traffic accident emergency response documents for RAG",
                "language": "Chinese"
            }
        )
        
        self._cached_events: List[str] = []

        logger.info(f"Initialized ChromaDB at {db_path} with collection '{collection_name}'")
    
    def add_text_chunks(self, chunks: List[Dict], overwrite: bool = False) -> int:
        """
        Add text chunks to ChromaDB collection
        
        Args:
            chunks: List of text chunk dictionaries from TextFileLoader
            overwrite: If True, clear collection before adding (default: False for incremental updates)
            
        Returns:
            Number of chunks added
        """
        if not chunks:
            logger.warning("No text chunks to add")
            return 0
        
        if overwrite:
            logger.info("Clearing existing collection...")
            try:
                # Get all existing IDs and delete them
                existing = self.collection.get()
                if existing and existing['ids']:
                    self.collection.delete(ids=existing['ids'])
                    logger.info(f"Deleted {len(existing['ids'])} existing chunks")
            except Exception as e:
                logger.warning(f"Could not clear collection (may be empty): {e}")
        
        ids = []
        documents = []
        metadatas = []
        
        for idx, chunk in enumerate(chunks):
            # Create unique ID from file name and chunk number
            chunk_id = f"{Path(chunk['file_name']).stem}_{chunk['chunk_id']}"
            ids.append(chunk_id)
            documents.append(chunk['content'])
            chunk_metadata = chunk.get('metadata') or {}
            metadatas.append({
                'file_name': chunk['file_name'],
                'chunk_id': str(chunk['chunk_id']),
                'source': chunk.get('source', ''),
                'type': 'document',
            })
        
        # Add to ChromaDB
        self.collection.add(ids=ids, documents=documents, metadatas=metadatas)
        logger.info(f"Added {len(chunks)} text chunks to ChromaDB")
        
        return len(chunks)
    
    def search(self, query_text: str, n_results: int = 5,
               file_filter: Optional[str] = None,
               metadata_filter: Optional[Dict] = None,
               allowed_types: Optional[Iterable[str]] = None) -> List[Dict]:
        """
        Semantic search for relevant documents
        
        Args:
            query_text: Query text for searching relevant documents
            n_results: Number of results to return (default: 5)
            file_filter: Optional filter by source file name
            metadata_filter: Optional metadata filter passed to Chroma (e.g., {'type': 'event'})
            allowed_types: Optional whitelist for metadata 'type' values in the final results
            
        Returns:
            List of search results with format:
                - content: Retrieved text chunk
                - file_name: Source document file name
                - chunk_id: Chunk number within the document
                - distance: Embedding distance (lower = more relevant)
        """
        try:
            # Perform semantic search
            apply_default_filter = False
            where = metadata_filter
            if metadata_filter is None:
                where = {'type': 'document'}
                apply_default_filter = True

            try:
                if where:
                    results = self.collection.query(
                        query_texts=[query_text],
                        n_results=n_results,
                        where=where
                    )
                else:
                    results = self.collection.query(
                        query_texts=[query_text],
                        n_results=n_results
                    )
            except Exception:
                # Older Chroma versions might not support where; fall back
                results = self.collection.query(
                    query_texts=[query_text],
                    n_results=n_results
                )
            
            # Format results
            formatted_results = []
            allowed_type_set = set(allowed_types) if allowed_types else {'document'}

            def matches_metadata_filter(metadata: Dict) -> bool:
                if not metadata_filter:
                    return True
                for key, expected_value in metadata_filter.items():
                    if metadata.get(key) != expected_value:
                        return False
                return True

            if results['ids'] and len(results['ids']) > 0:
                for i, doc_id in enumerate(results['ids'][0]):
                    metadata = results['metadatas'][0][i]
                    result = {
                        'content': results['documents'][0][i],
                        'file_name': metadata.get('file_name'),
                        'chunk_id': metadata.get('chunk_id'),
                        'distance': results['distances'][0][i],
                    }

                    metadata_type = metadata.get('type', 'document')
                    if allowed_type_set and metadata_type not in allowed_type_set:
                        continue

                    if not matches_metadata_filter(metadata):
                        continue
                    
                    # Apply optional file filter
                    if file_filter is None or file_filter in result['file_name']:
                        formatted_results.append(result)
            
            if not formatted_results and apply_default_filter:
                return self.search(query_text, n_results, file_filter, metadata_filter={})

            return formatted_results
        
        except Exception as e:
            logger.error(f"Error during search: {e}")
            return []
    
    def get_stats(self) -> Dict:
        """
        Get statistics about the vector store
        
        Returns:
            Dictionary with collection statistics
        """
        try:
            count = self.collection.count()
            return {
                'collection_name': self.collection_name,
                'total_chunks': count,
                'db_path': self.db_path
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}

    def get_quality_report(self) -> Dict:
        """返回当前 collection 的质量报告，用于判断是否可作为正式库。"""
        try:
            payload = self.collection.get(include=['metadatas'])
            metadatas = payload.get('metadatas') or []
            total = len(metadatas)

            file_counter = Counter((metadata or {}).get('file_name', '<missing>') for metadata in metadatas)
            type_counter = Counter((metadata or {}).get('type', '<missing>') for metadata in metadatas)

            return {
                'collection_name': self.collection_name,
                'total_chunks': total,
                'type_distribution': dict(type_counter),
                'source_files': dict(file_counter),
            }
        except Exception as e:
            logger.error(f"Error getting quality report: {e}")
            return {}
    
    def persist(self):
        """Persist ChromaDB to disk"""
        try:
            # Try new API method
            if hasattr(self.client, 'persist'):
                self.client.persist()
            logger.info("ChromaDB persisted to disk")
        except Exception as e:
            logger.warning(f"ChromaDB persistence note: {e}")

    def _build_document_filter(self, metadata_filter: Optional[Dict] = None,
                               accident_type: Optional[str] = None,
                               weather: Optional[str] = None,
                               severity: Optional[str] = None) -> Dict:
        """构建 document 检索过滤条件。

        `accident_type`、`weather`、`severity` 参数保留仅为兼容旧调用侧，
        当前版本不再使用这些业务字段做规则化 metadata 过滤。
        """
        base_filter: Dict = {'type': 'document'}

        if metadata_filter:
            base_filter.update({key: value for key, value in metadata_filter.items() if value is not None})

        return base_filter

    def rebuild_collection(self, source_directory: str, chunk_size: int = 500,
                           file_patterns: Optional[List[str]] = None,
                           semantic_chunking: bool = True) -> Dict:
        """重建文档集合，供离线入库使用。"""
        chunks = TextFileLoader.load_all_text_files(
            directory=source_directory,
            chunk_size=chunk_size,
            file_patterns=file_patterns or ['*.txt'],
            semantic_chunking=semantic_chunking,
        )
        added_count = self.add_text_chunks(chunks, overwrite=True)
        self.persist()
        stats = self.get_stats()
        stats['added_count'] = added_count
        return stats

    def offline_ingest(self, source_directory: str, chunk_size: int = 500,
                       file_patterns: Optional[List[str]] = None,
                       semantic_chunking: bool = True) -> Dict:
        """离线入库别名，便于重构后的调用侧统一使用。"""
        return self.rebuild_collection(
            source_directory=source_directory,
            chunk_size=chunk_size,
            file_patterns=file_patterns,
            semantic_chunking=semantic_chunking,
        )

    def search_evidence(self, query_text: str, n_results: int = 5,
                        metadata_filter: Optional[Dict] = None,
                        accident_type: Optional[str] = None,
                        weather: Optional[str] = None,
                        severity: Optional[str] = None) -> List[Dict]:
        """面向审查与佐证场景的检索封装。"""
        document_filter = self._build_document_filter(
            metadata_filter=metadata_filter,
            accident_type=accident_type,
            weather=weather,
            severity=severity,
        )
        return self.search(
            query_text=query_text,
            n_results=n_results,
            metadata_filter=document_filter,
            allowed_types=['document'],
        )

    # --- Semantic routing utilities -------------------------------------------------

    def _normalize_event_names(self, event_names: Iterable[str]) -> List[str]:
        return sorted({name.strip() for name in event_names if name and name.strip()})

    def sync_event_terms(self, event_names: Iterable[str], force: bool = False) -> int:
        """Persist event names as lightweight documents for semantic routing."""
        event_list = self._normalize_event_names(event_names)
        if not event_list:
            return 0

        if not force and set(event_list) == set(self._cached_events):
            return 0

        try:
            self.collection.delete(where={'type': 'event'})
        except Exception:
            # delete may fail if collection empty on some backends; ignore
            pass

        ids = []
        documents = []
        metadatas = []

        for idx, name in enumerate(event_list):
            digest = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
            doc_id = f"event::{idx}::{digest}"
            ids.append(doc_id)
            documents.append(name)
            metadatas.append({
                'type': 'event',
                'event_name': name,
                'chunk_id': str(idx),
                'file_name': 'neo4j_event'
            })

        if ids:
            self.collection.add(ids=ids, documents=documents, metadatas=metadatas)

        self._cached_events = event_list

        return len(ids)

    def semantic_route(self, query_text: str, n_results: int = 5,
                       min_relevance: float = 0.35) -> List[Dict]:
        """Match user text to event names stored in the collection."""
        if not query_text:
            return []

        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where={'type': 'event'}
            )
        except Exception:
            return []

        routed = []
        if results.get('ids'):
            distances = results.get('distances', [[]])
            documents = results.get('documents', [[]])
            metadatas = results.get('metadatas', [[]])
            for i, doc_id in enumerate(results['ids'][0]):
                metadata = metadatas[0][i] if metadatas and metadatas[0] else {}
                distance = distances[0][i] if distances and distances[0] else None
                similarity = None if distance is None else 1 - distance
                if similarity is not None and similarity < min_relevance:
                    continue
                routed.append({
                    'event_name': metadata.get('event_name') or (documents[0][i] if documents and documents[0] else None),
                    'distance': distance,
                    'similarity': similarity,
                    'raw_id': doc_id
                })
        return routed


def main():
    """Main execution function - Initialize vector database with all text documents"""
    
    # Define paths
    project_root = Path(__file__).parent.parent
    data_raw_path = project_root / "data_raw"
    db_path = project_root / "chroma_data"
    
    logger.info("=" * 60)
    logger.info("Starting ChromaDB Vector Store Initialization")
    logger.info("=" * 60)
    
    # Initialize components
    text_loader = TextFileLoader()
    vector_store = ChromaDBVectorStore(str(db_path), collection_name="traffic_documents")
    
    # Load all text files from data_raw/ with semantic-aware chunking
    logger.info("\nLoading text files from data_raw/ with semantic-aware chunking...")
    stats = vector_store.offline_ingest(
        source_directory=str(data_raw_path),
        chunk_size=500,
        file_patterns=['*.txt'],
        semantic_chunking=True,
    )
    logger.info("\n" + "=" * 60)
    logger.info("ChromaDB Initialization Complete")
    logger.info("=" * 60)
    logger.info(f"Collection: {stats.get('collection_name')}")
    logger.info(f"Total chunks stored: {stats.get('total_chunks')}")
    logger.info(f"Chunks added this run: {stats.get('added_count')}")
    logger.info(f"Database path: {stats.get('db_path')}")
    
    # Example search queries
    logger.info("\n" + "=" * 60)
    logger.info("Example RAG Searches")
    logger.info("=" * 60)
    
    example_queries = [
        "危化品泄漏处置",
        "特别重大交通事件",
        "伤员救治流程"
    ]
    
    for query in example_queries:
        logger.info(f"\nQuery: '{query}'")
        results = vector_store.search_evidence(query, n_results=2)
        for idx, result in enumerate(results, 1):
            logger.info(f"  Result {idx}: {result['file_name']} (chunk {result['chunk_id']}, distance: {result['distance']:.3f})")
            logger.info(f"    Content preview: {result['content'][:100]}...")



if __name__ == "__main__":
    main()
