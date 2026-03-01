# 更新日志

所有重要的更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [v4.2.0] - 2026-03-01

### 修复（二审反馈）

#### 1. 核心逻辑风险
- ❌ 移除"随机卦回退"逻辑
- ✅ 卦象匹配失败时抛出 `RuntimeError` 并记录详细日志
- ✅ 确保用户看到的卦象与实际起卦结果一致

#### 2. 卦象映射实现
- ✅ 程序化构造 `LINES_TO_TRIGRAM` 映射（从 `TRIGRAM_LINES` 自动生成）
- ✅ 添加 `_validate_mapping_consistency()` 初始化时验证映射一致性
- ✅ 避免手写字符串拼接导致的不一致

#### 3. 懒加载恢复路径
- ✅ `ai_divine()` 添加 `_load_hexagrams()` 调用
- ✅ `hexagram_info()` 添加 `_load_hexagrams()` 调用
- ✅ `list_hexagrams()` 添加 `_load_hexagrams()` 调用
- ✅ 所有入口统一恢复路径

#### 4. 数据校验
- ✅ `validate_hexagram_data()` 补充严格校验
- ✅ 检查爻辞长度必须为 6
- ✅ 检查每个爻辞元素必须是字符串
- ✅ 检查卦象格式（长度为1或2）

### 改进
- 📝 `_get_reply_content()` 返回值增加 `message_id`
- 🛡️ 添加更详细的错误日志

---

## [v4.1.7] - 2026-03-01

### 修复
- 🧹 移除不必要的 f-string（3处）
- 📝 为 `_do_divination` 和 `_get_reply_content` 添加返回类型声明
- 🔧 AI解卦时尝试从引用内容中提取变爻位置

---

## [v4.1.6] - 2026-03-01

### 修复
- 🧹 删除未使用的 `divine_six_yaos()` 死代码
- 📝 修复类型提示：`list[int] = None` → `Optional[list[int]] = None`
- ⚡ 改进异常处理：排除 `asyncio.CancelledError`

---

## [v4.1.0] - 2026-03-01

### 新增
- 🪙 采用传统金钱卦起卦法
- 根据老阳/老阴自然产生变爻

---

## [v4.0.0] - 2026-03-01

### 新增
- 重构插件
- 同时支持指令调用和 LLM 工具调用

---

## 版本命名规则

- **主版本号**: 不兼容的 API 更改
- **次版本号**: 向后兼容的功能新增
- **修订号**: 向后兼容的问题修复
