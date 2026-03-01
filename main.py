"""
AstrBot 算卦插件
支持指令调用和 LLM 函数工具调用两种方式
"""

import json
import random
import re
from pathlib import Path
from typing import Optional

from astrbot.api import llm_tool, logger, star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import Reply
from astrbot.api.star import Context


# 八卦线条映射
TRIGRAM_LINES: dict[str, list[str]] = {
    "☰": ["━━━", "━━━", "━━━"],  # 乾
    "☷": ["━ ━", "━ ━", "━ ━"],  # 坤
    "☳": ["━ ━", "━ ━", "━━━"],  # 震
    "☶": ["━━━", "━ ━", "━ ━"],  # 艮
    "☲": ["━━━", "━ ━", "━━━"],  # 离
    "☵": ["━ ━", "━━━", "━ ━"],  # 坎
    "☱": ["━━━", "━━━", "━ ━"],  # 兑
    "☴": ["━ ━", "━━━", "━━━"],  # 巽
}

# 线条到八卦符号的反向映射
LINES_TO_TRIGRAM: dict[str, str] = {
    "━━━━━━━━━━": "☰",  # 三阳
    "━ ━━ ━━ ━": "☷",  # 三阴
    "━ ━━ ━━━━━": "☳",  # 初阳
    "━━━━━ ━━ ━": "☶",  # 上阳
    "━━━━━ ━━━━━": "☲",  # 中阴
    "━ ━━━━━━━━ ━": "☵",  # 中阳
    "━━━━━━━━━━━ ━": "☱",  # 上阴
    "━ ━━━━━━━━━━━": "☴",  # 初阴
}

# 爻的名称
YAO_NAMES = ["初爻", "二爻", "三爻", "四爻", "五爻", "上爻"]


def get_hexagram_display(hexagram_data: dict) -> str:
    """将卦象转换为六行显示格式"""
    gua_xiang = hexagram_data.get("卦象", "")
    
    if not gua_xiang:
        return "卦象缺失"
    
    if len(gua_xiang) == 1 and gua_xiang in TRIGRAM_LINES:
        lines = TRIGRAM_LINES[gua_xiang]
        return "\n".join(lines + lines)
    
    if len(gua_xiang) == 2:
        upper_lines = TRIGRAM_LINES.get(gua_xiang[0], ["?", "?", "?"])
        lower_lines = TRIGRAM_LINES.get(gua_xiang[1], ["?", "?", "?"])
        return "\n".join(upper_lines + lower_lines)
    
    return gua_xiang


def validate_hexagram_data(data: dict, name: str) -> bool:
    """验证卦象数据结构"""
    required_fields = ["卦象", "性质", "含义", "爻辞"]
    for field in required_fields:
        if field not in data:
            logger.warning(f"卦象「{name}」缺少字段: {field}")
            return False
        if field == "爻辞" and not isinstance(data[field], list):
            logger.warning(f"卦象「{name}」爻辞字段类型错误")
            return False
    return True


def get_hexagram_lines(hexagram_data: dict) -> list[str]:
    """获取卦象的六爻线条列表"""
    gua_xiang = hexagram_data.get("卦象", "")
    
    if len(gua_xiang) == 1 and gua_xiang in TRIGRAM_LINES:
        lines = TRIGRAM_LINES[gua_xiang]
        return lines + lines
    
    if len(gua_xiang) == 2:
        upper_lines = TRIGRAM_LINES.get(gua_xiang[0], ["?", "?", "?"])
        lower_lines = TRIGRAM_LINES.get(gua_xiang[1], ["?", "?", "?"])
        return upper_lines + lower_lines
    
    return ["?"] * 6


def flip_line(line: str) -> str:
    """翻转爻线（阳变阴，阴变阳）"""
    if "━━━" in line and "━ ━" not in line:
        return "━ ━"  # 阳变阴
    elif "━ ━" in line:
        return "━━━"  # 阴变阳
    return line


