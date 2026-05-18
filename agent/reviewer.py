"""Natural-language rendering for executable CamBot command scripts."""

from __future__ import annotations

from collections import defaultdict

from schemas.script_schema import MotionCommand, ScriptPlan


class PlanReviewer:
    """Render executable JSON scripts into user-facing Chinese text."""

    TARGET_LABELS = {
        "base": "底盘",
        "lift": "升降",
        "arm": "机械臂",
        "wait": "等待",
    }

    ACTION_LABELS = {
        "connect": "连接",
        "move": "移动",
        "move_to": "移动到指定高度",
        "move_by": "相对移动",
        "preset": "预置动作",
        "stop": "停止",
        "wait": "等待",
    }

    def render(self, plan: ScriptPlan) -> str:
        """Return a simple summary plus the ordered filming action script."""
        lines = [
            "## 简单摘要",
            plan.script.summary,
            f"预计动作时长：约 {plan.script.total_duration_s:.1f} 秒",
            f"下位控制指令数：{len(plan.commands)} 条",
            "",
            "## 具体拍摄动作规划",
        ]

        phase_map: dict[str, list[MotionCommand]] = defaultdict(list)
        for command in plan.commands:
            phase_map[command.phase].append(command)

        for phase, commands in phase_map.items():
            lines.append(f"### {phase}")
            for command in commands:
                lines.append(f"- {command.id}：{command.description}（{self._command_detail(command)}）")

        lines.extend(
            [
                "",
                "## 下位控制指令",
                *[f"- {command.id}: {self._machine_command(command)}" for command in plan.commands],
            ]
        )
        return "\n".join(lines)

    def _command_detail(self, command: MotionCommand) -> str:
        target = self.TARGET_LABELS.get(command.target, command.target)
        action = self.ACTION_LABELS.get(command.action, command.action)
        details = [target, action]

        if command.action == "move":
            details.append(f"线速度 {command.linear_x or 0.0:.2f} m/s")
            details.append(f"角速度 {command.angular_z or 0.0:.2f} rad/s")
            if command.duration_s:
                details.append(f"持续 {command.duration_s:.1f} 秒")
        elif command.action == "move_to" and command.height_m is not None:
            details.append(f"高度 {command.height_m:.2f} m")
        elif command.action == "move_by" and command.delta_m is not None:
            details.append(f"增量 {command.delta_m:.2f} m")
        elif command.action == "preset" and command.preset:
            details.append(f"preset={command.preset}")
        elif command.action == "wait" and command.duration_s:
            details.append(f"持续 {command.duration_s:.1f} 秒")

        return "，".join(details)

    @staticmethod
    def _machine_command(command: MotionCommand) -> str:
        if command.target == "base" and command.action == "move":
            return (
                f"base.move(linear_x={command.linear_x or 0.0:.3f}, "
                f"angular_z={command.angular_z or 0.0:.3f}, duration_s={command.duration_s:.2f})"
            )
        if command.target == "base" and command.action == "connect":
            return "base.connect()"
        if command.target == "base" and command.action == "stop":
            return "base.stop()"
        if command.target == "lift" and command.action == "connect":
            return "lift.connect()"
        if command.target == "lift" and command.action == "move_to":
            return f"lift.move_to(height_m={command.height_m or 0.0:.3f})"
        if command.target == "lift" and command.action == "move_by":
            return f"lift.move_by(delta_m={command.delta_m or 0.0:.3f})"
        if command.target == "lift" and command.action == "stop":
            return "lift.stop()"
        if command.target == "arm" and command.action == "connect":
            return "arm.connect()"
        if command.target == "arm" and command.action == "preset":
            return f"arm.execute_preset(name={command.preset or 'ready'})"
        if command.target == "arm" and command.action == "stop":
            return "arm.stop()"
        if command.target == "wait" or command.action == "wait":
            return f"wait(duration_s={command.duration_s:.2f})"
        return f"{command.target}.{command.action}()"
