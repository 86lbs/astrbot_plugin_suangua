"""
AstrBot 算卦插件
支持指令调用和 LLM 函数工具调用两种方式
采用传统金钱卦起卦法
"""

import asyncio
import json
import random
import re
from pathlib import Path
from typing import Optional

from astrbot.api import llm_tool, logger, star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import Plain, Reply
from astrbot.api.star import Context
from astrbot.core.config.astrbot_config import AstrBotConfig


# 常量定义
YAO_COUNT = 6  # 六爻
YAO_NAMES = ["初爻", "二爻", "三爻", "四爻", "五爻", "上爻"]  # 从下到上

# 八卦线条映射（初爻→上爻顺序，即从下到上）
# 索引0=初爻，索引1=二爻，索引2=三爻
TRIGRAM_LINES: dict[str, list[str]] = {
    "☰": ["━━━", "━━━", "━━━"],  # 乾：三阳
    "☷": ["━ ━", "━ ━", "━ ━"],  # 坤：三阴
    "☳": ["━━━", "━ ━", "━ ━"],  # 震：初阳二三阴（下阳上阴）
    "☶": ["━ ━", "━ ━", "━━━"],  # 艮：初阴二阴三阳（下阴上阳）
    "☲": ["━━━", "━ ━", "━━━"],  # 离：初阳中阴上阳
    "☵": ["━ ━", "━━━", "━ ━"],  # 坎：初阴中阳上阴
    "☱": ["━ ━", "━━━", "━━━"],  # 兑：初阴二阳三阳（下阴上阳）
    "☴": ["━━━", "━━━", "━ ━"],  # 巽：初阳二阳三阴（下阳上阴）
}

# 程序化构造：线条到八卦符号的反向映射
# 从 TRIGRAM_LINES 自动生成，避免手写不一致
LINES_TO_TRIGRAM: dict[str, str] = {}
for symbol, lines in TRIGRAM_LINES.items():
    key = "".join(lines)
    LINES_TO_TRIGRAM[key] = symbol

# 金钱卦结果映射
COIN_RESULT = {
    6: {"name": "老阴", "line": "━ ━", "changing": True},
    7: {"name": "少阳", "line": "━━━", "changing": False},
    8: {"name": "少阴", "line": "━ ━", "changing": False},
    9: {"name": "老阳", "line": "━━━", "changing": True},
}


def get_hexagram_display(
    hexagram_data: dict, 
    changing_positions: Optional[list[int]] = None
) -> str:
    """将卦象转换为六行显示格式，标记变爻
    
    显示顺序：从上爻到初爻（传统卦象显示方式）
    
    Args:
        hexagram_data: 卦象数据字典
        changing_positions: 变爻位置列表（从0开始，0=初爻）
    
    Returns:
        格式化的卦象显示字符串
    """
    gua_xiang = hexagram_data.get("卦象", "")
    lines = []
    
    # 构建 all_lines：索引0=初爻，索引5=上爻
    if len(gua_xiang) == 1 and gua_xiang in TRIGRAM_LINES:
        # 纯卦：上下卦相同
        display_lines = TRIGRAM_LINES[gua_xiang]
        all_lines = display_lines + display_lines  # 初爻→上爻
    elif len(gua_xiang) == 2:
        # 重卦：gua_xiang[0]=上卦符号，gua_xiang[1]=下卦符号
        upper_lines = TRIGRAM_LINES.get(gua_xiang[0], ["?", "?", "?"])
        lower_lines = TRIGRAM_LINES.get(gua_xiang[1], ["?", "?", "?"])
        # all_lines: 索引0-2=下卦（初二三爻），索引3-5=上卦（四五上爻）
        all_lines = lower_lines + upper_lines
    else:
        return gua_xiang
    
    # 从上爻到初爻显示（索引 5 到 0）
    for i in range(YAO_COUNT - 1, -1, -1):
        line_text = all_lines[i]
        if changing_positions and i in changing_positions:
            # 标记变爻：老阳变阴○，老阴变阳×
            if "━━━" in line_text:
                line_text += " ○"
            else:
                line_text += " ×"
        lines.append(line_text)
    
    return "\n".join(lines)


