import Foundation

// ── 剧情游戏平台 DTO(对齐 web mobile_v2 的 /api/scripts /api/saves /api/state)──

// 剧本 GET /api/scripts → [..] 或 {items:[..]}
struct ScriptItem: Codable, Identifiable, Hashable {
    let id: Int
    let title: String?
    let chapter_count: Int?
    let word_count: Int?
    let is_public: Bool?
    let is_subscribed: Bool?
    var cover_image_url: String? = nil
    var display: String { (title?.isEmpty == false ? title : nil) ?? "剧本 #\(id)" }
    var chapters: Int { chapter_count ?? 0 }
    var words: Int { word_count ?? 0 }
}
struct ScriptsResponse: Codable {
    let items: [ScriptItem]?
    let scripts: [ScriptItem]?
    var list: [ScriptItem] { items ?? scripts ?? [] }
}

// 存档 GET /api/saves → {items:[..]} 或 {saves:[..]}
struct SaveRaw: Codable, Hashable {
    let player_name: String?
    let turn: Int?
    let world_time: String?
    let snippet: String?
    let last_message: String?
    let script_title: String?
}
struct SaveItem: Codable, Identifiable, Hashable {
    let id: Int
    let title: String?
    let script_id: Int?
    let script_title: String?
    let current: Bool?
    let branch_count: Int?
    let last_played_at: String?
    var updated_at: String? = nil
    let ts: String?
    let save_kind: String?
    let turn: Int?
    var world_time: String? = nil     // 后端 saves 列表实际返回(替代从未下发的 _raw.snippet)
    var player_name: String? = nil
    let raw: SaveRaw?
    enum CodingKeys: String, CodingKey {
        case id, title, script_id, script_title, current, branch_count
        case last_played_at, updated_at, ts, save_kind, turn, world_time, player_name
        case raw = "_raw"
    }
    var display: String { (title?.isEmpty == false ? title : nil) ?? "存档 #\(id)" }
    var scriptTitle: String? { (script_title?.isEmpty == false ? script_title : nil) ?? raw?.script_title }
    var branches: Int { branch_count ?? 0 }
    var isCurrent: Bool { current ?? false }
    var turns: Int { turn ?? raw?.turn ?? 0 }
    // 把原始 ISO 时间戳格式化为本地化相对时间(原来直接显示 "2026-06-22T07:03:10.869792+00:00")。
    var updated: String? {
        guard let s = last_played_at ?? updated_at ?? ts, !s.isEmpty else { return nil }
        guard let d = SaveItem.isoFrac.date(from: s) ?? SaveItem.isoPlain.date(from: s) else { return String(s.prefix(10)) }
        let rel = RelativeDateTimeFormatter()
        rel.unitsStyle = .short
        rel.locale = Locale(identifier: AppLanguage.localeId(for: currentUILang))
        return rel.localizedString(for: d, relativeTo: Date())
    }
    private static let isoFrac: ISO8601DateFormatter = { let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]; return f }()
    private static let isoPlain = ISO8601DateFormatter()
    var snippet: String? {
        // 后端 saves 列表不下发 _raw.snippet(从未存在),改用真实返回的 world_time 作预览。
        let s = (world_time?.isEmpty == false ? world_time : nil) ?? raw?.snippet ?? raw?.last_message
        return (s?.isEmpty == false) ? s : nil
    }
    var isTavern: Bool { (save_kind ?? "") == "tavern" }
}
struct SavesResponse: Codable {
    let items: [SaveItem]?
    let saves: [SaveItem]?
    var list: [SaveItem] { items ?? saves ?? [] }
}

// 个人统计 GET /api/me/stats
struct MeStats: Codable {
    let total_rounds: Int?
    let branches: Int?
    let login_streak: Int?
    let play_minutes_total: Int?
    let assets: Int?
    let imported: Imported?
    struct Imported: Codable { let scripts: Int?; let words: Int? }
    var rounds: Int { total_rounds ?? 0 }
    var streak: Int { login_streak ?? 0 }
    var playHours: Double { Double(play_minutes_total ?? 0) / 60.0 }
    var importedScripts: Int { imported?.scripts ?? 0 }
    var importedWords: Int { imported?.words ?? 0 }
}

// 进入游戏的句柄(驱动 GameConsoleView 的全屏呈现)
struct GameLaunch: Identifiable, Equatable {
    let id: Int            // save_id
    let title: String
    let scriptTitle: String?
}

// 待确认项
struct PendingQuestion: Identifiable { let id: String; let index: Int; let text: String; let options: [String] }
struct PendingWrite: Identifiable { let id: String; let index: Int; let path: String; let to: String; let reason: String; let risk: String }

