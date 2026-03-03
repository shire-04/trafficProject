# AI Coding Agent Instructions - Traffic Emergency Response System (E-KELL)

## Project Context
This is a **local LLM-powered traffic emergency response system** that generates policy strategies for accidents. It combines:
1.  **Neo4j Knowledge Graph**: Structured reasoning engine with ~1300 nodes (Events, Actions, Resources) and complex logic (Severity, Workflow, Causality).
2.  **ChromaDB Vector Database**: RAG context from regulations and case studies.
3.  **Local LLM (Ollama)**: `qwen3-vl:4b` for multi-agent reasoning and generation.
4.  **Streamlit**: Frontend interface.

## Architecture & Core Components

### 1. Reasoning Engine (`src/reasoning_engine.py`)
- **Role**: Central logic coordinator.
- **Key Class**: `TrafficReasoningEngine`.
- **Integration**: Connects to Neo4j (`bolt://localhost:7687`) and ChromaDB.
- **Logic (V3.0 Algorithm)**:
    - `_normalize_search_terms`: Expands user queries using Semantic Routing (ChromaDB) + Fuzzy Matching.
    - `query_graph`: Executes complex Cypher traversals:
        - **Severity**: `Event -[:CLASSIFIED_AS]-> Standard`
        - **Direct Response**: `Event -[:TRIGGERS]-> Action`
        - **Chain of Command**: `Event -[:LEADS_TO]-> Consequence`
        - **Detailed Ops**: `Consequence -[:CONSISTS_OF]-> Action` OR `Action -[:MITIGATES]-> Consequence`
        - **Workflow**: `Action -[:NEXT_STEP]-> Action`
        - **Resourcing**: `Action -[:REQUIRES]-> Resource`

### 2. AI Agents (`src/agents.py`)
- **Framework**: Custom `BaseAgent` class wrapping `ollama` calls.
- **Roles**:
    - `Analyst` (`PROMPT_ANALYST`): Multimodal fact-checking (Text + Image).
    - `Legal` (`PROMPT_LEGAL`): RAG-based regulatory advice.
    - `Critic` (`PROMPT_CRITIC`): JSON-based output for validation.
    - `Commander` (`PROMPT_COMMANDER`): Final strategy synthesis.
- **Pattern**: Agents have a `speak()` method with retry logic and support for base64 image inputs.

### 3. Vector Database (`src/vectorDB.py`)
- **Storage**: ChromaDB persisted in `./chroma_data`.
- **Data Source**: `data_raw/*.txt` (Regulations, Cases).
- **Pipeline**: Load -> Chunk (500 chars) -> Embed (Sentence-Transformers) -> Store.

### 4. Frontend (`src/app.py`)
- **Framework**: Streamlit.
- **Flow**: User Input -> Reasoning Engine (Graph Query) -> Agent Orchestration -> Display Results.

## Developer Workflow

### Environment Setup
- **Python**: 3.10+ recommended.
- **Dependencies**: `pip install -r requirements.txt`
- **External Services**:
    - Ensure Neo4j is running locally on port 7687.
    - Ensure Ollama is running with `qwen3-vl:4b` pulled.

### Running the Application
```powershell
# Initialize Vector DB (if data_raw changes)
python src/vectorDB.py

# Run the Web UI
streamlit run src/app.py
```

### Debugging
- **Streamlit**: Use `st.write()` or `st.sidebar` for debug outputs.
- **Agents**: `BaseAgent` prints `DEBUG` and `WARN` logs to the console.
- **Neo4j**: Verify queries in Neo4j Browser if graph results are empty.

## Coding Conventions

- **Language**: Python (Type hints recommended).
- **Comments**: Use Chinese for comments and docstrings (project standard).
- **Paths**: Use relative paths from project root (e.g., `./chroma_data`, `data_raw/`).
- **Error Handling**:
    - `BaseAgent` includes retry logic for LLM calls.
    - `vectorDB.py` uses `errors='ignore'` for UTF-8 text loading.
- **Data Separation**:
    - **Neo4j**: Structured triples (CSV loaded externally).
    - **ChromaDB**: Unstructured text (TXT loaded by script).

## Specific Module Instructions: Neo4j

### Graph Schema (V3.0)
- **Nodes**:
    - `Event` (~520): Accident scenarios.
    - `Action` (~660): Response measures.
    - `Resource` (~24): Assets (Note: Only ~10% of actions have defined resources).
    - `Consequence` (~160): Intermediate states.
    - **Note**: Labels are Case-Sensitive. Use `Action`, `Resource`, `Event`.
- **Relationships & Logic Strength**:
    - `TRIGGERS` (Event -> Action): **Primary Logic** (500+ paths). Strongest response pattern.
    - `REQUIRES` (Action -> Resource): **Sparse** (~50 paths). AI must handle missing resource data gracefully.
    - `LEADS_TO` (Event -> Consequence): Available for impact analysis.
    - `CONSISTS_OF` (Consequence -> Action): **Now Available** (~370 paths). Use for causal chain reasoning.
    - `CLASSIFIED_AS`: Severity grading.

## Specific Module Instructions: VectorDB

### Data Organization
- **`data_raw/`**: Source materials.
    - `*.txt`: Loaded by `vectorDB.py` for RAG.
    - `*.csv`: Managed by Neo4j (NOT loaded by `vectorDB.py`).
- **`chroma_data/`**: Persistent storage.

### Critical Constraints
- **MUST DO**:
    - Load and chunk **text files only** (*.txt).
    - Store chunks in single ChromaDB collection: `traffic_documents`.
    - Return results with: content, file_name, chunk_id, distance.
- **MUST NOT DO**:
    - Load or process CSV files (that's Neo4j's job).
    - Normalize entities (Neo4j's job).

### Implementation Details
- **Encoding**: UTF-8 with `errors='ignore'`.
- **Chunk Size**: Default 500 chars.
- **Search Interface**: `search(query_text, n_results)` returning dicts.

### Chinese-Specific Notes
- All documents are UTF-8 encoded with error='ignore' for robustness
- Domain uses Simplified Chinese terminology (交通事故处置, 危化品 for hazmat, etc.)
- Chunk size of 500 chars balances semantic coherence with query granularity
- Embedding distance scores range [0, 1], lower scores indicate higher relevance