def lines_to_trigram_symbol(lines: list[str]) -> str:
    """将三爻线条转换为八卦符号"""
    joined = "".join(lines)
    return LINES_TO_TRIGRAM.get(joined, "?")


def find_hexagram_by_lines(lines: list[str], hexagrams: dict) -> Optional[tuple[str, dict]]:
    """根据六爻线条查找对应的卦"""
    if len(lines) != 6:
        return None
    
    # 分为上下卦
    upper_lines = lines[0:3]  # 上卦（4、5、6爻）
    lower_lines = lines[3:6]  # 下卦（1、2、3爻）
    
    upper_symbol = lines_to_trigram_symbol(upper_lines)
    lower_symbol = lines_to_trigram_symbol(lower_lines)
    
    # 查找对应的卦
    for name, data in hexagrams.items():
        gua_xiang = data.get("卦象", "")
        if len(gua_xiang) == 1:
            # 纯卦
            if gua_xiang == upper_symbol == lower_symbol:
                return name, data
        elif len(gua_xiang) == 2:
            if gua_xiang[0] == upper_symbol and gua_xiang[1] == lower_symbol:
                return name, data
    
    return None


class SuanguaPlugin(star.Star):
    """算卦插件 - 支持指令调用和 LLM 工具调用"""
    
    def __init__(self, context: Context):
        super().__init__(context)
        self._hexagrams: dict[str, dict] = {}
        self._loaded = False
    
    def _load_hexagrams(self) -> bool:
        """加载六十四卦数据"""
        if self._loaded:
            return bool(self._hexagrams)
        
        data_file = Path(__file__).parent / "hexagrams.json"
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            
            valid_count = 0
            for name, data in raw_data.items():
                if validate_hexagram_data(data, name):
                    self._hexagrams[name] = data
                    valid_count += 1
            
            if valid_count == 0:
                logger.error("没有有效的卦象数据")
                return False
            
            logger.info(f"算卦插件成功加载 {valid_count} 个卦象")
            self._loaded = True
            return True
            
        except FileNotFoundError:
            logger.error(f"卦象数据文件不存在: {data_file}")
            return False
        except json.JSONDecodeError as e:
            logger.error(f"卦象数据文件格式错误: {e}")
            return False
        except Exception as e:
            logger.error(f"加载卦象数据失败: {e}")
            return False
    
    async def initialize(self):
        """插件初始化"""
        if self._load_hexagrams():
            logger.info("算卦插件初始化完成")
        else:
            logger.warning("算卦插件初始化失败，请检查 hexagrams.json 文件")
    
    def _get_reply_content(self, event: AstrMessageEvent) -> tuple[bool, str]:
        """获取引用消息的内容"""
        messages = event.get_messages()
        for msg in messages:
            if isinstance(msg, Reply):
                if hasattr(msg, 'message_str') and isinstance(msg.message_str, str) and msg.message_str.strip():
                    return True, msg.message_str.strip()
                
                reply_text = ""
                if hasattr(msg, 'chain') and msg.chain:
                    for comp in msg.chain:
                        if hasattr(comp, 'text') and isinstance(comp.text, str):
                            reply_text += comp.text
                if reply_text.strip():
                    return True, reply_text.strip()
                
        return False, ""
    
    def _generate_changing_yaos(self) -> list[int]:
        """生成变爻位置（返回要变化的爻的位置索引，0-5）"""
        # 随机选择0-6个变爻，权重分布
        num_changes = random.choices([0, 1, 2, 3, 4, 5, 6], weights=[10, 30, 25, 20, 10, 4, 1])[0]
        if num_changes == 0:
            return []
        
        all_positions = list(range(6))
        return random.sample(all_positions, num_changes)
    
    def _apply_changes(self, original_lines: list[str], changing_positions: list[int]) -> list[str]:
        """应用变爻，返回新的六爻"""
        new_lines = original_lines.copy()
        for pos in changing_positions:
            new_lines[pos] = flip_line(original_lines[pos])
        return new_lines
    
    def _build_divination_result(
        self, 
        hexagram_name: str, 
        hexagram_data: dict, 
        question: str = "",
        include_change: bool = False
    ) -> tuple[str, Optional[str], Optional[dict]]:
        """构建算卦结果，返回 (结果文本, 变卦名称, 变卦数据)"""
        lines = []
        
        # 本卦信息
        hexagram_display = get_hexagram_display(hexagram_data)
        lines.append(f"【{hexagram_name}卦】")
        lines.append(hexagram_display)
        lines.append(f"卦性：{hexagram_data.get('性质', '未知')}")
        lines.append(f"含义：{hexagram_data.get('含义', '未知')}")
        
        # 变卦处理
        changed_hexagram_name = None
        changed_hexagram_data = None
        changing_yaos = []
        
        if include_change:
            original_lines = get_hexagram_lines(hexagram_data)
            changing_yaos = self._generate_changing_yaos()
            
            if changing_yaos:
                # 应用变爻
                new_lines = self._apply_changes(original_lines, changing_yaos)
                
                # 查找变卦
                result = find_hexagram_by_lines(new_lines, self._hexagrams)
                if result:
                    changed_hexagram_name, changed_hexagram_data = result
        
        # 爻辞
        yao_ci_list = hexagram_data.get('爻辞', [])
        if yao_ci_list:
            # 如果有变爻，优先选择变爻位置的爻辞
            if changing_yaos:
                yao_index = changing_yaos[0]  # 取第一个变爻
                if yao_index < len(yao_ci_list):
                    yao_ci = yao_ci_list[yao_index]
                    lines.append(f"爻辞（{YAO_NAMES[yao_index]}动）：{yao_ci}")
                else:
                    yao_ci = random.choice(yao_ci_list)
                    lines.append(f"爻辞：{yao_ci}")
            else:
                yao_ci = random.choice(yao_ci_list)
                lines.append(f"爻辞：{yao_ci}")
        
        # 变卦信息
        if changed_hexagram_name and changed_hexagram_data:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━")
            lines.append(f"【变卦：{changed_hexagram_name}卦】")
            changed_display = get_hexagram_display(changed_hexagram_data)
            lines.append(changed_display)
            lines.append(f"卦性：{changed_hexagram_data.get('性质', '未知')}")
            lines.append(f"含义：{changed_hexagram_data.get('含义', '未知')}")
            
            # 变爻说明
            yao_names = [YAO_NAMES[pos] for pos in changing_yaos]
            lines.append(f"变爻：{'、'.join(yao_names)}")
        
        # 运势指引
        interpretations = [
            "当前运势稳中有进，宜保持耐心。",
            "事业方面：脚踏实地，稳扎稳打。",
            "感情方面：真诚待人，缘分自来。",
            "财运方面：量入为出，积少成多。",
            "健康方面：劳逸结合，注意休息。"
        ]
        
        lines.append("")
        lines.append("运势指引：")
        for interp in random.sample(interpretations, min(3, len(interpretations))):
            lines.append(f"  • {interp}")
        
        result = "\n".join(lines)
        
        if question:
            result = f"求卦问题：{question}\n\n{result}"
        
        return result, changed_hexagram_name, changed_hexagram_data
    
    def _extract_hexagram_name(self, content: str) -> Optional[str]:
        """从内容中提取卦名（使用正则）"""
        pattern = r"【(.+?)卦】"
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            name = match.group(1)
            if name in self._hexagrams:
                return name
        return None
    
    async def _get_ai_interpretation(
        self, 
        event: AstrMessageEvent, 
        hexagram_name: str, 
        hexagram_data: dict,
        changed_name: str = None,
        changed_data: dict = None
    ) -> str:
        """调用 AI 进行解卦"""
        try:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        except Exception as e:
            logger.error(f"获取 provider 失败: {e}")
            return "未检测到可用的大语言模型提供商。"
        
        if not provider:
            return "未检测到可用的大语言模型提供商。"
        
        user_prompt = f"""请根据以下卦象为求卦者解卦。

本卦：{hexagram_name}
卦象：{hexagram_data.get('卦象', '未知')}
性质：{hexagram_data.get('性质', '未知')}
基本含义：{hexagram_data.get('含义', '未知')}"""

        if changed_name and changed_data:
            user_prompt += f"""

变卦：{changed_name}
卦象：{changed_data.get('卦象', '未知')}
性质：{changed_data.get('性质', '未知')}
基本含义：{changed_data.get('含义', '未知')}

请结合本卦和变卦进行综合解卦，说明事物的发展变化。"""

        user_prompt += """

请提供详细的解卦分析，用通俗易懂的语言，给出积极正面的指引。"""

        system_prompt = "你是一位精通易经的算命大师，擅长用通俗易懂的语言为人们解卦指引。"
        
        try:
            llm_resp = await provider.text_chat(
                prompt=user_prompt,
                context=[],
                system_prompt=system_prompt,
                image_urls=[],
            )
            
            completion_text = getattr(llm_resp, "completion_text", None)
            if completion_text and isinstance(completion_text, str) and completion_text.strip():
                return completion_text.strip()
            
            text = getattr(llm_resp, "text", None)
            if text and isinstance(text, str) and text.strip():
                return text.strip()
            
            logger.warning(f"AI 返回内容为空，响应对象: {type(llm_resp).__name__}")
            return "AI未返回有效内容，请稍后重试。"
            
        except Exception as e:
            logger.error(f"AI 解卦失败: {e}")
            return "AI解卦出错，请稍后重试。"
    
    # ==================== LLM 工具调用 ====================
    
    @llm_tool(name="divine_hexagram")
    async def divine_hexagram(self, event: AstrMessageEvent, question: str = "", change: bool = False) -> str:
        """易经算卦工具。当用户想要算卦、占卜、预测运势、询问未来时使用此工具。
        调用此工具后，请在回复中完整展示卦象结果，然后进行解卦分析。
        
        Args:
            question(string): 用户想要询问的问题或想要了解的方面（可选）
            change(boolean): 是否启用变卦功能（可选，默认否）
        """
        # 检查是否有引用消息
        has_reply, reply_content = self._get_reply_content(event)
        if has_reply and reply_content:
            question = reply_content
        
        if not self._hexagrams:
            if not self._load_hexagrams():
                return "卦象数据加载失败，请联系管理员检查插件配置。"
        
        hexagram_name = random.choice(list(self._hexagrams.keys()))
        hexagram_data = self._hexagrams[hexagram_name]
        
        result, _, _ = self._build_divination_result(hexagram_name, hexagram_data, question, include_change=change)
        
        # 添加提示，让AI在回复中展示结果
        result += "\n\n---\n请在回复中完整展示以上卦象结果，然后进行详细的解卦分析。"
        
        return result
    
    # ==================== 指令调用 ====================
    
    @filter.command("suangua", alias={"算卦", "算一卦", "占卜"})
    async def divine(self, event: AstrMessageEvent):
        """算卦 - 生成卦象并输出本地解卦"""
        logger.info("收到算卦请求")
        
        if not self._hexagrams:
            if not self._load_hexagrams():
                event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。"))
                return
        
        hexagram_name = random.choice(list(self._hexagrams.keys()))
        hexagram_data = self._hexagrams[hexagram_name]
        
        result, _, _ = self._build_divination_result(hexagram_name, hexagram_data, include_change=False)
        result += "\n\n💡 引用此消息发送「ai解卦」可获取AI详细解读\n💡 发送「变卦」可获取带变卦的算卦结果"
        
        event.set_result(MessageEventResult().message(result))
    
    @filter.command("biangua", alias={"变卦", "变一卦"})
    async def divine_with_change(self, event: AstrMessageEvent):
        """变卦 - 生成带变卦的算卦结果"""
        logger.info("收到变卦请求")
        
        if not self._hexagrams:
            if not self._load_hexagrams():
                event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。"))
                return
        
        hexagram_name = random.choice(list(self._hexagrams.keys()))
        hexagram_data = self._hexagrams[hexagram_name]
        
        result, _, _ = self._build_divination_result(hexagram_name, hexagram_data, include_change=True)
        result += "\n\n💡 引用此消息发送「ai解卦」可获取AI详细解读"
        
        event.set_result(MessageEventResult().message(result))
    
    @filter.command("aijiiegua", alias={"ai解卦", "AI解卦"})
    async def ai_divine(self, event: AstrMessageEvent):
        """AI解卦 - 引用算卦结果进行AI解卦"""
        logger.info("收到AI解卦请求")
        
        if not self._hexagrams:
            event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。"))
            return
        
        has_reply, reply_content = self._get_reply_content(event)
        
        if not has_reply or not reply_content:
            event.set_result(MessageEventResult().message("请引用算卦结果后再发送「ai解卦」"))
            return
        
        # 提取本卦名
        hexagram_name = self._extract_hexagram_name(reply_content)
        
        if not hexagram_name:
            event.set_result(MessageEventResult().message("无法识别引用的卦象，请引用正确的算卦结果"))
            return
        
        hexagram_data = self._hexagrams[hexagram_name]
        
        # 检查是否有变卦
        changed_name = None
        changed_data = None
        change_match = re.search(r"【变卦：(.+?)卦】", reply_content)
        if change_match:
            changed_name = change_match.group(1)
            if changed_name in self._hexagrams:
                changed_data = self._hexagrams[changed_name]
        
        # 先发送等待提示
        await event.send(event.plain_result(f"正在为您AI解卦【{hexagram_name}卦】，请稍候..."))
        
        ai_result = await self._get_ai_interpretation(
            event, hexagram_name, hexagram_data, changed_name, changed_data
        )
        
        hexagram_display = get_hexagram_display(hexagram_data)
        result = f"【{hexagram_name}卦 · AI解卦】\n"
        result += f"{hexagram_display}\n"
        result += f"卦性：{hexagram_data.get('性质', '未知')}\n"
        
        if changed_name and changed_data:
            result += f"\n变卦：{changed_name}卦\n"
        
        result += f"\n{ai_result}"
        
        event.set_result(MessageEventResult().message(result))
    
    @filter.command("guaxiang", alias={"卦象", "查卦"})
    async def hexagram_info(self, event: AstrMessageEvent, name: str = ""):
        """卦象查询
        
        Args:
            name: 卦名
        """
        if not self._hexagrams:
            event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。"))
            return
        
        hexagram_name = name.strip() if name else ""
        
        if not hexagram_name or hexagram_name not in self._hexagrams:
            available = "、".join(list(self._hexagrams.keys())[:8]) + "..."
            event.set_result(MessageEventResult().message(f"未找到「{hexagram_name}」卦\n可查询的卦象包括：{available}"))
            return
        
        hexagram_data = self._hexagrams[hexagram_name]
        hexagram_display = get_hexagram_display(hexagram_data)
        
        result = f"【{hexagram_name}卦】\n"
        result += f"{hexagram_display}\n\n"
        result += f"性质：{hexagram_data.get('性质', '未知')}\n"
        result += f"含义：{hexagram_data.get('含义', '未知')}\n\n"
        result += "爻辞：\n"
        for yao in hexagram_data.get('爻辞', []):
            result += f"  {yao}\n"
        
        event.set_result(MessageEventResult().message(result))
    
    @filter.command("liushisigua", alias={"六十四卦", "卦列表"})
    async def list_hexagrams(self, event: AstrMessageEvent):
        """六十四卦列表"""
        if not self._hexagrams:
            event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。"))
            return
        
        result = "【六十四卦列表】\n\n"
        
        hexagrams = list(self._hexagrams.keys())
        for i in range(0, len(hexagrams), 8):
            batch = hexagrams[i:i+8]
            result += "、".join(batch) + "\n"
        
        result += "\n💡 使用「卦象+卦名」查询详细信息"
        
        event.set_result(MessageEventResult().message(result))
    
    async def terminate(self):
        """插件销毁"""
        logger.info("算卦插件已卸载")
