import json
import pandas as pd
import re
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from llm_provider import generate_json_response, get_default_model  # noqa: E402

# ================= 配置区 =================
MODEL_NAME = os.getenv("TRAFFIC_LLM_MODEL", get_default_model())

# 定义 Schema 提示词模板
PROMPT_TEMPLATE = """
你是一个专业的交通应急知识图谱构建专家。请阅读提供的《国家交通应急预案》文本片段，严格按照我给定的 Schema 提取实体和关系，并以 JSON 格式输出。

【允许的实体类型】
突发事件, 事件级别, 预警, 响应, 措施, 部门, 应急资源

【允许的关系类型约束】
1. [突发事件] -> 引发 -> [突发事件]
2. [突发事件] -> 定级为 -> [事件级别]
3. [事件级别] -> 触发 -> [预警]
4. [事件级别] -> 触发 -> [响应]
5. [突发事件] -> 触发 -> [措施]
6. [预警] -> 触发 -> [措施]
7. [响应] -> 触发 -> [措施]
8. [预警] -> 实施主体 -> [部门]
9. [响应] -> 实施主体 -> [部门]
10. [措施] -> 实施主体 -> [部门]
11. [措施] -> 调用 -> [应急资源]

【抽取要求】
1. 节点名称必须精简，去除冗余修饰词。
2. 绝对不要创造上述【允许的实体类型】和【允许的关系类型约束】之外的任何标签或关系。
3. 必须严格输出合法的 JSON 格式，不要包含任何其他解释性文字。

输出格式示例：
{
  "nodes": [
    {"id": "NODE_1", "name": "路网中心", "label": "部门"},
    {"id": "NODE_2", "name": "提出预警建议", "label": "措施"}
  ],
  "edges": [
    {"source": "NODE_2", "target": "NODE_1", "type": "实施主体"}
  ]
}

【待处理文本】：
"""

# ================= 核心函数 =================

def call_llm_extractor(text_chunk):
    """调用统一 LLM Provider 进行信息抽取"""
    full_prompt = PROMPT_TEMPLATE + text_chunk

    try:
        result = generate_json_response(
            model=MODEL_NAME,
            system_prompt="你是交通应急知识图谱构建专家。请严格返回 JSON。",
            user_content=full_prompt,
        )
        return str(result.get("content") or "")
    except Exception as e:
        print(f"调用 LLM API 失败: {e}")
        return None

def parse_llm_json(response_text):
    """清洗并解析大模型返回的 JSON 数据"""
    if not response_text:
        return {"nodes": [], "edges": []}
    
    # 移除可能存在的 Markdown 代码块标记
    clean_text = re.sub(r'```json\n|\n```|```', '', response_text).strip()
    
    try:
        data = json.loads(clean_text)
        return data
    except json.JSONDecodeError as e:
        print(f"JSON 解析错误: {e}\n原始返回内容:\n{clean_text}")
        return {"nodes": [], "edges": []}

def save_to_csv(graph_data, nodes_file="nodes.csv", edges_file="edges.csv"):
    """将提取的数据追加保存到 CSV 文件中"""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    
    # 处理节点
    if nodes:
        df_nodes = pd.DataFrame(nodes)
        # 如果文件不存在则写入表头，存在则追加
        write_header = not os.path.exists(nodes_file)
        df_nodes.to_csv(nodes_file, mode='a', index=False, header=write_header, encoding='utf-8-sig')
        print(f"成功追加 {len(nodes)} 个节点到 {nodes_file}")

    # 处理关系
    if edges:
        df_edges = pd.DataFrame(edges)
        write_header = not os.path.exists(edges_file)
        df_edges.to_csv(edges_file, mode='a', index=False, header=write_header, encoding='utf-8-sig')
        print(f"成功追加 {len(edges)} 条关系到 {edges_file}")

# ================= 执行区 =================

