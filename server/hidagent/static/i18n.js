// UI strings and plain-language step descriptions, in English and Simplified Chinese.
const I18N = {
  en: {
    targetMode: "Device mode", targetAuto: "Auto detect", targetWaiting: "Waiting to detect a device", portrait: "Portrait", landscape: "Landscape",
    waiting: "Waiting for the device screen", liveCaption: "Click to tap · drag to swipe · scroll to scroll",
    estop: "Emergency stop", motionOn: "Motion: on", motionOff: "Motion: off", estopHint: "Stops the AI and lets go of the screen",
    clear: "Clear history",
    manual: "Manual controls", home: "Home", back: "Back", calibrate: "Calibrate", type: "Type", press: "Press",
    typePh: "Type text", keyPh: "Key: enter, cmd+a…", log: "Live log",
    workbenchExample: "Inventory → English work note",
    workbenchTask: "Read the current inventory and create a new Notes note with an original concise restock summary, needed item quantities, total cost, and remaining budget.",
    inventoryOCRWorkflow: "Local OCR → {model} organizes inventory → Exact arithmetic → Jev / Haiku checks original image → ESP32",
    inventoryWorkflow: "{model} reads inventory → Local exact arithmetic → Jev checks (Haiku backup) → ESP32 → Actual result",
    sourceWorkflow: "{model} reads & writes → Jev checks (Haiku backup) → ESP32 types once → Actual screen verification",
    workflow: "Haiku creates & plans → Jev checks (Haiku backup) → ESP32 sends Bluetooth input → iPhone acts → Screen feedback",
    tagline: "Describe a task. The agent reads the screen and taps for you.", taskLabel: "Task",
    s1: "Connect the controller", s1d: "Power the ESP32; pair the Bluetooth version with your device. Turn on Settings → Accessibility → Touch → AssistiveTouch.",
    s2: "Share the device screen", s2d: "On your device: Control Center → Screen Mirroring → {name}.",
    s3: "Teach it where the pointer is", s3d: "Manual controls → Calibrate, then click the round pointer on the live screen.",
    taskPh: "Create a note for Shopify Builder Sundays", start: "Start", stop: "Stop",
    status: "status", actions: "actions", total: "total time", perAction: "per action", plan: "Plan",
    idle: "Idle", working: "Working…", done: "Done", gaveUp: "Gave up", stopped: "Stopped", errorState: "Error",
    calNow: "Now click exactly on the round pointer in the live screen.", calFinding: "Finding the pointer…", calOk: "Calibrated",
    needTask: "Describe a task first", thinking: "thinking", doing: "doing",
    step: "Step", planner: "Planner", decision: "Planner", executor: "Actor", avg: "avg",
    examples: ["Set an alarm for 7:00 AM that repeats every day (check all days under Repeat), then save it", {"label": "Shopify Builder Sundays · AI writes live", "task": "Open Notes and create a NEW note using Command-N. Write an ORIGINAL, fresh mini pitch for the Shopify Builder Sundays audience about a tiny ESP32 board helping AI control a real iPhone. Invent the wording yourself in one or two short English sentences. Use Shopify Builder Sundays as the title. Put the pitch in its own paragraph. Put the principle explanation Haiku -> Jev -> ESP32 -> iPhone on a separate line. Stop when all three blocks are visible. Do not edit existing notes or open drawing tools."},
      "Open Settings and go to Wi-Fi", "Open Notes and write \"hello\""],
  },
  zh: {
    targetMode: "设备模式", targetAuto: "自动识别", targetWaiting: "等待识别设备", portrait: "竖屏", landscape: "横屏",
    waiting: "等待设备画面", liveCaption: "点击 = 点按 · 拖动 = 滑动 · 滚轮 = 滚动",
    estop: "紧急停止", motionOn: "动效：开", motionOff: "动效：关", estopHint: "立即停止 AI，并松开屏幕",
    clear: "清空记录",
    manual: "手动操控", home: "主屏幕", back: "返回", calibrate: "校准", type: "输入", press: "按键",
    typePh: "输入文字", keyPh: "按键：enter、cmd+a…", log: "实时日志",
    workbenchExample: "库存补货 · 英文工作笔记",
    workbenchTask: "Read the current inventory and create a new Notes note with an original concise restock summary, needed item quantities, total cost, and remaining budget.",
    inventoryOCRWorkflow: "本地 OCR → {model} 整理库存 → 精确计算 → Jev / Haiku 核对原图 → ESP32",
    inventoryWorkflow: "{model} 读库存 → 本地精确计算 → Jev 核对（Haiku 兜底） → ESP32 → 实际结果",
    sourceWorkflow: "{model} 读屏创作 → Jev 核对（Haiku 兜底） → ESP32 一次输入 → 实际画面验收",
    workflow: "Haiku 创作与规划 → Jev 判断（Haiku 兜底） → ESP32 蓝牙输入 → iPhone 执行 → 屏幕反馈",
    tagline: "描述任务，代理会看屏幕并替你点按。", taskLabel: "任务",
    s1: "连接控制器", s1d: "给 ESP32 供电；蓝牙版先与设备配对。打开 设置 → 辅助功能 → 触控 → 辅助触控。",
    s2: "共享设备屏幕", s2d: "设备上：控制中心 → 屏幕镜像 → {name}。",
    s3: "告诉它指针在哪", s3d: "手动操控 → 校准，然后在实时画面里点一下圆形指针。",
    taskPh: "为 Shopify Builder Sundays 新建演示笔记", start: "开始", stop: "停止",
    status: "状态", actions: "动作数", total: "总用时", perAction: "每步", plan: "计划",
    idle: "空闲", working: "进行中…", done: "完成", gaveUp: "放弃", stopped: "已停止", errorState: "出错",
    calNow: "现在在实时画面里精确点一下圆形指针。", calFinding: "正在找指针…", calOk: "校准完成",
    needTask: "先写下任务", thinking: "思考", doing: "执行",
    step: "第", planner: "规划", decision: "规划模型", executor: "动作模型", avg: "平均",
    examples: ["设置早上 7:00 的闹钟，在「重复」里把每一天都勾上，然后保存", {"label": "Shopify Builder Sundays · AI 现场创作", "task": "Open Notes and create a NEW note using Command-N. Write an ORIGINAL, fresh mini pitch for the Shopify Builder Sundays audience about a tiny ESP32 board helping AI control a real iPhone. Invent the wording yourself in one or two short English sentences. Use Shopify Builder Sundays as the title. Put the pitch in its own paragraph. Put the principle explanation Haiku -> Jev -> ESP32 -> iPhone on a separate line. Stop when all three blocks are visible. Do not edit existing notes or open drawing tools."}, "打开设置，进入 Wi-Fi", "打开备忘录写 hello"],
  },
};

