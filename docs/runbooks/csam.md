# CSAM 处理运维手册

**适用范围**: Stellatrix 平台运营团队（严格限制知情范围）  
**依据**: 18 U.S.C. § 2258A（CyberTipline 强制上报义务）、CODE_COMPLIANCE_CHECKLIST CSAM-01..04  
**更新日期**: 2026-05-31  
**紧急联系**: `abuse@stellatrix.icu`（内部监控，7x24）

> **警告**: 本手册所涉内容极为敏感。处理人员须将知情范围限制在绝对必要的最小集合。任何人不得以"审查"为由直接查看、下载或传播疑似 CSAM 内容本体。

---

## 1. 举报接收渠道

| 渠道 | 说明 |
|------|------|
| 平台内 `/report` 端点 | 用户选择举报类型 → `csam`，后端写入 `csam_reports` 表，状态 `pending` |
| 邮件 `abuse@stellatrix.icu` | 外部举报，运营人员手动录入 Admin UI |
| NCMEC CyberTipline 回溯 | NCMEC 主动联系时，按本手册第 3 节处理 |

所有渠道收到举报后，**必须在 24 小时内进行分类决定**（CSAM-02）。

---

## 2. 分类决定流程

收到举报后，指定单名处理人（最好为高级运营或法务），依以下步骤决定：

### 2.1 初步判定（不接触内容本体）

1. 查看举报描述、元数据（内容 ID、用户 ID、上传时间）。
2. 若平台技术层面可取得内容哈希（如 PhotoDNA 等），核查已知哈希数据库。
3. **不得以人工方式打开、预览或下载疑似内容**；如必须核查，须经法务主管批准并记录授权。

### 2.2 分类结论

| 结论 | 条件 | 后续动作 |
|------|------|----------|
| `founded`（成立） | 有理由相信内容为 CSAM | 立即执行第 3 节上报 + 第 4 节证据保全 + 帐号终止 |
| `escalate`（升级） | 无法判断，需更高级别确认 | 升级至法务主管，4h 内给出最终决定 |
| `unfounded`（不成立） | 明确属于误报 | 关闭 Ticket，告知举报人（谨慎措辞，不透露内容信息） |

在 Admin UI **→ CSAM Reports 标签** 执行决定：
```
POST /api/admin/csam/reports/{id}/decision
Body: { "decision": "founded|unfounded|escalate", "notes": "..." }
```

---

## 3. NCMEC CyberTipline 上报（18 U.S.C. § 2258A）

**法定义务**: 一旦发现（或有合理理由相信存在）CSAM，须在**知悉后尽快（通常 24 小时内）**向 NCMEC CyberTipline 上报。不上报属联邦犯罪。

### 3.1 上报地址

`https://report.cybertip.org`（NCMEC CyberTipline，需注册服务提供商账号）

### 3.2 上报信息清单

上报须提供（按 NCMEC 表单要求）：

- [ ] ESP（电子服务提供商）名称：Stellatrix
- [ ] 联系人姓名及联系方式
- [ ] 内容描述（尽量详细，但处理人员无需观看内容）
- [ ] 内容 URL 或唯一标识符（内容 ID、文件名等）
- [ ] 涉事用户信息：用户名、注册邮箱、注册 IP、最后登录 IP（如可获取）
- [ ] 上传时间（UTC）
- [ ] 举报收到时间（UTC）
- [ ] 内容哈希（如有）
- [ ] 服务提供商对内容的处置状态（已下架/保全中）

### 3.3 上报后操作

1. 下载 CyberTipline 报告回执，保存至内部文件系统 `docs/legal/csam/YYYYMMDD-cybertip-{report_id}.pdf`。
2. 在 `csam_reports` 表更新 `cybertip_report_id` 字段（TODO：待 v37 表结构确认字段名）。
3. 在 Ticket 中记录上报时间戳。

---

## 4. 证据保全（CSAM-03）

**目的**: 配合执法机构后续调查；NCMEC 报告后可能收到传票或法院命令。

### 4.1 证据保全步骤

1. **不要删除**原始内容（即使平台已对用户隐藏），保留至 NCMEC/执法机构明确指示可销毁。
2. 将涉事内容的密文副本（不解密）复制至隔离存储：

```
# 占位说明：实际生产环境应使用 S3 Object Lock（WORM）
# Bucket: stellatrix-csam-evidence（私有，禁止公开访问）
# Object Lock 模式: COMPLIANCE，保留期: 90 天

# 示例命令（实际执行须在有权限的运维账号下进行）
aws s3 cp s3://stellatrix-content/<object_key> \
    s3://stellatrix-csam-evidence/<case_id>/<object_key> \
    --no-progress

aws s3api put-object-retention \
    --bucket stellatrix-csam-evidence \
    --key <case_id>/<object_key> \
    --retention '{"Mode":"COMPLIANCE","RetainUntilDate":"<90_days_from_now>"}'
```

3. 记录证据链：复制时间戳、操作人、源对象 ETag/版本号。
4. **90 天后**: 若无执法机构继续持有要求，可申请销毁（需法务审批）。

### 4.2 架构红线：不主动扫描削弱加密

平台采用端到端加密架构。以下操作**严格禁止**：
- 为实施 CSAM 扫描而在传输/存储层解密用户内容；
- 在客户端植入任何形式的内容扫描钩子（Client-Side Scanning）；
- 将用户加密密钥用于服务端内容审查目的。

合规扫描方式仅限：
- 对用户**主动举报**或**公开可访问**内容的元数据/哈希进行比对；
- 对举报内容由有授权的人员单次核查（须记录授权）。

---

## 5. 涉事账户处置

一旦决定为 `founded`：

1. **立即暂停账户**（防止继续上传）：
   ```
   POST /api/admin/users/{user_id}/suspend
   Body: { "reason": "CSAM 内容举报，等待调查", "duration_days": null }
   ```
2. **CyberTipline 上报完成后**（或同步进行），**终止账户**：
   ```
   POST /api/admin/users/{user_id}/terminate
   Body: { "reason": "CSAM 内容确认，账户永久终止（参见 cybertip_report_id: XXXX）" }
   ```
3. 终止操作会将邮箱/IP 写入 `banned_users`，阻止重新注册。

---

## 6. 内部通报与保密

- 处理 CSAM 的内部沟通**不得经由普通即时通讯工具**；使用加密邮件或安全频道。
- 知情人员名单须记录，非必要不扩大。
- 本手册及相关案例文件须存储在访问受控位置。
- 若有媒体或外部询问，统一由法务负责人回应，其他人员拒绝置评。

---

## 7. 记录保留

| 类型 | 保留期 | 位置 |
|------|--------|------|
| CyberTipline 上报回执 | 永久 | `docs/legal/csam/` |
| 证据保全记录（链） | 90 天+，直至执法许可销毁 | S3 WORM Bucket |
| 内部 Ticket 及决定记录 | 5 年 | 工单系统 |
| 用户账户元数据（举报相关） | 5 年 | 数据库 `csam_reports` 表 |

---

## 8. 参考法规

- 18 U.S.C. § 2258A — 强制上报义务
- 18 U.S.C. § 2258B — 豁免条款（善意上报人受保护）
- 18 U.S.C. § 2256 — CSAM 定义
- NCMEC CyberTipline 服务提供商指南: `https://www.missingkids.org/gethelpnow/cybertipline`
