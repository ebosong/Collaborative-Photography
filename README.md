# CamBot Interactive Agent

[English](./README.md) | [简体中文](./README-CN.md)

CamBot uses an interactive LLM agent for filming-script design. The agent keeps a structured JSON command script internally, shows users a natural-language review, accepts natural-language revision requests, and only treats the script as final after the user confirms it.

Current flow:

Natural-language instruction -> local JSON RAG retrieval -> LLM strict executable JSON script -> JSON repair and validation -> natural-language review -> user revisions -> confirmation -> command dispatch

## Current Scope

- Start a planning session from one filming instruction
- Let the LLM output the complete executable JSON command script
- Show users a simple summary plus detailed action-by-action filming plan
- Support iterative natural-language revisions until confirmation
- Ask a clarification question for broad or vague feedback
- Save JSON, review text, conversation history, and metadata per session
- Expose a web-ready Python service layer for future frontend integration
- Continue to support the existing Qwen/OpenAI-compatible provider and mock fallback
- Confirm and execute the final JSON script from both CLI and future web calls
- Print wrapped lower-level control commands instead of sending them to real hardware in the current mock setup

## Repository Structure

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

## Agent API For Future Web UI

The frontend-facing layer is `agent.service.PlanAgentService`. A web backend can call these methods directly:

- `create_session(initial_instruction)`
- `send_message(session_id, user_message)`
- `review_plan(session_id)`
- `confirm_plan(session_id)` confirms and executes the final JSON command script
- `confirm_plan_only(session_id)` confirms and saves without execution
- `unconfirm_plan(session_id)`
- `get_current_plan(session_id)`
- `execute_confirmed_plan(session_id)` returns the confirmed script for lower-level integrations

`AgentResponse` returns the `session_id`, status, user-facing review text, optional JSON plan, and confirmation flag.

## CLI Usage

Mock mode is enabled by default in `config/default.yaml`, so the app can run without live Qwen credentials.

```bash
python app.py --instruction "Give me a smooth medium follow shot, keep the subject near the center, then stop at the end."
```

If `--instruction` is omitted, the CLI asks for the first filming requirement.

Interactive commands:

```text
/review     show the current natural-language shooting plan
/confirm    confirm, save, and execute the current script
/unconfirm  cancel confirmation and keep editing
/quit       exit
```

Any other input is treated as a natural-language revision request, such as:

```text
Move the subject to the left side and make the shot a little closer.
```

After `/confirm`, the CLI sends the confirmed JSON command script to the CamBot executor and exits when execution finishes.

## Save Without Execution

For planning-only debugging, disable execution after confirmation:

```bash
python app.py --instruction "A stable centered follow shot." --no-execute-after-confirm
```

The web-facing `confirm_plan()` has the same meaning as the CLI confirmation command: it confirms the final JSON script and dispatches it through the executor. In the current mock setup, the executor prints the wrapped lower-level commands rather than sending them to real hardware.

## Session Logs

Each session is saved under:

```text
logs/sessions/<session_id>/
```

Files:

- `plan.json`: latest structured command script
- `review.md`: latest user-facing natural-language review
- `conversation.jsonl`: user, assistant, and system messages
- `metadata.json`: session id, timestamps, confirmation state

The app-level log is still written to:

```text
logs/cambot.log
```

## Qwen API Configuration

Configure Qwen directly in `config/default.yaml`. No environment variables are required.

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

If `api_key` or `base_url` is left empty, the app falls back to the built-in mock planner output.

## Expected JSON Shape

The planner must return one complete executable command script:

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

Supported command targets: `base`, `lift`, `arm`, `wait`.

Supported command actions: `connect`, `move`, `move_to`, `move_by`, `preset`, `stop`, `wait`.

The validator clips unsafe command values and appends missing final stop commands for `base`, `lift`, and `arm`.

## Dependencies

Minimal recommended install:

```bash
pip install pydantic PyYAML langchain-core langchain-openai pyserial
```

Notes:

- `pyserial` is only needed if the real arm connection is enabled later.
- In default mock mode, live Qwen credentials are not required.

## Notes

- The LLM now plans the full executable command script, not only high-level filming parameters.
- The natural-language review is rendered directly from validated JSON commands so users see the same action sequence that `/confirm` will dispatch.
- JSON repair first tries extraction and strict validation, then asks the configured provider to repair the JSON, then falls back to the previous valid plan when available.
- `runtime/cambot_executor.py` dispatches the command list through wrapped lower-level controller interfaces.
- Existing RoArm control files remain unchanged.