// One short phrase per action, in both languages: [english, chinese].
function describeStep(s) {
  const sideways = s.action === "swipe" && Math.abs(s.x2 - s.x1) > Math.abs(s.y2 - s.y1);
  const table = {
    plan: ["Made a plan", "制定了计划"],
    "replan (stuck)": ["Stuck — made a new plan", "卡住了，重新规划"],
    replan: ["Rethinking the plan", "重新考虑计划"],
    tap: ["Tap", "点按"],
    swipe: sideways ? ["Swipe sideways", "左右滑动"] : ["Swipe up / down", "上下滑动"],
    paragraph: ["New paragraph", "另起一段"],
    path: ["Draw a stroke", "画一笔"],
    circle: ["Draw a circle", "画一个圆"],
    type: [`Type “${s.text}”`, `输入“${s.text}”`],
    key: [`Press ${s.combo}`, `按 ${s.combo}`],
    home: ["Go to the Home Screen", "回到主屏幕"],
    back: ["Go back", "返回"],
    wait: ["Wait for the screen", "等待画面加载"],
    hiccup: ["Hiccup — recovering", "出了点小问题，正在恢复"],
    done: ["Done", "完成"],
    fail: ["Gave up", "放弃了"],
    stopped: ["Stopped by you", "已被你停止"],
    error: ["Something went wrong", "出错了"],
  };
  return table[s.action] || [s.action, s.action];
}

Object.assign(I18N.en, {
  chipDevice: "ESP32", chipScreen: "Mirror", chipCalibrated: "Calibration",
  waitingHow: "On the iPad: Control Center → Screen Mirroring → {name}", examplesLabel: "Try",
  stepsTab: "Steps", voiceTab: "Voice", panels: "Activity",
  logEmpty: "No activity yet", logEmptyHow: "Each action shows up here with its reasoning and timing.",
  stepsEmpty: "No steps yet", stepsEmptyHow: "Start a task to see a screenshot of every action.",
  modelContent: "Content", modelPlanning: "Planning", modelBackup: "Check · {model} backup", modelVision: "Vision check", modelJudgment: "Judgment",
});
Object.assign(I18N.zh, {
  chipDevice: "ESP32", chipScreen: "镜像", chipCalibrated: "校准",
  waitingHow: "在 iPad 上：控制中心 → 屏幕镜像 → {name}", examplesLabel: "试试",
  stepsTab: "步骤", voiceTab: "语音", panels: "活动",
  logEmpty: "还没有记录", logEmptyHow: "代理的每个动作、理由和用时都会显示在这里。",
  stepsEmpty: "还没有步骤", stepsEmptyHow: "开始任务后，每个动作都会附上截图。",
  modelContent: "内容", modelPlanning: "规划", modelBackup: "核对 · {model} 兜底", modelVision: "视觉核对", modelJudgment: "判断",
});
Object.assign(I18N.en, {voiceStart: "Start voice", voiceEnd: "End voice", voicePath: "Voice → Gemini Live → Jev / Haiku → ESP32 → iPhone"});
Object.assign(I18N.zh, {voiceStart: "开始语音", voiceEnd: "结束语音", voicePath: "语音 → Gemini Live → Jev / Haiku → ESP32 → iPhone"});
Object.assign(I18N.en, {voiceCameraStart: "Open camera"});
Object.assign(I18N.zh, {voiceCameraStart: "打开相机"});
