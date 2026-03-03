"""
Demo script to compare fixed-size vs semantic-aware text chunking

Run this to see the difference in how sentences are chunked
"""

from pathlib import Path
from vectorDB import TextFileLoader


def compare_chunking_strategies():
    """Compare chunking results between two strategies"""
    
    project_root = Path(__file__).parent.parent
    test_file = project_root / "data_raw" / "案例.txt"
    
    if not test_file.exists():
        print(f"Test file not found: {test_file}")
        return
    
    print("=" * 80)
    print("文本分块策略对比演示")
    print("=" * 80)
    
    # Strategy 1: Fixed-size chunking
    print("\n【方案 A】固定大小分块 (semantic_chunking=False)")
    print("-" * 80)
    chunks_fixed = TextFileLoader.load_text_file(
        str(test_file),
        chunk_size=500,
        semantic_chunking=False
    )
    
    print(f"总块数: {len(chunks_fixed)}")
    print(f"平均块大小: {sum(len(c['content']) for c in chunks_fixed) // len(chunks_fixed):.0f} 字符")
    print(f"\n前 3 个块的内容预览:")
    for i, chunk in enumerate(chunks_fixed[:3], 1):
        content_preview = chunk['content'][:100].replace('\n', ' ')
        print(f"\n  Chunk {i} ({len(chunk['content'])} 字符):")
        print(f"  {content_preview}...")
        # Check if sentence is broken
        if chunk['content'][-1] not in {'。', '！', '？', '\n'}:
            print(f"  ⚠️  警告: 句子被切割！最后一个字符是 '{chunk['content'][-1]}'")
    
    # Strategy 2: Semantic-aware chunking
    print("\n\n【方案 B】语义感知分块 (semantic_chunking=True)")
    print("-" * 80)
    chunks_semantic = TextFileLoader.load_text_file(
        str(test_file),
        chunk_size=500,
        semantic_chunking=True
    )
    
    print(f"总块数: {len(chunks_semantic)}")
    print(f"平均块大小: {sum(len(c['content']) for c in chunks_semantic) // len(chunks_semantic):.0f} 字符")
    print(f"\n前 3 个块的内容预览:")
    for i, chunk in enumerate(chunks_semantic[:3], 1):
        content_preview = chunk['content'][:100].replace('\n', ' ')
        print(f"\n  Chunk {i} ({len(chunk['content'])} 字符):")
        print(f"  {content_preview}...")
        # Check if sentence is complete
        if chunk['content'][-1] in {'。', '！', '？'}:
            print(f"  ✅ 句子完整，以'{chunk['content'][-1]}'结尾")
        else:
            print(f"  ⚠️  警告: 句子不完整，以'{chunk['content'][-1]}'结尾")
    
    # Summary comparison
    print("\n\n" + "=" * 80)
    print("对比总结")
    print("=" * 80)
    print(f"\n{'指标':<15} {'固定分块':^20} {'语义感知分块':^20}")
    print("-" * 80)
    print(f"{'块数':<15} {len(chunks_fixed):^20} {len(chunks_semantic):^20}")
    print(f"{'平均块大小':<15} {sum(len(c['content']) for c in chunks_fixed) // len(chunks_fixed):^20.0f} {sum(len(c['content']) for c in chunks_semantic) // len(chunks_semantic):^20.0f}")
    
    # Check sentence completeness
    broken_fixed = sum(1 for c in chunks_fixed if c['content'][-1] not in {'。', '！', '？', '\n'})
    complete_semantic = sum(1 for c in chunks_semantic if c['content'][-1] in {'。', '！', '？'})
    
    print(f"{'完整句子数':<15} {len(chunks_fixed) - broken_fixed:^20} {complete_semantic:^20}")
    print(f"{'句子完整率':<15} {(1 - broken_fixed/len(chunks_fixed))*100:^19.1f}% {complete_semantic/len(chunks_semantic)*100:^19.1f}%")
    
    print("\n💡 建议: 对于 RAG 应用，推荐使用语义感知分块以获得更好的检索质量")
    print("=" * 80)


if __name__ == "__main__":
    compare_chunking_strategies()
