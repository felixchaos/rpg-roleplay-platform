# DMCA 下架处理运维手册

**适用范围**: Stellatrix 平台运营团队  
**依据**: 17 U.S.C. § 512（DMCA 安全港）、CODE_COMPLIANCE_CHECKLIST DM-01..04  
**更新日期**: 2026-05-31  
**DMCA 指定代理**: 待向 `dmca.copyright.gov/osp/` 登记（见第 7 节）

---

## 1. 收件渠道

| 渠道 | 地址 | 处理优先级 |
|------|------|-----------|
| 邮件（首选） | `abuse@stellatrix.icu` | P1 — 工作日 4h 内签收 |
| 法律事务邮件 | `legal@stellatrix.icu` | P1 — 同上 |
| 服务内举报表单 | `/report` 端点 → 类型选 `dmca` | P2 — 同工作日处理 |
| 传真/邮寄 | 暂不支持；收到后扫描归档 | P2 |

收到通知后，**立即回复确认收件**（模板见附录 A），并在内部工单系统开 Ticket，标签 `dmca-notice`。

---

## 2. 通知格式验证（DMCA §512(c)(3) 要求）

按 §512(c)(3)(A) 核查以下 6 项，**全部满足**才视为合规通知：

| # | 要素 | 验证方法 |
|---|------|----------|
| 1 | 版权所有者（或授权代理人）的物理或电子签名 | 邮件签名 / 附件签名文件 |
| 2 | 被侵权作品的具体描述（含原始链接或登记号） | 人工确认 |
| 3 | 平台上侵权内容的 URL 或足以定位的描述 | 核验 URL 存在性 |
| 4 | 通知人联系方式（姓名、地址、电话、邮箱） | 人工确认 |
| 5 | 善意声明（"我善意相信该使用未经授权"） | 检查声明措辞 |
| 6 | 准确性声明（"以上信息准确，如有虚假我承担法律责任"） | 检查声明措辞 |

**任何一项缺失 → 回复要求补充材料，记录并暂不下架。**  
**全部满足 → 进入第 3 节下架流程，24 小时时钟从此刻开始计时。**

---

## 3. 24 小时内下架流程

> **目标**: 从收到合规通知起，**24 小时内**完成内容不可访问。

### 3.1 通过 Admin UI 操作

1. 登录 `https://play.stellatrix.icu` → 进入管理后台 → **DMCA Takedowns** 标签。
2. 点击 **录入通知**，填写：
   - 通知人名称、邮箱
   - 涉嫌侵权内容 URL（平台 URL，如 `/story/123/chapter/45`）
   - 原始作品描述
   - 通知收到时间（UTC）
3. 提交后系统分配 `takedown_id`，状态自动设为 `open`。
4. 点击该记录 **→ 执行下架** (`action: takedown`)，填写操作原因（如 `DMCA §512 合规下架`）。
5. 系统将调用 `POST /api/admin/dmca/takedowns/{id}/action`，后端执行内容隐藏并写审计日志。

### 3.2 如 Admin UI 暂不可用，直接 SQL 操作

```sql
-- 1. 隐藏内容（以 story chapters 为例，按实际表名调整）
UPDATE story_chapters
SET    hidden = true, hidden_reason = 'dmca', hidden_at = now()
WHERE  id = <chapter_id>;

-- 2. 写 DMCA 下架队列记录（表由 v37 迁移创建）
INSERT INTO dmca_takedowns
  (complainant_name, complainant_email, infringing_url, original_work_desc, status, created_at, actioned_at)
VALUES
  ('<name>', '<email>', '<url>', '<desc>', 'closed', now(), now());

-- 3. 写审计日志
INSERT INTO admin_audit_log (actor_id, actor_username, action, target_type, target_id, details, ip)
VALUES (1, '<admin_username>', 'dmca.takedown', 'content', '<content_id>',
        '{"reason":"DMCA §512 合规下架"}'::jsonb, '<your_ip>');
```

### 3.3 通知内容所有者

- 向账户注册邮件发送"内容下架通知"（模板见附录 B）。
- 说明：内容因 DMCA 下架通知被临时隐藏，如认为下架有误可提交反通知。
- **不要提供举报人真实信息**（参考 §512(g)(2)(A)，仅告知已收到通知）。

### 3.4 时限记录

在 Ticket 中打时间戳：
- `T0`: 合规通知收到时间
- `T1`: 内容下架完成时间
- `T1 - T0` 必须 ≤ 24 小时，否则在 Ticket 中标注超时原因并上报。

---

## 4. 反通知与 10-14 天恢复窗口

### 4.1 反通知要素（§512(g)(3)）

内容所有者可向 `legal@stellatrix.icu` 提交书面反通知，须包含：

| # | 要素 |
|---|------|
| 1 | 物理或电子签名 |
| 2 | 被下架内容的识别信息（URL、ID） |
| 3 | 善意声明（内容被错误或误识别而下架） |
| 4 | 用户姓名、地址、电话、同意联邦司法管辖的声明 |

### 4.2 反通知收到后的处理步骤

