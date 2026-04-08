# CamBot MVP

Minimal runnable single-camera robot filming pipeline inside this repository. The MVP flow is:

Natural language instruction -> local JSON RAG retrieval -> prompt assembly -> LLM strict JSON plan -> pydantic validation and clipping -> mock execution loop -> printed low-level commands

This version is intentionally small and safe:
- No frontend
- No LangGraph
- No multi-robot logic
- No real chassis or lift hardware output
- Existing RoArm arm code is wrapped, not rewritten

## Current MVP Scope

- Accept one filming instruction
- Retrieve relevant local templates and rules from `rag/`
- Build a strict JSON-only prompt
- Use a Qwen OpenAI-compatible model when configured
- Fall back to mock JSON output by default
- Validate with pydantic and clip unsafe parameters
- Run a mock CamBot executor loop
- Print and log chassis, lift, and arm commands for inspection

## Repository Structure

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

## Reused Existing Code

- `RoArm-M2-S_python/roarm_motion_api.py`: existing RoArm serial motion API
- `runtime/arm_adapter.py`: thin adapter that loads and wraps the existing arm implementation

The MVP does not change the original RoArm control files.

## Dependencies

Minimal recommended install:

```bash
pip install pydantic PyYAML langchain-core langchain-openai pyserial
```

Notes:
- `pyserial` is only needed if you later enable the real arm connection.
- In default mock mode, the app still runs even if live Qwen credentials are not configured.

## Qwen API Configuration

Configure Qwen directly in [`config/default.yaml`]. No environment variables are required.

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

If `api_key` or `base_url` is left empty, the app falls back to the built-in mock planner output by default.

## How To Run In Mock Mode

Mock mode is enabled by default in `config/default.yaml`.

Example:

```bash
python app.py --instruction "Give me a smooth medium follow shot, keep the subject near the center, then stop at the end."
```

If you omit `--instruction`, the app will prompt for one interactively.

## Example User Instruction

```text
Give me a smooth medium follow shot, keep the subject near the center, then stop at the end.
```

## Expected JSON Output

The planner is required to return this shape:

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

## Where Commands Are Printed

During execution, low-level hardware-facing commands are not sent to a real receiver. They are printed and logged instead:

- chassis commands: `[BASE CMD] ...`
- lift commands: `[LIFT CMD] ...`
- arm commands: `[ARM CMD] ...`
- lost-target placeholder: `[SEARCH] ...`

Logs are written to:

```text
logs/cambot.log
```

## Replacing Mock Chassis And Lift Later

To replace mock hardware safely:

1. Implement real transport code behind `runtime/base_controller.py`
2. Implement real lift hardware logic behind `runtime/lift_controller.py`
3. Keep the existing method interfaces unchanged:
   - `connect()`
   - `move(...)` or `move_to(...)`
   - `move_by(...)`
   - `stop()`
   - `close()`
4. Leave `runtime/cambot_executor.py` unchanged so the control loop still works

## Notes

- The LLM only plans high-level filming parameters.
- Low-level motion remains rule-based in `runtime/framing_controller.py` and `runtime/safety_controller.py`.
- The tracker is currently mock-based but has a clean `get_target_state()` interface for future vision integration.