def validate_hexagram_data(data: dict, name: str) -> bool:
    """验证卦象数据结构（严格校验）
    
    Args:
        data: 卦象数据字典
        name: 卦名（用于日志）
    
    Returns:
        数据是否有效
    """
    required_fields = ["卦象", "性质", "含义", "爻辞"]
    
    # 检查必需字段
    for field in required_fields:
        if field not in data:
            logger.warning(f"卦象 {name} 缺少字段: {field}")
            return False
    
    # 检查卦象格式
    gua_xiang = data.get("卦象", "")
    if len(gua_xiang) not in [1, 2]:
        logger.warning(f"卦象 {name} 的卦象格式无效: {gua_xiang}")
        return False
    
    # 检查爻辞：必须是长度为6的列表，每个元素都是字符串
    yao_ci = data.get("爻辞")
    if not isinstance(yao_ci, list):
        logger.warning(f"卦象 {name} 的爻辞不是列表")
        return False
    if len(yao_ci) != YAO_COUNT:
        logger.warning(f"卦象 {name} 的爻辞长度不是6: {len(yao_ci)}")
        return False
    for i, yao in enumerate(yao_ci):
        if not isinstance(yao, str):
            logger.warning(f"卦象 {name} 的第{i}爻不是字符串")
            return False
    
    return True


def throw_three_coins() -> dict:
    """模拟抛掷三枚铜钱
    
    正面为阳(3)，反面为阴(2)
    总和：6=老阴(变)，7=少阳，8=少阴，9=老阳(变)
    
    Returns:
        包含爻象信息的字典
    """
    coins = [random.choice([2, 3]) for _ in range(3)]
    total = sum(coins)
    return COIN_RESULT[total]


def lines_to_hexagram(lines: list[str], hexagrams: dict) -> Optional[tuple[str, dict]]:
    """根据六爻线条查找对应的卦
    
    Args:
        lines: 从初爻到上爻的线条列表（索引0=初爻）
        hexagrams: 卦象数据字典
    
    Returns:
        (卦名, 卦象数据) 或 None（匹配失败）
    """
    if len(lines) != YAO_COUNT:
        return None
    
    # 下卦（初二三爻）：lines[0:3]
    # 上卦（四五上爻）：lines[3:6]
    lower_lines = lines[0:3]
    upper_lines = lines[3:6]
    
    lower_key = "".join(lower_lines)
    upper_key = "".join(upper_lines)
    
    lower_symbol = LINES_TO_TRIGRAM.get(lower_key)
    upper_symbol = LINES_TO_TRIGRAM.get(upper_key)
    
    if lower_symbol is None or upper_symbol is None:
        logger.warning(f"无法识别卦象: 下卦={lower_key}, 上卦={upper_key}")
        return None
    
    # 在卦象数据中查找匹配的卦
    for name, data in hexagrams.items():
        gua_xiang = data.get("卦象", "")
        if len(gua_xiang) == 1:
            # 纯卦（上下卦相同）
            if gua_xiang == upper_symbol == lower_symbol:
                return name, data
        elif len(gua_xiang) == 2:
            # 重卦：gua_xiang[0]=上卦，gua_xiang[1]=下卦
            if gua_xiang[0] == upper_symbol and gua_xiang[1] == lower_symbol:
                return name, data
    
    return None