// ── 游戏运行时快照:/api/state 顶层 payload(深层变形,用 dict 包装最稳)──
struct GameSnapshot {
    let raw: [String: Any]

    init(_ input: [String: Any]) {
        if let s = input["state"] as? [String: Any], (s["player"] != nil || s["history"] != nil) {
            raw = s
        } else if let d = input["data"] as? [String: Any], (d["player"] != nil || d["history"] != nil) {
            raw = d
        } else {
            raw = input
        }
    }
    static let empty = GameSnapshot([:])

    private var player: [String: Any]? { raw["player"] as? [String: Any] }
    private var world: [String: Any]? { raw["world"] as? [String: Any] }
    private var memory: [String: Any]? { raw["memory"] as? [String: Any] }
    private var app: [String: Any]? { raw["app"] as? [String: Any] }

    private func s(_ v: Any?) -> String? {
        guard let str = v as? String, !str.isEmpty else { return nil }
        return str
    }
    private func strings(_ v: Any?) -> [String] {
        if let arr = v as? [String] { return arr.filter { !$0.isEmpty } }
        if let arr = v as? [[String: Any]] {
            return arr.compactMap { d in (d["text"] as? String) ?? (d["event"] as? String) ?? (d["name"] as? String) }
                      .filter { !$0.isEmpty }
        }
        return []
    }

    // 历史
    struct Turn: Identifiable { let id = UUID(); let role: String; let content: String; let index: Int }
    var history: [Turn] {
        guard let arr = raw["history"] as? [[String: Any]] else { return [] }
        // 保留原始 history 下标 index:本地数组会跳过空内容回合(compactMap),与服务端
        // message_index(按全部回合计)错位 → 回滚/分叉传本地下标会命中错回合。带上原始下标。
        return arr.enumerated().compactMap { (i, d) in
            let content = (d["content"] as? String) ?? ""
            let role = (d["role"] as? String) ?? "assistant"
            guard !content.isEmpty else { return nil }
            return Turn(role: role, content: content, index: i)
        }
    }

    // 场景条
    var sceneTime: String? { s(world?["time"]) }
    var sceneWeather: String? { s(world?["weather"]) }
    var sceneLocation: String? { s(player?["current_location"]) ?? s(player?["location"]) }

    // 建议 chips
    var suggestions: [String] { strings(raw["suggestions"]) }

    // 权限
    var permission: String {
        if let p = raw["permissions"] as? [String: Any], let m = p["mode"] as? String { return m }
        return (raw["permission"] as? String) ?? "full_access"
    }

    // 待确认:GM 询问(选项 chips)/ 状态写入(审核模式)
    private var permsDict: [String: Any]? { raw["permissions"] as? [String: Any] }
    private func anyStr(_ v: Any?) -> String {
        if let s = v as? String { return s }
        if let n = v as? NSNumber { return n.stringValue }
        guard let v else { return "" }
        return String(describing: v)
    }
    private func optionStrings(_ v: Any?) -> [String] {
        if let arr = v as? [String] { return arr.filter { !$0.isEmpty } }
        if let arr = v as? [[String: Any]] {
            return arr.compactMap { ($0["text"] as? String) ?? ($0["label"] as? String) ?? ($0["id"] as? String) }.filter { !$0.isEmpty }
        }
        return []
    }
    var pendingQuestions: [PendingQuestion] {
        guard let arr = permsDict?["pending_questions"] as? [[String: Any]] else { return [] }
        return arr.enumerated().compactMap { idx, d in
            let id = (d["id"] as? String) ?? (d["id"] as? NSNumber).map { $0.stringValue } ?? "\(idx)"
            let text = (d["question"] as? String) ?? (d["text"] as? String) ?? ""
            let opts = optionStrings(d["options"] ?? d["choices"])
            if text.isEmpty && opts.isEmpty { return nil }
            return PendingQuestion(id: id, index: idx, text: text, options: opts)
        }
    }
    var pendingWrites: [PendingWrite] {
        guard let arr = permsDict?["pending_writes"] as? [[String: Any]] else { return [] }
        return arr.enumerated().map { idx, d in
            PendingWrite(
                id: (d["id"] as? String) ?? (d["id"] as? NSNumber).map { $0.stringValue } ?? "\(idx)",
                index: idx,
                path: (d["path"] as? String) ?? (d["field"] as? String) ?? "",
                to: anyStr(d["to"] ?? d["value"]),
                reason: (d["reason"] as? String) ?? "",
                risk: (d["risk"] as? String) ?? "low")
        }
    }

