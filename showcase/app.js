const basePlan = {
  name: "language_to_cinematic_timeline",
  version: "2.0",
  mode: "timeline",
  summary: "底盘先以保守速度调整拍摄距离，随后降低机位并执行人物居中检查；最终保持中性打光意图，供下层调度器和视觉伺服执行。",
  timeline: [
    {
      id: "b1",
      type: "base_longitudinal",
      start_at_s: 0,
      device: "s3",
      channel: "base",
      params: {
        distance_m: -0.2,
        speed_m_s: 0.1
      },
      timeout_s: 8,
      blocking: true,
      on_fail: "stop_all",
      description: "底盘后退 20 厘米，为主体留出安全构图距离。"
    },
    {
      id: "l1",
      type: "lift_delta",
      start_after: ["b1"],
      device: "s3",
      channel: "lift",
      params: {
        delta_cm: -8
      },
      timeout_s: 8,
      blocking: true,
      on_fail: "stop_all",
      description: "升降杆下降 8 厘米，形成更稳定的半身视角。"
    },
    {
      id: "cp1",
      type: "checkpoint",
      start_after: ["l1"],
      device: "local",
      channel: "vision",
      expected_frame: {
        enabled: true,
        target_class: "person",
        target_id: "main_actor",
        bbox_format: "cxcywh_norm",
        bbox: [0.5, 0.52, 0.35, 0.65],
        tolerance: {
          center_x: 0.05,
          center_y: 0.05,
          width: 0.08,
          height: 0.1
        }
      },
      servo: {
        max_iters: 8,
        allow_base: true,
        allow_lift: true,
        allow_arm: false
      },
      timeout_s: 30,
      blocking: true,
      on_vision_fail: "continue",
      description: "检查人物是否处于期望画面区域，并允许底盘与升降杆微调。"
    },
    {
      id: "f1",
      type: "follow_mode",
      start_after: ["cp1"],
      device: "local",
      channel: "vision",
      duration_s: 6,
      target_frame: {
        target_class: "person",
        target_id: "main_actor",
        bbox_format: "cxcywh_norm",
        bbox: [0.5, 0.52, 0.36, 0.62],
        tolerance: {
          center_x: 0.06,
          center_y: 0.06,
          width: 0.1,
          height: 0.12
        }
      },
      servo: {
        max_iters: 50,
        allow_base: true,
        allow_lift: true,
        allow_arm: false
      },
      timeout_s: 10,
      blocking: true,
      on_fail: "continue",
      description: "进入短时视觉跟随，保持人物半身构图。"
    }
  ],
  lighting_plan: [
    {
      id: "light1",
      start_at_s: 0,
      color_temperature: "neutral",
      intensity: "medium",
      azimuth: "front",
      height: "middle",
      description: "中性光、中等强度、正面中光，作为未指定打光时的稳健默认。"
    }
  ]
};

const planVariants = {
  checkpoint: {
    summary: "底盘后退并降低机位后执行人物居中检查；打光采用暖色侧面中光，以强化主体轮廓。",
    timelinePatch: {
      b1: { params: { distance_m: -0.2, speed_m_s: 0.1 }, description: "底盘后退 20 厘米，扩大人物与镜头的安全距离。" },
      l1: { params: { delta_cm: -8 }, description: "升降杆下降 8 厘米，让视线更接近人物胸口高度。" }
    },
    lighting: {
      color_temperature: "warm",
      azimuth: "side",
      description: "暖色光、中等强度、侧面中光，提供柔和立体感。"
    },
    response: "已采用保守后退、机位下降与 checkpoint 居中检查，打光改为暖色侧面中光。"
  },
  follow: {
    summary: "以侧前方角度执行 8 秒中景跟拍，保留视觉伺服修正空间并限制运动速度。",
    timelinePatch: {
      b1: { params: { distance_m: 0.12, speed_m_s: 0.08 }, description: "底盘以低速向前进入侧前方跟拍距离。" },
      f1: { duration_s: 8, description: "侧前方保持人物中景，视觉伺服持续约束中心偏差。" }
    },
    lighting: {
      color_temperature: "neutral",
      azimuth: "front",
      description: "中性光、中等强度、正面中光，优先保证运动镜头可读性。"
    },
    response: "已切换为 8 秒侧前跟拍模板，并把底盘速度保持在保守范围。"
  },
  arm: {
    summary: "机械臂先进入准备位，再执行轻微上移，配合人物居中检查完成稳定展示镜头。",
    timelinePatch: {},
    replaceTimeline: [
      {
        id: "a0",
        type: "arm_init_pose",
        start_at_s: 0,
        device: "p4",
        channel: "arm",
        params: { wait_first_s: 2 },
        timeout_s: 10,
        blocking: true,
        on_fail: "stop_all",
        description: "机械臂进入准备位，等待姿态稳定。"
      },
      {
        id: "a1",
        type: "arm_move_delta",
        start_after: ["a0"],
        device: "p4",
        channel: "arm",
        params: {
          front_cm: 0,
          left_cm: 0,
          up_cm: 4,
          wrist_delta_deg: 0,
          speed: 0.18,
          target_t_rad: 3.05
        },
        timeout_s: 10,
        blocking: true,
        on_fail: "stop_all",
        description: "机械臂轻微上移 4 厘米，速度受安全层约束。"
      },
      basePlan.timeline[2]
    ],
    lighting: {
      color_temperature: "cool",
      azimuth: "front",
      description: "冷色光、中等强度、正面中光，突出机械臂展示的实验质感。"
    },
    response: "已生成机械臂准备位与轻微上移方案，保留 checkpoint 作为画面验证。"
  }
};