def apply_changing_yaos(lines: list[str], changing_positions: list[int]) -> list[str]:
    """应用变爻，返回新的六爻
    
    老阳变阴，老阴变阳
    
    Args:
        lines: 原始六爻线条（初爻→上爻）
        changing_positions: 变爻位置列表
    
    Returns:
        变化后的六爻线条
    """
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
        self._config = config
        # 配置项（带默认值）
        self._enable_changing = True
        self._show_divination_process = False
        self._ai_divine_use_t2i = True
    
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
            
        except Exception as e:
            logger.error(f"加载卦象数据失败: {e}")
            return False
    
    def _load_config(self) -> None:
        """加载插件配置"""
        if self._config:
            try:
                self._enable_changing = self._config.get("enable_changing", True)
                self._show_divination_process = self._config.get("show_divination_process", False)
                self._ai_divine_use_t2i = self._config.get("ai_divine_use_t2i", True)
                logger.info(f"算卦插件配置：变卦={'开启' if self._enable_changing else '关闭'}，AI解卦T2I={'开启' if self._ai_divine_use_t2i else '关闭'}")
            except Exception as e:
                logger.warning(f"读取插件配置失败，使用默认值: {e}")
    
    def _validate_mapping_consistency(self) -> bool:
        """验证卦象映射一致性
        
        检查所有加载的卦象是否能正确映射到八卦符号
        
        Returns:
            映射是否一致
        """
        for name, data in self._hexagrams.items():
            gua_xiang = data.get("卦象", "")
            if len(gua_xiang) == 1:
                if gua_xiang not in TRIGRAM_LINES:
                    logger.error(f"卦象 {name} 的符号 {gua_xiang} 不在八卦映射中")
                    return False
            elif len(gua_xiang) == 2:
                if gua_xiang[0] not in TRIGRAM_LINES:
                    logger.error(f"卦象 {name} 的上卦符号 {gua_xiang[0]} 不在八卦映射中")
                    return False
                if gua_xiang[1] not in TRIGRAM_LINES:
                    logger.error(f"卦象 {name} 的下卦符号 {gua_xiang[1]} 不在八卦映射中")
                    return False
        return True
    
    async def initialize(self) -> None:
        """插件初始化"""
        self._load_config()
        if self._load_hexagrams():
            if self._validate_mapping_consistency():
                logger.info("算卦插件初始化完成，卦象映射验证通过")
            else:
                logger.warning("算卦插件初始化完成，但卦象映射存在不一致")
        else:
            logger.warning("算卦插件初始化失败，请检查 hexagrams.json 文件")
    
    def _get_reply_content(self, event: AstrMessageEvent) -> tuple[bool, str, Optional[str]]:
        """获取引用消息的内容
        
        Args:
            event: 消息事件
        
        Returns:
            (是否找到引用, 引用内容, 消息ID)
        """
        messages = event.get_messages()
        
        for msg in messages:
            if isinstance(msg, Reply):
                message_id = getattr(msg, 'message_id', None)
                
                # 方法1：直接使用 message_str
                if hasattr(msg, 'message_str') and isinstance(msg.message_str, str) and msg.message_str.strip():
                    return True, msg.message_str.strip(), message_id
                
                # 方法2：从 chain 中提取文本
                if hasattr(msg, 'chain') and msg.chain:
                    reply_text = ""
                    for comp in msg.chain:
                        if isinstance(comp, Plain) and hasattr(comp, 'text'):
                            reply_text += comp.text
                        elif hasattr(comp, 'text') and isinstance(comp.text, str):
                            reply_text += comp.text
                    if reply_text.strip():
                        return True, reply_text.strip(), message_id
                
                # 方法3：使用 text 字段
                if hasattr(msg, 'text') and isinstance(msg.text, str) and msg.text.strip():
                    return True, msg.text.strip(), message_id
        
        return False, "", None
    
    def _build_divination_result(
        self, 
        hexagram_name: str, 
        hexagram_data: dict, 
        changing_positions: list[int],
        changed_hexagram_name: Optional[str] = None,
        changed_hexagram_data: Optional[dict] = None,
        question: str = "",
        divination_process: Optional[list[dict]] = None
    ) -> str:
        """构建算卦结果
        
        Args:
            hexagram_name: 本卦名
            hexagram_data: 本卦数据
            changing_positions: 变爻位置
            changed_hexagram_name: 变卦名
            changed_hexagram_data: 变卦数据
            question: 求卦问题
            divination_process: 起卦过程数据
        
        Returns:
            格式化的算卦结果字符串
        """
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
        
        # 爻辞
        yao_ci_list = hexagram_data.get('爻辞', [])
        if yao_ci_list:
            if changing_positions:
                yao_texts = []
                for pos in changing_positions:
                    if pos < len(yao_ci_list):
                        yao_texts.append(f"{YAO_NAMES[pos]}：{yao_ci_list[pos]}")
                if yao_texts:
                    lines.append("动爻爻辞：")
                    for yt in yao_texts:
                        lines.append(f"  {yt}")
                else:
                    lines.append(f"爻辞：{random.choice(yao_ci_list)}")
            else:
                lines.append(f"爻辞：{random.choice(yao_ci_list)}")
        
        # 变卦信息
        if changed_hexagram_name and changed_hexagram_data:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━")
            lines.append(f"【变卦：{changed_hexagram_name}卦】")
            lines.append(get_hexagram_display(changed_hexagram_data))
            lines.append(f"卦性：{changed_hexagram_data.get('性质', '未知')}")
            lines.append(f"含义：{changed_hexagram_data.get('含义', '未知')}")
            
            # 变卦存在时，changing_positions 应该不为空
            if changing_positions:
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
    
    def _do_divination(
        self, 
        enable_change: bool = True
    ) -> tuple[str, dict, list[int], Optional[str], Optional[dict], list[dict]]:
        """执行算卦
        
        Args:
            enable_change: 是否启用变卦
        
        Returns:
            (本卦名, 本卦数据, 变爻位置, 变卦名, 变卦数据, 起卦过程)
        
        Raises:
            RuntimeError: 卦象匹配失败
        """
        lines = []  # 索引0=初爻，索引5=上爻
        changing_positions = []
        divination_process = []
        
        # 抛掷六次铜钱（从初爻到上爻）
        for i in range(YAO_COUNT):
            result = throw_three_coins()
            lines.append(result["line"])
            
            divination_process.append({
                "name": result["name"],
                "line": result["line"],
                "changing": result["changing"]
            })
            
            if result["changing"]:
                changing_positions.append(i)
        
        if not enable_change:
            changing_positions = []
        
        # 查找本卦
        result = lines_to_hexagram(lines, self._hexagrams)
        if not result:
            # 匹配失败，记录详细信息并报错
            lines_str = ", ".join(lines)
            logger.error(f"卦象匹配失败！六爻线条（初爻→上爻）: [{lines_str}]")
            raise RuntimeError(
                f"卦象解析失败，无法识别六爻组合。"
                f"请检查卦象数据是否完整。六爻: {lines_str}"
            )
        
        hexagram_name, hexagram_data = result
        
        # 计算变卦
        changed_hexagram_name = None
        changed_hexagram_data = None
        if changing_positions:
            new_lines = apply_changing_yaos(lines, changing_positions)
            changed_result = lines_to_hexagram(new_lines, self._hexagrams)
            if changed_result:
                changed_hexagram_name, changed_hexagram_data = changed_result
            else:
                # 变卦匹配失败，记录警告但继续
                new_lines_str = ", ".join(new_lines)
                logger.warning(f"变卦匹配失败！变爻后六爻: [{new_lines_str}]")
        
        return (hexagram_name, hexagram_data, changing_positions, 
                changed_hexagram_name, changed_hexagram_data, divination_process)
    
    async def _get_ai_interpretation(
        self, 
        event: AstrMessageEvent, 
        hexagram_name: str, 
        hexagram_data: dict,
        changed_name: Optional[str] = None,
        changed_data: Optional[dict] = None,
        changing_positions: Optional[list[int]] = None,
        use_t2i: bool = True
    ) -> str:
        """调用 AI 进行解卦（使用当前会话的人格）
        
        Args:
            event: 消息事件
            hexagram_name: 本卦名
            hexagram_data: 本卦数据
            changed_name: 变卦名
            changed_data: 变卦数据
            changing_positions: 变爻位置
            use_t2i: 是否使用T2I（禁用时同时要求AI不使用Markdown）
        
        Returns:
            AI 解卦结果
        """
        try:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        except Exception as e:
            logger.error(f"获取 provider 失败: {e}")
            return "未检测到可用的大语言模型提供商。"
        
        if not provider:
            return "未检测到可用的大语言模型提供商。"
        
        # 获取当前会话的人格
        persona_system_prompt = ""
        try:
            conversation = await self.context.conversation_manager.get_conversation(
                event.unified_msg_origin
            )
            conversation_persona_id = conversation.persona_id if conversation else None
            
            _, persona, _, _ = await self.context.persona_manager.resolve_selected_persona(
                conversation_persona_id=conversation_persona_id,
            )
            
            if persona:
                persona_system_prompt = persona.get("prompt", "")
                # 仅记录元信息，不记录实际内容
                logger.debug(f"已加载人格提示词，长度: {len(persona_system_prompt)}")
        except Exception as e:
            logger.debug(f"获取人格失败，使用默认提示词: {e}")
        
        # 构建用户提示词
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
变爻位置：{'、'.join(yao_names) if yao_names else '未知'}

