/* RATH — Platform 内嵌子页(#rath,游玩 / Play 导航组下,与酒馆平级)。
 *
 * 设计:docs/design/rath_observation_deck_v0.md。后端 rpg/routes/rath.py。
 * 核心铁律(前端视角):这是「离线活世界」实验的**观测面板**,不写游戏 state ——
 * 所有操作(建实验/tick/加速/暂停/归档/引导)都只作用于 rath_experiments / rath_events,
 * 与玩家正在玩的存档 state 无关(存档只被只读地取材料)。
 *
 * 布局:
 *   顶部 — 实验选择/创建区(无实验→存档下拉+启动按钮;有实验→下拉切换)。
 *   实验面板(选中后 GET 详情,30s 轮询):
 *     · 状态行卡片:世界时间 + 加速档 SegmentedControl + 世界运行 Toggle(自动推进说明)。
 *     · 操作条:推进一步 / 归档(二次确认)。
 *     · 角色动态板:fluctlights 卡片网格(goal/stance/private_memories)。
 *     · 日志:顶部引导插入行(单行输入+按钮) + events 列表(新在上),互动事件可展开 transcript。
 *
 * 403(flag 未开放)→ 整页空态提示,不渲染任何操作 UI。
 */
import React from 'react';
import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';

import CSHeader from '@cloudscape-design/components/header';
import CSContainer from '@cloudscape-design/components/container';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSAlert from '@cloudscape-design/components/alert';
import CSSelect from '@cloudscape-design/components/select';
import CSSegmentedControl from '@cloudscape-design/components/segmented-control';
import CSInput from '@cloudscape-design/components/input';
import CSToggle from '@cloudscape-design/components/toggle';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSExpandableSection from '@cloudscape-design/components/expandable-section';

import { ConfirmModal } from '../tavern-app.jsx';

const POLL_MS = 30000;
const ACCEL_OPTIONS = [1, 60, 240];

function StatCell({ label, value }) {
  return (
    <div>
      <CSBox variant="awsui-key-label">{label}</CSBox>
      <div>{value}</div>
    </div>
  );
}

