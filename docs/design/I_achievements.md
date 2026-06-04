# I · 成就系统(Achievements)实施级设计

> 状态:设计定稿,待进实施 Phase 1。
> 决策(用户已拍板):**目录存 DB 表 + admin 可编辑**;**v1 做分类成就墙(~16 条)**;**先落本设计文档**。

---

## 0. 现状与缺口

成就 UI 现全部在 `frontend/src/platform-app.jsx` 的 `MeOverview()` 里:
- 登录用户:8 条成就**硬编码在 JSX**,前端拿 `/api/me/stats` 真实数字现算 `unlocked = value >= target`。
- 匿名访客:纯 mock `ME_ACHIEVEMENTS`。

**已经是真数据派生**(不是纯 mock),但缺系统骨架:

| 缺口 | 现状 | 本设计 |
|---|---|---|
| 定义来源 | 硬编码 JSX,前端算 | DB 表 `achievement_defs`,admin 可编辑,服务端权威 |
| 解锁时间 | 无,每次现算 | `user_achievements.unlocked_at` 落库,只记一次 |
| 解锁通知 | 无 | 评估器返回 `newly_unlocked` → toast;`seen` 标记做通知中心 |
| 持久化 | 无 | 解锁状态入库,稀有度可按全站占比算 |
| 分类/隐藏 | 无 | `category` / `hidden` 字段 |
| 公开成就墙 | 仅 UI 文案 | `user_achievements` + 公开投影端点(Phase 3) |

数据源已就绪:`/api/me/stats`(`rpg/platform_app/api/me.py:144`)已算好
`imported.{scripts,words,chapters}` / `saves_count` / `total_rounds` /
`branch_nodes` / `branches` / `max_branch_depth` / `login_streak` /
`longest_login_streak`。Phase 1 阈值型成就**全部吃这份快照,零新增数据源**。

---

## 1. 核心原则

1. **定义在 DB、判定在确定性代码**。目录可被 admin 增改(无需发版),但**解锁判定永远是纯函数** `eval_rule(rule, snapshot)`,跑在确定性事件缝,绝不让 GM/提示词决定给不给成就。
2. **规则是声明式白名单**,不是可执行代码。`rule` 只能引用**白名单 metric** + 白名单算子 + 数字 target → admin 编辑安全,无 eval 注入面。
3. **进度不落库,只落解锁**。未解锁项的进度每次从快照现算;DB 只存"已解锁"行,避免每回合写库。

---

## 2. 数据库

### 2.1 `achievement_defs`(目录,admin 可编辑)

```sql
CREATE TABLE achievement_defs (
  id          text PRIMARY KEY,             -- 稳定 slug,如 'turns_100'(不可改,改名=新建)
  name        text NOT NULL,
  description text NOT NULL,                 -- 如何获得
  icon        text,                         -- 图标名 / emoji
  category    text NOT NULL,                -- 启程/叙事/探索/收藏/坚持/隐藏
  tier        text,                         -- bronze/silver/gold/null
  rule        jsonb NOT NULL,               -- 声明式判定(见 §3)
  hidden      boolean NOT NULL DEFAULT false,
  sort_order  int     NOT NULL DEFAULT 0,
  enabled     boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_achv_defs_cat ON achievement_defs (enabled, category, sort_order);
```

### 2.2 `user_achievements`(解锁状态)

```sql
CREATE TABLE user_achievements (
  user_id            bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  achievement_id     text   NOT NULL REFERENCES achievement_defs(id) ON DELETE CASCADE,
  unlocked_at        timestamptz NOT NULL DEFAULT now(),
  progress_at_unlock int,
  seen               boolean NOT NULL DEFAULT false,  -- 玩家是否已看过解锁提示
  PRIMARY KEY (user_id, achievement_id)
);
CREATE INDEX idx_user_achv_user ON user_achievements (user_id);
CREATE INDEX idx_user_achv_unseen ON user_achievements (user_id) WHERE seen = false;
```

> `users.id` 类型需对齐实际(bigint / uuid),实施时以 `users` 表为准。
> `ON DELETE CASCADE`:def 软删走 `enabled=false`(见 §6),硬删才级联清用户行。

迁移版本:`migrations.py` 当前最新 v57 → 本系统占 **v58**(建两表 + seed §5)。

---

## 3. 规则(rule jsonb)声明式规范

判定器只认两种结构,叶子节点的 `metric` 必须在白名单内:

```jsonc
// 单阈值
{ "metric": "total_rounds", "op": ">=", "target": 100 }

// 复合(全部满足)
{ "all": [
    { "metric": "scripts", "op": ">=", "target": 10 },
    { "metric": "words",   "op": ">=", "target": 10000000 }
] }
```

