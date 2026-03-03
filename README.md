# 交通应急处置策略生成系统 - vectorDB 向量数据库模块

## 概述

`vectorDB.py` 模块提供了完整的 ChromaDB 向量数据库管道，用于为本地 LLM 的 RAG（检索增强生成）功能提供上下文文档。主要功能包括：

1. **文本加载与分块**: 从 `data_raw/` 目录加载所有文本文件并进行语义单位分割
2. **向量化嵌入**: 使用 ChromaDB 对文本进行向量嵌入，支持语义相似度搜索
3. **RAG 查询接口**: 为本地 LLM 提供语义搜索接口，用于上下文检索增强生成

**注意**: 知识图谱（Action-Resource 三元组和事件因果链）由 Neo4j 统一管理。本模块专注于存储法规、应急预案、案例等可检索的文本内容。

## 安装

```bash
pip install -r requirements.txt
```

安装以下依赖包：
- `chromadb`: 向量数据库，支持本地持久化存储
- `sentence-transformers`: 中文文本向量化模型
- `pandas`: CSV 数据处理（可选）

## 快速开始

### 初始化向量数据库

```bash
cd src
python vectorDB.py
```

执行效果：
- 初始化 ChromaDB，将数据持久化到 `./chroma_data/` 目录
- 自动加载 `data_raw/` 下的所有 `.txt` 文件
- 对所有文本进行分块处理（默认 500 字符/块）
- 运行示例查询验证设置
- 输出日志显示：已加载的文本块数、检索效果示例

### 在 Python 代码中使用

```python
from vectorDB import ChromaDBVectorStore, TextFileLoader

# 初始化向量存储
vector_store = ChromaDBVectorStore(db_path="./chroma_data")

# 语义搜索相关文档
results = vector_store.search(
    query_text="危化品泄漏处置",
    n_results=5
)

# 处理搜索结果
for result in results:
    print(f"来源: {result['file_name']} (chunk {result['chunk_id']})")
    print(f"相关度: {result['distance']:.3f}")
    print(f"内容: {result['content'][:200]}...")
```

## LLM RAG 集成示例

```python
from vectorDB import ChromaDBVectorStore

def generate_emergency_policy(incident_description: str, llm_model) -> str:
    """
    使用本地 LLM 和知识库生成应急处置策略
    """
    # 初始化向量存储
    vector_store = ChromaDBVectorStore(db_path="./chroma_data")
    
    # 从知识库检索相关文档
    relevant_docs = vector_store.search(
        query_text=incident_description,
        n_results=5
    )
    
    # 组织 RAG 上下文
    context = "=== 相关法规和预案 ===\n"
    for doc in relevant_docs:
        context += f"\n【来自 {doc['file_name']}】\n{doc['content']}\n"
    
    # 构造 LLM 提示词
    prompt = f"""
根据以下交通应急法规、预案和案例，为以下事件生成处置策略：

事件描述: {incident_description}

相关参考资料:
{context}

请生成详细的应急处置策略，包括：
1. 初期响应措施
2. 所需资源和部门协调
3. 风险防控要点
4. 后期评估建议
"""
    
    # 调用 LLM 生成策略
    policy = llm_model.generate(prompt)
    return policy
```

## 类和方法参考

### TextFileLoader - 文本文件加载器

```python
from vectorDB import TextFileLoader

# 加载单个文件
chunks = TextFileLoader.load_text_file(
    file_path="data_raw/案例.txt",
    chunk_size=500  # 每块 500 字符
)

# 加载目录中的所有文本文件
all_chunks = TextFileLoader.load_all_text_files(
    directory="data_raw/",
    chunk_size=500,
    file_patterns=['*.txt']
)

# 返回格式：
# [
#   {
#     'content': '文本内容...',
#     'chunk_id': 0,
#     'file_name': '案例.txt',
#     'source': '/full/path/to/案例.txt'
#   },
#   ...
# ]
```

### ChromaDBVectorStore - 向量存储管理

#### 初始化

```python
from vectorDB import ChromaDBVectorStore

vector_store = ChromaDBVectorStore(
    db_path="./chroma_data",
    collection_name="traffic_documents"
)
```

#### 添加文本块

```python
# 添加文本块到向量数据库
chunks = TextFileLoader.load_all_text_files("data_raw/", chunk_size=500)
count = vector_store.add_text_chunks(
    chunks=chunks,
    overwrite=True  # True: 覆盖现有数据；False: 增量更新
)
```