/* ── 实验选择/创建条 ─────────────────────────────────────────────── */
function ExperimentPicker({ experiments, activeId, onSwitch, onCreated }) {
  const { t } = useTranslation();
  const [saves, setSaves] = useState(null);
  const [pickedSaveId, setPickedSaveId] = useState(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (experiments.length > 0) return; // 已有实验时不必拉存档列表
    let alive = true;
    window.api.saves.list()
      .then((r) => {
        const list = Array.isArray(r) ? r : (r?.items || r?.saves || []);
        if (alive) setSaves(list);
      })
      .catch(() => { if (alive) setSaves([]); });
    return () => { alive = false; };
  }, [experiments.length]);

  const saveOptions = (saves || []).map((s) => ({
    value: String(s.id),
    label: `${s.title || t('rath_page.picker.untitled_save', { defaultValue: '未命名存档' })} · #${s.id}`,
  }));

  const doCreate = async () => {
    if (!pickedSaveId || creating) return;
    setCreating(true);
    try {
      const r = await window.api.rath.create({ save_id: Number(pickedSaveId) });
      if (!r || r.ok === false) throw new Error(r?.error || t('rath_page.toast.create_failed', { defaultValue: '建立实验失败' }));
      window.__apiToast?.(t('rath_page.toast.created', { defaultValue: '实验已启动' }), { kind: 'ok', duration: 2000 });
      onCreated?.(r.experiment);
    } catch (e) {
      window.__apiToast?.(t('rath_page.toast.create_failed', { defaultValue: '建立实验失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      setCreating(false);
    }
  };

  if (experiments.length > 0) {
    const activeOpt = experiments.find((e) => String(e.id) === String(activeId));
    return (
      <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
        <CSSelect
          selectedOption={activeOpt ? { value: String(activeOpt.id), label: `#${activeOpt.id} · ${activeOpt.world_clock_label || ''}` } : null}
          options={experiments.map((e) => ({ value: String(e.id), label: `#${e.id} · ${e.world_clock_label || ''}${e.status === 'paused' ? t('rath_page.picker.paused_suffix', { defaultValue: '(已暂停)' }) : ''}` }))}
          onChange={({ detail }) => onSwitch(Number(detail.selectedOption.value))}
          placeholder={t('rath_page.picker.switch_placeholder', { defaultValue: '切换实验' })}
        />
      </CSSpaceBetween>
    );
  }

  return (
    <CSContainer>
      <CSSpaceBetween size="s">
        <div>
          <CSBox variant="h3">{t('rath_page.picker.intro_title', { defaultValue: '离线世界仍在继续' })}</CSBox>
          <CSBox color="text-body-secondary" fontSize="body-s">
            {t('rath_page.picker.intro_desc', { defaultValue: '给一个存档开一场有界实验:你离开之后,世界按真实时间继续运转,议程 NPC 会真的互相对话、留下痕迹——回来问问他们,他们记得。' })}
          </CSBox>
        </div>
        <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
          <CSSelect
            selectedOption={pickedSaveId ? saveOptions.find((o) => o.value === pickedSaveId) : null}
            options={saveOptions}
            onChange={({ detail }) => setPickedSaveId(detail.selectedOption.value)}
            placeholder={t('rath_page.picker.save_placeholder', { defaultValue: '选择一个存档' })}
            empty={t('rath_page.picker.no_saves', { defaultValue: '暂无存档' })}
            loadingText={t('common.loading')}
            statusType={saves == null ? 'loading' : 'finished'}
          />
          <CSButton variant="primary" disabled={!pickedSaveId || creating} loading={creating} onClick={doCreate}>
            {t('rath_page.picker.launch_btn', { defaultValue: '启动实验' })}
          </CSButton>
        </CSSpaceBetween>
      </CSSpaceBetween>
    </CSContainer>
  );
}

/* ── 单条日志事件(事件 / 互动 / 引导,互动可展开 transcript)────────────── */
function EventRow({ ev }) {
  const { t } = useTranslation();
  const isScene = ev.kind === 'scene';
  const isDirective = ev.kind === 'directive';
  const transcript = (ev.payload && Array.isArray(ev.payload.transcript)) ? ev.payload.transcript : [];
  const badgeColor = isDirective ? 'blue' : (isScene ? 'green' : 'grey');
  const badgeText = isDirective
    ? t('rath_page.event.kind_directive', { defaultValue: '引导' })
    : (isScene ? t('rath_page.event.kind_scene', { defaultValue: '互动' }) : t('rath_page.event.kind_heartbeat', { defaultValue: '事件' }));
  const body = (
    <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
      <CSBadge color={badgeColor}>{badgeText}</CSBadge>
      <CSBox color="text-body-secondary" fontSize="body-s">{ev.world_clock_label}</CSBox>
      <CSBox>{ev.summary || t('rath_page.event.no_summary', { defaultValue: '(无摘要)' })}</CSBox>
    </CSSpaceBetween>
  );
  if (!isScene || transcript.length === 0) {
    return <div className="rath-event-row">{body}</div>;
  }
  return (
    <div className="rath-event-row">
      <CSExpandableSection headerText={body} variant="footer">
        <CSSpaceBetween size="xs">
          {transcript.map((line, i) => (
            <div key={i} style={{ fontSize: 13 }}>
              <strong>{line.speaker || '?'}</strong>{'：'}{line.line}
            </div>
          ))}
        </CSSpaceBetween>
      </CSExpandableSection>
    </div>
  );
}

/* ── 选中实验的完整面板 ─────────────────────────────────────────────── */
function ExperimentPanel({ expId }) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [ticking, setTicking] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  const [accelBusy, setAccelBusy] = useState(false);
  const [archiveConfirm, setArchiveConfirm] = useState(false);
  const pollRef = useRef(null);

  const load = useCallback(async (silent) => {
    try {
      const r = await window.api.rath.detail(expId);
      if (!r || r.ok === false) throw new Error(r?.error || t('rath_page.toast.load_failed', { defaultValue: '加载实验失败' }));
      setDetail(r);
      setLoadErr(null);
    } catch (e) {
      if (!silent) setLoadErr(e?.message || String(e));
    }
  }, [expId, t]);

  useEffect(() => {
    setDetail(null); setLoadErr(null);
    load(false);
    pollRef.current = setInterval(() => load(true), POLL_MS);
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [expId, load]);

  const exp = detail?.experiment;
  const fluctlights = detail?.fluctlights || [];
  const events = detail?.events || [];
  const trace = detail?.trace || [];
  // 推进后快轮询(实时看到运行日志):点击推进一步后 2 分钟内每 5s 拉一次
  const fastPollUntilRef = useRef(0);
  useEffect(() => {
    const iv = setInterval(() => {
      if (Date.now() < fastPollUntilRef.current) load(true);
    }, 5000);
    return () => clearInterval(iv);
  }, [load]);

  const doTick = async () => {
    if (ticking) return;
    setTicking(true);
    try {
      // 后端已异步化:立即返回 started,推进在后台完成(约1分钟),日志轮询自然刷出。
      const r = await window.api.rath.tick(expId);
      if (!r || r.ok === false) {
        window.__apiToast?.(t('rath_page.toast.tick_declined', { defaultValue: '本次推进未执行' }), { kind: 'warn', detail: r?.error || r?.reason });
      } else {
        window.__apiToast?.(t('rath_page.toast.tick_started', { defaultValue: '已开始推进,运行日志实时可见(约1分钟完成)' }), { kind: 'ok', duration: 3600 });
        fastPollUntilRef.current = Date.now() + 120000;
      }
      await load(true);
    } catch (e) {
      window.__apiToast?.(t('rath_page.toast.tick_failed', { defaultValue: '推进请求失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      setTicking(false);
    }
  };

  const doPauseResume = async () => {
    if (statusBusy || !exp) return;
    setStatusBusy(true);
    const action = exp.status === 'running' ? 'pause' : 'resume';
    try {
      const r = await window.api.rath[action](expId);
      if (!r || r.ok === false) throw new Error(r?.error);
      setDetail((d) => (d ? { ...d, experiment: r.experiment } : d));
      window.__apiToast?.(
        action === 'pause' ? t('rath_page.toast.paused', { defaultValue: '已暂停' }) : t('rath_page.toast.resumed', { defaultValue: '已恢复' }),
        { kind: 'ok', duration: 1600 },
      );
    } catch (e) {
      window.__apiToast?.(t('rath_page.toast.action_failed', { defaultValue: '操作失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      setStatusBusy(false);
    }
  };

  const doArchive = async () => {
    setArchiveConfirm(false);
    try {
      const r = await window.api.rath.archive(expId);
      if (!r || r.ok === false) throw new Error(r?.error);
      window.__apiToast?.(t('rath_page.toast.archived', { defaultValue: '实验已归档' }), { kind: 'ok', duration: 1800 });
      window.dispatchEvent(new CustomEvent('rpg-rath-archived', { detail: { id: expId } }));
    } catch (e) {
      window.__apiToast?.(t('rath_page.toast.action_failed', { defaultValue: '操作失败' }), { kind: 'danger', detail: e?.message });
    }
  };

  const [directiveInput, setDirectiveInput] = useState('');
  const [directiveBusy, setDirectiveBusy] = useState(false);
  const doDirective = async () => {
    const text = directiveInput.trim();
    if (directiveBusy || !text) return;
    setDirectiveBusy(true);
    try {
      const r = await window.api.rath.directive(expId, text);
      if (r && r.ok === false) throw new Error(r.error || t('rath_page.directive.fail', { defaultValue: '插入失败' }));
      setDirectiveInput('');
      window.__apiToast?.(t('rath_page.directive.inserted', { defaultValue: '引导已插入,从当前时间点开始生效' }), { kind: 'ok' });
      await load(true);
    } catch (e) {
      window.__apiToast?.(t('rath_page.directive.fail', { defaultValue: '插入失败' }), { kind: 'danger', detail: String(e?.message || e) });
    } finally { setDirectiveBusy(false); }
  };

  const doAccel = async (accel) => {
    if (accelBusy || !exp || Number(exp.accel) === Number(accel)) return;
    setAccelBusy(true);
    try {
      const r = await window.api.rath.accel(expId, accel);
      if (!r || r.ok === false) throw new Error(r?.error);
      setDetail((d) => (d ? { ...d, experiment: r.experiment } : d));
    } catch (e) {
      window.__apiToast?.(t('rath_page.toast.action_failed', { defaultValue: '操作失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      setAccelBusy(false);
    }
  };

  if (loadErr && !exp) {
    return <CSAlert type="error" header={t('rath_page.load_error_header', { defaultValue: '加载失败' })}>{loadErr}</CSAlert>;
  }
  if (!exp) {
    return <CSBox color="text-body-secondary">{t('common.loading')}</CSBox>;
  }

  const budget = exp.budget || {};
  const running = exp.status === 'running';
  const accelMin = Math.round(Number(exp.accel) || 1);
  const tickIntervalMinutes = Math.round((Number(exp.tick_interval_sec) || 1800) / 60);

  return (
    <CSSpaceBetween size="l">
      {/* 状态行卡片 */}
      <CSContainer>
        <CSColumnLayout columns={2} variant="text-grid">
          <StatCell
            label={t('rath_page.stat.clock', { defaultValue: '世界时间' })}
            value={
              <>
                <CSBox fontSize="heading-xl" fontWeight="bold">{exp.world_clock_label || '—'}</CSBox>
                <CSBox color="text-body-secondary" fontSize="body-s" margin={{ top: 'xxs' }}>
                  {t('rath_page.stat.clock_hint', {
                    defaultValue: `时间流速 ${accelMin}× — 现实 1 分钟 ≈ 世界 ${accelMin} 分钟;推进一步 ≈ 世界 1 小时`,
                    accel: accelMin,
                  })}
                </CSBox>
              </>
            }
          />
          <StatCell
            label={t('rath_page.stat.accel', { defaultValue: '时间流速' })}
            value={
              <CSSegmentedControl
                selectedId={String(exp.accel)}
                options={ACCEL_OPTIONS.map((a) => ({ id: String(a), text: `${a}×` }))}
                onChange={({ detail }) => doAccel(Number(detail.selectedId))}
              />
            }
          />
        </CSColumnLayout>
      </CSContainer>

      {/* 世界运行开关 */}
      <CSContainer>
        <CSSpaceBetween size="xs">
          <CSToggle checked={running} disabled={statusBusy} onChange={doPauseResume}>
            {t('rath_page.run_toggle.label', { defaultValue: '世界运行' })}
          </CSToggle>
          <CSBox color="text-body-secondary" fontSize="body-s">
            {t('rath_page.run_toggle.hint', {
              defaultValue: `开启时每 ${tickIntervalMinutes} 分钟自动推进一次 · 今日 ${exp.ticks_today ?? 0}/${budget.ticks_per_day ?? 48} 次`,
              minutes: tickIntervalMinutes,
              ticksToday: exp.ticks_today ?? 0,
              ticksPerDay: budget.ticks_per_day ?? 48,
            })}
          </CSBox>
        </CSSpaceBetween>
      </CSContainer>

      {/* 操作条 */}
      <CSSpaceBetween direction="horizontal" size="xs">
        <CSButton variant="primary" loading={ticking} onClick={doTick}>
          {t('rath_page.action.tick', { defaultValue: '推进一步' })}
        </CSButton>
        <CSButton onClick={() => setArchiveConfirm(true)}>
          {t('rath_page.action.archive', { defaultValue: '归档' })}
        </CSButton>
      </CSSpaceBetween>

      {/* 运行日志(思考流/执行层) */}
      <CSContainer header={<CSHeader variant="h2" description={t('rath_page.trace.desc', { defaultValue: '引擎每一步的决策与验收结果:材料装配、选角、模型产出与拒收原因。' })}>{t('rath_page.trace.title', { defaultValue: '运行日志' })}</CSHeader>}>
        {trace.length === 0 ? (
          <CSBox color="text-body-secondary" fontSize="body-s">{t('rath_page.trace.empty', { defaultValue: '暂无运行记录——推进一步后这里会实时滚动。' })}</CSBox>
        ) : (
          <div style={{ maxHeight: 260, overflowY: 'auto', fontFamily: 'var(--font-family-monospace, monospace)', fontSize: 12, lineHeight: 1.7 }}>
            {trace.map((r) => (
              <div key={r.id} style={{ borderBottom: '1px solid var(--color-border-divider-default, #e9ebed)', padding: '2px 0' }}>
                <span style={{ opacity: 0.55, marginRight: 8 }}>{r.world_clock_label}</span>
                {r.summary}
              </div>
            ))}
          </div>
        )}
      </CSContainer>

      {/* 角色动态板 */}
      <CSContainer header={<CSHeader variant="h2">{t('rath_page.fluctlights.title', { defaultValue: '角色动态' })}</CSHeader>}>
        {fluctlights.length === 0 ? (
          <CSBox color="text-body-secondary" textAlign="center" padding={{ vertical: 'l' }}>
            {t('rath_page.fluctlights.empty', { defaultValue: '尚无议程 NPC——先在这个存档里玩几回合,让角色拥有自己的目标。' })}
          </CSBox>
        ) : (
          <div className="rath-fluctlight-grid">
            {fluctlights.map((f) => (
              <div key={f.name} className="rath-fluctlight-card">
                <CSBox fontWeight="bold">{f.name}</CSBox>
                {f.goal && <CSBox fontSize="body-s" margin={{ top: 'xxs' }}>{t('rath_page.fluctlights.goal_prefix', { defaultValue: '目标：' })}{f.goal}</CSBox>}
                {f.stance && <CSBox fontSize="body-s" color="text-body-secondary">{t('rath_page.fluctlights.stance_prefix', { defaultValue: '态度：' })}{f.stance}</CSBox>}
                {Array.isArray(f.private_memories) && f.private_memories.length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    {f.private_memories.map((m, i) => (
                      <div key={i} style={{ fontSize: 12, fontStyle: 'italic', color: 'var(--text-quiet, #a8a195)', marginTop: 2 }}>
                        {'“'}{m}{'”'}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CSContainer>

      {/* 日志:顶部插入引导 + events 列表 */}
      <CSContainer header={<CSHeader variant="h2">{t('rath_page.timeline.title', { defaultValue: '日志' })}</CSHeader>}>
        <CSSpaceBetween size="s">
          <CSSpaceBetween size="xs">
            <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
              <div style={{ flex: 1, minWidth: 240 }}>
                <CSInput
                  value={directiveInput}
                  onChange={({ detail }) => setDirectiveInput(detail.value)}
                  placeholder={t('rath_page.directive.placeholder', { defaultValue: '在此刻插入一条引导,影响之后的世界演化…' })}
                  onKeyDown={({ detail }) => { if (detail.key === 'Enter') doDirective(); }}
                />
              </div>
              <CSButton loading={directiveBusy} disabled={!directiveInput.trim()} onClick={doDirective}>
                {t('rath_page.directive.insert_btn', { defaultValue: '插入引导' })}
              </CSButton>
            </CSSpaceBetween>
            <CSBox color="text-body-secondary" fontSize="body-s">
              {t('rath_page.directive.hint', { defaultValue: '最新一条引导生效;历史引导保留在日志中。' })}
            </CSBox>
          </CSSpaceBetween>

          {events.length === 0 ? (
            <CSBox color="text-body-secondary" textAlign="center" padding={{ vertical: 'l' }}>
              {t('rath_page.timeline.empty', { defaultValue: '尚无日志——点「推进一步」触发第一次自动推进。' })}
            </CSBox>
          ) : (
            <CSSpaceBetween size="xs">
              {events.map((ev) => <EventRow key={ev.id} ev={ev} />)}
            </CSSpaceBetween>
          )}
        </CSSpaceBetween>
      </CSContainer>

      <ConfirmModal
        open={archiveConfirm}
        title={t('rath_page.archive_confirm.title', { defaultValue: '归档这场实验？' })}
        body={t('rath_page.archive_confirm.body', { defaultValue: '归档后实验停止运行,已产生的观测记录仍会保留,但不能再恢复运行。' })}
        confirmLabel={t('rath_page.action.archive', { defaultValue: '归档' })}
        danger
        onClose={() => setArchiveConfirm(false)}
        onConfirm={doArchive}
      />
    </CSSpaceBetween>
  );
}

export default function RathPage() {
  const { t } = useTranslation();
  const [experiments, setExperiments] = useState(null);
  const [denied, setDenied] = useState(false);
  const [activeId, setActiveId] = useState(null);

  const reloadList = useCallback(async () => {
    try {
      const r = await window.api.rath.list();
      if (r && r.ok === false) {
        setDenied(true);
        setExperiments([]);
        return;
      }
      const list = Array.isArray(r?.experiments) ? r.experiments : [];
      setExperiments(list);
      setActiveId((cur) => {
        if (cur != null && list.some((e) => String(e.id) === String(cur))) return cur;
        return list.length ? list[0].id : null;
      });
    } catch (e) {
      if (e && e.status === 403) { setDenied(true); setExperiments([]); return; }
      window.__apiToast?.(t('rath_page.toast.load_failed', { defaultValue: '加载实验失败' }), { kind: 'danger', detail: e?.message });
      setExperiments([]);
    }
  }, [t]);

  useEffect(() => { reloadList(); }, [reloadList]);

  useEffect(() => {
    const onArchived = () => { setActiveId(null); reloadList(); };
    window.addEventListener('rpg-rath-archived', onArchived);
    return () => window.removeEventListener('rpg-rath-archived', onArchived);
  }, [reloadList]);

  if (denied) {
    return (
      <div className="pl-sec">
        <CSAlert type="info" header={t('rath_page.denied_header', { defaultValue: 'RATH 未对当前账号开放' })}>
          {t('rath_page.denied_body', { defaultValue: 'RATH 实验未对当前账号开放' })}
        </CSAlert>
      </div>
    );
  }

  return (
    <div className="pl-sec">
      <CSSpaceBetween size="l">
        <CSHeader variant="h1" description={t('rath_page.header.description', { defaultValue: '离线活世界实验:玩家不在时,世界仍按有界规则继续运转。' })}>
          {t('rath_page.header.title', { defaultValue: 'RATH' })}
        </CSHeader>

        {experiments == null ? (
          <CSBox color="text-body-secondary">{t('common.loading')}</CSBox>
        ) : (
          <ExperimentPicker
            experiments={experiments}
            activeId={activeId}
            onSwitch={setActiveId}
            onCreated={(exp) => { reloadList(); if (exp && exp.id != null) setActiveId(exp.id); }}
          />
        )}

        {activeId != null && <ExperimentPanel expId={activeId} />}
      </CSSpaceBetween>

      <style>{`
        .rath-fluctlight-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
          gap: 12px;
        }
        .rath-fluctlight-card {
          border: 1px solid var(--border, #3a352e);
          border-radius: 8px;
          padding: 12px 14px;
        }
        .rath-event-row {
          border-bottom: 1px solid var(--border, #3a352e);
          padding: 6px 0;
        }
        .rath-event-row:last-child { border-bottom: none; }
      `}</style>
    </div>
  );
}
