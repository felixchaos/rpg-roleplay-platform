// 文档反馈「这对我有帮助？」的唯一实现。主站页脚(components/DocFooter.astro)和
// 嵌入视图(pages/embed/[slug].astro)都引用这个模块;Astro 会把它打包成 module
// 脚本并按页去重,每页只注入一次。别再在 .astro 里写 is:inline 的平行版本。
//
// 挂载点:带 data-doc-feedback 的根节点。根节点上的文案属性:
//   data-lang          随反馈上报的语言(zh-CN / en)
//   data-sending       请求在途时的状态行
//   data-thanks        提交成功
//   data-fail          提交失败(网络错误 / 超时 / 服务端 5xx)
//   data-rate-limited  服务端限流(429)
// 子节点约定:
//   [data-vote="up"|"down"]  投票按钮
//   [data-fb]                展开正文反馈表单的按钮(可选)
//   form[data-fb-form]       正文反馈表单(可选),里面的 [data-fb-text] 是输入框,
//                            [data-cancel] 收起表单
//   [data-hide-on-done]      提交成功后隐藏的节点
//   [data-fb-status]         状态行
//
// 一次页面浏览只算一票:第一次提交(投票或正文)就进入 sending,锁住根节点下全部
// 投票按钮、「反馈」按钮和表单提交按钮;成功进入 done,不再接受任何提交;只有失败
// (含 15 秒超时)才回到 idle、解锁,允许重试。
// 设计选择:投票成功后正文表单也随 data-hide-on-done 一起隐藏,不支持「投完票再补
// 一段文字」,与改动前的行为一致。要支持的话,正文需要另一把锁和另一种 done 状态。

const ENDPOINT = '/api/doc-feedback';
const TIMEOUT_MS = 15000;

function newVoteId() {
  try {
    if (typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch (_) { /* 非安全上下文等情况下 randomUUID 可能抛错,走回退 */ }
  return 'v-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
}

// 发请求并带超时。结果统一成 { ok, status },status=0 表示网络错误或超时。
// 不用 AbortSignal.timeout(老浏览器没有,裸调会直接抛错);AbortController 缺失时
// 仍靠 setTimeout 那一侧的 race 兜住超时,只是没法真正中断请求。
function postJson(body) {
  const ac = typeof AbortController === 'function' ? new AbortController() : null;
  let timer = null;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      if (ac) { try { ac.abort(); } catch (_) { /* ignore */ } }
      resolve({ ok: false, status: 0 });
    }, TIMEOUT_MS);
  });
  const request = fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal: ac ? ac.signal : undefined,
  })
    .then((r) => ({ ok: r.ok, status: r.status }))
    .catch(() => ({ ok: false, status: 0 }));
  return Promise.race([request, timeout]).then((res) => {
    clearTimeout(timer);
    return res;
  });
}

function wire(root) {
  if (root.dataset.wired) return;
  root.dataset.wired = '1';

  const lang = root.getAttribute('data-lang') || 'zh-CN';
  const msg = {
    sending: root.getAttribute('data-sending') || '',
    thanks: root.getAttribute('data-thanks') || '',
    fail: root.getAttribute('data-fail') || '',
    rateLimited: root.getAttribute('data-rate-limited') || root.getAttribute('data-fail') || '',
  };
  const votes = Array.from(root.querySelectorAll('[data-vote]'));
  const form = root.querySelector('form[data-fb-form]');
  const text = root.querySelector('[data-fb-text]');
  const status = root.querySelector('[data-fb-status]');
  // 需要随状态一起锁住的全部可提交控件:投票、「反馈」、表单提交按钮。
  const lockables = Array.from(
    root.querySelectorAll('[data-vote], [data-fb], form[data-fb-form] [type="submit"]'),
  );
  const voteId = newVoteId();
  let state = 'idle'; // idle → sending → done;失败时 sending → idle

  function setStatus(s) {
    if (!status) return;
    status.textContent = s;
    status.hidden = !s;
  }
  function lock(on) {
    lockables.forEach((el) => { el.disabled = on; });
  }

  function send(payload, pressed) {
    if (state !== 'idle') return; // 在途或已完成:后续点击一律忽略
    state = 'sending';
    lock(true);
    if (pressed) pressed.setAttribute('aria-pressed', 'true');
    setStatus(msg.sending);
    postJson(Object.assign({ page: location.pathname, lang, vote_id: voteId }, payload)).then((res) => {
      if (res.ok) {
        state = 'done';
        root.querySelectorAll('[data-hide-on-done]').forEach((el) => { el.hidden = true; });
        setStatus(msg.thanks);
        return;
      }
      state = 'idle';
      lock(false);
      if (pressed) pressed.removeAttribute('aria-pressed');
      setStatus(res.status === 429 ? msg.rateLimited : msg.fail);
    });
  }

  votes.forEach((b) => {
    b.addEventListener('click', () => send({ helpful: b.getAttribute('data-vote') === 'up' }, b));
  });

  const fbBtn = root.querySelector('[data-fb]');
  const cancel = root.querySelector('[data-cancel]');
  if (fbBtn && form) {
    fbBtn.addEventListener('click', () => {
      if (state !== 'idle') return;
      form.hidden = false;
      if (text) text.focus();
    });
  }
  if (cancel && form) cancel.addEventListener('click', () => { form.hidden = true; });
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      if (state !== 'idle') return;
      const t = ((text && text.value) || '').trim();
      if (!t) { form.hidden = true; return; }
      send({ text: t }, null);
    });
  }
}

document.querySelectorAll('[data-doc-feedback]').forEach(wire);
