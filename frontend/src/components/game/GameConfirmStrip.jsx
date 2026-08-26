/* Game Console composer — 非阻塞确认条(ConfirmStrip)+ config_card 内联卡片(ConfigCard)。
   纯机械从 game-composer.jsx 搬出,DOM/视觉/行为零变化。 */
import React from 'react';
import { useState as useStateC, useRef as useRefC, useEffect as useEffectC } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import AgentModelPicker from '../AgentModelPicker.jsx';
import { capConfig } from '../ModelConfigInterceptModal.jsx';
import { plGoto } from '../../router.js';

// task 53：onApprove/onReject/onAnswer 现在签名是 (it) → 调用方拿 {id, index}
// 双字段发后端（id 优先；老数据没 id 时走 index 兜底，确保历史 pending 也能清掉）。
// config_card 是后端 agent:config_card 往 pending_questions 里塞的「配置引导」条目
// (kind === "config_card")。它复用同一个 pending 列表,但渲染成一张独立的配置卡片(非普通问句行)。
//   - mode "ask_default" / "missing_key" → 内联在 strip 里(本组件渲染)
//   - mode "model_not_configured" (hard===true) → 不内联,交给父组件开阻塞弹窗(onHardConfig)
const isConfigCard = (q) => q && q.kind === 'config_card';

