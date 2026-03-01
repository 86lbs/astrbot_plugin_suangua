"""
AstrBot 算卦插件
支持指令调用和 LLM 函数工具调用两种方式
采用传统金钱卦起卦法
"""

import json
import random
from pathlib import Path
from typing import Optional

from astrbot.api import llm_tool, logger, star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import Reply
from astrbot.api.star import Context
from astrbot.core.config.astrbot_config import AstrBotConfig


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

# 线条到八卦符号的反向映射（上中下爻组合）
LINES_TO_TRIGRAM: dict[str, str] = {
    "━━━━━━━━━━": "☰",  # 三阳 - 乾
    "━ ━━ ━━ ━": "☷",  # 三阴 - 坤
    "━ ━━ ━━━━━": "☳",  # 下阳 - 震
    "━━━━━ ━━ ━": "☶",  # 上阳 - 艮
    "━━━━━ ━━━━━": "☲",  # 中阴 - 离
    "━ ━━━━━━━━ ━": "☵",  # 中阳 - 坎
    "━━━━━━━━━━━ ━": "☱",  # 上阴 - 兑
    "━ ━━━━━━━━━━━": "☴",  # 下阴 - 巽
}

# 爻的名称（从下到上）
YAO_NAMES = ["初爻", "二爻", "三爻", "四爻", "五爻", "上爻"]

# 金钱卦结果映射
# 三枚铜钱：正面为阳(3)，反面为阴(2)
# 总和：6=老阴(变)，7=少阳(不变)，8=少阴(不变)，9=老阳(变)
COIN_RESULT = {
    6: {"name": "老阴", "line": "━ ━", "changing": True, "symbol": "×"},
    7: {"name": "少阳", "line": "━━━", "changing": False, "symbol": "○"},
    8: {"name": "少阴", "line": "━ ━", "changing": False, "symbol": "○"},
    9: {"name": "老阳", "line": "━━━", "changing": True, "symbol": "○"},
}


def get_hexagram_display(hexagram_data: dict, changing_positions: list[int] = None) -> str:
    """将卦象转换为六行显示格式，标记变爻"""
    gua_xiang = hexagram_data.get("卦象", "")
    lines = []
    
    if len(gua_xiang) == 1 and gua_xiang in TRIGRAM_LINES:
        display_lines = TRIGRAM_LINES[gua_xiang]
        all_lines = display_lines + display_lines
    elif len(gua_xiang) == 2:
        upper_lines = TRIGRAM_LINES.get(gua_xiang[0], ["?", "?", "?"])
        lower_lines = TRIGRAM_LINES.get(gua_xiang[1], ["?", "?", "?"])
        all_lines = upper_lines + lower_lines
    else:
        return gua_xiang
    
    # 从上到下显示（上爻到初爻）
    for i in range(5, -1, -1):
        line_text = all_lines[i]
        if changing_positions and i in changing_positions:
            # 标记变爻
            if "━━━" in line_text:
                line_text += " ○"  # 老阳变阴
            else:
                line_text += " ×"  # 老阴变阳
        lines.append(line_text)
    
    return "\n".join(lines)


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


def throw_three_coins() -> dict:
    """
    模拟抛掷三枚铜钱
    正面为阳(3)，反面为阴(2)
    返回结果：6=老阴，7=少阳，8=少阴，9=老阳
    """
    # 三枚铜钱，每枚正面(3)或反面(2)
    coins = [random.choice([2, 3]) for _ in range(3)]
    total = sum(coins)
    return COIN_RESULT[total]


def divine_six_yaos() -> tuple[list[str], list[int]]:
    """
    传统金钱卦起卦法
    抛掷六次，从初爻到上爻
    返回：(六爻线条列表, 变爻位置列表)
    """
    lines = []
    changing_positions = []
    
    for i in range(6):
        result = throw_three_coins()
        lines.append(result["line"])
        if result["changing"]:
            changing_positions.append(i)  # i 是从下到上的位置 (0=初爻)
    
    return lines, changing_positions


