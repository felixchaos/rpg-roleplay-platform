// CF Pages Function:接收文档站「这对我有帮助」反馈(同源,免 CORS),转发到
// RPG Roleplay 的匿名反馈接口 /api/feedback/anon(免登录 + NSFW 预审 + IP 限流)。
// 前端实现见 src/scripts/doc-feedback.js。
const CENTRAL = 'https://rpg-roleplay.stellatrix.icu/api/feedback/anon';

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

// vote_id:前端每次页面浏览生成一个,用来区分「同一次浏览点了两下」和「两个人各点一次」。
// 可选字段:已打开的旧标签页、旧版嵌入页不带它。只收 [A-Za-z0-9-],最长 64 位。
function cleanVoteId(v) {
  if (typeof v !== 'string') return null;
  const s = v.replace(/[^A-Za-z0-9-]/g, '').slice(0, 64);
  return s || null;
}

export async function onRequestPost(context) {
  let body = {};
  try { body = await context.request.json(); } catch (_) { body = {}; }
  if (!body || typeof body !== 'object') body = {};

  const helpful = body.helpful === true ? true : body.helpful === false ? false : null;
  const text = String(body.text || '').trim().slice(0, 3500);
  const page = String(body.page || '').slice(0, 300);
  const lang = String(body.lang || '').slice(0, 8);
  const voteId = cleanVoteId(body.vote_id);

  // 既不是投票也没有正文:不转发。以前这种请求会在后台生成一条空的「[docs] 文档反馈」。
  if (helpful === null && !text) return json({ ok: false, error: 'empty' }, 400);

  // free_text 必填:把投票/正文组织成一条可读反馈
  let free_text = text;
  if (!free_text) free_text = helpful ? '文档反馈:有帮助' : '文档反馈:没帮助';
  free_text = '[docs] ' + free_text + (page ? '\npage: ' + page : '');

  const env_snapshot = {
    source: 'docs',
    page,
    lang,
    helpful: helpful === true ? 'yes' : helpful === false ? 'no' : null,
  };
  if (voteId) env_snapshot.vote_id = voteId;

  const payload = {
    free_text,
    client_id: 'docs-site',
    app_version: 'docs',
    env_snapshot,
  };

  try {
    const resp = await fetch(CENTRAL, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'user-agent': 'stellatrix-docs' },
      body: JSON.stringify(payload),
    });
    if (resp.ok) return json({ ok: true }, 200);
    // 限流原样透传,前端据此提示「提交过于频繁」;其余失败统一 502。
    if (resp.status === 429) return json({ ok: false, error: 'rate_limited' }, 429);
    return json({ ok: false }, 502);
  } catch (_) {
    return json({ ok: false }, 502);
  }
}
