"""Natural-language rendering for CamBot TimelineScript plans."""

from __future__ import annotations

from typing import Any

from schemas.timeline_script_schema import TimelineActionType, TimelineScript


class PlanReviewer:
    """Render TimelineScript JSON into user-facing Chinese text."""

    TYPE_LABELS = {
        "base_longitudinal": "底盘前后移动",
        "base_rotate": "底盘原地旋转",
        "lift_delta": "升降杆相对移动",
        "arm_init_pose": "机械臂准备位",
        "arm_move_delta": "机械臂相对移动",
        "arm_move_xyz": "机械臂绝对移动",
        "wait": "本地等待",
        "checkpoint": "画面检查点",
        "follow_mode": "视觉跟随模式",
    }

    LIGHT_COLOR_LABELS = {
        "warm": "暖光",
        "cool": "冷光",
        "neutral": "中性光",
    }

    LIGHT_INTENSITY_LABELS = {
        "strong": "强",
        "medium": "中等",
        "weak": "弱",
    }

    LIGHT_AZIMUTH_LABELS = {
        "front": "正面",
        "side": "侧面",
        "back": "背面",
    }

    LIGHT_HEIGHT_LABELS = {
        "bottom": "底光",
        "middle": "中光",
        "top": "顶光",
    }

    def render(self, plan: TimelineScript) -> str:
        """Return a concise review of timeline actions and lighting intent."""
        lines = [
            "## 简单摘要",
            plan.summary,
            f"协议版本：{plan.version}；模式：{plan.mode}",
            f"时间轴动作数：{len(plan.timeline)} 条",
            f"打光方案数：{len(plan.lighting_plan)} 条",
            "",
            "## 时间轴动作规划",
        ]

        for action in plan.timeline:
            lines.append(f"- {action.id}：{action.description}（{self._action_detail(action)}）")

        lines.extend(["", "## 打光计划"])
        for light in plan.lighting_plan:
            lines.append(f"- {light.id}：{light.description}（{self._lighting_detail(light)}）")

        return "\n".join(lines)

    def _action_detail(self, action: Any) -> str:
        action_type = str(action.type)
        label = self.TYPE_LABELS.get(action_type, action_type)
        details = [label]

        start_detail = self._start_detail(action)
        if start_detail:
            details.append(start_detail)

        if action_type == TimelineActionType.BASE_LONGITUDINAL.value:
            details.append(f"距离 {action.params.distance_m:.2f} m")
            details.append(f"速度 {action.params.speed_m_s:.2f} m/s")
        elif action_type == TimelineActionType.BASE_ROTATE.value:
            details.append(f"角度 {action.params.angle_deg:.1f} 度")
            details.append(f"角速度 {action.params.angular_speed_rad_s:.2f} rad/s")
        elif action_type == TimelineActionType.LIFT_DELTA.value:
            details.append(f"升降 {action.params.delta_cm:.1f} cm")
        elif action_type == TimelineActionType.ARM_INIT_POSE.value:
            details.append(f"等待 {action.params.wait_first_s:.1f} 秒")
        elif action_type == TimelineActionType.ARM_MOVE_DELTA.value:
            details.append(
                f"前后 {action.params.front_cm:.1f} cm，左右 {action.params.left_cm:.1f} cm，上下 {action.params.up_cm:.1f} cm"
            )
            details.append(f"腕部变化 {action.params.wrist_delta_deg:.1f} 度")
            details.append(f"速度 {action.params.speed:.2f}")
        elif action_type == TimelineActionType.ARM_MOVE_XYZ.value:
            x_m, y_m, z_m = action.params.target_xyz_m
            details.append(f"目标 ({x_m:.2f}, {y_m:.2f}, {z_m:.2f}) m")
            details.append(f"速度 {action.params.speed:.2f}")
        elif action_type == TimelineActionType.WAIT.value:
            details.append(f"持续 {action.params.duration_s:.1f} 秒")
        elif action_type == TimelineActionType.CHECKPOINT.value:
            bbox = action.expected_frame.bbox
            details.append(f"目标 {action.expected_frame.target_class}/{action.expected_frame.target_id}")
            details.append(f"期望框 [{bbox[0]:.2f}, {bbox[1]:.2f}, {bbox[2]:.2f}, {bbox[3]:.2f}]")
            details.append(f"最多修正 {action.servo.max_iters} 次")
        elif action_type == TimelineActionType.FOLLOW_MODE.value:
            bbox = action.target_frame.bbox
            details.append(f"持续 {action.duration_s:.1f} 秒")
            details.append(f"目标框 [{bbox[0]:.2f}, {bbox[1]:.2f}, {bbox[2]:.2f}, {bbox[3]:.2f}]")
            details.append(f"最多反馈 {action.servo.max_iters} 次")

        details.append(f"timeout {action.timeout_s:.1f}s")
        details.append("阻塞" if action.blocking else "非阻塞")
        return "，".join(details)

    @staticmethod
    def _start_detail(action: Any) -> str:
        parts: list[str] = []
        if action.start_at_s is not None:
            parts.append(f"{action.start_at_s:.1f}s 后可开始")
        if action.start_after:
            parts.append("等待 " + ",".join(action.start_after))
        return "；".join(parts)

    def _lighting_detail(self, light: Any) -> str:
        parts = []
        if light.start_at_s is not None:
            parts.append(f"{light.start_at_s:.1f}s 生效")
        if light.start_after:
            parts.append("等待 " + ",".join(light.start_after))
        parts.extend(
            [
                self.LIGHT_COLOR_LABELS.get(str(light.color_temperature), str(light.color_temperature)),
                self.LIGHT_INTENSITY_LABELS.get(str(light.intensity), str(light.intensity)) + "强度",
                self.LIGHT_AZIMUTH_LABELS.get(str(light.azimuth), str(light.azimuth)),
                self.LIGHT_HEIGHT_LABELS.get(str(light.height), str(light.height)),
            ]
        )
        return "，".join(parts)