def lines_to_hexagram(lines: list[str], hexagrams: dict) -> Optional[tuple[str, dict]]:
    """
    根据六爻线条查找对应的卦
    lines: 从初爻到上爻的列表
    """
    if len(lines) != 6:
        return None
    
    # 分为上下卦（注意：上卦是4、5、6爻，下卦是1、2、3爻）
    # 在列表中：上卦是索引3-5，下卦是索引0-2
    upper_lines = lines[3:6]  # 上卦
    lower_lines = lines[0:3]  # 下卦
    
    upper_symbol = LINES_TO_TRIGRAM.get("".join(upper_lines), "?")
    lower_symbol = LINES_TO_TRIGRAM.get("".join(lower_lines), "?")
    
    # 查找对应的卦
    for name, data in hexagrams.items():
        gua_xiang = data.get("卦象", "")
        if len(gua_xiang) == 1:
            # 纯卦（上下卦相同）
            if gua_xiang == upper_symbol == lower_symbol:
                return name, data
        elif len(gua_xiang) == 2:
            if gua_xiang[0] == upper_symbol and gua_xiang[1] == lower_symbol:
                return name, data
    
    return None


def apply_changing_yaos(lines: list[str], changing_positions: list[int]) -> list[str]:
    """应用变爻，返回新的六爻"""
    new_lines = lines.copy()
    for pos in changing_positions:
        if "━━━" in lines[pos]:
            new_lines[pos] = "━ ━"  # 阳变阴
        else:
            new_lines[pos] = "━━━"  # 阴变阳
    return new_lines


