/* Generic Chinese-novel mock state. Do not hardcode this into UI components —
   render from these structures so swapping data updates the entire surface. */

window.MOCK_NOVEL = {
  script_title: "雾港未尽",
  script_author: "示例小说",
  script_chapter_count: 1208,
  script_word_count: 2_413_000,
  script_mode_label: "标准章节标题",
  script_confidence: 0.92,
};

window.MOCK_STATE = {
  player: {
    name: "顾承砚",
    role: "漂流的史官",
    background:
      "出身南陵旧学世家，被卷入雾港事件后，意外获得在三个王朝间穿越的能力。能记录但难以改变。",
    current_location: "北港码头 · 废弃灯塔下",
    inventory: [
      { name: "残页·光绪十三年", quality: "可推断时间" },
      { name: "黑铁怀表", quality: "停在三时四十二分" },
      { name: "母亲的玉佩", quality: "已碎，二片" },
    ],
  },
  world: {
    time: "申时三刻 · 霜降前两日",
    weather: "海雾未散，能见度约二十丈",
    timeline: {
      anchor_state: "locked",
      current_label: "雾港事件 · 第二日",
      current_phase: "幸存者搜寻",
      pending_jump: null,
      anchors: [
        { id: "a1", label: "渡海前夜", phase: "起", at: 0 },
        { id: "a2", label: "雾港事件 · 首日", phase: "承", at: 1 },
        { id: "a3", label: "雾港事件 · 第二日", phase: "转·此处", at: 2, current: true },
        { id: "a4", label: "灯塔会面", phase: "转", at: 3, pending: true },
        { id: "a5", label: "南陵归途", phase: "合", at: 4, locked: true },
      ],
    },
    known_events: [
      "晨雾中浮尸三具，其一着前朝官服。",
      "灯塔守人于昨夜失踪，留下半截烛芯。",
      "码头税吏私下打听『史官』的下落。",
    ],
  },
  relationships: {
    沈知微: { tone: "信任", note: "雾港医师，知晓你的真实身份" },
    韩司直: { tone: "戒备", note: "南陵驻雾港巡检，正在追查你" },
    阿衡: { tone: "亲近", note: "灯塔守人之女，年十四" },
  },
  permissions: {
    mode: "full_access",
    pending_writes: [
      {
        id: "pw-1",
        field: "world.timeline.pending_jump",
        from: null,
        to: { label: "灯塔会面", reason: "若揭示身份则触发会面" },
        risk: "high",
        reason: "重大世界线分支：会改变后续三章的剧情走向",
      },
      {
        id: "pw-2",
        field: "relationships.韩司直.tone",
        from: "戒备",
        to: "敌对",
        risk: "medium",
        reason: "玩家拒绝了巡检的盘问",
      },
    ],
    pending_questions: [
      {
        id: "pq-1",
        text: "是否向沈知微揭示自己的真实身份？",
        choices: ["揭示", "继续隐瞒", "用残页换信任"],
      },
    ],
  },
  worldline: {
    user_variables: {
      "顾承砚.身份暴露度": "37%",
      "韩司直.警觉": "缓慢上升",
      "灯塔.是否点燃": "未",
    },
    constraints: ["不能修改光绪十三年之前的事件", "残页只能被引用三次"],
    last_projection: "若此刻揭示身份，82% 概率触发『灯塔会面』，14% 概率被巡检拘押。",
  },
  memory: {
    mode: "normal",
    main_quest: "找到雾港事件真正的发起者",
    current_objective: "在天黑前到达灯塔，与阿衡核对父亲留下的话",
    pinned: [
      "黑铁怀表停在三时四十二分，与残页时间吻合",
      "沈知微说过『若残页足三，则可推时』",
    ],
    facts: [
      "光绪十三年雾港曾沉船一艘，载客 142 人",
      "灯塔守人姓童，与南陵童氏同源",
    ],
    notes: [
      "玩家笔记：阿衡可能不是守人亲女",
    ],
    last_retrieval:
      "雾港事件 · 第二日清晨，海雾未散。码头税吏自昨夜便在打听一名持残页的史官。沈知微在医馆放下手中铜针，望向窗外灯塔方向。",
    last_context: {
      chapter_refs: ["第 312 章 · 雾港初日", "第 313 章 · 浮尸"],
      retrieval_chunks: 6,
      tokens_used: 4180,
    },
  },
  suggestions: [
    "沿码头石阶向灯塔走去，避开税吏视线",
    "先回医馆，告诉沈知微残页的新发现",
    "在灯塔下等到天黑，借海雾掩护接近",
    "翻看怀表，确认残页所指的时刻是否已到",
  ],
  history: [
    {
      role: "assistant",
      content:
        "海雾在码头之上铺得很匀，像一层未干透的纸。你站在废弃灯塔的影子里，能听见自己怀表的走音——它仍停在三时四十二分。\n\n税吏从远处经过，靴底踩过湿石的声音被雾闷在喉咙里。他朝你这边看了一眼，没停，向北港的方向去了。\n\n身后传来轻轻的脚步。你不必回头，也知道是沈知微。她说：『顾先生，残页够三片了吗？』",
      ts: "21:42",
      game_ts: "申时一刻",
    },
    {
      role: "user",
      content: "我把母亲玉佩的碎片放在手心，递给她看。",
      ts: "21:44",
      game_ts: "申时二刻",
    },
    {
      role: "assistant",
      content:
        "沈知微的目光在两片玉上停了一会儿，没有伸手去接。\n\n『这不是残页。』她说，声音很低，『但我懂你的意思——你还不愿意让我看那些纸。』\n\n海雾忽地浓了一层，把灯塔的顶遮住。远处北港方向传来铜锣声，三短一长，是雾港老规矩里『有外乡人到岸』的信号。沈知微抬眼望去，眉间微皱。",
      ts: "21:47",
      game_ts: "申时三刻",
      streaming_done: true,
    },
  ],
};

