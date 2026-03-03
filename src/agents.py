import ollama
import base64

# 辅助函数：编码图片
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')
# 定义智能体基类
class BaseAgent:
    def __init__(self, name, role_prompt, model="qwen3-vl:4b"):
        self.name = name
        self.system_prompt = role_prompt
        self.model = model
    
    # 关键修改点：确保这里有 context_data 参数，且默认为 None
    def speak(self, user_input, context_data=None, image_data=None):
        """
        image_data: 图片的 Base64 字符串或路径
        """
        content_text = f"【当前警情】：{user_input}\n"
        if context_data:
            content_text += f"【参考情报】：{context_data}\n"
        
        # 构造多模态消息
        message_payload = {"role": "user", "content": content_text}
        
        # 如果有图，就把图加进去
        if image_data:
            # Ollama API 支持直接传 images 列表
            message_payload["images"] = [image_data] 

        messages = [
            {"role": "system", "content": self.system_prompt},
            message_payload # 用户消息（含图）
        ]
        
        # --- 新增：重试循环 ---
        max_retries = 2
        for attempt in range(max_retries):
            try:
                print(f"DEBUG: {self.name} 正在思考 (尝试 {attempt+1}/{max_retries})...")
                response = ollama.chat(model=self.model, messages=messages)
                result = response['message']['content']
                
                # 如果结果有效（不是空字符串），直接返回
                if result and result.strip():
                    return result
                else:
                    print(f"WARN: {self.name} 返回了空内容，准备重试...")
            
            except Exception as e:
                print(f"ERROR: 调用出错 {e}")
        
        # 如果三次都失败了，返回一个兜底的默认回复
        return f"（{self.name} 思考超时，默认通过）方案审核通过。"

# --- 定义四个角色的 Prompt (人设) ---

PROMPT_ANALYST = """你是指挥中心的【多模态情报研判员】。
职责：结合【用户文字描述】和【现场图片】进行事实核查。

核心逻辑（Visual Grounding）：
1. 你的第一优先级是“视觉证据”,文字描述是对图片内容的补充。
2. 如果图片内容与用户描述不符（例如：用户说是小剐蹭，但图片显示车头严重变形且有烟雾），**必须以图片为准**，并明确指出用户的描述偏差。
3. 从图片中提取关键要素：车型、受损程度、是否起火、是否有人员倒地、路况（雨雪/拥堵）。

输出要求：
1. 如果视觉输入不为空，则输出必须包含【视觉研判】章节。
2. 基于视觉事实和文字描述，推荐图谱检索方向，注意，知识图谱中存储的关键词为中文。
"""

PROMPT_LEGAL = """你是指挥中心的【法规参谋】。
职责：提供法律支持，确保执法合规。
数据来源：你拥有法规库检索结果（Vector Data）。
要求：
1. 引用具体的法律条款名称。
2. 指出必须执行的强制措施（如“保护现场”、“立即抢救”）。
输出风格：严谨、引用法条。"""


PROMPT_CRITIC = """你是指挥中心的【安全审查员】。
职责：批判性审核初版方案。

请严格按照以下【思维链】步骤进行思考和输出：
Step 1: 提取用户描述中的核心车型（是小轿车、大货车，还是危化品车？）。
Step 2: 检查初版方案中调度的资源（如防化兵、重型吊车）是否与 Step 1 的车型匹配。
Step 3: 检查是否遗漏了关键动作（如有人受伤是否调度了救护车）。
Step 4: 基于以上分析，输出最终结论（通过 或 驳回）。

【输出格式要求】：
必须始终输出严格的 JSON 字符串（不得包含额外文字或注释）。使用下列字段：
{
    "status": "APPROVED" 或 "REJECTED",
    "reason": "给出通过或驳回的核心理由",
    "missing_actions": ["如缺少调度的资源/动作，逐条列出"],
    "risk_notes": ["可选，补充风险提醒或条件"]
}
若无需填写某字段，可返回空字符串或空数组，但键名必须存在。
"""

PROMPT_COMMANDER = """你是【首席指挥官】。
职责：汇总情报、法规和审查意见，下达最终指令。
任务：
1. 如果审查员驳回了方案，你必须根据意见进行修正。
2. 生成最终的结构化管控策略（交通流控制、资源调度、信息发布）。
输出风格：权威、指令清晰、分点陈述。"""