class SuanguaPlugin(star.Star):
    """算卦插件 - 支持指令调用和 LLM 工具调用"""
    
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self._hexagrams: dict[str, dict] = {}
        self._loaded = False
        # 插件配置
        self._config = config
        self._enable_changing = True  # 启用变卦
        self._show_divination_process = False  # 显示起卦过程
    
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
    
    def _load_config(self):
        """加载插件配置"""
        if self._config:
            try:
                self._enable_changing = self._config.get("enable_changing", True)
                self._show_divination_process = self._config.get("show_divination_process", False)
                logger.info(f"算卦插件配置：变卦={'开启' if self._enable_changing else '关闭'}，显示过程={'开启' if self._show_divination_process else '关闭'}")
            except Exception as e:
                logger.warning(f"读取插件配置失败，使用默认值: {e}")
    
    async def initialize(self):
        """插件初始化"""
        self._load_config()
        if self._load_hexagrams():
            logger.info(f"算卦插件初始化完成")
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
    
    def _build_divination_result(
        self, 
        hexagram_name: str, 
        hexagram_data: dict, 
        changing_positions: list[int],
        changed_hexagram_name: str = None,
        changed_hexagram_data: dict = None,
        question: str = "",
        divination_process: list[dict] = None
    ) -> str:
        """构建算卦结果"""
        lines = []
        
        # 显示起卦过程（可选）
        if divination_process and self._show_divination_process:
            lines.append("【起卦过程】")
            for i, result in enumerate(divination_process):
                lines.append(f"  第{i+1}掷：{result['name']} → {result['line']}")
            lines.append("")
        
        # 本卦信息
        lines.append(f"【{hexagram_name}卦】")
        lines.append(get_hexagram_display(hexagram_data, changing_positions))
        lines.append(f"卦性：{hexagram_data.get('性质', '未知')}")
        lines.append(f"含义：{hexagram_data.get('含义', '未知')}")
        
        # 爻辞 - 如果有变爻，显示变爻位置的爻辞
        yao_ci_list = hexagram_data.get('爻辞', [])
        if yao_ci_list:
            if changing_positions:
                # 显示所有变爻的爻辞
                yao_texts = []
                for pos in changing_positions:
                    if pos < len(yao_ci_list):
                        yao_texts.append(f"{YAO_NAMES[pos]}：{yao_ci_list[pos]}")
                if yao_texts:
                    lines.append("动爻爻辞：")
                    for yt in yao_texts:
                        lines.append(f"  {yt}")
                else:
                    yao_ci = random.choice(yao_ci_list)
                    lines.append(f"爻辞：{yao_ci}")
            else:
                # 无变爻，随机选一爻
                yao_ci = random.choice(yao_ci_list)
                lines.append(f"爻辞：{yao_ci}")
        
        # 变卦信息
        if changed_hexagram_name and changed_hexagram_data:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━")
            lines.append(f"【变卦：{changed_hexagram_name}卦】")
            lines.append(get_hexagram_display(changed_hexagram_data))
            lines.append(f"卦性：{changed_hexagram_data.get('性质', '未知')}")
            lines.append(f"含义：{changed_hexagram_data.get('含义', '未知')}")
            
            # 变爻说明
            yao_names = [YAO_NAMES[pos] for pos in changing_positions]
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
        
        return result
    
    def _do_divination(self, enable_change: bool = True) -> tuple:
        """
        执行算卦
        返回：(卦名, 卦数据, 变爻位置列表, 变卦名, 变卦数据, 起卦过程)
        """
        # 金钱卦起卦
        lines = []
        changing_positions = []
        divination_process = []
        
        for i in range(6):
            result = throw_three_coins()
            lines.append(result["line"])
            
            # 记录起卦过程
            divination_process.append({
                "name": result["name"],
                "line": result["line"],
                "changing": result["changing"]
            })
            
            if result["changing"]:
                changing_positions.append(i)
        
        # 如果关闭变卦功能，清空变爻
        if not enable_change:
            changing_positions = []
        
        # 查找本卦
        result = lines_to_hexagram(lines, self._hexagrams)
        if not result:
            # 如果找不到，随机选一个（理论上不应该发生）
            hexagram_name = random.choice(list(self._hexagrams.keys()))
            hexagram_data = self._hexagrams[hexagram_name]
            changing_positions = []
        else:
            hexagram_name, hexagram_data = result
        
        # 计算变卦
        changed_hexagram_name = None
        changed_hexagram_data = None
        if changing_positions:
            new_lines = apply_changing_yaos(lines, changing_positions)
            changed_result = lines_to_hexagram(new_lines, self._hexagrams)
            if changed_result:
                changed_hexagram_name, changed_hexagram_data = changed_result
        
        return (hexagram_name, hexagram_data, changing_positions, 
                changed_hexagram_name, changed_hexagram_data, divination_process)
    
    async def _get_ai_interpretation(
        self, 
        event: AstrMessageEvent, 
        hexagram_name: str, 
        hexagram_data: dict,
        changed_name: str = None,
        changed_data: dict = None,
        changing_positions: list[int] = None
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
            yao_names = [YAO_NAMES[pos] for pos in (changing_positions or [])]
            user_prompt += f"""

变卦：{changed_name}
卦象：{changed_data.get('卦象', '未知')}
性质：{changed_data.get('性质', '未知')}
基本含义：{changed_data.get('含义', '未知')}
变爻位置：{'、'.join(yao_names)}

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
    async def divine_hexagram(self, event: AstrMessageEvent, question: str = "") -> str:
        """易经算卦工具。当用户想要算卦、占卜、预测运势、询问未来时使用此工具。
        采用传统金钱卦起卦法，抛掷六次铜钱确定卦象，根据老阳老阴确定变爻。
        调用此工具后，请在回复中完整展示卦象结果，然后进行解卦分析。
        
        Args:
            question(string): 用户想要询问的问题或想要了解的方面（可选）
        """
        # 检查是否有引用消息
        has_reply, reply_content = self._get_reply_content(event)
        if has_reply and reply_content:
            question = reply_content
        
        if not self._hexagrams:
            if not self._load_hexagrams():
                return "卦象数据加载失败，请联系管理员检查插件配置。"
        
        # 执行算卦
        (hexagram_name, hexagram_data, changing_positions,
         changed_name, changed_data, divination_process) = \
            self._do_divination(enable_change=self._enable_changing)
        
        result = self._build_divination_result(
            hexagram_name, hexagram_data, changing_positions,
            changed_name, changed_data, question,
            divination_process if self._show_divination_process else None
        )
        
        # 添加提示
        result += "\n\n---\n请在回复中完整展示以上卦象结果，然后进行详细的解卦分析。"
        
        return result
    
    # ==================== 指令调用 ====================
    
    @filter.command("suangua", alias={"算卦", "算一卦", "占卜"})
    async def divine(self, event: AstrMessageEvent):
        """算卦 - 采用传统金钱卦起卦法"""
        logger.info("收到算卦请求")
        
        if not self._hexagrams:
            if not self._load_hexagrams():
                event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。"))
                return
        
        # 执行算卦
        (hexagram_name, hexagram_data, changing_positions,
         changed_name, changed_data, divination_process) = \
            self._do_divination(enable_change=self._enable_changing)
        
        result = self._build_divination_result(
            hexagram_name, hexagram_data, changing_positions,
            changed_name, changed_data,
            divination_process=divination_process if self._show_divination_process else None
        )
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
        import re
        pattern = r"【(.+?)卦】"
        match = re.search(pattern, reply_content)
        if not match:
            event.set_result(MessageEventResult().message("无法识别引用的卦象，请引用正确的算卦结果"))
            return
        
        hexagram_name = match.group(1)
        if hexagram_name not in self._hexagrams:
            event.set_result(MessageEventResult().message(f"未找到「{hexagram_name}」卦"))
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
        for i, yao in enumerate(hexagram_data.get('爻辞', [])):
            result += f"  {YAO_NAMES[i]}：{yao}\n"
        
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
