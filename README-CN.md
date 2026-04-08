# CamBot MVP

仓库内最小可运行的单摄像头机器人拍摄 MVP。整体流程如下：

自然语言指令 -> 本地 JSON RAG 检索 -> Prompt 组装 -> LLM 输出严格 JSON 拍摄计划 -> Pydantic 校验与裁剪 -> Mock 执行循环 -> 打印底层控制命令

这个版本刻意保持最小化：

- 不做前端
- 不使用 LangGraph
- 不做多机器人协同
- 不向真实底盘或升降硬件发命令
- 机械臂复用现有 `RoArm-M2-S_python` 代码，通过适配器接入

## 当前 MVP 范围

- 接收一条自然语言拍摄指令
- 从 `rag/` 中检索模板、技能规则和安全规则
- 构建严格要求只输出 JSON 的 Prompt
- 调用兼容 OpenAI 接口的 Qwen 模型
- 未配置 Qwen 时默认回退到 Mock JSON
- 使用 Pydantic 进行校验和参数裁剪
- 运行 Mock CamBot 执行循环
- 清晰打印并记录底盘、升降、机械臂控制命令

## 目录结构

```text
app.py
config/
  default.yaml
rag/
  shot_templates.json
  skill_rules.json
  safety_rules.json
chain/
  retriever.py
  prompt_builder.py
  planner.py
  validator.py
schemas/
  script_schema.py
runtime/
  tracker.py
  framing_controller.py
  safety_controller.py
  cambot_executor.py
  base_controller.py
  lift_controller.py
  arm_adapter.py
providers/
  llm_provider.py
utils/
  logger.py
  io.py
logs/
RoArm-M2-S_python/
  roarm_motion_api.py
  ...
```

## Qwen 配置方式

Qwen 的 API 配置直接写在 [`config/default.yaml`]里，不需要设置环境变量。

```yaml
llm:
  provider: qwen_openai_compatible
  api_key: "your_api_key"
  base_url: "https://your-openai-compatible-endpoint/v1"
  model: "qwen-plus"
  temperature: 0.1
  timeout_s: 30
  use_mock_when_unconfigured: true
```

如果 `api_key` 或 `base_url` 留空，系统会默认使用 Mock 规划结果。

## 依赖安装

建议最小依赖：

```bash
pip install pydantic PyYAML langchain-core langchain-openai pyserial
```

说明：

- `pyserial` 只有在后续启用真实机械臂连接时才需要
- 即使没有配置真实 Qwen，默认 Mock 模式也可以跑通

## 运行方式

Mock 模式默认在 [`config/default.yaml`]中开启。

```bash
python app.py --instruction "Give me a smooth medium follow shot, keep the subject near the center, then stop at the end."
```

如果不传 `--instruction`，程序会进入交互式输入。

## 示例输出 JSON

```json
{
  "shot_plan": {
    "template": "mid_follow",
    "duration_s": 8,
    "distance_m": 2.2,
    "height_m": 1.2,
    "subject_region": "center",
    "subject_scale_target": 0.4
  },
  "robot_task": {
    "name": "track_subject_with_framing"
  },
  "safety_rules": {
    "max_speed": 0.5,
    "min_distance": 0.8,
    "lost_target_action": "slow_stop_and_search"
  },
  "fallback": {
    "template": "mid_follow_safe"
  }
}
```

## 控制命令打印位置

运行时不会把底层命令真正发到硬件，而是先打印并写日志，便于检查：

- 底盘命令：`[BASE CMD] ...`
- 升降命令：`[LIFT CMD] ...`
- 机械臂命令：`[ARM CMD] ...`
- 丢失目标占位行为：`[SEARCH] ...`

日志文件在 [`logs/cambot.log`]。

## 后续接入真实硬件

如果后续要替换 Mock 底盘和升降控制，只需要：

1. 在 [`runtime/base_controller.py`]中实现真实底盘通信
2. 在 [`runtime/lift_controller.py`]中实现真实升降控制
3. 保持这些接口不变：
   - `connect()`
   - `move(...)`
   - `move_to(...)`
   - `move_by(...)`
   - `stop()`
   - `close()`
4. 保持 [`runtime/cambot_executor.py`]不变
5. 机械臂已经通过适配器接入，后续只需要在 [`runtime/arm_adapter.py`]中完善 `send_arm_command()` 即可
## 说明

- LLM 只负责高层拍摄语义规划和 JSON 输出
- 底层控制仍然是规则式的，位于 [`runtime/framing_controller.py`] 和 [`runtime/safety_controller.py`]
- 当前 Tracker 是 Mock 实现，但已经预留了 `get_target_state()` 接口，方便后续接真实视觉模块
