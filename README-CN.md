# CamBot 交互式 Agent

[English](./README.md) | [简体中文](./README-CN.md)

CamBot 使用交互式 LLM Agent 生成拍摄剧本。Agent 内部维护结构化 JSON 控制指令脚本，给使用者展示自然语言 review，接收自然语言修改意见，并且只有在使用者确认后才把当前脚本视为最终方案。

当前流程：

自然语言拍摄需求 -> 本地 JSON RAG 检索 -> LLM 生成严格可执行 JSON 剧本 -> JSON 修复与校验 -> 自然语言 review -> 使用者多轮修改 -> 使用者确认 -> 指令分发

## 当前范围

- 根据一条初始拍摄需求创建规划会话
- 由 LLM 直接输出完整可执行 JSON 控制指令脚本
- 给使用者展示“简单摘要 + 逐条拍摄动作规划”
- 支持自然语言多轮修改，直到使用者确认
- 对过于宽泛或模糊的反馈主动追问
- 每个会话保存 JSON、review 文本、对话历史和元信息
- 提供面向未来网页后端调用的 Python service 层
- 继续兼容现有 Qwen/OpenAI-compatible provider 和 mock fallback
- CLI 与未来网页确认后都会执行最终 JSON 脚本
- 当前 mock 设置下只打印封装好的下位控制指令，不真实下发到硬件

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
- `confirm_plan(session_id)`：确认并执行最终 JSON 控制指令脚本
- `confirm_plan_only(session_id)`：只确认保存，不执行
- `unconfirm_plan(session_id)`
- `get_current_plan(session_id)`
- `execute_confirmed_plan(session_id)`：返回已确认脚本，供更底层集成使用

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
/confirm    确认、保存并执行当前脚本
/unconfirm  取消确认，继续修改
/quit       退出
```

除此之外的普通输入都会被当作自然语言修改意见，例如：

```text
把主体放到画面左侧，镜头再靠近一点。
```

执行 `/confirm` 后，命令行会把确认后的 JSON 控制指令脚本交给 CamBot 执行器，执行结束后退出。

## 只保存不执行

如果只想调试规划流程，不想在确认后运行执行器：

```bash
python app.py --instruction "A stable centered follow shot." --no-execute-after-confirm
```

网页侧调用 `confirm_plan()` 与 CLI 的 `/confirm` 语义一致：确认最终 JSON 脚本，并通过执行器分发。当前 mock 设置下，执行器只会打印封装好的下位控制指令，不会真实发送到硬件。

## 会话日志

每个会话会保存到：

```text
logs/sessions/<session_id>/
```

文件包括：

- `plan.json`：最新结构化控制指令脚本
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

Planner 需要返回一个完整的可执行控制指令脚本：

```json
{
  "script": {
    "title": "Smooth centered follow shot",
    "summary": "逐条下发底盘、升降和机械臂控制指令，完成稳定中景跟拍。",
    "total_duration_s": 8.0
  },
  "commands": [
    {
      "id": "cmd_01",
      "phase": "准备阶段",
      "target": "base",
      "action": "connect",
      "description": "连接底盘控制器。"
    },
    {
      "id": "cmd_02",
      "phase": "起拍动作",
      "target": "lift",
      "action": "move_to",
      "height_m": 1.2,
      "description": "升降调整到中景跟拍高度。"
    },
    {
      "id": "cmd_03",
      "phase": "跟拍动作",
      "target": "base",
      "action": "move",
      "linear_x": 0.18,
      "angular_z": 0.0,
      "duration_s": 6.0,
      "description": "底盘低速向前移动，保持主体稳定跟拍。"
    },
    {
      "id": "cmd_04",
      "phase": "结束动作",
      "target": "base",
      "action": "stop",
      "description": "停止底盘运动。"
    }
  ]
}
```

支持的 command target：`base`、`lift`、`arm`、`wait`。

支持的 command action：`connect`、`move`、`move_to`、`move_by`、`preset`、`stop`、`wait`。

validator 会裁剪不安全的指令数值，并在缺失时自动补齐 `base`、`lift`、`arm` 的最终停止指令。

## 依赖

建议最小安装：

```bash
pip install pydantic PyYAML langchain-core langchain-openai pyserial
```

说明：

- `pyserial` 只有在后续启用真实机械臂连接时才需要。
- 默认 mock mode 下，不需要真实 Qwen 凭证也能运行。

## 说明

- LLM 现在负责生成完整可执行控制指令脚本，而不只是高层拍摄参数。
- 自然语言 review 直接从校验后的 JSON commands 渲染，确保使用者看到的动作序列与 `/confirm` 实际分发的一致。
- JSON 修复会先尝试提取和严格校验，再调用已配置 provider 修复，仍失败时优先保留上一版有效方案。
- `runtime/cambot_executor.py` 负责按顺序把 commands 分发给封装好的下位控制接口。
- 原有 RoArm 控制文件保持不变。