请结合本卦和变卦进行综合解卦，说明事物的发展变化。"""

        user_prompt += "\n\n请提供详细的解卦分析，用通俗易懂的语言，给出积极正面的指引。"
        
        # 如果禁用T2I，要求AI不使用Markdown语法
        if not use_t2i:
            user_prompt += "\n\n【重要】请使用纯文本格式输出，不要使用任何Markdown语法（如**粗体**、#标题、```代码块等），直接用普通文字表达即可。"
        
        # 如果有人格系统提示词，使用人格的；否则使用默认的
        if persona_system_prompt:
            system_prompt = persona_system_prompt
        else:
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
            
            logger.warning("AI 返回内容为空")
            return "AI未返回有效内容，请稍后重试。"
            
        except asyncio.CancelledError:
            logger.warning("AI 解卦被取消")
            raise
        except Exception as e:
            logger.error(f"AI 解卦失败: {e}")
            return "AI解卦出错，请稍后重试。"
    
    # ==================== LLM 工具调用 ====================
    
    @llm_tool(name="divine_hexagram")
    async def divine_hexagram(self, event: AstrMessageEvent, question: str = "") -> str:
        """易经算卦工具。当用户想要算卦、占卜、预测运势、询问未来时使用此工具。
        采用传统金钱卦起卦法，抛掷六次铜钱确定卦象，根据老阳老阴确定变爻。
        
        Args:
            question(string): 用户想要询问的问题或想要了解的方面（可选）
        """
        has_reply, reply_content, _ = self._get_reply_content(event)
        if has_reply and reply_content:
            question = reply_content
        
        if not self._hexagrams:
            if not self._load_hexagrams():
                return "卦象数据加载失败，请联系管理员检查插件配置。"
        
        try:
            (hexagram_name, hexagram_data, changing_positions,
             changed_name, changed_data, divination_process) = \
                self._do_divination(enable_change=self._enable_changing)
        except RuntimeError as e:
            return f"算卦过程出错：{e}"
        
        result = self._build_divination_result(
            hexagram_name, hexagram_data, changing_positions,
            changed_name, changed_data, question,
            divination_process if self._show_divination_process else None
        )
        
        result += "\n\n---\n请在回复中完整展示以上卦象结果，然后进行详细的解卦分析。"
        
        return result
    
    # ==================== 指令调用 ====================
    
    @filter.command("算卦", alias={"算一卦"})
    async def divine(self, event: AstrMessageEvent) -> None:
        """算卦 - 采用传统金钱卦起卦法"""
        logger.info("收到算卦请求")
        
        if not self._hexagrams:
            if not self._load_hexagrams():
                event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。").use_t2i(False))
                return
        
        try:
            (hexagram_name, hexagram_data, changing_positions,
             changed_name, changed_data, divination_process) = \
                self._do_divination(enable_change=self._enable_changing)
        except RuntimeError as e:
            logger.error(f"算卦失败: {e}")
            event.set_result(MessageEventResult().message(f"算卦过程出错：{e}").use_t2i(False))
            return
        
        result = self._build_divination_result(
            hexagram_name, hexagram_data, changing_positions,
            changed_name, changed_data,
            divination_process=divination_process if self._show_divination_process else None
        )
        result += "\n\n💡 引用此消息发送「AI解卦」可获取AI详细解读"
        
        event.set_result(MessageEventResult().message(result).use_t2i(False))
    
    @filter.command("AI解卦", alias={"ai解卦"})
    async def ai_divine(self, event: AstrMessageEvent) -> None:
        """AI解卦 - 引用算卦结果进行AI解卦（使用当前会话人格）"""
        logger.info("收到AI解卦请求")
        
        # 统一懒加载路径
        if not self._hexagrams:
            if not self._load_hexagrams():
                event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。").use_t2i(False))
                return
        
        has_reply, reply_content, message_id = self._get_reply_content(event)
        logger.info(f"引用消息检测结果: has_reply={has_reply}, message_id={message_id}")
        
        if not has_reply or not reply_content:
            event.set_result(MessageEventResult().message(
                "请引用算卦结果后再发送「AI解卦」\n\n"
                "📝 使用方法：\n"
                "1. 长按算卦结果消息\n"
                "2. 选择「引用」\n"
                "3. 发送「AI解卦」"
            ).use_t2i(False))
            return
        
        # 从引用内容中提取卦名
        match = re.search(r"【(.+?)卦】", reply_content)
        if not match:
            logger.warning("无法从引用内容中提取卦名")
            event.set_result(MessageEventResult().message("无法识别引用的卦象，请引用正确的算卦结果").use_t2i(False))
            return
        
        hexagram_name = match.group(1)
        logger.info(f"提取到卦名: {hexagram_name}")
        
        if hexagram_name not in self._hexagrams:
            event.set_result(MessageEventResult().message(f"未找到「{hexagram_name}」卦").use_t2i(False))
            return
        
        hexagram_data = self._hexagrams[hexagram_name]
        
        # 检查是否有变卦
        changed_name = None
        changed_data = None
        changing_positions = []
        change_match = re.search(r"【变卦：(.+?)卦】", reply_content)
        if change_match:
            changed_name = change_match.group(1)
            if changed_name in self._hexagrams:
                changed_data = self._hexagrams[changed_name]
                # 尝试从引用内容中提取变爻位置
                yao_match = re.search(r"变爻：(.+?)(?:\n|$)", reply_content)
                if yao_match:
                    yao_str = yao_match.group(1)
                    for i, name in enumerate(YAO_NAMES):
                        if name in yao_str:
                            changing_positions.append(i)
        
        # 先发送等待提示
        await event.send(event.plain_result(f"正在为您AI解卦【{hexagram_name}卦】，请稍候..."))
        
        use_t2i = self._ai_divine_use_t2i
        ai_result = await self._get_ai_interpretation(
            event, hexagram_name, hexagram_data, changed_name, changed_data, changing_positions,
            use_t2i=use_t2i
        )
        
        hexagram_display = get_hexagram_display(hexagram_data)
        result = f"【{hexagram_name}卦 · AI解卦】\n"
        result += f"{hexagram_display}\n"
        result += f"卦性：{hexagram_data.get('性质', '未知')}\n"
        
        if changed_name and changed_data:
            result += f"\n变卦：{changed_name}卦\n"
        
        result += f"\n{ai_result}"
        
        event.set_result(MessageEventResult().message(result).use_t2i(use_t2i))
    
    @filter.command("算卦帮助", alias={"卦帮助", "帮助算卦"})
    async def help_info(self, event: AstrMessageEvent) -> None:
        """显示算卦插件帮助信息"""
        help_text = """【易经算卦插件帮助】

