/* RebuildJobBanner — Editorial × 古籍数字化 重设计版.
   保留完整 SSE 数据流逻辑,只替换 Cloudscape Alert 视觉层.
*/

import React from 'react';
import { useTranslation } from 'react-i18next';
import s from './editorial.module.css';

function moduleLabel(t, module) {
  if (!module) return '';
  return t(`modules.${module}.title`, { defaultValue: module });
}

const SHOWN_KINDS = ['full_pipeline', 'llm_extract', 'knowledge_sync'];
function shouldShowBanner(job) {
  if (!job) return false;
  if (!(job.job_id || job.id)) return false;
  const kind = String(job.kind || '');
  if (kind.startsWith('rebuild_')) return true;
  if (SHOWN_KINDS.includes(kind)) return true;
  if (job.module) return true;
  return false;
}

function getKindFallbackLabel(t) {
  return {
    full_pipeline:  t('rebuild_banner.kind_full_pipeline'),
    llm_extract:    t('rebuild_banner.kind_llm_extract'),
    knowledge_sync: t('rebuild_banner.kind_knowledge_sync'),
  };
}

/* Character progress bar helper (20 blocks for the banner — wider context) */
const B_FULL  = '▰';
const B_EMPTY = '▱';
const BANNER_CELLS = 20;
function bannerBar(pct) {
  const filled = Math.round((pct / 100) * BANNER_CELLS);
  return B_FULL.repeat(filled) + B_EMPTY.repeat(BANNER_CELLS - filled);
}

export function RebuildJobBanner({ scriptId, activeJob, onChange, onDone }) {
  const { t } = useTranslation();
  const [job, setJob] = React.useState(activeJob || null);
  // 「取消中」本地态:后端取消是协作式的(worker 在章/条边界才退出),点下去到真正落
  // 'cancelled' 之间有几秒到十几秒空窗。此前这段时间按钮毫无反应、进度条照走,用户只能
  // 反复点(群反馈:"点取消会一直卡在那里")。这里立刻把按钮切成禁用的「取消中…」。
  const [cancelling, setCancelling] = React.useState(false);
  const esRef = React.useRef(null);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  React.useEffect(() => { setJob(activeJob || null); setCancelling(false); }, [activeJob && activeJob.job_id]);

  React.useEffect(() => {
    const jid = activeJob && (activeJob.job_id || activeJob.id);
    if (!jid) return;
    if (!window.api?.scripts?.streamImport) return;
    try { esRef.current && esRef.current.close && esRef.current.close(); } catch (_) {}
    esRef.current = window.api.scripts.streamImport(jid, {
      on_message: (jb) => {
        if (!jb || typeof jb !== 'object') return;
        const next = { ...jb, job_id: jb.job_id || jb.id || jid };
        setJob(next);
        onChange && onChange(next);
      },
      on_done: (jb) => {
        const final = jb || job;
        setJob(prev => ({ ...(prev || {}), ...(final || {}), status: (final && final.status) || 'done' }));
        onDone && onDone(final);
      },
      on_error: () => {},
    });
    return () => {
      try { esRef.current && esRef.current.close && esRef.current.close(); } catch (_) {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeJob && (activeJob.job_id || activeJob.id)]);

  if (!shouldShowBanner(job)) return null;
  const status = job.status || 'running';

  /* ── Error state ── */
  if (status === 'failed') {
    return (
      <div className={`${s.banner} ${s.bannerError}`}>
        <div className={s.bannerHead}>
          <span className={`${s.bannerTitle} ${s.bannerErrorTitle}`}>
            ⚠ {t('modules.banner.failed', { defaultValue: '重做失败' })}
          </span>
        </div>
        <div className={s.bannerErrorMsg}>
          {job.error || t('modules.banner.failed_detail', { defaultValue: '后端日志可查询任务 ID' })}
        </div>
      </div>
    );
  }

  /* ── Done / cancelled — don't render ── */
  if (status === 'done' || status === 'cancelled') return null;

  /* ── Running ── */
  // 后端 import_jobs.cancel_requested 会随 job 快照一起下发(get_job_status 是 select *),
  // 于是刷新页面 / 换标签页回来时也能看出「这个任务已经在取消途中」,而不是又显示成可取消。
  const isCancelling = !!(job.cancel_requested || job.canceling);
  const overall      = job.overall_progress || 0;
  const overallTotal = job.overall_total || 100;
  const pct          = overallTotal ? Math.round((overall / overallTotal) * 100) : 0;

  const kindStr = String(job.kind || '');
  const KIND_FALLBACK_LABEL = getKindFallbackLabel(t);
  let moduleName;
  if (kindStr.startsWith('rebuild_') || job.module) {
    moduleName = moduleLabel(t, job.module || kindStr.replace(/^rebuild_/, ''));
  } else if (KIND_FALLBACK_LABEL[kindStr]) {
    moduleName = KIND_FALLBACK_LABEL[kindStr];
  } else {
    moduleName = job.title || kindStr || t('rebuild_banner.kind_background_task');
  }

  const before = job.before_count;
  const after  = job.after_count;
  const arrow  = (before != null && after != null)
    ? `${before} → ${after}`
    : (before != null ? `${before} → …` : null);

  const onCancel = async () => {
    if (cancelling) return;
    setCancelling(true);
    try {
      const r = await window.api?.scripts?.jobCancel?.(job.job_id || job.id);
      if (r && r.ok === false) throw new Error(r.error || 'cancel failed');
      window.__apiToast?.(t('modules.toast.cancel_requested', { defaultValue: '已请求取消,正在收尾当前这一章/条' }),
        { kind: 'ok', duration: 2600 });
    } catch (e) {
      // 取消请求本身失败(掉线/任务已不存在)→ 放开按钮让用户重试,别假装在取消。
      setCancelling(false);
      window.__apiToast?.(t('modules.toast.cancel_fail', { defaultValue: '取消请求失败' }),
        { kind: 'danger', detail: (e && e.message) || '' });
    }
  };

  return (
    <div className={s.banner}>
      {/* Head */}
      <div className={s.bannerHead}>
        <span className={s.bannerTitle}>
          {t('modules.banner.running', { defaultValue: '正在重做' })}
          &nbsp;·&nbsp;
          <span className={s.bannerModule}>{moduleName}</span>
          {arrow && <span className={s.bannerArrow}>{arrow}</span>}
        </span>
        <button className={s.bannerCancel} type="button" onClick={onCancel}
          disabled={cancelling || isCancelling}
          aria-busy={cancelling || isCancelling}>
          {(cancelling || isCancelling)
            ? t('modules.banner.cancelling', { defaultValue: '取消中…' })
            : t('common.cancel', { defaultValue: '取消' })}
        </button>
      </div>

      {/* Character progress bar */}
      <div className={s.bannerProgressRow}>
        <span className={s.bannerCharBar}>{bannerBar(pct)}</span>
        <span className={s.bannerPct}>{pct}% · {overall}/{overallTotal}</span>
      </div>

      {/* Stage label */}
      {job.stage_label && (
        <div className={s.bannerStage}>{job.stage_label}</div>
      )}
    </div>
  );
}

export default RebuildJobBanner;
