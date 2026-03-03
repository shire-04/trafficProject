import csv
from neo4j import GraphDatabase

# --- 配置区 ---
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "moxiao0906")
CSV_FILE_PATH = "E:\\trafficProject\\data_raw\\极端重大交通事故案例.csv"

class DataImporter:
    def __init__(self):
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    def close(self):
        self.driver.close()

    def import_data(self):
        print(f"🚀 开始导入数据...")
        
        with open(CSV_FILE_PATH, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            with self.driver.session() as session:
                for row in reader:
                    # 去除首尾空格
                    source = row['source'].strip()
                    relation = row['relation'].strip().upper()
                    target = row['target'].strip()
                    
                    if not source or not target: continue

                    # -------------------------------------------------
                    # 场景 1: MITIGATES (特殊处理：反转方向)
                    # CSV原意: "火灾(Source), mitigates, 封路(Target)"
                    # 图谱存入: (Action:封路)-[:MITIGATES]->(Consequence:火灾)
                    # -------------------------------------------------
                    if relation.lower() == 'mitigates':
                        # 注意：这里我们把 CSV 的 Target 作为关系的“起点”(Start Node)
                        # 把 CSV 的 Source 作为关系的“终点”(End Node)
                        self._merge_triple(
                            session, 
                            start_name=target, start_label="Action",  # 动作是起点
                            rel_type="MITIGATES", 
                            end_name=source, end_label="Consequence"  # 后果是终点
                        )

                    # -------------------------------------------------
                    # 场景 2: 动作需要资源 (REQUIRES / CONSUMES)
                    # -------------------------------------------------
                    elif relation.upper() in ['REQUIRES', 'CONSUMES']:
                        self._merge_triple(
                            session, 
                            start_name=source, start_label="Action",
                            rel_type="REQUIRES",  # 统一存为 REQUIRES
                            end_name=target, end_label="Resource"
                        )

                    # -------------------------------------------------
                    # 场景 3: 事件导致后果 (LEADS_TO / RESULTS_IN)
                    # -------------------------------------------------
                    elif relation.upper() in ['LEADS_TO', 'RESULTS_IN']:
                        self._merge_triple(
                            session, 
                            start_name=source, start_label="Event",
                            rel_type="LEADS_TO",  # 统一存为 LEADS_TO
                            end_name=target, end_label="Consequence"
                        )

                    # -------------------------------------------------
                    # 场景 4: 事件触发动作 (TRIGGERS)
                    # -------------------------------------------------
                    elif relation.upper() == 'TRIGGERS':
                        self._merge_triple(
                            session, 
                            start_name=source, start_label="Event",
                            rel_type="TRIGGERS", 
                            end_name=target, end_label="Action"
                        )

    def _merge_triple(self, session, start_name, start_label, rel_type, end_name, end_label):
        """
        通用导入函数：MERGE (Start)-[:Type]->(End)
        """
        query = f"""
            MERGE (a:{start_label} {{name: $start_name}})
            MERGE (b:{end_label} {{name: $end_name}})
            MERGE (a)-[:{rel_type}]->(b)
        """
        session.run(query, start_name=start_name, end_name=end_name)
        print(f"导入: ({start_name}) -[:{rel_type}]-> ({end_name})")

if __name__ == "__main__":
    importer = DataImporter()
    importer.import_data()
    importer.close()