if __name__ == "__main__":
    # 模拟切分好的 txt 文本块
    sample_text = """
1 总则
1.1 编制目的
　　为切实加强公路交通突发事件的应急管理工作，建立完善应急管理体制和机制，提高突发事件预防和应对能力，控制、减轻和消除公路交通突发事件引起的严重社会危害，及时恢复公路交通正常运行，保障公路畅通，并指导地方建立应急预案体系和组织体系，增强应急保障能力，满足有效应对公路交通突发事件的需要，保障经济社会正常运行，制定本预案。
1.2 编制依据
　　依据《中华人民共和国突发事件应对法》、《中华人民共和国公路法》、《中华人民共和国道路运输条例》等法律法规，《国家突发公共事件总体应急预案》及国家相关专项预案和部门预案制订本预案。
1.3 分类分级
　　本预案所称公路交通突发事件是指由下列突发事件引发的造成或者可能造成公路以及重要客运枢纽出现中断、阻塞、重大人员伤亡、大量人员需要疏散、重大财产损失、生态环境破坏和严重社会危害，以及由于社会经济异常波动造成重要物资、旅客运输紧张需要交通运输部门提供应急运输保障的紧急事件。
　　（1）自然灾害。主要包括水旱灾害、气象灾害、地震灾害、地质灾害、海洋灾害、生物灾害和森林草原火灾等。
　　（2）公路交通运输生产事故。主要包括交通事故、公路工程建设事故、危险货物运输事故。
　　（3）公共卫生事件。主要包括传染病疫情、群体性不明原因疾病、食品安全和职业危害、动物疫情，以及其他严重影响公众健康和生命安全的事件。
　　（4）社会安全事件。主要包括恐怖袭击事件、经济安全事件和涉外突发事件。
　　各类公路交通突发事件按照其性质、严重程度、可控性和影响范围等因素，一般分为四级：Ⅰ级（特别重大）、Ⅱ级（重大）、Ⅲ级（较大）和Ⅳ级（一般）。
1.4 适用范围
　　本预案适用于涉及跨省级行政区划的，或超出事发地省级交通运输主管部门处置能力的，或由国务院责成的，需要由交通运输部负责处置的特别重大（Ⅰ级）公路交通突发事件的应对工作，以及需要由交通运输部提供公路交通运输保障的其它紧急事件。
　　本预案指导地方公路交通突发事件应急预案的编制。
1.5 工作原则
1.5.1 以人为本、平急结合、科学应对、预防为主
　　切实履行政府的社会管理和公共服务职能，把保障人民群众生命财产安全作为首要任务，高度重视公路交通突发事件应急处置工作，提高应急科技水平，增强预警预防和应急处置能力，坚持预防与应急相结合，常态与非常态相结合，提高防范意识，做好预案演练、宣传和培训工作，做好有效应对公路交通突发事件的各项保障工作。
1.5.2 统一领导、分级负责、属地管理、联动协调
　　本预案确定的公路交通突发事件应急工作在人民政府的统一领导下，由交通运输主管部门具体负责，分级响应、条块结合、属地管理、上下联动，充分发挥各级公路交通应急管理机构的作用。 
1.5.3 职责明确、规范有序、部门协作、资源共享
　　明确应急管理机构职责，建立统一指挥、分工明确、反应灵敏、协调有序、运转高效的应急工作机制和响应程序，实现应急管理工作的制度化、规范化。加强与其他部门密切协作，形成优势互补、资源共享的公路交通突发事件联动处置机制。 
1.6 应急预案体系
　　公路交通突发事件应急预案体系包括：
　　（1）公路交通突发事件应急预案。公路交通突发事件应急预案是全国公路交通突发事件应急预案体系的总纲及总体预案，是交通运输部应对特别重大公路交通突发事件的规范性文件，由交通运输部制定并公布实施，报国务院备案。
　　（2）公路交通突发事件应急专项预案。交通突发事件应急专项预案是交通运输部为应对某一类型或某几种类型公路交通突发事件而制定的专项应急预案，由交通运输部制定并公布实施。主要涉及公路气象灾害、水灾与地质灾害、地震灾害、重点物资运输、危险货物运输、重点交通枢纽的人员疏散、施工安全、特大桥梁安全事故、特长隧道安全事故、公共卫生事件、社会安全事件等方面。
　　（3）地方公路交通突发事件应急预案。地方公路交通突发事件应急预案是由省级、地市级、县级交通运输主管部门按照交通运输部制定的公路交通突发事件应急预案的要求，在上级交通运输主管部门的指导下，为及时应对辖区内发生的公路交通突发事件而制订的应急预案（包括专项预案）。由地方交通运输主管部门制订并公布实施，报上级交通运输主管部门备案。
　　（4）公路交通运输企业突发事件预案。由各公路交通运输企业根据国家及地方的公路交通突发事件应急预案的要求，结合自身实际，为及时应对企业范围内可能发生的各类突发事件而制订的应急预案。由各公路交通运输企业组织制订并实施。
    """
    
    print(f"正在请求模型 {MODEL_NAME} 进行知识抽取...")
    llm_response = call_llm_extractor(sample_text)
    
    if llm_response:
        print("抽取成功，正在解析与保存...")
        graph_data = parse_llm_json(llm_response)
        save_to_csv(graph_data)
        print("处理完成！")