- **op 白名单**:`>=` `>` `==`(v1 只用 `>=`)。
- **metric 白名单**(映射到快照字段):
  - Phase 1(已有数据源):`saves_count` `total_rounds` `branches` `max_branch_depth` `scripts` `words` `chapters` `login_streak` `longest_login_streak`
  - Phase 2(事件埋点后注入快照):`night_turns` `max_single_save_rounds` `anchors_completed` `forks_created`
- **进度**:单阈值进度 = `min(snapshot[metric], target)`;复合取**最小子项完成比**作为整体进度(用于进度条)。
- 校验:写入/更新 def 时,后端递归校验 rule 的每个 `metric` ∈ 白名单、`op` ∈ 白名单、`target` 为数字。非法 → 400。**这是阻止 admin 写出可执行/越权规则的闸门。**

### 评估器伪码

```python
# rpg/platform_app/achievements/engine.py
def build_snapshot(db, user) -> dict:
    # 复用 api/me.py 的统计查询,抽成共享函数,返回 metric→数值 的扁平 dict
    ...

OPS = {">=": op.ge, ">": op.gt, "==": op.eq}
ALLOWED_METRICS = {...}  # §3 白名单

def eval_rule(rule, snap) -> tuple[bool, float, float]:
    if "all" in rule:
        parts = [eval_rule(r, snap) for r in rule["all"]]
        unlocked = all(p[0] for p in parts)
        ratio = min(p[1] / p[2] if p[2] else 1 for p in parts)
        return unlocked, ratio, 1.0          # 复合进度按比例
    v = snap.get(rule["metric"], 0)
    t = rule["target"]
    return OPS[rule["op"]](v, t), min(v, t), t

def evaluate(db, user) -> dict:
    snap  = build_snapshot(db, user)
    defs  = load_enabled_defs(db)            # achievement_defs where enabled
    have  = load_user_unlocked(db, user.id)  # set of achievement_id
    items, newly = [], []
    for d in defs:
        ok, prog, tgt = eval_rule(d.rule, snap)
        if ok and d.id not in have:
            insert_user_achievement(db, user.id, d.id, prog)  # unlocked_at=now(), seen=false
            newly.append(d.id)
        items.append(project(d, unlocked=ok or d.id in have, progress=prog, target=tgt))
    return {"items": items, "newly_unlocked": newly}
```

---

## 4. 触发点(确定性,去抖)

| 时机 | 动作 |
|---|---|
| `GET /api/me/achievements` | 懒评估 + 落新解锁(主路径) |
| 存档创建后 / 导入 job 完成后 / 登录成功后 | 调 `evaluate()`,把 `newly_unlocked` 带进响应或写入 unseen |
| App 外壳加载 | 拉一次 unseen,有则 toast |

回合型成就不每回合查库——靠"打开主页 / 存档创建 / 应用加载"兜底评估即可。
登录连击(streak)在登录后评估最自然。

---

## 5. Seed 目录(v58 内插入,~16 条,全部已有数据源)

| id | name | category | tier | rule |
|---|---|---|---|---|
| `first_save` | 初次启程 | 启程 | bronze | saves_count ≥ 1 |
| `first_turn` | 落笔成文 | 启程 | bronze | total_rounds ≥ 1 |
| `turns_100` | 破雾之刻 | 叙事 | bronze | total_rounds ≥ 100 |
| `turns_1k` | 千回百转 | 叙事 | silver | total_rounds ≥ 1000 |
| `turns_10k` | 万语千言 | 叙事 | gold | total_rounds ≥ 10000 |
| `branch_5` | 命运分叉 | 探索 | bronze | branches ≥ 5 |
| `depth_10` | 平行世界 | 探索 | silver | max_branch_depth ≥ 10 |
| `depth_20` | 多重宇宙 | 探索 | gold | max_branch_depth ≥ 20 |
| `scripts_3` | 藏书初成 | 收藏 | bronze | scripts ≥ 3 |
| `scripts_10` | 汗牛充栋 | 收藏 | silver | scripts ≥ 10 |
| `words_1m` | 字海泛舟 | 收藏 | silver | words ≥ 1,000,000 |
| `words_10m` | 著作等身 | 收藏 | gold | all[scripts≥10, words≥10,000,000] |
| `streak_7` | 笔耕不辍 | 坚持 | bronze | login_streak ≥ 7 |
| `streak_30` | 持之以恒 | 坚持 | silver | login_streak ≥ 30 |
| `streak_100` | 风雨无阻 | 坚持 | gold | longest_login_streak ≥ 100 |
| `chapters_1k` | 通读千章 | 收藏 | silver | chapters ≥ 1000 |

