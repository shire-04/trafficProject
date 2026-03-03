import difflib
from neo4j import GraphDatabase
from typing import Dict, List, Tuple
from vectorDB import ChromaDBVectorStore 

# --- 配置区 ---
URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")  

class TrafficReasoningEngine:
    def __init__(self):
        self.driver = GraphDatabase.driver(URI, auth=AUTH)
        self._event_name_cache: List[str] = []
        self.vector_store = ChromaDBVectorStore(db_path="./chroma_data")
        self._events_synced = False
        self._synced_event_names: set[str] = set()

    def close(self):
        self.driver.close()

    def _load_event_names(self) -> List[str]:
        """加载 Neo4j 中所有 Event 节点的名字用于模糊匹配"""
        if not self._event_name_cache:
            with self.driver.session() as session:
                result = session.run("MATCH (e:Event) RETURN e.name as name")
                names = [record["name"] for record in result if record["name"]]
                if set(names) != set(self._event_name_cache):
                    self._event_name_cache = names
                    self._events_synced = False
        return self._event_name_cache

    def _normalize_search_terms(self, keyword: str) -> Tuple[List[str], List[Dict]]:
        """语义路由 + 模糊匹配，扩展搜索词"""
        if not keyword:
            return [], []

        keyword = keyword.strip()
        seen = set()
        ordered_terms: List[str] = []

        def add_term(term: str):
            if term and term not in seen:
                ordered_terms.append(term)
                seen.add(term)

        add_term(keyword)
        semantic_routes: List[Dict] = []
        event_names = self._load_event_names()

        if event_names:
            # 确保向量库索引同步
            if not self._events_synced or set(event_names) != self._synced_event_names:
                self.vector_store.sync_event_terms(event_names, force=True)
                self._events_synced = True
                self._synced_event_names = set(event_names)

            # 1. 向量语义路由 (Semantic Routing)
            semantic_routes = self.vector_store.semantic_route(keyword, n_results=5, min_relevance=0.35)
            for route in semantic_routes:
                add_term(route.get('event_name', ''))

            # 2. 字符串包含匹配
            contains_matches = [name for name in event_names if keyword in name or name in keyword]
            for name in contains_matches:
                add_term(name)

            # 3. 模糊匹配 (Fuzzy Match)
            fuzzy_matches = difflib.get_close_matches(keyword, event_names, n=5, cutoff=0.4)
            for name in fuzzy_matches:
                add_term(name)

        return ordered_terms, semantic_routes

    def query_graph(self, event_keyword: str) -> Dict:
        """
        V3.0 终极版算法：适配 CLASSIFIED_AS, CONSISTS_OF, MITIGATES 新架构
        """
        search_terms, semantic_routes = self._normalize_search_terms(event_keyword)
        if not search_terms and event_keyword:
            search_terms = [event_keyword.strip()]

        context_data = {
            "Trigger_Event": event_keyword,
            "Query_Terms": search_terms,
            "Matched_Events": [],
            "Semantic_Routes": semantic_routes,
            "Severity_Standards": [], # 【新增】对应 CLASSIFIED_AS
            "Direct_Actions": [],     # 对应 TRIGGERS
            "Consequences": [],       # 对应 LEADS_TO
            "Indirect_Actions": [],   # 对应 CONSISTS_OF / MITIGATES / NEXT_STEP
            "Resources": []           # 对应 REQUIRES
        }
        
        with self.driver.session() as session:
            matched_events = set()

            # --- 路径 1：事故定级 (Event -> CLASSIFIED_AS -> Standard) 【新增功能】 ---
            # 这对应原来的 FALLS_UNDER
            result_severity = session.run("""
                MATCH (e:Event)-[:CLASSIFIED_AS]->(s:Event)
                WHERE any(term IN $terms WHERE e.name CONTAINS term OR term CONTAINS e.name)
                RETURN e.name as event, s.name as standard
            """, terms=search_terms)
            
            for record in result_severity:
                matched_events.add(record["event"])
                context_data["Severity_Standards"].append(record["standard"])

            # --- 路径 2：直接快速响应 (Event -> TRIGGERS -> Action) ---
            result_direct = session.run("""
                MATCH (e:Event)-[:TRIGGERS]->(a:Action)
                WHERE any(term IN $terms WHERE e.name CONTAINS term OR term CONTAINS e.name)
                RETURN e.name as event, a.name as action
            """, terms=search_terms)

            for record in result_direct:
                matched_events.add(record["event"])
                context_data["Direct_Actions"].append(record["action"])

            # --- 路径 3：深度推理链条 ---
            # 逻辑：Event -> LEADS_TO -> Consequence
            #      然后 Consequence -[:CONSISTS_OF]-> Action (原 REALIZED_AS)
            #      或者 Action -[:MITIGATES]-> Consequence (反向缓解)
            #      或者 Action -[:NEXT_STEP]-> Action (流程)
            result_chain = session.run("""
                MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)
                WHERE any(term IN $terms WHERE e.name CONTAINS term OR term CONTAINS e.name)
                
                // 查找关联动作：
                // 1. 包含关系 (宏观后果/方案 -> 微观动作)
                OPTIONAL MATCH (c)-[:CONSISTS_OF]->(a1:Action)
                // 2. 缓解关系 (动作 -> 缓解 -> 后果)
                OPTIONAL MATCH (a2:Action)-[:MITIGATES]->(c)
                // 3. 顺承步骤 (动作 -> 下一步 -> 动作) - 可选扩展
                OPTIONAL MATCH (a1)-[:NEXT_STEP]->(a3:Action)
                
                RETURN e.name as event, c.name as consequence, 
                       a1.name as act_consists, a2.name as act_mitigates, a3.name as act_next
            """, terms=search_terms)
            
            for record in result_chain:
                matched_events.add(record["event"])
                if record["consequence"]:
                    context_data["Consequences"].append(record["consequence"])
                
                # 收集所有相关动作
                for key in ["act_consists", "act_mitigates", "act_next"]:
                    if record[key]:
                        context_data["Indirect_Actions"].append(record[key])

            # --- 兜底逻辑：如果前面的复杂关系没查到，尝试根据后果关键词硬搜 ---
            all_actions = list(set(context_data["Direct_Actions"] + context_data["Indirect_Actions"]))
            
            if not all_actions and context_data["Consequences"]:
                print("⚠ 路径断裂，启动模糊匹配兜底...")
                for cons in context_data["Consequences"]:
                    if "伤" in cons or "亡" in cons or "火" in cons:
                        fallback_query = """
                            MATCH (a:Action) 
                            WHERE a.name CONTAINS "救治" OR a.name CONTAINS "消防" OR a.name CONTAINS "现场"
                            RETURN a.name as action LIMIT 3
                        """
                        fallback_res = session.run(fallback_query)
                        for r in fallback_res:
                            context_data["Indirect_Actions"].append(r["action"])
            
            # 更新 all_actions
            all_actions = list(set(context_data["Direct_Actions"] + context_data["Indirect_Actions"]))

            # --- 路径 4：资源查找 (Action -> REQUIRES -> Resource) ---
            if all_actions:
                result_res = session.run("""
                    MATCH (a:Action)-[:REQUIRES]->(r:Resource)
                    WHERE a.name IN $actions
                    RETURN r.name as resource
                """, actions=all_actions)
                context_data["Resources"] = [r["resource"] for r in result_res]

            # 数据清洗去重
            context_data["Severity_Standards"] = list(set(context_data["Severity_Standards"]))
            context_data["Consequences"] = list(set(context_data["Consequences"]))
            context_data["Direct_Actions"] = list(set(context_data["Direct_Actions"]))
            context_data["Resources"] = list(set(context_data["Resources"]))
            context_data["Indirect_Actions"] = list(set(context_data["Indirect_Actions"]))
            context_data["Matched_Events"] = list(matched_events) if matched_events else search_terms

        return context_data

    def query_vector_db(self, query_text: str, semantic_hint: str = "", n_results: int = 3) -> str:
        try:
            combined_query = query_text.strip()
            if semantic_hint:
                combined_query += f"\n情报提示：{semantic_hint}"

            results = self.vector_store.search(
                combined_query,
                n_results=n_results,
                allowed_types=["document"]
            )
            
            if not results:
                return "未检索到具体法规原文。"
            
            context_str = ""
            for res in results:
                context_str += f"   - [来源：{res['file_name']}]：{res['content'][:150]}...\n"
            return context_str
        except Exception as e:
            return f"向量库检索失败: {str(e)}"
    
    def generate_llm_prompt(self, data: Dict, vector_context: str = "") -> str:
        # 1. 处理空跑情况
        if not any([data["Direct_Actions"], data["Consequences"], data["Indirect_Actions"]]):
            return f"""
### 角色设定
你是指挥城市交通应急指挥中心（TCC）的**首席调度官**。
报警内容："{data['Trigger_Event']}"。
（注：图谱未收录特定预案，请基于通用交通安全常识生成策略。）
请生成包含交通流控制、警力调度、信息发布的管控策略。
"""
        
        user_desc = data.get('User_Input', data['Trigger_Event'])
        
        # 2. 格式化“候选标准”
        # 我们把图谱查出来的所有标准列出来，让 LLM 自己选
        standards_text = "（系统检索到以下候选定级标准，请根据死伤人数逻辑判断适用哪一条）：\n"
        if data['Severity_Standards']:
            for i, std in enumerate(data['Severity_Standards'], 1):
                standards_text += f"   - 候选标准 {i}: {std}\n"
        else:
            standards_text = "   - 暂无明确分级标准参考"

        # 3. 构建 Prompt
        prompt = f"""
### 角色设定
你是指挥城市交通应急指挥中心（TCC）的**首席调度官**。你具备极强的逻辑判断能力和法规意识。

### 当前警情
**用户报警描述**："{user_desc}" 

### 📊 知识图谱研判数据
1. **事故定级参考** (法务关注)：
{standards_text}
2. **潜在后果链**：{', '.join(data['Consequences'])}
3. **推荐处置方案** (含战术动作)：{'; '.join(list(set(data['Direct_Actions'] + data['Indirect_Actions'])))}
4. **关键资源需求**：{', '.join(data['Resources'])}

### ⚖️ 法规与预案原文依据 (RAG检索)
{vector_context}

### 决策指令
请综合上述信息，生成最终管控方案。

**🔥 核心任务一：定性分析（必须执行）**
请阅读“用户报警描述”中的伤亡/损失情况，与“事故定级参考”中的候选标准进行比对。
* **排除错误标准**：指明哪些标准不符合当前数值（例如：当前6死，不符合“30人以上死亡”的标准）。
* **确定最终等级**：明确指出当前事故属于哪一级（如：重大、较大、一般）。

**核心任务二：管控策略生成**
基于定级结论，制定详细策略：
1. **交通流控制**：...
2. **资源调度**：确保涵盖上述关键资源。
3. **信息发布**：...

**核心任务三：法规引用**
在方案末尾引用生效的法律条款。
"""
        return prompt

if __name__ == "__main__":
    engine = TrafficReasoningEngine()
    test_kw = "隧道火灾" # 建议用你CSV里有的词测试
    
    print(f"正在全路径检索 '{test_kw}' ...")
    data = engine.query_graph(test_kw)
    print("检索结果：", data)
    print("-" * 50)
    print(engine.generate_llm_prompt(data))
    engine.close()