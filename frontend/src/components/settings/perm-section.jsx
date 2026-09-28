// 权限设置区(PermSection + AuditLogView)。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { useAutoSave } from '../../platform-app.jsx';
import { SetGroup, SetRow, SetSelect } from './shared.jsx';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSAlert from '@cloudscape-design/components/alert';

// 这里只保留后端真的会读的设置:默认权限模式(新建存档时注入 state.permissions.mode,见
// rpg/platform_app/workspace/snapshot.py)。以前还有「高风险白名单」和「自定义高风险白名单」两项,
// 存进偏好后没有任何后端代码读:写入闸(state/path_ops._write_path_allowed)在完全访问模式下
// 一律放行,根本没有「按字段在完全访问下仍弹确认」的机制,内置的几个路径也不是真实状态路径
// (如 world.constraints)。开关点了什么都不变,是装饰,已下线(手机端 / iOS 同批)。
function PermSection() {
  const { t } = useTranslation();
  // task 52：从 user_preferences 拉真实值，改动 patch /api/me/preference
  const [defaultMode, setDefaultMode] = useStatePL("review");
  const save = useAutoSave(t('settings.nav.permissions'), "perm");

  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        const v = p["perm.default_mode"] || p.default_perm_mode;
        if (v) setDefaultMode(v);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <SetGroup title={t('settings.permissions.title')}>
      <SetRow label={t('settings.permissions.default_mode')} description={t('settings.permissions.default_mode_desc')}>
        <SetSelect
          value={defaultMode}
          options={[
            { value: "default",     label: t('settings.permissions.mode_default') },
            { value: "review",      label: t('settings.permissions.mode_review') },
            { value: "full_access", label: t('settings.permissions.mode_full') },
          ]}
          onChange={(val) => { setDefaultMode(val); save("default_mode", val); }}
        />
      </SetRow>

      <AuditLogView />
    </SetGroup>
  );
}

