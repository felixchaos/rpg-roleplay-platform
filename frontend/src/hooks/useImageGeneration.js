/* useImageGeneration — AI 生图「提交 + 每 2s 轮询到 done/failed + 凭据错误分类」内核收口。
 *
 * 此前 components/GenerateImageModal.jsx(GEN 弹窗)与 components/MediaStudio.jsx(GEN tab)
 * 各自手抄一份 images.generate → 每 2s images.get 轮询 → done/failed → isCredentialsError 分类。
 * 本 hook 把这个内核提炼成单一实现 —— 行为零变化:两组件各自的 UI/tab 结构、文案、busy 表示
 * (布尔 vs 'generating')、response 预检差异(MediaStudio 额外查 quota/响应级 creds)、轮询 catch
 * 间隔(2000 vs 2500)全部用参数/回调逐字保留。
 *
 *   useImageGeneration({ onDone, onFail }) → { generate, generating, error, credsMissing, reset, stop }
 *
 *   · generate(body, perCall):
 *       body    = { prompt, kind, api_id, model, attach, size, save_id, ... }(由调用方按各自语义组装)。
 *       perCall = {
 *         inspect(r)        在拿到 generate() 响应后、判定 image_id 前调用;返回 truthy 表示「已处理,
 *                           别再继续」(MediaStudio 用它查响应级 credentials/quota)。可选。
 *         doneFromStatus(r) 从轮询响应推 status(默认 r.status;MediaStudio 传 r.status||(r.ok&&'done'))。
 *         failFallback      failed 时取错文的兜底('生成失败' / 'generation_error')。
 *         noImageIdMsg      响应无 image_id 时的错误文('服务端未返回任务 ID');不传则用 r.error。
 *         pollCatchMs       轮询 catch 后重试间隔(默认 2000;MediaStudio 传 2500)。
 *         emptyResStops     轮询返回空响应时:true→停并报错(GenerateImageModal),false→继续轮询(MediaStudio)。
 *       }
 *   · generating  布尔(调用方各自映射到自己的 busy 表示)。
 *   · error / credsMissing  分类后的错误态(isCredentialsError → credsMissing)。
 *   · onDone(url)  done 时回调(成功 url)。onFail(msg,{creds})  可选,失败时回调(承接 MediaStudio
 *                  把生图失败路由进它与上传/图库共用的 fail())。
 *
 * 轮询固定 2s(setTimeout 链);凭据分类统一走 lib/creds.isCredentialsError(对字符串即
 * /credentials_required|needs_credentials/i,与两宿主原逻辑等价)。
 */
import { useState, useRef, useCallback, useEffect } from 'react';
import { isCredentialsError } from '../lib/creds.js';

export function useImageGeneration({ onDone, onFail } = {}) {
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState(null);
  const [credsMissing, setCredsMissing] = useState(false);
  const pollRef = useRef(null);
  // 陈旧取消守卫:每次 generate() 递增,poll 在每个 await 后校验是否仍为当前轮次。
  const genIdRef = useRef(0);

  const stop = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    stop();
    setGenerating(false);
    setError(null);
    setCredsMissing(false);
  }, [stop]);

  // 卸载时清理轮询。
  useEffect(() => stop, [stop]);

  // 失败分类:isCredentialsError(msg) → 缺凭据;否则原文。同时调用方可经 onFail 接管(MediaStudio)。
  const handleFail = useCallback((msg, perCall) => {
    stop();
    setGenerating(false);
    const m = msg || '';
    const creds = isCredentialsError(m);
    if (perCall && perCall.onFail) { perCall.onFail(m, { creds }); return; }
    if (onFail) { onFail(m, { creds }); return; }
    if (creds) { setCredsMissing(true); setError(perCall && perCall.credsErrorText ? perCall.credsErrorText : null); }
    else { setCredsMissing(false); setError(m || '操作失败'); }
  }, [stop, onFail]);

  const handleDone = useCallback((url) => {
    stop();
    setGenerating(false);
    if (onDone) onDone(url);
  }, [stop, onDone]);

  const poll = useCallback((imageId, perCall, myGen) => {
    const pc = perCall || {};
    const intervalMs = 2000;
    const catchMs = Number.isFinite(pc.pollCatchMs) ? pc.pollCatchMs : 2000;
    stop();
    (async () => {
      try {
        const r = await window.api.images.get(imageId);
        if (genIdRef.current !== myGen) return;  // 陈旧轮次,已被新 generate() 取代
        if (!r) {
          if (pc.emptyResStops) { handleFail(pc.emptyResMsg || '轮询返回空响应', pc); return; }
          pollRef.current = setTimeout(() => poll(imageId, pc, myGen), intervalMs);
          return;
        }
        const status = pc.doneFromStatus ? pc.doneFromStatus(r) : r.status;
        if (status === 'done' && (!pc.requireUrl || r.url)) { handleDone(r.url); return; }
        if (status === 'failed') { handleFail(r.error || pc.failFallback || '生成失败', pc); return; }
        pollRef.current = setTimeout(() => poll(imageId, pc, myGen), intervalMs);
      } catch (e) {
        if (genIdRef.current !== myGen) return;  // 陈旧轮次
        if (pc.catchStops) { handleFail((e && e.message) || pc.pollCatchMsg || '轮询出错', pc); return; }
        pollRef.current = setTimeout(() => poll(imageId, pc, myGen), catchMs);
      }
    })();
  }, [stop, handleDone, handleFail]);

  const generate = useCallback(async (body, perCall) => {
    const pc = perCall || {};
    const myGen = ++genIdRef.current;  // 每次生成递增,供 poll 的陈旧守卫比对
    setError(null);
    setCredsMissing(false);
    setGenerating(true);
    try {
      const r = await window.api.images.generate(body);
      if (genIdRef.current !== myGen) return;  // generate() 已被新调用取代
      if (pc.inspect && pc.inspect(r, { fail: (m) => handleFail(m, pc) })) return;
      if (r && r.image_id) { poll(r.image_id, pc, myGen); return; }
      if (pc.noImageIdMsg) { handleFail(pc.noImageIdMsg, pc); return; }
      handleFail(r && r.error, pc);
    } catch (e) {
      if (pc.rawCatch) {
        // MediaStudio 语义:catch 只把 e.message 交给 fail(),由 fail() 的 creds 正则自行分类(逐字保留)。
        handleFail((e && e.message) || pc.genericErrorMsg || '', pc);
        return;
      }
      // GenerateImageModal 语义:creds 可能藏在 e / e.payload.detail|error 里 —— 预分类后给 detail。
      const errMsg = (e && e.message) || pc.genericErrorMsg || '请求失败';
      const payload = e && e.payload;
      const detail = (payload && (payload.detail || payload.error)) || errMsg;
      const creds = isCredentialsError(e) || isCredentialsError(detail);
      handleFail(creds ? 'credentials_required' : detail, pc);
    }
  }, [poll, handleFail]);

  return { generate, generating, error, credsMissing, reset, stop, setError, setCredsMissing };
}

export default useImageGeneration;