1. 在 Admin UI **→ DMCA Takedowns** 找到对应记录，点击 **录入反通知**
   (`POST /api/admin/dmca/takedowns/{id}/counter`)。
2. 系统自动记录 `counter_received_at`，计算 `restore_after`（`counter_received_at + 10 天`）。
3. **立即将反通知（含用户联系方式）转发给原举报人**，告知其有 10-14 天提起诉讼。
4. **等待 10 个工作日**（日历天 14 天），检查：
   - 若举报人提交法院禁令 → 保持下架；
   - 若未收到禁令通知 → 执行恢复（`action: restore`），内容重新可见。

### 4.3 恢复操作

```sql
-- 恢复内容可见
UPDATE story_chapters
SET    hidden = false, hidden_reason = null, hidden_at = null
WHERE  id = <chapter_id>;
```

或通过 Admin UI 点击 **恢复** 按钮（`action: restore`）。

### 4.4 无反通知

若内容所有者在 30 天内未提交反通知，维持下架，Ticket 状态改为 `closed-no-counter`。

---

## 5. 累犯阈值与账户终止

**阈值**: 同一账户累计 **3 次** 有效 DMCA 下架（DM-04）→ 触发账户终止流程。

### 5.1 Strike 计数

每次执行内容下架后，在 Admin UI **→ Strikes 标签** 操作：

- 点击 **+Strike** (`POST /api/admin/dmca/strikes/{user_id}/increment`)，填写原因（关联 takedown_id）。
- 系统在 `dmca_strikes` 表写入记录，返回当前累计数量。
- **第 3 次 strike**: 后端自动调用账户终止逻辑，向 `account_delete_queue` 插入记录，同时写入 `banned_users`（含邮箱、注册 IP）。

### 5.2 手动触发终止（如需跳过自动）

```
POST /api/admin/users/{user_id}/terminate
Body: { "reason": "DMCA 累犯三次，已送达 3 份合规下架通知" }
```

终止前务必在 Ticket 中记录：三次 takedown_id + 通知时间 + 决定人。

---

## 6. 拒绝处理（DM-03）

以下情形可合规拒绝下架：

- 通知格式不合规（缺少 §3 要素）；
- 内容 URL 不存在或已不在平台；
- 明显滥用举报（同一举报人反复提交，内容与版权无关联）。

拒绝操作：`POST /api/admin/dmca/takedowns/{id}/action`，`action: reject`，填写拒绝理由。  
回复举报人（模板见附录 C），保存往来邮件至 Ticket。

---

## 7. DMCA 指定代理注册任务清单

> **法规要求**: 自 2017 年起须在 Copyright Office 目录登记，方可享受 §512 安全港。  
> **注册地址**: `https://dmca.copyright.gov/osp/`  
> **费用**: 每个服务提供商 $6，每 3 年续期一次。

- [ ] 确定注册信息（服务名称：Stellatrix、法人名称、注册地址）
- [ ] 创建 Copyright.gov 账户（需 EIN 或个人信息）
- [ ] 在 OSP 目录填写 DMCA 代理姓名、邮寄地址、电话、邮箱（`legal@stellatrix.icu`）
- [ ] 支付 $6 注册费（信用卡/ACH）
- [ ] 下载注册确认函，存档至 `docs/legal/dmca-agent-registration.pdf`
- [ ] 在网站可见处发布指定代理信息（建议在 `/legal/dmca` 页面）
- [ ] **设日历提醒**: 3 年后（≤ 2029-05）续期，逾期则失去安全港保护

---

## 8. 记录保留

所有 DMCA 往来邮件、下架记录、反通知及决定过程，保留 **3 年**（超出诉讼时效）。  
数据库层面：`dmca_takedowns` 表物理行不删除，软删除或状态归档。

---

## 附录 A：收件确认模板

```
Subject: Re: DMCA Takedown Notice — [Your Reference]

Dear [Name],

We have received your DMCA takedown notification dated [Date] and 
are reviewing it for compliance with 17 U.S.C. § 512(c)(3).

We will complete our review and take appropriate action within 24 hours 
of confirming the notice meets statutory requirements.

Ticket ID: [TICKET-XXXX]

Stellatrix Legal Team
legal@stellatrix.icu
```

## 附录 B：内容所有者下架通知模板

```
Subject: 您的内容因 DMCA 通知已被临时隐藏

您好，

我们收到了一份针对您账户内以下内容的 DMCA 版权下架通知：
[内容 URL]

根据《数字千年版权法》§512 的要求，我们已暂时隐藏上述内容。

如您认为该内容被错误下架，您可以向 legal@stellatrix.icu 提交书面反通知。
反通知须包含：您的姓名、地址、电话、内容标识、善意声明及司法管辖同意声明。

Stellatrix 团队
```

## 附录 C：拒绝下架通知模板

```
Subject: Re: DMCA Takedown Notice — [Your Reference]

Dear [Name],

Thank you for your notice. After review, we are unable to process 
your request at this time for the following reason(s):

[具体原因]

You may resubmit a corrected notice that complies with 
17 U.S.C. § 512(c)(3) to legal@stellatrix.icu.

Stellatrix Legal Team
```