📋 指令列表：
  [唤醒词]算卦 [问题]   - 生成卦象
  [唤醒词]算一卦        - 同上
  [唤醒词]AI解卦        - AI详细解读（需引用算卦结果）
  [唤醒词]卦象 <卦名>   - 查询特定卦象
  [唤醒词]六十四卦      - 列出所有卦象

📖 使用方法：
1. 发送 [唤醒词]算卦 进行起卦
2. 长按算卦结果，选择「引用」
3. 发送 [唤醒词]AI解卦 获取AI解读

⚙️ 配置项：
• 启用变卦 - 是否产生变爻
• 显示起卦过程 - 显示抛掷铜钱详情
• AI解卦使用T2I - AI解卦结果转图片

💡 提示：
• [唤醒词]默认为 /，可在管理面板修改
• 可在算卦后附带问题，如：[唤醒词]算卦 事业
• AI解卦会使用当前会话的人格风格"""

        event.set_result(MessageEventResult().message(help_text).use_t2i(False))
    
    @filter.command("卦象", alias={"查卦"})
    async def hexagram_info(self, event: AstrMessageEvent, name: str = "") -> None:
        """卦象查询
        
        Args:
            name: 卦名
        """
        if not self._hexagrams:
            if not self._load_hexagrams():
                event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。").use_t2i(False))
                return
        
        hexagram_name = name.strip() if name else ""
        
        if not hexagram_name or hexagram_name not in self._hexagrams:
            available = "、".join(list(self._hexagrams.keys())[:8]) + "..."
            event.set_result(MessageEventResult().message(f"未找到「{hexagram_name}」卦\n可查询的卦象包括：{available}").use_t2i(False))
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
        
        event.set_result(MessageEventResult().message(result).use_t2i(False))
    
    @filter.command("六十四卦", alias={"卦列表"})
    async def list_hexagrams(self, event: AstrMessageEvent) -> None:
        """六十四卦列表"""
        if not self._hexagrams:
            if not self._load_hexagrams():
                event.set_result(MessageEventResult().message("卦象数据加载失败，请联系管理员检查插件配置。").use_t2i(False))
                return
        
        result = "【六十四卦列表】\n\n"
        
        hexagrams = list(self._hexagrams.keys())
        for i in range(0, len(hexagrams), 8):
            batch = hexagrams[i:i+8]
            result += "、".join(batch) + "\n"
        
        result += "\n💡 使用「卦象+卦名」查询详细信息"
        
        event.set_result(MessageEventResult().message(result).use_t2i(False))
    
    async def terminate(self) -> None:
        """插件销毁"""
        logger.info("算卦插件已卸载")
