# CamBot 交互式 Agent

[English](./README.md) | [简体中文](./README-CN.md)

CamBot 现在使用交互式 LLM Agent 来设计拍摄方案。Agent 内部持续维护结构化 JSON 拍摄计划，给使用者展示自然语言版 review，接收自然语言修改意见，并且只有在使用者确认后才把当前方案视为最终方案。

当前流程：

自然语言拍摄需求 -> 本地 JSON RAG 检索 -> LLM 生成严格 JSON 拍摄计划 -> JSON 修复与校验 -> 自然语言 review -> 使用者继续修改 -> 使用者确认 -> 可选执行器运行

## 当前范围

- 根据一条初始拍摄需求创建规划会话
- 保持当前 JSON 输出 schema 不变
- 给使用者展示“简单摘要 + 详细计划”的自然语言方案
- 支持自然语言多轮修改，直到使用者确认
- 对过于宽泛或模糊的反馈主动追问
- 每个会话保存 JSON、review 文本、对话历史和元信息
- 提供面向未来网页后端调用的 Python service 层
- 继续兼容现有 Qwen/OpenAI-compatible provider 和 mock fallback
- 底层机器人执行仍然独立，并且默认不自动执行

## 目录结构

```text
app.py
agent/
  models.py
  service.py
  reviewer.py
  json_repair.py
  log_store.py
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

## 预留给网页的接口

未来网页后端可以直接调用 `agent.service.PlanAgentService`：

- `create_session(initial_instruction)`
- `send_message(session_id, user_message)`
- `review_plan(session_id)`
- `confirm_plan(session_id)`
- `unconfirm_plan(session_id)`
- `get_current_plan(session_id)`
- `execute_confirmed_plan(session_id)`

返回的 `AgentResponse` 包含 `session_id`、状态、给使用者看的 review 文本、可选 JSON 方案，以及确认状态。

## 命令行使用

`config/default.yaml` 默认开启 mock mode，所以不配置真实 Qwen key 也可以运行。

```bash
python app.py --instruction "Give me a smooth medium follow shot, keep the subject near the center, then stop at the end."
```

如果不传 `--instruction`，命令行会先要求输入初始拍摄需求。

交互命令：

```text
/review     查看当前自然语言拍摄方案
/confirm    确认并保存当前方案
/unconfirm  取消确认，继续修改
/quit       退出
```

除此之外的普通输入都会被当作自然语言修改意见，例如：

```text
把主体放到画面左侧，镜头再靠近一点。
```

执行 `/confirm` 之后，使用者仍然可以继续输入修改意见。系统会自动取消确认，并基于已确认版本继续修改。

## 可选执行

默认情况下，确认只保存方案，不自动运行执行器。若希望确认后运行现有 mock 执行器：

```bash
python app.py --instruction "A stable centered follow shot." --execute-after-confirm
```

底层硬件相关命令仍然由 `runtime/` 负责，和 LLM Agent 保持分离。

## 会话日志

每个会话会保存到：

```text
logs/sessions/<session_id>/
```

文件包括：

- `plan.json`：最新结构化拍摄计划
- `review.md`：最新自然语言 review
- `conversation.jsonl`：使用者、assistant、system 消息
- `metadata.json`：会话 ID、时间戳、确认状态

应用主日志仍然写入：

```text
logs/cambot.log
```

## Qwen API 配置

Qwen 配置仍然直接写在 `config/default.yaml` 中，不要求环境变量。

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

如果 `api_key` 或 `base_url` 为空，系统默认使用内置 mock planner 输出。

## JSON 输出格式

Planner 仍然需要返回这个结构：

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

## 依赖

建议最小安装：

```bash
pip install pydantic PyYAML langchain-core langchain-openai pyserial
```

说明：

- `pyserial` 只有在后续启用真实机械臂连接时才需要。
- 默认 mock mode 下，不需要真实 Qwen 凭证也能运行。

## 说明

- LLM 只负责高层拍摄语义规划。
- JSON 修复会先尝试提取和严格校验，再调用已配置 provider 修复，仍失败时优先保留上一版有效方案。
- 自然语言 review 从校验后的 JSON 渲染，确保给使用者看的描述和机器读取的计划一致。
- 底层运动控制仍然是规则式逻辑，位于 `runtime/framing_controller.py` 和 `runtime/safety_controller.py`。
- 原有 RoArm 控制文件保持不变。