window.MOCK_RUN_STEPS = [
  { phase: "context_retrieve", message: "正在召回上下文 · 检索原文与历史", status: "done", elapsed_ms: 820, detail: "返回 6 个章节片段，含第 312–315 章。" },
  { phase: "context_agent", message: "子代理筛选上下文 · 仅保留与『揭示身份』相关", status: "done", elapsed_ms: 1240, detail: "丢弃 11 段，保留 4 段；预算 4180 tokens。" },
  { phase: "world_check", message: "校验世界线约束 · 残页引用次数", status: "done", elapsed_ms: 180, detail: "残页可用次数：剩余 1 次。" },
  { phase: "prompt_assemble", message: "组装提示词 · 注入状态、记忆、固定记忆", status: "done", elapsed_ms: 90 },
  { phase: "main_gm", message: "主 GM 正在生成正文", status: "running", elapsed_ms: 2860 },
];

window.MOCK_PLATFORM = {
  user: {
    username: "yanling",
    display_name: "晓卡",
    role: "admin",
    uid: "u_2f7a1c0d",
    bio: "做长篇拆书 RPG，主要测试中文叙事。",
  },
  database: { driver: "PostgreSQL", url: "postgresql://localhost/rpg", ok: true },
  stats: { scripts: 4, saves: 12, branches: 38, assets: 67, api_calls: 21_400 },
  scripts: [
    {
      id: 1, uid: "scr_7c1a", title: "雾港未尽", description: "示例剧本 · 已识别 1208 章",
      chapter_count: 1208, word_count: 2_413_000,
      import_report: { mode_label: "标准章节标题", confidence: 0.92, problem_label: "未发现明显异常" },
      updated_at: "2 小时前",
    },
    {
      id: 2, uid: "scr_3e44", title: "南陵旧灯录", description: "导入剧本 · 612 章",
      chapter_count: 612, word_count: 1_204_000,
      import_report: { mode_label: "中文章节规则", confidence: 0.86, problem_label: "卷标题不规则", reasons: ["跨卷尾章缺失"] },
      updated_at: "昨天",
    },
    {
      id: 3, uid: "scr_b811", title: "海上落星", description: "导入剧本 · 短篇集 88 篇",
      chapter_count: 88, word_count: 220_000,
      import_report: { mode_label: "数字点号规则", confidence: 0.74, problem_label: "存在 3 段疑似引言", reasons: ["第 12、43、77 段长度异常"] },
      updated_at: "3 天前",
    },
    {
      id: 4, uid: "scr_a02f", title: "雾港异闻录（外卷）", description: "导入剧本 · 124 段",
      chapter_count: 124, word_count: 340_000,
      import_report: { mode_label: "蕾穆丽娜规则", confidence: 0.81, problem_label: "未发现明显异常" },
      updated_at: "上周",
    },
  ],
  saves: [
    { id: 11, uid: "sv_a1", title: "雾港·主线·顾承砚", script_id: 1, branch_count: 14, updated_at: "12 分钟前", current: true },
    { id: 12, uid: "sv_a2", title: "雾港·支线·沈知微视角", script_id: 1, branch_count: 6, updated_at: "昨天" },
    { id: 13, uid: "sv_a3", title: "南陵旧灯录·开场", script_id: 2, branch_count: 2, updated_at: "上周" },
    { id: 14, uid: "sv_a4", title: "海上落星·试玩", script_id: 3, branch_count: 1, updated_at: "上月" },
  ],
  recent_assets: [
    { name: "南陵地图_v2.png", size: 1_240_000, kind: "image", at: "今天" },
    { name: "光绪十三年残页扫描.zip", size: 18_400_000, kind: "archive", at: "昨天" },
    { name: "雾港人物谱.md", size: 12_400, kind: "markdown", at: "3 天前" },
  ],
};
