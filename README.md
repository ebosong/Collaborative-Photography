# CamBot Interactive Timeline Agent

[English](./README.md) | [简体中文](./README-CN.md)

CamBot uses an interactive LLM agent to generate top-level filming scripts. The current protocol is `TimelineScript`: the top-level Agent outputs strict JSON with timeline actions, checkpoint/follow-mode vision intent, and lighting intent. Concrete S3/P4/YOLO/lighting-car scheduling belongs to the lower execution system.

Current flow:

Natural-language instruction -> local JSON RAG retrieval -> LLM strict `TimelineScript` -> JSON repair and validation -> natural-language review -> user revisions -> confirmation -> saved final JSON

## Current Scope

- Start a planning session from one filming instruction
- Let the LLM output a complete `TimelineScript`
- Support `base_longitudinal`, `base_rotate`, `lift_delta`, `arm_init_pose`, `arm_move_delta`, `arm_move_xyz`, and `wait`
- Support `checkpoint` and `follow_mode` vision target configuration
- Always output `lighting_plan`; use neutral/medium/front/middle by default
- Render a user-facing review of timeline actions and lighting intent
- Support iterative natural-language revisions until confirmation
- Save JSON, review text, conversation history, and metadata per session
- On confirmation, save the final JSON and optionally invoke the lower `TimelineScheduler`; the top-level Agent still emits only abstract `TimelineScript`

## Repository Structure

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

## Agent API For Future Web UI

The frontend-facing layer is `agent.service.PlanAgentService`:

- `create_session(initial_instruction)`
- `send_message(session_id, user_message)`
- `review_plan(session_id)`
- `confirm_plan(session_id)` confirms and saves the final `TimelineScript`
- `confirm_plan_only(session_id)` confirms and saves without invoking the confirmation adapter
- `unconfirm_plan(session_id)`
- `get_current_plan(session_id)`
- `execute_confirmed_plan(session_id)` returns the confirmed script for lower-layer integrations

## CLI Usage

```bash
python app.py --instruction "Back up a little, lower the camera, check that the person is centered, and use warm side middle light."
```

Save without invoking the confirmation adapter:

```bash
python app.py --instruction "A stable centered shot." --no-execute-after-confirm
```

Use a local runtime override when running real hardware:

```bash
copy config\runtime.example.yaml config.yaml
python app.py --instruction "Back up a little and check the subject framing."
```

`config.yaml`, model weights, camera videos, template images, manual test scripts, and bundled third-party binaries are intentionally ignored so the GitHub repository stays focused on source code and reproducible configuration.

Interactive commands:

```text
/review     show the current natural-language shooting plan
/confirm    confirm and save the current script
/unconfirm  cancel confirmation and keep editing
/quit       exit
```

## LLM Provider Configuration

`config/default.yaml` keeps hardcoded provider profiles. Qwen remains the default; switch to DeepSeek by changing `llm.provider`:

```yaml
llm:
  provider: deepseek_openai_compatible
  providers:
    deepseek_openai_compatible:
      api_key: "sk-your-deepseek-api-key"
      base_url: "https://api.deepseek.com"
      model: "deepseek-v4-flash"
```

The provider is OpenAI-compatible, so the same `langchain-openai` path is used for Qwen and DeepSeek.

## Expected JSON Shape

The planner must return one strict JSON object:

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

Constraint summary:

- `version` is exactly `"2.0"` and `mode` is exactly `"timeline"`
- `timeline[].id` values are globally unique
- `start_after` references must exist
- Only `follow_mode` may be described as following/tracking
- `checkpoint` outputs only expected framing and servo configuration, not correction actions
- `lighting_plan` outputs lighting intent only, not lighting-car paths
- The top-level Agent does not output `stop` actions, lower-device native commands, or arm `T=100/T=104/T=1041` commands

## Dependencies

```bash
pip install -r requirements.txt
```

In default mock mode, live Qwen credentials are not required.
