"""Main CamBot execution loop for mock-safe filming plan execution."""

from __future__ import annotations

import logging
import time
from typing import Any

from runtime.arm_adapter import ArmAdapter
from runtime.base_controller import BaseController
from runtime.framing_controller import FramingController
from runtime.lift_controller import LiftController
from runtime.safety_controller import SafetyController
from runtime.tracker import Tracker


class CamBotExecutor:
    """Execute a validated filming plan using rule-based runtime control."""

    def __init__(self, config: dict[str, Any], repo_root: str):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.loop_hz = float(config["app"].get("loop_hz", 2.0))
        self.tracker = Tracker(config["tracker"])
        self.framing_controller = FramingController(config)
        self.safety_controller = SafetyController(config)
        self.base_controller = BaseController()
        self.lift_controller = LiftController(
            initial_height=float(config["limits"]["height_m"]["default"])
        )
        self.arm_adapter = ArmAdapter(repo_root=repo_root, arm_config=config["arm"])

    def execute(self, plan: Any) -> None:
        """Run the main execution loop until the planned duration elapses."""
        self.logger.info("Starting CamBot executor for %.2f seconds.", plan.shot_plan.duration_s)

        try:
            self.base_controller.connect()
            self.lift_controller.connect()
            self.arm_adapter.connect()
            self.arm_adapter.execute_preset("ready")

            period_s = 1.0 / max(self.loop_hz, 0.1)
            start_time = time.time()

            while time.time() - start_time < float(plan.shot_plan.duration_s):
                cycle_started = time.time()
                target_state = self.tracker.get_target_state()

                control = self.framing_controller.compute_control(
                    shot_plan=plan.shot_plan,
                    target_state=target_state,
                    current_height=self.lift_controller.get_height(),
                )
                safe_control = self.safety_controller.apply(
                    control=control,
                    plan=plan,
                    target_state=target_state,
                )

                if not target_state.get("detected", False):
                    self._handle_lost_target(plan)
                else:
                    self.base_controller.move(
                        linear_x=safe_control["linear_x"],
                        angular_z=safe_control["angular_z"],
                    )
                    self.lift_controller.move_by(safe_control["lift_delta"])

                self.logger.info(
                    "Cycle summary | target=%s | control=%s | safe_control=%s",
                    target_state,
                    control,
                    safe_control,
                )

                elapsed = time.time() - cycle_started
                time.sleep(max(0.0, period_s - elapsed))

            self.base_controller.stop()
            self.lift_controller.stop()
            self.logger.info("CamBot executor finished planned duration cleanly.")
        except Exception as exc:
            self.logger.exception("Executor stopped due to unexpected error: %s", exc)
            self.base_controller.stop()
            self.lift_controller.stop()
            self.arm_adapter.stop()
            raise
        finally:
            self.arm_adapter.close()
            self.base_controller.close()
            self.lift_controller.close()

    def _handle_lost_target(self, plan: Any) -> None:
        """Minimal placeholder behavior when the target is lost."""
        action = plan.safety_rules.lost_target_action
        if action == "slow_stop_and_search":
            self.base_controller.stop()
            self.lift_controller.stop()
            print("[SEARCH] target lost -> waiting/search placeholder")
            self.logger.warning("Target lost, entered minimal search/wait placeholder.")