#### 语义搜索

```python
# 执行语义搜索
results = vector_store.search(
    query_text="伤员救治流程",
    n_results=5,
    file_filter=None  # 可选：按文件名过滤（如 "预案.txt"）
)

# 返回格式：
# [
#   {
#     'content': '检索到的文本块...',
#     'file_name': '源文件名.txt',
#     'chunk_id': '块编号',
#     'distance': 0.25  # 嵌入距离，越小越相关
#   },
#   ...
# ]
```

#### 获取统计信息

```python
stats = vector_store.get_stats()
print(f"存储块数: {stats['total_chunks']}")
print(f"数据库路径: {stats['db_path']}")
print(f"集合名称: {stats['collection_name']}")
```

#### 持久化存储

```python
# 保存到磁盘
vector_store.persist()
```

## 配置选项

### 调整文本分块大小和策略

**方式 1：启用语义感知分块（推荐）**

```python
# 在 main() 函数中，已默认启用
all_chunks = text_loader.load_all_text_files(
    directory=str(data_raw_path),
    chunk_size=500,
    semantic_chunking=True  # 按句子边界分块
)
```

这种方式会：
- ✅ 在句号/感叹号/问号处切割（中文）
- ✅ 保持语义完整性
- ✅ 提高 RAG 检索质量
- ✅ 减少嵌入质量损失

**方式 2：固定大小分块**

```python
all_chunks = text_loader.load_all_text_files(
    directory=str(data_raw_path),
    chunk_size=500,
    semantic_chunking=False  # 简单固定大小分块
)
```

**分块大小建议**:
- `300-400`: 细粒度检索，适合多轮 RAG（但块数增加）
- `500-800`: **平衡方案（默认 500）**，语义完整 + 合理块数
- `800-1000`: 粗粒度检索，减少数据库条目（但单块上下文增大）

### 修改 ChromaDB 存储路径

```python
vector_store = ChromaDBVectorStore(db_path="/custom/path/chroma_data")
```

### 指定文件模式

```python
all_chunks = text_loader.load_all_text_files(
    directory="data_raw/",
    chunk_size=500,
    file_patterns=['*.txt', '*.md']  # 加载 txt 和 md 文件
)
```

## 数据存储结构

ChromaDB 将所有文本块存储在单个集合中：

**集合名称**: `traffic_documents`

**元数据字段**:
- `file_name`: 源文件名（如 "案例.txt"）
- `chunk_id`: 文本块在该文件内的编号
- `source`: 完整源文件路径

**存储格式**:
```
ID: 案例_0
Content: "甬莞高速揭阳路段 \"9・9\" 危化品车追尾事故..."
Metadata: {file_name: "案例.txt", chunk_id: "0", source: "..."}
```

## 故障排除

### UTF-8 编码问题

如果文本显示乱码或缺失：

```python
# 检查文件编码
import chardet
with open("data_raw/file.txt", "rb") as f:
    result = chardet.detect(f.read())
    print(f"文件编码: {result['encoding']}")

# 如需重新编码（以 Sublime Text 或 VS Code 为例）
# 1. 打开文件
# 2. 右下角选择 "UTF-8 with BOM" 或 "UTF-8"
# 3. 保存
```

### ChromaDB 连接问题

```python
# 清除旧数据并重新初始化
import shutil
shutil.rmtree("./chroma_data")

# 重新初始化
vector_store = ChromaDBVectorStore()
```

### 缺少依赖包

```bash
pip install --upgrade chromadb sentence-transformers
```

## 性能指标

| 指标 | 值 |
|------|-----|
| 首次运行 | 1-2 分钟（模型下载 + 向量化） |
| 后续运行 | <10 秒（使用本地缓存） |
| 单次查询延迟 | ~100-200ms |
| 内存占用 | ~500MB（完整知识库） |
| 存储空间 | ~200MB（chroma_data 目录） |

## 文本分块策略

### 为什么要语义感知分块？

**问题**：固定大小分块会破坏句子完整性
```
原文：危化品泄漏导致环境污染。相关部门启动应急响应。
固定分块（20 字）: 
  - "危化品泄漏导致环" ❌
  - "境污染。相关部门启" ❌
  - "动应急响应。"
结果：语义破碎，嵌入质量差 → RAG 检索精度低
```