let currentPlan = structuredClone(basePlan);

const scriptPreview = document.querySelector("#scriptPreview");
const chatLog = document.querySelector("#chatLog");
const promptInput = document.querySelector("#promptInput");
const agentForm = document.querySelector("#agentForm");
const sessionState = document.querySelector("#sessionState");
const reviewButton = document.querySelector("#reviewButton");
const confirmButton = document.querySelector("#confirmButton");
const copyJsonButton = document.querySelector("#copyJsonButton");
const demoVideo = document.querySelector("#demoVideo");
const videoFallback = document.querySelector("#videoFallback");

function renderPlan() {
  scriptPreview.textContent = JSON.stringify(currentPlan, null, 2);
}

function addMessage(role, speaker, text) {
  const message = document.createElement("div");
  message.className = `message ${role}`;

  const label = document.createElement("span");
  label.textContent = speaker;

  const body = document.createElement("p");
  body.textContent = text;

  message.append(label, body);
  chatLog.appendChild(message);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setStatus(status) {
  sessionState.textContent = status;
  sessionState.classList.toggle("confirmed", status === "confirmed");
}

function classifyPrompt(text) {
  if (/机械臂|准备位|上移|展示/.test(text)) return "arm";
  if (/侧前|跟随|跟拍|中景|8 秒|8秒/.test(text)) return "follow";
  return "checkpoint";
}

function applyVariant(key) {
  const variant = planVariants[key];
  currentPlan = structuredClone(basePlan);
  currentPlan.summary = variant.summary;

  if (variant.replaceTimeline) {
    currentPlan.timeline = structuredClone(variant.replaceTimeline);
  } else {
    for (const action of currentPlan.timeline) {
      const patch = variant.timelinePatch[action.id];
      if (!patch) continue;
      Object.assign(action, patch);
      if (patch.params) {
        action.params = { ...action.params, ...patch.params };
      }
    }
  }

  currentPlan.lighting_plan = [
    {
      ...currentPlan.lighting_plan[0],
      ...variant.lighting
    }
  ];
  renderPlan();
  return variant.response;
}

agentForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = promptInput.value.trim();
  if (!text) return;

  addMessage("user", "研究员", text);
  const response = applyVariant(classifyPrompt(text));
  setStatus("draft");
  addMessage("assistant", "语绘镜界 Agent", response);
});

reviewButton.addEventListener("click", () => {
  const actionCount = currentPlan.timeline.length;
  const light = currentPlan.lighting_plan[0];
  addMessage(
    "assistant",
    "Plan Reviewer",
    `当前方案包含 ${actionCount} 个时间轴动作；协议版本为 ${currentPlan.version}；打光为 ${light.color_temperature}/${light.intensity}/${light.azimuth}/${light.height}。`
  );
});

confirmButton.addEventListener("click", () => {
  setStatus("confirmed");
  addMessage("assistant", "语绘镜界 Agent", "方案已确认，可交由 TimelineScheduler 等待 S3/P4 连接并执行。");
});

copyJsonButton.addEventListener("click", async () => {
  const text = JSON.stringify(currentPlan, null, 2);
  try {
    await navigator.clipboard.writeText(text);
    copyJsonButton.textContent = "已复制";
  } catch {
    copyJsonButton.textContent = "可选中复制";
  }
  window.setTimeout(() => {
    copyJsonButton.textContent = "复制 JSON";
  }, 1400);
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    promptInput.value = button.dataset.prompt;
    promptInput.focus();
  });
});

document.querySelectorAll("[data-video]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-video]").forEach((item) => item.classList.remove("is-active"));
    button.classList.add("is-active");
    videoFallback.classList.remove("is-visible");
    demoVideo.src = button.dataset.video;
    demoVideo.play().catch(() => undefined);
  });
});

demoVideo.addEventListener("error", () => {
  videoFallback.classList.add("is-visible");
});

demoVideo.addEventListener("loadeddata", () => {
  videoFallback.classList.remove("is-visible");
});

renderPlan();
