# CamBot Interactive Agent

[English](./README.md) | [简体中文](./README-CN.md)

CamBot now uses an interactive LLM agent for filming-plan design. The agent keeps a structured JSON shooting plan internally, shows users a natural-language review, accepts natural-language revision requests, and only treats the plan as final after the user confirms it.

Current flow:

Natural-language instruction -> local JSON RAG retrieval -> LLM strict JSON plan -> JSON repair and validation -> natural-language review -> user revisions -> confirmation -> executor run

## Current Scope

- Start a planning session from one filming instruction
- Keep the canonical JSON output schema unchanged
- Show users a simple summary plus detailed natural-language plan
- Support iterative natural-language revisions until confirmation
- Ask a clarification question for broad or vague feedback
- Save JSON, review text, conversation history, and metadata per session
- Expose a web-ready Python service layer for future frontend integration
- Continue to support the existing Qwen/OpenAI-compatible provider and mock fallback
- Run the executor from the CLI after confirmation while keeping web service confirmation/execution separate

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

## Agent API For Future Web UI

The frontend-facing layer is `agent.service.PlanAgentService`. A web backend can call these methods directly:

- `create_session(initial_instruction)`
- `send_message(session_id, user_message)`
- `review_plan(session_id)`
- `confirm_plan(session_id)`
- `unconfirm_plan(session_id)`
- `get_current_plan(session_id)`
- `execute_confirmed_plan(session_id)`

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
/confirm    confirm, save, and execute the current plan
/unconfirm  cancel confirmation and keep editing
/quit       exit
```

Any other input is treated as a natural-language revision request, such as:

```text
Move the subject to the left side and make the shot a little closer.
```

After `/confirm`, the CLI sends the confirmed plan to the existing CamBot executor and exits when execution finishes.

## Save Without Execution

For planning-only debugging, disable execution after confirmation:

```bash
python app.py --instruction "A stable centered follow shot." --no-execute-after-confirm
```

The web-facing service still keeps `confirm_plan()` and `execute_confirmed_plan()` separate, so a future frontend can decide exactly when to dispatch the plan. Low-level hardware-facing commands are still handled by `runtime/`.

## Session Logs

Each session is saved under:

```text
logs/sessions/<session_id>/
```

Files:

- `plan.json`: latest structured shooting plan
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

The planner is still required to return this shape:

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

## Dependencies

Minimal recommended install:

```bash
pip install pydantic PyYAML langchain-core langchain-openai pyserial
```

Notes:

- `pyserial` is only needed if the real arm connection is enabled later.
- In default mock mode, live Qwen credentials are not required.

## Notes

- The LLM only plans high-level filming parameters.
- JSON repair first tries extraction and strict validation, then asks the configured provider to repair the JSON, then falls back to the previous valid plan when available.
- Natural-language review is rendered from validated JSON so the user-facing description stays aligned with the machine-readable plan.
- Low-level motion remains rule-based in `runtime/framing_controller.py` and `runtime/safety_controller.py`.
- Existing RoArm control files remain unchanged.