> 隐藏/事件型(`night_turns`/`max_single_save_rounds`/`anchors_completed`)留 Phase 2,需埋点后再 seed。

---

## 6. API

### 公开 / 用户态
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/achievements` | 公开目录:enabled 定义。`hidden` 项名/描述返回 `???` 占位。匿名预览用此 → **删 `ME_ACHIEVEMENTS` mock** |
| GET | `/api/me/achievements` | 用户态:`{items:[{id,name,desc,icon,category,tier,unlocked,unlocked_at,progress,target,hidden}], newly_unlocked:[id]}`,触发懒评估 |
| POST | `/api/me/achievements/seen` | 标记全部 unseen→seen(看过通知后调) |

### admin(目录 CRUD,`require_admin`)
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/achievements` | 列全部定义(含 disabled) |
| POST | `/api/admin/achievements` | 新建。校验 id slug 唯一 + rule 白名单 |
| PUT | `/api/admin/achievements/{id}` | 改 name/desc/icon/category/tier/rule/hidden/sort/enabled。`id` 不可改 |
| DELETE | `/api/admin/achievements/{id}` | **软删**:置 `enabled=false`(保留 user 行)。有引用时禁硬删 |

`api-client.js` 加 `account.achievements()` / `account.achievementsSeen()` /
`achievements.publicList()` 及 `admin.achievements.{list,create,update,remove}`。

---

## 7. admin UI

新增 admin tab「成就」(`frontend/src/pages/admin.jsx`):
- 定义表格(Cloudscape Table):id / name / 类目 / 分级 / 规则摘要 / hidden / enabled / 排序。
- 新建/编辑表单(Modal 或行内 Drawer):
  - 基础:id(新建时可填,编辑只读)、name、description、icon、category(Select)、tier(Select)、sort、hidden(Toggle)、enabled(Toggle)。
  - 规则:**v1 单阈值表单** = metric(Select,白名单)+ op(固定 `≥`)+ target(数字)。复合规则给「高级 JSON」textarea(可选,带前端预校验)。
- 保存前前端先按白名单校验 rule,后端再校验一遍(后端为准)。

---

## 8. 前端(玩家侧)改造

`MeOverview()`(`platform-app.jsx`):
- **删** 客户端 `ACHIEVEMENTS` 派生(L1031-1056)+ `ME_ACHIEVEMENTS` mock。
- 登录态拉 `/api/me/achievements`;匿名拉 `/api/achievements`(全锁、进度 0)。
- 成就墙:**按 category 分组**渲染;解锁项显 `unlocked_at` 日期 + tier 配色;未解锁显进度条;`hidden && !unlocked` 名/描述打码为「???」。
- `newly_unlocked` 非空 → 复用现有 toast 弹「🏆 解锁成就:XXX」,随后 `POST /api/me/achievements/seen`。
- App 外壳加载时拉一次 unseen,跨页面也能弹解锁提示。

复用现有 `.pl-achv` / `.pl-achv-mark` 等 class,新增 category 分组标题 + tier 配色 token。

---

## 9. 分期

- **Phase 1(本次,杀 mock + 分类墙)**:v58 双表 + seed §5 + 评估器 + 公开/用户态 3 端点 + admin CRUD + admin「成就」tab + 主页接真实 API + 解锁 toast。阈值型成就全部真实/持久/有通知/可后台编辑。
- **Phase 2(事件 & 隐藏)**:在回合提交/导入/锚点链路埋点,注入 `night_turns`/`max_single_save_rounds`/`anchors_completed` 等 metric → seed 隐藏成就;稀有度 = 全站解锁占比(物化计数或按需聚合)。
- **Phase 3(社交)**:`GET /api/u/{username}/achievements` 公开成就墙投影(受个人主页可见性开关约束,见 `platform-app.jsx:1640`)+ 成就分享卡片。

---

## 10. 待确认/风险

- `users.id` 实际类型(bigint/uuid)→ 决定外键列类型。
- 复合规则进度条语义(取最小子项比例)是否够直观;可后续改成「已完成 N/M 条件」。
- 解锁通知跨页面分发:v1 用「外壳加载 + 关键动作响应回带 newly_unlocked」即可;若要实时(正在游戏中解锁立即弹),可接现有 Redis 事件总线/SSE,留 Phase 2。
- admin 改 rule 后,已解锁用户**不回收**(只增不减),避免"成就被夺走"的负体验;调低门槛会让更多人补解锁(下次评估时)。这是刻意取舍,实施时在 admin UI 标注。