    // 玩家
    var playerName: String? { s(player?["display_name"]) ?? s(player?["name"]) }
    var playerRole: String? { s(player?["role"]) }
    var playerBackground: String? { s(player?["background"]) }

    // 记忆
    var objective: String? { s(memory?["current_objective"]) }
    var facts: [String] { Array(strings(memory?["facts"]).prefix(30)) }

    // 在场实体(人物面板)
    struct Entity: Identifiable { let id = UUID(); let name: String; let role: String? }
    var entities: [Entity] {
        guard let arr = raw["active_entities"] as? [[String: Any]] else { return [] }
        return arr.compactMap { d in
            guard let n = (d["name"] as? String) ?? (d["id"] as? String), !n.isEmpty else { return nil }
            let r = (d["role"] as? String) ?? (d["status"] as? String)
            return Entity(name: n, role: (r?.isEmpty == false) ? r : nil)
        }
    }

    // 时间线(已知事件)
    var knownEvents: [String] { Array(strings(world?["known_events"]).prefix(40)) }

    // 模型 / 上下文
    var modelLabel: String? { s(app?["model"]) }
    var contextWindow: Int {
        if let n = app?["context_window"] as? Int { return n }
        if let n = app?["context_window"] as? Double { return Int(n) }
        return 0
    }

    // 世界书(活跃条目,对齐 web WorldbookPanel)
    var worldbookEntries: [(name: String, content: String)] {
        let src = raw["worldbook"] ?? raw["world_book"] ?? (raw["content_pack"] as? [String: Any])?["worldbook"]
        guard let arr = src as? [[String: Any]] else { return [] }
        return arr.prefix(40).compactMap { d in
            let name = (d["name"] as? String) ?? (d["key"] as? String) ?? (d["title"] as? String) ?? ""
            let content = (d["content"] as? String) ?? (d["text"] as? String) ?? (d["value"] as? String) ?? ""
            if name.isEmpty && content.isEmpty { return nil }
            return (name.isEmpty ? "—" : name, content)
        }
    }

    // 规则(对齐 web RulesPanel:规则集 / PC 数值 / 骰子日志)
    var rulesetName: String? {
        if let d = raw["ruleset"] as? [String: Any] { return s(d["name"]) ?? s(d["id"]) }
        return s(raw["ruleset"])
    }
    var pcStats: [(String, String)] {
        guard let d = (raw["pc_stats"] as? [String: Any]) ?? (player?["stats"] as? [String: Any]) else { return [] }
        return d.sorted { $0.key < $1.key }.compactMap { k, v in
            let val = anyStr(v); return val.isEmpty ? nil : (k, val)
        }
    }
    var diceLog: [String] {
        if let arr = raw["dice_log"] as? [String] { return Array(arr.suffix(12).reversed()) }
        if let arr = raw["dice_log"] as? [[String: Any]] {
            return Array(arr.suffix(12).reversed()).map { d in
                let note = (d["notation"] as? String) ?? (d["roll"] as? String) ?? ""
                let res = anyStr(d["result"] ?? d["total"] ?? d["value"])
                let reason = (d["reason"] as? String) ?? (d["label"] as? String) ?? ""
                return [note, res.isEmpty ? "" : "= \(res)", reason].filter { !$0.isEmpty }.joined(separator: " ")
            }.filter { !$0.isEmpty }
        }
        return []
    }

    // 本轮结构化更新(对齐 web 左抽屉 last_structured_updates)
    var structuredUpdates: [String] {
        let v = memory?["last_structured_updates"]
        if let arr = v as? [String] { return arr.filter { !$0.isEmpty } }
        if let arr = v as? [[String: Any]] {
            return arr.compactMap { d in
                let path = (d["path"] as? String) ?? (d["field"] as? String) ?? ""
                let to = anyStr(d["to"] ?? d["value"])
                if path.isEmpty && to.isEmpty { return nil }
                return to.isEmpty ? path : "\(path) → \(to)"
            }
        }
        return []
    }

    // /set 强制设定的世界线变量(worldline.user_variables),对齐 web/移动端 ForcedSetSection。
    private var worldline: [String: Any]? { raw["worldline"] as? [String: Any] }
    var forcedSetVars: [(String, String)] {
        let v = worldline?["user_variables"] ?? worldline?["variables"] ?? worldline?["vars"]
        guard let d = v as? [String: Any] else { return [] }
        return d.keys.sorted().map { k in (k, anyStr(d[k])) }
    }
}
