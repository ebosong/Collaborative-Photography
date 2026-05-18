"""Natural-language rendering for structured CamBot filming plans."""

from __future__ import annotations

from schemas.script_schema import ScriptPlan


class PlanReviewer:
    """Render JSON plans into concise, user-facing Chinese text."""

    TEMPLATE_LABELS = {
        "mid_follow": "中景跟拍",
        "side_front_follow": "侧前方跟拍",
        "mid_follow_safe": "安全中景跟拍",
    }

    REGION_LABELS = {
        "left": "画面左侧",
        "center": "画面中央",
        "right": "画面右侧",
    }

    TASK_LABELS = {
        "track_subject_with_framing": "跟踪主体并保持构图",
    }

    def render(self, plan: ScriptPlan) -> str:
        """Return a simple summary plus detailed filming plan."""
        shot = plan.shot_plan
        safety = plan.safety_rules
        template = self.TEMPLATE_LABELS.get(shot.template, shot.template)
        region = self.REGION_LABELS.get(shot.subject_region, shot.subject_region)
        task = self.TASK_LABELS.get(plan.robot_task.name, plan.robot_task.name)
        fallback = self.TEMPLATE_LABELS.get(plan.fallback.template, plan.fallback.template)

        lines = [
            "## 简单摘要",
            (
                f"这版方案采用{template}，预计拍摄 {shot.duration_s} 秒，"
                f"主体保持在{region}，机器人与主体约 {shot.distance_m:.1f} 米距离，"
                f"镜头高度约 {shot.height_m:.1f} 米。"
            ),
            "",
            "## 详细计划",
            f"- 拍摄模板：{template}（{shot.template}）",
            f"- 执行任务：{task}（{plan.robot_task.name}）",
            f"- 拍摄时长：{shot.duration_s} 秒",
            f"- 主体距离：约 {shot.distance_m:.1f} 米",
            f"- 镜头高度：约 {shot.height_m:.1f} 米",
            f"- 主体位置：{region}",
            f"- 主体画面占比目标：约 {shot.subject_scale_target:.2f}",
            "",
            "## 安全与兜底",
            f"- 最大速度：{safety.max_speed:.2f} m/s",
            f"- 最小安全距离：{safety.min_distance:.1f} 米",
            f"- 目标丢失处理：{safety.lost_target_action}",
            f"- 兜底模板：{fallback}（{plan.fallback.template}）",
        ]
        return "\n".join(lines)
