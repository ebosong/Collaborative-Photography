"""Natural-language rendering for structured CamBot filming plans."""

from __future__ import annotations

from schemas.script_schema import ScriptPlan


class PlanReviewer:
    """Render JSON plans into concise, user-facing Chinese text."""

    TEMPLATE_LABELS = {
        "mid_follow": "\u4e2d\u666f\u8ddf\u62cd",
        "side_front_follow": "\u4fa7\u524d\u65b9\u8ddf\u62cd",
        "mid_follow_safe": "\u5b89\u5168\u4e2d\u666f\u8ddf\u62cd",
    }

    REGION_LABELS = {
        "left": "\u753b\u9762\u5de6\u4fa7",
        "center": "\u753b\u9762\u4e2d\u592e",
        "right": "\u753b\u9762\u53f3\u4fa7",
    }

    TASK_LABELS = {
        "track_subject_with_framing": "\u8ddf\u8e2a\u4e3b\u4f53\u5e76\u4fdd\u6301\u6784\u56fe",
    }

    def render(self, plan: ScriptPlan) -> str:
        """Return a simple summary plus detailed filming and action description."""
        shot = plan.shot_plan
        safety = plan.safety_rules
        template = self.TEMPLATE_LABELS.get(shot.template, shot.template)
        region = self.REGION_LABELS.get(shot.subject_region, shot.subject_region)
        task = self.TASK_LABELS.get(plan.robot_task.name, plan.robot_task.name)
        fallback = self.TEMPLATE_LABELS.get(plan.fallback.template, plan.fallback.template)

        lines = [
            "## \u7b80\u5355\u6458\u8981",
            (
                f"\u8fd9\u7248\u65b9\u6848\u91c7\u7528{template}\uff0c"
                f"\u9884\u8ba1\u62cd\u6444 {shot.duration_s} \u79d2\uff0c"
                f"\u4e3b\u4f53\u4fdd\u6301\u5728{region}\uff0c"
                f"\u673a\u5668\u4eba\u4e0e\u4e3b\u4f53\u7ea6 {shot.distance_m:.1f} \u7c73\u8ddd\u79bb\uff0c"
                f"\u955c\u5934\u9ad8\u5ea6\u7ea6 {shot.height_m:.1f} \u7c73\u3002"
            ),
            "",
            "## \u8be6\u7ec6\u8ba1\u5212",
            f"- \u62cd\u6444\u6a21\u677f\uff1a{template}\uff08{shot.template}\uff09",
            f"- \u6267\u884c\u4efb\u52a1\uff1a{task}\uff08{plan.robot_task.name}\uff09",
            f"- \u62cd\u6444\u65f6\u957f\uff1a{shot.duration_s} \u79d2",
            f"- \u4e3b\u4f53\u8ddd\u79bb\uff1a\u7ea6 {shot.distance_m:.1f} \u7c73",
            f"- \u955c\u5934\u9ad8\u5ea6\uff1a\u7ea6 {shot.height_m:.1f} \u7c73",
            f"- \u4e3b\u4f53\u4f4d\u7f6e\uff1a{region}",
            f"- \u4e3b\u4f53\u753b\u9762\u5360\u6bd4\u76ee\u6807\uff1a\u7ea6 {shot.subject_scale_target:.2f}",
            "",
            "## \u5177\u4f53\u62cd\u6444\u52a8\u4f5c",
            *self._action_lines(plan),
            "",
            "## \u5b89\u5168\u4e0e\u515c\u5e95",
            f"- \u6700\u5927\u901f\u5ea6\uff1a{safety.max_speed:.2f} m/s",
            f"- \u6700\u5c0f\u5b89\u5168\u8ddd\u79bb\uff1a{safety.min_distance:.1f} \u7c73",
            f"- \u76ee\u6807\u4e22\u5931\u5904\u7406\uff1a{safety.lost_target_action}",
            f"- \u515c\u5e95\u6a21\u677f\uff1a{fallback}\uff08{plan.fallback.template}\uff09",
        ]
        return "\n".join(lines)

    def _action_lines(self, plan: ScriptPlan) -> list[str]:
        shot = plan.shot_plan
        region = self.REGION_LABELS.get(shot.subject_region, shot.subject_region)
        movement = self._movement_description(shot.template)
        timing = f"\u7ea6 {int(shot.duration_s)} \u79d2"

        return [
            (
                "- \u51c6\u5907\u9636\u6bb5\uff1a\u8fde\u63a5\u5e95\u76d8\u3001"
                "\u5347\u964d\u548c\u673a\u68b0\u81c2\u63a7\u5236\u5668\uff0c\u673a\u68b0\u81c2\u8fdb\u5165 ready \u9884\u7f6e\u4f4d\u3002"
            ),
            (
                f"- \u8d77\u62cd\u52a8\u4f5c\uff1a\u955c\u5934\u9ad8\u5ea6\u8c03\u6574\u5230\u7ea6 {shot.height_m:.1f} \u7c73\uff0c"
                f"\u4ee5{region}\u4e3a\u6784\u56fe\u76ee\u6807\uff0c\u8fdb\u5165{movement}\u3002"
            ),
            (
                f"- \u8ddf\u62cd\u52a8\u4f5c\uff1a\u5728 {timing} \u5185\u6301\u7eed\u8ddf\u8e2a\u4e3b\u4f53\uff0c"
                f"\u901a\u8fc7\u5e95\u76d8\u524d\u540e\u79fb\u52a8\u7ef4\u6301\u7ea6 {shot.distance_m:.1f} \u7c73\u8ddd\u79bb\uff0c"
                f"\u901a\u8fc7\u89d2\u901f\u5ea6\u8c03\u6574\u628a\u4e3b\u4f53\u7a33\u5b9a\u5728{region}\u3002"
            ),
            (
                f"- \u6784\u56fe\u6821\u6b63\uff1a\u5b9e\u65f6\u6839\u636e\u76ee\u6807\u5728\u753b\u9762\u4e2d\u7684\u4f4d\u7f6e\u548c\u5360\u6bd4\uff0c"
                f"\u5fae\u8c03\u5347\u964d\u548c\u5e95\u76d8\uff0c\u5c06\u4e3b\u4f53\u753b\u9762\u5360\u6bd4\u63a7\u5236\u5728\u7ea6 {shot.subject_scale_target:.2f}\u3002"
            ),
            (
                "- \u5b89\u5168\u52a8\u4f5c\uff1a\u82e5\u76ee\u6807\u4e22\u5931\uff0c\u7acb\u5373\u505c\u6b62\u5e95\u76d8\u548c\u5347\u964d\uff0c"
                "\u8fdb\u5165\u7b49\u5f85/\u641c\u7d22\u5360\u4f4d\u903b\u8f91\u3002"
            ),
            "- \u7ed3\u675f\u52a8\u4f5c\uff1a\u8fbe\u5230\u8ba1\u5212\u65f6\u957f\u540e\uff0c\u5e95\u76d8\u548c\u5347\u964d\u505c\u6b62\u5e76\u5173\u95ed\u63a7\u5236\u5668\u3002",
        ]

    @staticmethod
    def _movement_description(template: str) -> str:
        if template == "side_front_follow":
            return "\u4fa7\u524d\u65b9\u8ddf\u62cd\u8f68\u8ff9"
        if template == "mid_follow_safe":
            return "\u901f\u5ea6\u66f4\u4fdd\u5b88\u7684\u5b89\u5168\u4e2d\u666f\u8ddf\u62cd"
        return "\u4e2d\u666f\u6b63\u5411\u8ddf\u62cd"
