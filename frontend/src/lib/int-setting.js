// int-setting.js — 「整数设置」保存前的统一口径(设置 → 记忆页,web / 手机共用)。
//
// 后端 schemas.memory.MemorySettings 的数值项全是 int,get_memory_settings 逐字段校验:非整数
// (7.5、2.5)整项丢弃、回落默认值。以前前端照存 7.5,刷新后页面仍显示 7.5,GM 实际用的却是默认
// 20 —— 用户看到的不是生效的。这里先取整、再夹到界面给的区间里,调用方用返回值回显并保存。
// 空串 / 非数字返回 null:调用方不保存,回显上一次的有效值。

export function toIntSetting(raw, min, max) {
  if (raw === '' || raw === null || raw === undefined) return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  return Math.min(max, Math.max(min, Math.round(n)));
}

// 读回来的偏好值按后端口径解析:整数(含 "7"、7.0)照用;非整数 / 布尔 / 空,后端不认、实际用的是
// 默认值 —— 返回 null,调用方保持默认值显示。
export function storedIntSetting(raw) {
  if (raw === '' || raw === null || raw === undefined || typeof raw === 'boolean') return null;
  const n = Number(raw);
  return Number.isInteger(n) ? n : null;
}