// AuditLogView — task 65：把 state.permissions.audit_log 暴露给用户。
// 后端在多处写 audit 条目：
//   - kind=write           普通写入留痕（state.py:798）
//   - kind=parse_error     LLM 输出标签解析失败（task 60）
//   - kind=rejected        权限闸门拒绝（low/medium/high）
//   - kind=hard_forbidden  permissions.x / history.x 黑名单
//   - kind=extractor_error GM 第二步失败（task 65 新增）
//   - kind=question_skip   pending_question 玩家跳过
// 现在前端能看见这些，便于排查 GM 行为异常。
function AuditLogView() {
  const { t } = useTranslation();
  const [entries, setEntries] = useStatePL([]);
  const [loading, setLoading] = useStatePL(false);
  const [hasState, setHasState] = useStatePL(true);
  const [error, setError] = useStatePL("");
  const [kindFilter, setKindFilter] = useStatePL("all");
  const refresh = React.useCallback(async () => {
    setLoading(true); setError("");
    try {
      const s = await window.api.game.state();
      const perms = (s && (s.permissions || s.state?.permissions)) || {};
      const log = Array.isArray(perms.audit_log) ? perms.audit_log : [];
      // 倒序展示，最近的在前
      setEntries(log.slice().reverse());
      setHasState(!!s);
    } catch (e) {
      setError(e?.message || t('settings.permissions.audit_log'));
      setHasState(false);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffectPL(() => { refresh(); }, []);

  // 用 .ok / .danger（来自 tokens.css 的全局色类）+ 内联色给 warning/muted
  const KIND_META = {
    write:             { label: t('settings.permissions.kind_write'),            color: "var(--ok, #7eb88e)",      desc: "" },
    parse_error:       { label: t('settings.permissions.kind_parse_error'),      color: "var(--warning, #d4a857)", desc: "" },
    rejected:          { label: t('settings.permissions.kind_rejected'),         color: "var(--danger, #c8675d)",  desc: "" },
    hard_forbidden:    { label: t('settings.permissions.kind_hard_forbidden'),   color: "var(--danger, #c8675d)",  desc: "" },
    extractor_error:   { label: t('settings.permissions.kind_extractor_error'),  color: "var(--warning, #d4a857)", desc: "" },
    set_parser_error:  { label: t('settings.permissions.kind_set_parser_error'), color: "var(--warning, #d4a857)", desc: "" },
    clarify_yield:     { label: t('settings.permissions.kind_clarify_yield'),    color: "var(--ok, #7eb88e)",      desc: "" },
    acceptance_unmet:  { label: t('settings.permissions.kind_acceptance_unmet'), color: "var(--warning, #d4a857)", desc: "" },
    question_skip:     { label: t('settings.permissions.kind_question_skip'),    color: "var(--muted, #888)",      desc: "" },
  };
  const kinds = ["all", ...Object.keys(KIND_META)];
  const filtered = kindFilter === "all" ? entries : entries.filter(e => e.kind === kindFilter);

  return (
    <>
      <SetRow
        label={t('settings.permissions.audit_log')}
        description={t('settings.permissions.audit_log_desc')}
      >
        <CSSpaceBetween direction="horizontal" size="s">
          <CSButton variant="normal" onClick={refresh} disabled={loading}>
            {loading ? t('settings.permissions.audit_loading') : t('settings.permissions.audit_refresh')}
          </CSButton>
          {error && <CSAlert type="error">{error}</CSAlert>}
        </CSSpaceBetween>
      </SetRow>
      <SetRow label={t('settings.permissions.audit_filter')} description="">
        <CSSpaceBetween direction="horizontal" size="xs">
          {kinds.map(k => {
            const meta = KIND_META[k];
            const count = k === "all" ? entries.length : entries.filter(e => e.kind === k).length;
            return (
              <CSButton
                key={k}
                variant={kindFilter === k ? "primary" : "normal"}
                onClick={() => setKindFilter(k)}
                title={meta?.desc || ""}
              >
                {k === "all" ? t('settings.permissions.audit_all') : (meta?.label || k)} · {count}
              </CSButton>
            );
          })}
        </CSSpaceBetween>
      </SetRow>
      {!hasState ? (
        <CSAlert type="info">{t('settings.permissions.audit_no_state')}</CSAlert>
      ) : filtered.length === 0 ? (
        <CSAlert type="info">
          {entries.length === 0 ? t('settings.permissions.audit_empty') : t('settings.permissions.audit_empty_filter', { kind: kindFilter })}
        </CSAlert>
      ) : (
        <div style={{maxHeight: 360, overflowY: "auto", border: "1px solid var(--pl-line, #eee)", borderRadius: 6}}>
          <table className="pl-table" style={{width: "100%", fontSize: 12, borderCollapse: "collapse"}}>
            <thead>
              <tr style={{background: "var(--pl-bg-soft, #f7f7f9)"}}>
                <th style={{textAlign: "left", padding: "6px 8px", width: 130}}>{t('settings.permissions.audit_col_time')}</th>
                <th style={{textAlign: "left", padding: "6px 8px", width: 90}}>{t('settings.permissions.audit_col_type')}</th>
                <th style={{textAlign: "left", padding: "6px 8px", width: 80}}>{t('settings.permissions.audit_col_source')}</th>
                <th style={{textAlign: "left", padding: "6px 8px"}}>{t('settings.permissions.audit_col_detail')}</th>
                <th style={{textAlign: "right", padding: "6px 8px", width: 50}}>{t('settings.permissions.audit_col_turn')}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, idx) => {
                const meta = KIND_META[e.kind] || { label: e.kind, color: "var(--muted, #888)", desc: "" };
                const detail = e.path
                  ? `${e.path} = ${typeof e.value === "string" ? e.value : JSON.stringify(e.value)}`
                  : (e.raw_spec || e.hint || "—");
                return (
                  <tr key={idx} style={{borderTop: "1px solid var(--pl-line, #eee)"}}>
                    <td style={{padding: "4px 8px", fontFamily: "ui-monospace, monospace"}}>{(e.ts || "").replace("T", " ")}</td>
                    <td style={{padding: "4px 8px"}}>
                      <span className="pl-rule-chip" style={{fontSize: 11, color: meta.color, borderColor: meta.color}}>{meta.label}</span>
                    </td>
                    <td style={{padding: "4px 8px"}} className="muted">{e.source || "—"}</td>
                    <td style={{padding: "4px 8px", wordBreak: "break-word"}}>
                      <div>{detail}</div>
                      {e.hint && e.path && (
                        <div className="muted" style={{fontSize: 11, marginTop: 2}}>· {e.hint}</div>
                      )}
                    </td>
                    <td style={{padding: "4px 8px", textAlign: "right"}} className="muted">{e.turn ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export {
  PermSection,
  AuditLogView,
};
