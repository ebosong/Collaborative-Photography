# CamBot 交互式 Timeline Agent

[English](./README.md) | [简体中文](./README-CN.md)

CamBot 使用交互式 LLM Agent 生成顶层拍摄剧本。当前协议是 `TimelineScript`：顶层 Agent 只负责输出严格 JSON，包括时间轴动作、视觉检查/跟随配置和打光意图；S3/P4/YOLO/打光车的具体调度由更底层执行系统负责。

当前流程：

自然语言拍摄需求 -> 本地 JSON RAG 检索 -> LLM 生成严格 `TimelineScript` -> JSON 修复与校验 -> 自然语言 review -> 使用者多轮修改 -> 使用者确认 -> 保存最终 JSON

## 当前范围

- 根据一条初始拍摄需求创建规划会话
- 由 LLM 输出完整 `TimelineScript`
- 支持 `base_longitudinal`、`base_rotate`、`lift_delta`、`arm_init_pose`、`arm_move_delta`、`arm_move_xyz`、`wait`
- 支持 `checkpoint` 和 `follow_mode` 的视觉目标配置
- 始终输出 `lighting_plan`，没有明确打光要求时使用默认中性正面中光
- 给使用者展示时间轴动作和打光方案 review
- 支持自然语言多轮修改，直到使用者确认
- 每个会话保存 JSON、review 文本、对话历史和元信息
- CLI 与未来网页确认后保存最终 JSON，并可调用底层 `TimelineScheduler`；顶层 Agent 仍只输出抽象 `TimelineScript`

## 目录结构

```text
app.py
agent/
  models.py
  service.py
  reviewer.py
  json_repair.py
  log_store.py
chain/
  retriever.py
  prompt_builder.py
  planner.py
  validator.py
schemas/
  timeline_script_schema.py
runtime/
  cambot_executor.py
  timeline_scheduler.py
  timeline_command_translator.py
  checkpoint_correction_planner.py
  subject_match_detector.py
showcase/
  index.html
  styles.css
  app.js
providers/
  llm_provider.py
```

## 预留给网页的接口

未来网页后端可以直接调用 `agent.service.PlanAgentService`：

- `create_session(initial_instruction)`
- `send_message(session_id, user_message)`
- `review_plan(session_id)`
- `confirm_plan(session_id)`：确认并保存最终 `TimelineScript`
- `confirm_plan_only(session_id)`：只确认保存，不调用确认适配器
- `unconfirm_plan(session_id)`
- `get_current_plan(session_id)`
- `execute_confirmed_plan(session_id)`：返回已确认脚本，供底层集成使用

## 命令行使用

```bash
python app.py --instruction "先让机器人后退一点，再降低机位，检查人物是否在画面中部，打暖光侧面中光。"
```

只保存不执行确认适配器：

```bash
python app.py --instruction "A stable centered shot." --no-execute-after-confirm
```

真实硬件运行时可复制本地运行配置样例：

```bash
copy config\runtime.example.yaml config.yaml
python app.py --instruction "先后退一点，并检查人物构图。"
```

`config.yaml`、模型权重、相机视频、视觉模板图、手动测试脚本和第三方二进制包会被忽略，GitHub 仓库只保留源码与可复现配置样例。

交互命令：

```text
/review     查看当前自然语言拍摄方案
/confirm    确认并保存当前脚本
/unconfirm  取消确认，继续修改
/quit       退出
```

## LLM Provider 配置

`config/default.yaml` 中保留硬编码 provider profile。默认仍是 Qwen；如需切到 DeepSeek，修改 `llm.provider`：

```yaml
llm:
  provider: deepseek_openai_compatible
  providers:
    deepseek_openai_compatible:
      api_key: "sk-your-deepseek-api-key"
      base_url: "https://api.deepseek.com"
      model: "deepseek-v4-flash"
```

DeepSeek 使用 OpenAI-compatible 接口，因此和 Qwen 走同一套 `langchain-openai` 调用路径。

## JSON 输出格式

Planner 必须返回一个严格 JSON 对象：

```json
{
  "name": "back_lower_checkpoint_warm_side_light",
  "version": "2.0",
  "mode": "timeline",
  "summary": "机器人先开环后退，再降低机位，随后检查人物构图，并使用暖光侧面中光。",
  "timeline": [
    {
      "id": "b1",
      "type": "base_longitudinal",
      "start_at_s": 0.0,
      "device": "s3",
      "channel": "base",
      "params": {
        "distance_m": -0.2,
        "speed_m_s": 0.1
      },
      "timeout_s": 8,
      "blocking": true,
      "on_fail": "stop_all",
      "description": "小车后退 20 cm。"
    },
    {
      "id": "cp1",
      "type": "checkpoint",
      "start_after": ["b1"],
      "device": "local",
      "channel": "vision",
      "expected_frame": {
        "enabled": true,
        "target_class": "person",
        "target_id": "main_actor",
        "bbox_format": "cxcywh_norm",
        "bbox": [0.5, 0.52, 0.35, 0.65],
        "tolerance": {
          "center_x": 0.05,
          "center_y": 0.05,
          "width": 0.08,
          "height": 0.1
        }
      },
      "servo": {
        "max_iters": 8,
        "allow_base": true,
        "allow_lift": true,
        "allow_arm": false
      },
      "timeout_s": 30,
      "blocking": true,
      "on_vision_fail": "continue",
      "description": "检查人物是否位于预期画面中部。"
    }
  ],
  "lighting_plan": [
    {
      "id": "light1",
      "start_at_s": 0.0,
      "color_temperature": "warm",
      "intensity": "medium",
      "azimuth": "side",
      "height": "middle",
      "description": "暖光、中等强度、侧面中光。"
    }
  ]
}
```

约束摘要：

- `version` 固定为 `"2.0"`，`mode` 固定为 `"timeline"`
- `timeline[].id` 全局唯一
- `start_after` 引用必须存在
- 只有 `follow_mode` 可以描述为跟拍/跟随
- `checkpoint` 只输出期望画面和修正配置，不输出修正动作
- `lighting_plan` 只输出打光意图，不输出打光车轨迹
- 顶层 Agent 不输出 `stop` 动作、不输出下位机原生命令、不输出机械臂 `T=100/T=104/T=1041`

## 依赖

```bash
pip install -r requirements.txt
```

默认 mock mode 下，不需要真实 Qwen 凭证也能运行。