// task 53：onApprove/onReject/onAnswer 现在签名是 (it) → 调用方拿 {id, index}
// 双字段发后端（id 优先；老数据没 id 时走 index 兜底，确保历史 pending 也能清掉）。
// config_* 回调(可选,缺省时 config_card 退化为只显示文字+取消):
//   onConfigDefault(handleId, item, model)  ask_default「用 X 生成」:持久化偏好 + clearQuestions + startRun
//   onConfigContinue(handleId, item, label) missing_key 配好后「继续」/「重试」:clearQuestions + startRun
//   onHardConfig(item)                       model_not_configured:打开阻塞弹窗
//   onConfigSettings()                       「去模型设置」:跳设置(默认 window.location.hash)
function ConfirmStrip({ pendingWrites, pendingQuestions, onApprove, onReject, onAnswer, onDismiss, clicheNotice, onRetryCliche, onDismissCliche,
  onConfigDefault, onConfigContinue, onHardConfig, onConfigSettings }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useStateC({});
  // 防御：后端 /api/state 返回的 permissions 可能不带这两个数组（partial state），
  // 没兜底就 .map -> 白屏。task 5 修复点之一。
  const writes = Array.isArray(pendingWrites) ? pendingWrites : [];
  const questions = Array.isArray(pendingQuestions) ? pendingQuestions : [];
  // 关键：复合 key。原来用 `key={it.id}` 在三种场景下会重复触发 React key warning：
  //   1) backend 不给 id → 多个 undefined key
  //   2) question 和 write 各自有 id=1（不同列表里数字重合）
  //   3) backend 偶尔重复推送同一 pending 项
  // 用 `${kind}:${id ?? idx}` 保证跨 kind 不撞，缺 id 也用 index 兜底；任意原始数据形态都唯一。
  // 同时把 ridx 留作展开/动作回调的稳定句柄，避免依赖可能缺失的 it.id。
  // config_card 与普通问句共用 pending_questions 列表,但渲染分流:
  //   isConfigCard → kind:"config"(独立配置卡片);否则 kind:"question"(原 GM 问句行,行为不变)。
  const items = [
    ...questions.map((q, i) => ({
      kind: isConfigCard(q) ? "config" : "question",
      id: q && q.id, _ridx: i,
      key: `q:${q && q.id != null ? q.id : `idx${i}`}`, data: q || {},
    })),
    ...writes.map((w, i) => ({ kind: "write", id: w.id, _ridx: i, key: `w:${w && w.id != null ? w.id : `idx${i}`}`, data: w || {} })),
  ];
  // mode "model_not_configured"(hard===true)不内联:出现即让父组件开阻塞弹窗。
  // 用 id 去重触发,避免每次 re-render 重复 onHardConfig。
  const hardItem = questions.find((q) => isConfigCard(q) && q.hard === true && q.mode === 'model_not_configured');
  const hardKey = hardItem ? (hardItem.id != null ? hardItem.id : hardItem.question) : null;
  const lastHardRef = useRefC(null);
  useEffectC(() => {
    if (hardKey && hardKey !== lastHardRef.current && onHardConfig) {
      lastHardRef.current = hardKey;
      onHardConfig(hardItem);
    }
    if (!hardKey) lastHardRef.current = null;
  }, [hardKey]);
  // hard config_card 走阻塞弹窗,不内联占位 → 从可见列表里剔除。
  const visibleItems = items.filter((it) => !(it.kind === "config" && it.data.hard === true && it.data.mode === 'model_not_configured'));
  // 反馈 #22: 套路比喻提示 — 复用本 strip(GM 询问窗口)做承接,按钮复用 onRetry。
  const clichePhrases = (clicheNotice && Array.isArray(clicheNotice.phrases)) ? clicheNotice.phrases.filter(Boolean) : [];
  const hasCliche = clichePhrases.length > 0;
  if (!visibleItems.length && !hasCliche) return null;
  // expanded/onAnswer/onApprove/onReject/onDismiss 仍按 it.id 走（与父组件原契约一致）；
  // 缺 id 时回退到 key（复合字符串），父组件 filter(x => x.id !== id) 拿不到 undefined 不会误删。
  // task 53：返回 {id, index} 双字段。id 是后端 v2+ 给的稳定 id；老 pending
  // 没 id（如本地已有的 8 条 zombie question）走 index 兜底，后端 _pop_*_pending
  // 会按 id 优先 / index fallback 来弹出，保证所有历史 pending 都能被清掉。
  const handleId = (it) => ({ id: (it.id != null ? it.id : null), index: it._ridx });
  const tog = (id) => setExpanded(e => ({ ...e, [id]: !e[id] }));
  return (
    <div className="gc-confirm-strip">
      <div className="gc-confirm-strip-head">
        <span className="dot warn pulse" />
        <span>{t('game.confirm.pending_count', { count: visibleItems.length + (hasCliche ? 1 : 0) })}</span>
      </div>
      {hasCliche && (
        <div className="gc-confirm gc-confirm-q">
          <div className="gc-confirm-marker"><Icon name="info" size={12} /></div>
          <div className="gc-confirm-body">
            <div className="gc-confirm-row1">
              <span className="gc-confirm-tag">{t('game.composer.cliche_tag')}</span>
              <span className="gc-confirm-text serif">{t('game.composer.cliche_notice', { phrases: clichePhrases.join('、') })}</span>
            </div>
            <div className="gc-confirm-actions">
              <button className="gc-chip-btn gc-chip-primary" onClick={onRetryCliche}>{t('game.composer.cliche_retry')}</button>
            </div>
          </div>
          <button className="iconbtn" onClick={onDismissCliche} title={t('game.composer.dismiss_tip')}><Icon name="close" size={11} /></button>
        </div>
      )}
      {visibleItems.map(it => it.kind === "config" ? (
        <ConfigCard
          key={it.key}
          it={it}
          handleId={handleId(it)}
          onConfigDefault={onConfigDefault}
          onConfigContinue={onConfigContinue}
          onConfigSettings={onConfigSettings}
          onDismiss={onDismiss}
        />
      ) : it.kind === "question" ? (
        <div key={it.key} className="gc-confirm gc-confirm-q">
          <div className="gc-confirm-marker"><Icon name="info" size={12} /></div>
          <div className="gc-confirm-body">
            <div className="gc-confirm-row1">
              <span className="gc-confirm-tag">{t('game.confirm.gm_question')}</span>
              {/* task 46：后端 state.add_pending_question 写 {question, options, source, turn}；
                  旧前端读 it.data.text / it.data.choices 永远为空 → UI 显示『GM 询问』但内容为空。
                  双向兼容（question/text 取一，options/choices 取一）。 */}
              <span className="gc-confirm-text serif">{it.data.question || it.data.text || t('game.confirm.question_empty')}</span>
            </div>
            <div className="gc-confirm-actions gc-confirm-choices">
              {((it.data.options || it.data.choices) || []).map((c, ci) => (
                // c 本身可能重复 / null，复合 (key, ci, c) 保证唯一；
                // 即便 backend 给两个相同 "继续" 也不会撞 key。
                // gc-chip-choice:选项可能是长叙事句,需纵向全宽 + 可换行(不能用固定高横向 chip,否则叠在一起)。
                <button key={`${it.key}:${ci}:${c}`} className="gc-chip-btn gc-chip-choice"
                  onClick={() => onAnswer(handleId(it), c)}>{c}</button>
              ))}
            </div>
          </div>
          <button className="iconbtn" onClick={() => onDismiss(handleId(it))} title={t('game.confirm.no_answer_tip')}><Icon name="close" size={11} /></button>
        </div>
      ) : (
        <div key={it.key} className={`gc-confirm gc-confirm-w gc-confirm-risk-${it.data.risk}`}>
          <div className="gc-confirm-marker">
            <Icon name={it.data.risk === "high" ? "warn" : "info"} size={12} />
          </div>
          <div className="gc-confirm-body">
            <div className="gc-confirm-row1">
              <span className="gc-confirm-tag">{it.data.risk === "high" ? t('game.confirm.write_risk_high') : it.data.risk === "medium" ? t('game.confirm.write_risk_medium') : t('game.confirm.write_risk_low')}</span>
              <span className="gc-confirm-diff mono">
                <span className="gc-confirm-field">{it.data.field}</span>
                <span className="gc-diff-arrow"><Icon name="arrow_right" size={10} /></span>
                <span className="gc-diff-to">{formatVal(it.data.to)}</span>
              </span>
              <button className="gc-confirm-toggle muted-2" onClick={() => tog(it.key)} title={t('game.confirm.detail_tip')}>
                <Icon name={expanded[it.key] ? "chevron_up" : "chevron_down"} size={11} />
              </button>
            </div>
            {expanded[it.key] && (
              <div className="gc-confirm-expand">
                <div className="gc-confirm-diff-full mono">
                  <span className="gc-diff-from">{formatVal(it.data.from)}</span>
                  <Icon name="arrow_right" size={11} style={{color: "var(--muted-2)"}} />
                  <span className="gc-diff-to">{formatVal(it.data.to)}</span>
                </div>
                <div className="gc-confirm-reason muted">{it.data.reason}</div>
              </div>
            )}
            <div className="gc-confirm-actions">
              <button className="gc-chip-btn gc-chip-primary" onClick={() => onApprove(handleId(it))}>
                <Icon name="check" size={11} /> {t('game.confirm.allow')}
              </button>
              <button className="gc-chip-btn" onClick={() => onReject(handleId(it))}>
                <Icon name="close" size={11} /> {t('game.confirm.reject')}
              </button>
            </div>
          </div>
          <button className="iconbtn" onClick={() => onDismiss(handleId(it))} title={t('game.confirm.later_tip')}><Icon name="close" size={11} /></button>
        </div>
      ))}
    </div>
  );
}

