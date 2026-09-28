// save-create-payload.js — 「新建存档」弹窗(NewGameModal)的结果 → POST /api/saves 请求体。
//
// 平台壳的 __createAndEnterSave 与存档页 SavesList 以前各抄一份字段白名单,存档页那份漏了
// story_intent / player_origin / identity_known:从存档页新建的存档,玩家选的剧情走向、
// 穿越方式、开局是否知道身份被静默丢掉。两处统一走这里,加字段只改一处。
//
// opts.defaultTitle   —— 没填标题时的兜底(各入口文案不同,由调用方给)
// opts.fallbackScriptId —— 没选剧本时的兜底剧本 id(存档页用列表里第一个)
export function buildCreateSavePayload(vals, opts = {}) {
  const v = vals || {};
  return {
    title: v.title || opts.defaultTitle || '',
    script_id: v.script_id || opts.fallbackScriptId || null,
    character_id: v.character_id || null,
    character_kind: v.character_kind || null,
    npc_id: v.npc_id || null,
    new_card: v.new_card || null,
    birthpoint: v.birthpoint || null,
    identity: v.identity || null,
    // false(开局不知道身份)是合法选择,只有 undefined / null 才算没填
    identity_known: v.identity_known ?? null,
    story_intent: v.story_intent || null,
    player_origin: v.player_origin || null,
  };
}