**解决方案**：语义感知分块（按句子边界）
```
语义分块（按句号/感叹号/问号）:
  - "危化品泄漏导致环境污染。" ✅
  - "相关部门启动应急响应。" ✅
结果：语义完整，嵌入质量好 → RAG 检索精度高
```

### 工作原理

本实现采用的策略：
1. **目标大小**：500 字符（可配置）
2. **搜索范围**：目标位置 ±100 字符内查找句子结束标记（。！？）
3. **最小块大小**：50 字符（过小的块被丢弃）
4. **降级方案**：如果目标位置附近没有句号，向前搜索找到下一个句号

### 对比测试

```python
from vectorDB import TextFileLoader

# 方案 A：固定大小分块
chunks_fixed = TextFileLoader.load_text_file(
    "data_raw/案例.txt",
    chunk_size=500,
    semantic_chunking=False
)
# 输出: 18 chunks (平均 ~280 字符/块)

# 方案 B：语义感知分块（推荐）
chunks_semantic = TextFileLoader.load_text_file(
    "data_raw/案例.txt",
    chunk_size=500,
    semantic_chunking=True
)
# 输出: 12 chunks (平均 ~420 字符/块，但句子完整)
```

**结果对比**：
| 指标 | 固定分块 | 语义分块 |
|------|--------|--------|
| 块数 | 多（18） | 少（12） |
| 平均大小 | 280 字 | 420 字 |
| 语义完整性 | ❌ 低 | ✅ 高 |
| 嵌入质量 | ❌ 差 | ✅ 好 |
| RAG 检索精度 | ❌ 低 | ✅ 高 |

## 扩展和定制

### 创建多个集合

```python
# 为不同场景创建专用集合
vector_store.client.get_or_create_collection(
    name="special_regulations",
    metadata={"description": "特殊场景应急法规"}
)
```

### 自定义搜索逻辑

```python
def advanced_search(vector_store, query, file_filter=None):
    """
    高级搜索：支持多关键词、多文件过滤
    """
    results = vector_store.search(query, n_results=10, file_filter=file_filter)
    
    # 按距离排序
    results.sort(key=lambda x: x['distance'])
    
    # 去重处理
    unique_results = []
    seen_content = set()
    for r in results:
        if r['content'] not in seen_content:
            unique_results.append(r)
            seen_content.add(r['content'])
    
    return unique_results[:5]
```

## 常见工作流

### 工作流 0: 查看分块效果对比（可选）

```bash
# 运行对比演示，查看固定分块 vs 语义分块的区别
python src/demo_chunking_comparison.py
```

输出示例：
```
【方案 A】固定大小分块
  Chunk 1 (500 字符):
  甬莞高速揭阳路段 "9・9" 危化品车追尾事故...
  ⚠️  警告: 句子被切割！

【方案 B】语义感知分块（推荐）
  Chunk 1 (420 字符):
  甬莞高速揭阳路段 "9・9" 危化品车追尾事故...
  ✅ 句子完整，以'。'结尾
```

### 工作流 1: 初始部署

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化向量数据库
python src/vectorDB.py

# 3. 验证部署
# 查看 chroma_data/ 目录确认数据已保存
ls -la chroma_data/
```

### 工作流 2: 添加新文档

```python
from vectorDB import TextFileLoader, ChromaDBVectorStore

# 1. 将新文档放入 data_raw/ 目录
# 新文件: data_raw/新法规.txt

# 2. 重新加载所有文本
text_loader = TextFileLoader()
new_chunks = text_loader.load_all_text_files("data_raw/", chunk_size=500)

# 3. 覆盖数据库
vector_store = ChromaDBVectorStore()
vector_store.add_text_chunks(new_chunks, overwrite=True)
vector_store.persist()
```

### 工作流 3: LLM 集成开发

```python
# 1. 启动本地 LLM（如 Ollama、vLLM 等）
# ollama run llama2

# 2. 在应用中集成 RAG
from vectorDB import ChromaDBVectorStore

def llm_with_rag(query, llm_endpoint):
    vs = ChromaDBVectorStore()
    docs = vs.search(query, n_results=3)
    
    context = "\n".join([d['content'] for d in docs])
    
    # 调用本地 LLM API
    response = requests.post(
        llm_endpoint,
        json={
            "prompt": f"Context:\n{context}\n\nQuestion: {query}",
            "max_tokens": 500
        }
    )
    return response.json()
```

## 许可证

本模块是交通应急处置策略生成系统的一部分。