// ConfigCard —— config_card 的内联渲染(mode "ask_default" / "missing_key")。
// 与 GM 问句行用同一套 .gc-confirm 视觉骨架,只换 marker/tag,保持风格一致;无 emoji,用 Cloudscape iconName。
function ConfigCard({ it, handleId, onConfigDefault, onConfigContinue, onConfigSettings, onDismiss }) {
  const { t } = useTranslation();
  const item = it.data || {};
  const cap = capConfig(item.capability);
  const model = item.model || '';
  const mode = item.mode || '';
  // missing_key:用户在卡片里配好(选模型 / 加 key)后,才点亮「继续」重试。
  const [ready, setReady] = useStateC(false);
  useEffectC(() => {
    if (mode !== 'missing_key') return;
    const onCreds = () => setReady(true);
    window.addEventListener('rpg-credentials-updated', onCreds);
    return () => window.removeEventListener('rpg-credentials-updated', onCreds);
  }, [mode]);
  const goSettings = () => {
    if (onConfigSettings) onConfigSettings();
    else { plGoto('settings-models'); }
    if (onDismiss) onDismiss(handleId);
  };
  return (
    <div className="gc-confirm gc-confirm-config">
      <div className="gc-confirm-marker"><Icon name="settings" size={12} /></div>
      <div className="gc-confirm-body">
        <div className="gc-confirm-row1">
          <span className="gc-confirm-tag">{t('game.confirm.config_tag', { defaultValue: '配置' })}</span>
          <span className="gc-confirm-text serif">{item.question || ''}</span>
        </div>
        {mode === 'ask_default' && (
          <div className="gc-confirm-actions">
            <button className="gc-chip-btn gc-chip-primary"
              onClick={() => onConfigDefault && onConfigDefault(handleId, item, model)}>
              {t('game.composer.config_generate_with', { model: model || cap.label })}
            </button>
            <button className="gc-chip-btn" onClick={goSettings}>
              <Icon name="settings" size={11} /> {t('game.composer.config_go_model_settings')}
            </button>
          </div>
        )}
        {mode === 'missing_key' && (
          <div className="gc-config-inline">
            {/* 内嵌当前能力的模型选择器:用户可就地加 key / 选模型(AgentModelPicker 自带「无 key」告警+跳转链接)。 */}
            <AgentModelPicker
              prefPrefix={cap.prefPrefix}
              capabilityFilter={cap.capabilityFilter}
              variant="bare"
              preferProvider={item.api_id || null}
              defaultModel={model || null}
              configHash="settings-models"
              onChange={() => setReady(true)}
            />
            <div className="gc-confirm-actions">
              <button className="gc-chip-btn gc-chip-primary" disabled={!ready}
                onClick={() => onConfigContinue && onConfigContinue(handleId, item, t('game.composer.config_continue_label'))}>
                {t('game.composer.config_continue_label')}
              </button>
              <button className="gc-chip-btn" onClick={goSettings}>
                <Icon name="settings" size={11} /> {t('game.composer.config_go_settings')}
              </button>
            </div>
          </div>
        )}
      </div>
      <button className="iconbtn" onClick={() => onDismiss && onDismiss(handleId)} title={t('game.composer.dismiss_tip')}><Icon name="close" size={11} /></button>
    </div>
  );
}

function formatVal(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "object" && v.label) return v.label;
  return JSON.stringify(v);
}

export { ConfirmStrip };
