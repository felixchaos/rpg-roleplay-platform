import Foundation

enum APIError: LocalizedError {
    case badURL
    case http(Int, String)
    case decoding(String)
    case message(String)

    var errorDescription: String? {
        switch self {
        case .badURL: return "服务器地址无效"
        case .http(let code, let msg): return msg.isEmpty ? "请求失败(HTTP \(code))" : msg
        case .decoding(let m): return "数据解析失败:\(m)"
        case .message(let m): return m
        }
    }
}

/// 连接「官方服务器或用户自建服务器」的通用客户端。Cookie 会话(rpg_session)由
/// URLSession 的持久化 cookie 存储自动维护;切服务器/登出时清理。
final class API {
    private let session: URLSession

    init() {
        let cfg = URLSessionConfiguration.default
        cfg.httpCookieStorage = .shared
        cfg.httpCookieAcceptPolicy = .always
        cfg.timeoutIntervalForRequest = 60
        cfg.waitsForConnectivity = true
        self.session = URLSession(configuration: cfg)
    }

    // MARK: 请求构造

    private func makeURL(_ base: String, _ path: String) throws -> URL {
        var b = base.trimmingCharacters(in: .whitespaces)
        if b.hasSuffix("/") { b.removeLast() }
        guard let url = URL(string: b + path) else { throw APIError.badURL }
        return url
    }

    private func request(_ base: String, _ path: String, method: String = "GET",
                         json: [String: Any]? = nil, timeout: TimeInterval = 60) throws -> URLRequest {
        var req = URLRequest(url: try makeURL(base, path))
        req.httpMethod = method
        req.timeoutInterval = timeout
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let json {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: json)
        }
        return req
    }

    private func send<T: Decodable>(_ req: URLRequest, as type: T.Type) async throws -> T {
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 {
            throw APIError.http(code, serverError(from: data) ?? "")
        }
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw APIError.decoding(String(describing: error)) }
    }

    private func doubleVal(_ v: Any?) -> Double? {
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        if let s = v as? String { return Double(s) }
        return nil
    }

    private func serverError(from data: Data) -> String? {
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            return (obj["error"] as? String) ?? (obj["detail"] as? String)
        }
        return nil
    }

    // MARK: 鉴权

    func login(base: String, username: String, password: String) async throws -> APIUser {
        let req = try request(base, "/api/auth/login", method: "POST",
                              json: ["username": username, "password": password])
        let r = try await send(req, as: LoginResponse.self)
        guard r.ok, let user = r.user else { throw APIError.message(r.error ?? "登录失败") }
        return user
    }

    // MARK: 注册 / 邮箱验证 / 验证码登录 / 找回密码 / 注销账号

    /// 注册。返回 (user 非空=已自动登录) 或 (pendingEmail 非空=需邮箱验证码)。
    struct RegisterOutcome { let user: APIUser?; let pendingEmail: String? }
    func register(base: String, username: String, password: String, email: String, displayName: String, birthday: String) async throws -> RegisterOutcome {
        // 后端 /api/auth/register 强制要 birthday(YYYY-MM-DD)且算 ≥18,缺了直接 400「请提供出生日期」
        // → iOS 之前没传 birthday,注册必失败(群反馈)。
        let req = try request(base, "/api/auth/register", method: "POST", json: [
            "username": username, "password": password, "email": email,
            "display_name": displayName.isEmpty ? username : displayName,
            "birthday": birthday,
            "terms_accepted": true, "age_confirmed": true,
        ], timeout: 30)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? "注册失败")
        }
        if let u = obj["user"], let ud = try? JSONSerialization.data(withJSONObject: u),
           let user = try? JSONDecoder().decode(APIUser.self, from: ud) {
            return RegisterOutcome(user: user, pendingEmail: nil)
        }
        return RegisterOutcome(user: nil, pendingEmail: email)   // 需验证码
    }
    /// 提交邮箱验证码完成注册(成功置 session cookie)。
    func verifyEmail(base: String, email: String, code: String) async throws -> APIUser {
        let r = try await send(try request(base, "/api/auth/verify-email", method: "POST", json: ["email": email, "code": code]), as: LoginResponse.self)
        guard r.ok, let user = r.user else { throw APIError.message(r.error ?? "验证失败") }
        return user
    }
    func resendCode(base: String, email: String) async throws {
        try await postExpectOK(base, "/api/auth/resend-code", ["email": email], fail: "重发失败")
    }
    /// 请求免密登录验证码(发到邮箱)。
    func requestLoginCode(base: String, email: String) async throws {
        try await postExpectOK(base, "/api/auth/login-code/request", ["email": email], fail: "发送验证码失败")
    }
    private func postExpectOK(_ base: String, _ path: String, _ json: [String: Any], fail: String) async throws {
        let (data, resp) = try await session.data(for: try request(base, path, method: "POST", json: json))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? fail)
        }
    }
    /// 经鉴权会话 GET 一个文件,落临时目录,返回本地 URL(供 UIActivityViewController 分享)。
    private func downloadToTemp(_ base: String, _ path: String, _ filename: String) async throws -> URL {
        let (data, resp) = try await session.data(for: try request(base, path, timeout: 120))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "下载失败") }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(filename)
        try data.write(to: url, options: .atomic)
        return url
    }
    /// 经鉴权会话 GET 一个 JSON 数组(dict 元素),返回 [[String:Any]]。
    func getDictArray(_ base: String, _ path: String) async throws -> [[String: Any]] {
        let (data, resp) = try await session.data(for: try request(base, path, timeout: 60))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "加载失败") }
        if let arr = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]] { return arr }
        if let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            for k in ["items", "chapters", "commits", "anchors", "phases", "nodes"] {
                if let arr = obj[k] as? [[String: Any]] { return arr }
            }
        }
        return []
    }
    /// 校验免密登录验证码(成功置 session cookie)。
    func verifyLoginCode(base: String, email: String, code: String) async throws -> APIUser {
        let r = try await send(try request(base, "/api/auth/login-code/verify", method: "POST", json: ["email": email, "code": code]), as: LoginResponse.self)
        guard r.ok, let user = r.user else { throw APIError.message(r.error ?? "验证失败") }
        return user
    }
    /// 触发密码重置邮件(总是返回 ok,防枚举;用户去邮箱点链接重置)。
    func forgotPassword(base: String, email: String) async throws {
        _ = try? await session.data(for: try request(base, "/api/auth/forgot-password", method: "POST", json: ["email": email]))
    }
    /// 申请注销账号(软删除 + 宽限期,可撤销;满足 App Store 应用内删除要求)。返回提示文案。
    func requestAccountDelete(base: String, reason: String = "user-requested-ios") async throws -> String {
        let (data, resp) = try await session.data(for: try request(base, "/api/account/request-delete", method: "POST", json: ["reason": reason]))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? "注销申请失败")
        }
        return (obj["message"] as? String) ?? "账号注销申请已提交。"
    }
    func cancelAccountDelete(base: String) async throws {
        _ = try? await session.data(for: try request(base, "/api/account/cancel-delete", method: "POST", json: [:]))
    }

    func me(base: String) async throws -> APIUser? {
        let req = try request(base, "/api/auth/me")
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { return nil }
        let r = try? JSONDecoder().decode(MeResponse.self, from: data)
        return r?.user
    }

    func logout(base: String) async {
        // 后端登出端点存在与否都无所谓:清本地 cookie 即视为登出。
        _ = try? await session.data(for: try request(base, "/api/auth/logout", method: "POST", json: [:]))
        clearCookies(for: base)
    }

    func clearCookies(for base: String) {
        guard let host = URL(string: base)?.host?.lowercased() else { return }
        let store = HTTPCookieStorage.shared
        // [round-3-P2] 原 substring 匹配会误删/漏删:host="api.com" 会把 "evil-api.com" 的 cookie
        //   也判为命中(host.contains 反向亦然)。改用规范的域后缀匹配(等于域,或 host 是 .域 的子域)。
        for c in store.cookies ?? [] {
            let d = c.domain.lowercased()
            let dom = d.hasPrefix(".") ? String(d.dropFirst()) : d
            if host == dom || host.hasSuffix("." + dom) {
                store.deleteCookie(c)
            }
        }
    }

    // MARK: 酒馆对话

    func tavernList(base: String, archived: Bool = false) async throws -> [TavernChat] {
        let req = try request(base, archived ? "/api/tavern/chats?archived=1" : "/api/tavern/chats")
        return (try await send(req, as: TavernListResponse.self)).chats ?? []
    }

    func tavernCreateBlank(base: String) async throws -> Int {
        let req = try request(base, "/api/tavern/chats", method: "POST", json: [:])
        let r = try await send(req, as: TavernCreateResponse.self)
        guard let id = r.newId else { throw APIError.message("未返回对话 id") }
        return id
    }

    func tavernActivate(base: String, id: Int) async throws {
        let req = try request(base, "/api/tavern/chats/\(id)/activate", method: "POST", json: [:])
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "激活对话失败") }
    }

    func state(base: String) async throws -> GameState {
        let req = try request(base, "/api/state")
        return try await send(req, as: GameState.self)
    }

    // MARK: 剧情游戏平台(剧本 / 存档 / 状态 / 进入游戏)

    /// GET /api/scripts —— 后端可能返回裸数组或 {items:[..]},两种都吃。
    func scriptsList(base: String) async throws -> [ScriptItem] {
        let (data, resp) = try await session.data(for: try request(base, "/api/scripts"))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "") }
        if let arr = try? JSONDecoder().decode([ScriptItem].self, from: data) { return arr }
        return (try JSONDecoder().decode(ScriptsResponse.self, from: data)).list
    }

    /// GET /api/saves → {items:[..]} | {saves:[..]} | [..]
    func savesList(base: String) async throws -> [SaveItem] {
        let (data, resp) = try await session.data(for: try request(base, "/api/saves"))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "") }
        if let arr = try? JSONDecoder().decode([SaveItem].self, from: data) { return arr }
        return (try JSONDecoder().decode(SavesResponse.self, from: data)).list
    }

    /// 激活存档(进入游戏前置)POST /api/saves/{id}/activate
    func activateSave(base: String, id: Int) async throws {
        let req = try request(base, "/api/saves/\(id)/activate", method: "POST", json: [:])
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "激活存档失败") }
    }

    /// 游戏运行时快照(深层变形,取原始 dict)GET /api/state
    func rawState(base: String) async throws -> GameSnapshot {
        let (data, resp) = try await session.data(for: try request(base, "/api/state"))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "") }
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        return GameSnapshot(obj)
    }

    /// 当前激活存档 id(/api/state.save_id);存档列表无 current 标记,靠它判定「当前」。失败返 nil。
    func activeSaveId(base: String) async -> Int? {
        guard let (data, resp) = try? await session.data(for: try request(base, "/api/state")),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        return obj["save_id"] as? Int
    }

    /// 取剧本第一个出生点锚点 id GET /api/scripts/{id}/birthpoints(失败返 nil)
    func firstBirthpoint(base: String, scriptId: Int) async -> String? {
        guard let (data, resp) = try? await session.data(for: try request(base, "/api/scripts/\(scriptId)/birthpoints")),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let phases = obj["phases"] as? [[String: Any]] else { return nil }
        for ph in phases {
            if let anchors = ph["anchors"] as? [[String: Any]], let first = anchors.first {
                // 后端 anchor_id 是 JSON 整数;旧代码按 String 取永远 nil → 出生点彻底失效。
                if let i = first["anchor_id"] as? Int { return String(i) }
                if let s = first["anchor_id"] as? String { return s }
            }
        }
        return nil
    }

    /// 出生点全列表(按 phase 分组的锚点扁平化)。
    func birthpoints(base: String, scriptId: Int) async -> [Birthpoint] {
        guard let (data, resp) = try? await session.data(for: try request(base, "/api/scripts/\(scriptId)/birthpoints")),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let phases = obj["phases"] as? [[String: Any]] else { return [] }
        var out: [Birthpoint] = []
        for ph in phases {
            let plabel = (ph["phase_label"] as? String) ?? ""
            for a in (ph["anchors"] as? [[String: Any]] ?? []) {
                // anchor_id 是整数(兼容历史字符串)。
                guard let aid = (a["anchor_id"] as? Int).map(String.init) ?? (a["anchor_id"] as? String) else { continue }
                let cm = (a["chapter_min"] as? Int) ?? (a["chapter_min"] as? String).flatMap { Int($0) }
                let cx = (a["chapter_max"] as? Int) ?? (a["chapter_max"] as? String).flatMap { Int($0) }
                out.append(Birthpoint(
                    anchorId: aid,
                    label: (a["story_time_label"] as? String) ?? plabel,
                    phase: plabel,
                    chapterMin: cm,
                    chapterMax: cx,
                    summary: a["sample_summary"] as? String))
            }
        }
        return out
    }

    /// 新建存档 POST /api/saves → {ok, save:{id}}。
    /// (旧实现打 /api/new 是 legacy:只重置内存运行时、不落库、丢弃出生点/出身/引导,新游戏实际没建成。)
    func newGame(base: String, body: [String: Any]) async throws -> Int {
        let req = try request(base, "/api/saves", method: "POST", json: body, timeout: 120)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 {
            // [round-3-P2] 复核闸:后端返 400 + needs_review,给出明确指引而非裸 HTTP 错误。
            if (obj["needs_review"] as? Bool) == true {
                throw APIError.message((obj["error"] as? String) ?? "该剧本尚未通过提取复核,请先到剧本复核页核对后再开局。")
            }
            throw APIError.http(code, serverError(from: data) ?? "新建失败")
        }
        if let save = obj["save"] as? [String: Any], let id = save["id"] as? Int { return id }
        if let id = obj["save_id"] as? Int { return id }
        throw APIError.message("未返回存档 id")
    }

    /// 新建/游戏中写存档设置 PATCH /api/saves/{id}/settings({updates, is_create})。
    /// 建档时 is_create=true 才能设锁死项(引导强度/防剧透等)。
    func saveSettings(base: String, saveId: Int, updates: [String: Any], isCreate: Bool) async {
        let body: [String: Any] = ["updates": updates, "is_create": isCreate]
        _ = try? await session.data(for: try request(base, "/api/saves/\(saveId)/settings", method: "PATCH", json: body))
    }

    /// 删除 /set 强制设定的世界线变量 POST /api/worldline/variable/remove({key})——对齐 web ForcedSetSection 的删改能力。
    func worldlineVariableRemove(base: String, key: String) async {
        _ = try? await session.data(for: try request(base, "/api/worldline/variable/remove", method: "POST", json: ["key": key]))
    }

    // 存档导出:即时大小估计 + 自包含包(zip)落临时文件供分享;导入 = 多部分上传 .json/.zip。
    func saveExportEstimate(base: String, saveId: Int) async -> [String: Any] {
        guard let (data, resp) = try? await session.data(for: try request(base, "/api/saves/\(saveId)/export/estimate")),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [:] }
        return obj
    }
    func saveExportBundle(base: String, saveId: Int, tier: String) async throws -> URL {
        let (data, resp) = try await session.data(for: try request(base, "/api/saves/\(saveId)/export/bundle?tier=\(tier)", timeout: 180))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "导出失败") }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("save-\(saveId).zip")
        try data.write(to: url, options: .atomic)
        return url
    }
    func importSave(base: String, fileURL: URL) async throws -> [String: Any] {
        let data = try Data(contentsOf: fileURL)
        let isZip = fileURL.pathExtension.lowercased() == "zip"
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: try makeURL(base, "/api/saves/import"))
        req.httpMethod = "POST"; req.timeoutInterval = 180
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(fileURL.lastPathComponent)\"\r\n")
        append("Content-Type: \(isZip ? "application/zip" : "application/json")\r\n\r\n")
        body.append(data); append("\r\n--\(boundary)--\r\n")
        req.httpBody = body
        let (rdata, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: rdata)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? serverError(from: rdata) ?? "导入失败")
        }
        return obj
    }

    /// 个人统计 GET /api/me/stats(缺失/失败返 nil,不阻断)
    func meStats(base: String) async throws -> MeStats? {
        let (data, resp) = try await session.data(for: try request(base, "/api/me/stats"))
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return try? JSONDecoder().decode(MeStats.self, from: data)
    }

    /// 中止当前生成 POST /api/stop
    func stopGeneration(base: String) async {
        _ = try? await session.data(for: try request(base, "/api/stop", method: "POST", json: [:]))
    }

    func setImmersive(base: String, id: Int, enabled: Bool) async throws {
        let req = try request(base, "/api/tavern/chats/\(id)/immersive", method: "POST",
                             json: ["enabled": enabled])
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "设置失败") }
    }

    struct AIReplyResponse: Decodable { let ok: Bool?; let reply: String? }
    func aiReply(base: String, id: Int) async throws -> String {
        let req = try request(base, "/api/tavern/chats/\(id)/ai-reply", method: "POST", json: [:], timeout: 120)
        return (try await send(req, as: AIReplyResponse.self)).reply ?? ""
    }

    func setSystemPrompt(base: String, id: Int, prompt: String) async throws {
        let (data, resp) = try await session.data(for: try request(base, "/api/tavern/chats/\(id)/system-prompt", method: "POST", json: ["system_prompt": prompt]))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "保存失败") }
    }

    /// 绑定/更换本对话的 AI 角色卡 / 我的角色卡(role=character|persona,cardId=nil 解绑)。
    func bindCard(base: String, id: Int, role: String, cardId: Int?) async throws {
        var body: [String: Any] = ["role": role]
        body["card_id"] = cardId ?? NSNull()
        let (data, resp) = try await session.data(for: try request(base, "/api/tavern/chats/\(id)/bind-card", method: "POST", json: body))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "更换角色卡失败") }
    }

    /// AI 自动命名本对话,返回新标题(失败抛错)。
    struct AutotitleResponse: Decodable { let ok: Bool?; let title: String? }
    func autotitle(base: String, id: Int) async throws -> String {
        let req = try request(base, "/api/tavern/chats/\(id)/autotitle", method: "POST", json: [:], timeout: 90)
        return (try await send(req, as: AutotitleResponse.self)).title ?? ""
    }

    /// 导出本对话为 .jsonl:经鉴权会话取字节落临时文件,返回可分享的本地 URL。
    func exportJsonl(base: String, id: Int) async throws -> URL {
        let (data, resp) = try await session.data(for: try request(base, "/api/tavern/chats/\(id)/export-jsonl"))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "导出失败") }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("tavern-chat-\(id).jsonl")
        try data.write(to: url, options: .atomic)
        return url
    }

    func setPermission(base: String, mode: String) async throws {
        _ = try await session.data(for: try request(base, "/api/permissions", method: "POST", json: ["mode": mode]))
    }

    // MARK: 模型

    func models(base: String) async throws -> ModelsResponse {
        try await send(try request(base, "/api/models"), as: ModelsResponse.self)
    }

    func selectModel(base: String, apiId: String, modelId: String, saveId: Int?) async throws {
        var body: [String: Any] = ["api_id": apiId, "model_id": modelId]
        if let saveId { body["save_id"] = saveId }
        _ = try await session.data(for: try request(base, "/api/models/select", method: "POST", json: body))
    }

    /// 每用户「精选」:隐藏/显示自己同步来的单个模型(picker 安全,不动全局目录,任何用户可调)。
    /// POST /api/me/models/visibility {api_id, model, visible}
    func setModelVisibility(base: String, apiId: String, model: String, visible: Bool) async throws {
        let body: [String: Any] = ["api_id": apiId, "model": model, "visible": visible]
        let (data, resp) = try await session.data(for: try request(base, "/api/me/models/visibility", method: "POST", json: body))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "更新可见性失败") }
    }

    // MARK: BYOK 凭据(自配 API)

    func credentialsList(base: String) async throws -> [CredItem] {
        let (data, resp) = try await session.data(for: try request(base, "/api/me/credentials"))
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return [] }
        return (try? JSONDecoder().decode(CredListResponse.self, from: data))?.items ?? []
    }

    /// 设置/更新某 provider 的 API key(空 key + 空 base_url = 仅更新开关/代理)。
    func setCredential(base: String, apiId: String, key: String, baseURL: String, proxy: String, enabled: Bool = true) async throws {
        var body: [String: Any] = ["api_id": apiId, "api_key": key, "enabled": enabled]
        if !baseURL.isEmpty { body["base_url_override"] = baseURL }
        if !proxy.isEmpty { body["proxy"] = proxy }
        let req = try request(base, "/api/me/credentials", method: "POST", json: body)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "保存失败") }
        // 后端可能 200 但 {ok:false,error}
        if let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? "保存失败")
        }
    }

    func deleteCredential(base: String, apiId: String) async throws {
        let req = try request(base, "/api/me/credentials/delete", method: "POST", json: ["api_id": apiId])
        // [round-4-P1] 原来丢弃响应:删除失败(401/5xx)被静默吞,UI 误以为已删。检查状态码。
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "删除失败") }
    }

    /// 实测一次凭据可用性 GET /api/me/credentials/test?api_id=&force=1
    func testCredential(base: String, apiId: String) async throws -> CredTestResult {
        let enc = apiId.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? apiId
        let req = try request(base, "/api/me/credentials/test?api_id=\(enc)&force=1", timeout: 90)
        let (data, resp) = try await session.data(for: req)
        // [round-4-P2] 区分鉴权失败与连接失败:401 = 未登录,给明确指引而非笼统「连接失败」。
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 { return CredTestResult(ok: false, message: "请先登录后再测试") }
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let ok = obj["ok"] as? Bool ?? false
        if ok {
            if let ms = obj["latency_ms"] as? Int { return CredTestResult(ok: true, message: "连接正常 · \(ms)ms") }
            if let ms = obj["latency_ms"] as? Double { return CredTestResult(ok: true, message: "连接正常 · \(Int(ms))ms") }
            return CredTestResult(ok: true, message: "连接正常")
        }
        return CredTestResult(ok: false, message: (obj["error"] as? String) ?? "连接失败")
    }

    // MARK: 角色卡 / 资料 / 偏好

    struct CardsResponse: Decodable { let items: [CharacterCardItem]?; let cards: [CharacterCardItem]? ; var list: [CharacterCardItem] { items ?? cards ?? [] } }
    func characterCards(base: String) async throws -> [CharacterCardItem] {
        try await send(try request(base, "/api/me/character-cards"), as: CardsResponse.self).list
    }
    /// 创建/更新角色卡(含 id 则更新)。返回 card id。
    @discardableResult
    func cardUpsert(base: String, body: [String: Any]) async throws -> Int {
        let req = try request(base, "/api/me/character-cards", method: "POST", json: body, timeout: 30)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? "保存失败")
        }
        if let c = obj["card"] as? [String: Any], let id = c["id"] as? Int { return id }
        return (body["id"] as? Int) ?? 0
    }
    func cardDelete(base: String, id: Int) async throws {
        _ = try await session.data(for: try request(base, "/api/me/character-cards/\(id)/delete", method: "POST", json: [:]))
    }
    func cardVisibility(base: String, id: Int, isPublic: Bool) async throws {
        _ = try await session.data(for: try request(base, "/api/me/character-cards/\(id)/visibility", method: "POST", json: ["public": isPublic]))
    }
    func cardImportJson(base: String, json: String, aiSplit: Bool) async throws {
        let req = try request(base, "/api/me/character-cards/import-json", method: "POST", json: ["json_string": json, "ai_split": aiSplit], timeout: 60)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "导入失败") }
    }
    /// 导入酒馆角色卡(PNG/WEBP/JSON multipart)。
    func cardImportTavern(base: String, fileData: Data, filename: String, mime: String) async throws {
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: try makeURL(base, "/api/me/character-cards/import-tavern"))
        req.httpMethod = "POST"; req.timeoutInterval = 90
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(mime)\r\n\r\n")
        body.append(fileData); append("\r\n--\(boundary)--\r\n")
        req.httpBody = body
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "导入失败") }
    }

    // MARK: 人格 skill 导入(skill.md 上传 / GitHub 拉取 → 蒸馏角色卡 + 人设图)
    func personaSkills(base: String) async throws -> [PersonaSkillItem] {
        try await send(try request(base, "/api/me/persona-skills"), as: PersonaSkillsResponse.self).list
    }
    struct PersonaSkillImportResult { let ok: Bool; let cardName: String; let imageStatus: String }
    func importPersonaSkill(base: String, source: String, repoUrl: String = "", filename: String = "", content: String = "") async throws -> PersonaSkillImportResult {
        var body: [String: Any] = ["source": source, "generate_image": false]
        if source == "github" { body["repo_url"] = repoUrl }
        else { body["files"] = [["name": filename, "content": content]] }
        let (data, resp) = try await session.data(for: try request(base, "/api/me/persona-skills/import", method: "POST", json: body, timeout: 150))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? serverError(from: data) ?? "导入失败")
        }
        let card = obj["card"] as? [String: Any]
        return PersonaSkillImportResult(ok: true, cardName: (card?["name"] as? String) ?? "角色卡", imageStatus: (obj["image_status"] as? String) ?? "")
    }
    func deletePersonaSkill(base: String, id: Int) async throws {
        let (data, resp) = try await session.data(for: try request(base, "/api/me/persona-skills/\(id)/delete", method: "POST", json: [:]))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "删除失败") }
    }

    /// 在线/社区角色卡库 GET /api/cards/public(?q=)。
    func cardsPublicList(base: String, q: String = "") async throws -> [CharacterCardItem] {
        let enc = q.isEmpty ? "" : "?q=\(q.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? q)"
        return try await send(try request(base, "/api/cards/public\(enc)"), as: CardsResponse.self).list
    }
    /// 克隆一张公开卡到自己的卡库 POST /api/cards/public/{id}/clone。
    func cardCloneFromPublic(base: String, id: Int) async throws {
        try await postExpectOK(base, "/api/cards/public/\(id)/clone", [:], fail: "克隆失败")
    }
    /// 导出酒馆卡(JSON)/ PNG:经鉴权会话取字节落临时文件供分享。
    func cardExportTavern(base: String, id: Int) async throws -> URL {
        try await downloadToTemp(base, "/api/me/character-cards/\(id)/export-tavern", "card-\(id).json")
    }
    func cardExportPng(base: String, id: Int) async throws -> URL {
        try await downloadToTemp(base, "/api/me/character-cards/\(id)/export-png", "card-\(id).png")
    }

    // MARK: 剧本编辑器(script editor)—— 剧本级实体(角色卡 / 世界书 / 正史)
    // 对齐 web /md-editor:owner 可编辑角色卡;世界书/正史 v1 只读浏览(subscriber 亦可读)。

    /// 剧本的角色卡列表(剧本级,区别于 /api/me 的用户卡)。
    func scriptCards(base: String, scriptId: Int) async throws -> [CharacterCardItem] {
        try await send(try request(base, "/api/scripts/\(scriptId)/character-cards?limit=500"), as: CardsResponse.self).list
    }
    /// 创建/更新剧本角色卡(body 含 id 则更新)。返回保存后的卡。
    @discardableResult
    func scriptCardUpsert(base: String, scriptId: Int, body: [String: Any]) async throws -> CharacterCardItem {
        let req = try request(base, "/api/scripts/\(scriptId)/character-cards", method: "POST", json: body, timeout: 30)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? "保存失败")
        }
        guard let cardObj = obj["card"], let cd = try? JSONSerialization.data(withJSONObject: cardObj),
              let card = try? JSONDecoder().decode(CharacterCardItem.self, from: cd) else {
            throw APIError.message("保存成功但解析返回失败")
        }
        return card
    }
    func scriptCardDelete(base: String, scriptId: Int, cardId: Int) async throws {
        let req = try request(base, "/api/scripts/\(scriptId)/character-cards/\(cardId)/delete", method: "POST", json: [:])
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "删除失败") }
    }
    func scriptCardSetEnabled(base: String, scriptId: Int, cardId: Int, enabled: Bool) async throws {
        let req = try request(base, "/api/scripts/\(scriptId)/character-cards/\(cardId)/enabled", method: "POST", json: ["enabled": enabled])
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code < 200 || code >= 300 { throw APIError.http(code, serverError(from: data) ?? "切换失败") }
    }
    /// 世界书条目(只读浏览)。
    func scriptWorldbook(base: String, scriptId: Int) async throws -> [WorldbookEntryItem] {
        try await send(try request(base, "/api/scripts/\(scriptId)/worldbook?fetch_all=true"), as: WorldbookResponse.self).list
    }
    /// 正史实体(只读浏览)。
    func scriptCanon(base: String, scriptId: Int) async throws -> [CanonEntityItem] {
        try await send(try request(base, "/api/scripts/\(scriptId)/canon-entities?limit=500"), as: CanonResponse.self).list
    }
    struct WorldbookResponse: Decodable { let items: [WorldbookEntryItem]?; let entries: [WorldbookEntryItem]?; var list: [WorldbookEntryItem] { items ?? entries ?? [] } }
    struct CanonResponse: Decodable { let items: [CanonEntityItem]?; var list: [CanonEntityItem] { items ?? [] } }

    // MARK: 剧本库 / 章节 / 时间线 / 版本 / 嵌入(对齐 web MobileScripts)
    /// 公开剧本库 GET /api/scripts/public(?q=)。
    func scriptsPublicList(base: String, q: String = "") async throws -> [ScriptItem] {
        let enc = q.isEmpty ? "" : "?q=\(q.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? q)"
        let (data, resp) = try await session.data(for: try request(base, "/api/scripts/public\(enc)"))
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return [] }
        if let r = try? JSONDecoder().decode(ScriptsResponse.self, from: data) { return r.list }
        return (try? JSONDecoder().decode([ScriptItem].self, from: data)) ?? []
    }
    func scriptCloneFromPublic(base: String, id: Int) async throws {
        try await postExpectOK(base, "/api/scripts/public/\(id)/clone", [:], fail: "克隆失败")
    }
    func scriptUnsubscribe(base: String, id: Int) async throws {
        try await postExpectOK(base, "/api/scripts/\(id)/unsubscribe", [:], fail: "退订失败")
    }
    func scriptFork(base: String, id: Int) async throws {
        try await postExpectOK(base, "/api/scripts/\(id)/fork", [:], fail: "复刻失败")
    }
    func scriptSetVisibility(base: String, id: Int, isPublic: Bool) async throws {
        try await postExpectOK(base, "/api/scripts/\(id)/visibility", ["is_public": isPublic], fail: "设置失败")
    }
    func scriptRebuildEmbeddings(base: String, id: Int) async throws {
        try await postExpectOK(base, "/api/scripts/\(id)/rebuild/embeddings", [:], fail: "重建索引失败")
    }
    func scriptChapters(base: String, id: Int) async throws -> [[String: Any]] {
        try await getDictArray(base, "/api/scripts/\(id)/chapters")
    }
    func scriptChapterDetail(base: String, id: Int, index: Int) async -> [String: Any] {
        guard let (data, resp) = try? await session.data(for: try request(base, "/api/scripts/\(id)/chapters/\(index)")),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [:] }
        return obj
    }
    func scriptTimeline(base: String, id: Int) async -> [String: Any] {
        guard let (data, resp) = try? await session.data(for: try request(base, "/api/scripts/\(id)/timeline")),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [:] }
        return obj
    }
    func scriptCommits(base: String, id: Int) async throws -> [[String: Any]] {
        try await getDictArray(base, "/api/scripts/\(id)/commits")
    }
    func scriptCheckout(base: String, id: Int, commitId: String) async throws {
        try await postExpectOK(base, "/api/scripts/\(id)/checkout/\(commitId)", [:], fail: "回滚失败")
    }

    /// 返回 (displayName, username, prefs) —— prefs 是任意键值,用 JSONSerialization 解析。
    func profile(base: String) async throws -> (displayName: String?, username: String?, prefs: [String: Any]) {
        let (data, resp) = try await session.data(for: try request(base, "/api/me/profile"))
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return (nil, nil, [:]) }
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let user = obj["user"] as? [String: Any]
        let prefs = (obj["preferences"] as? [String: Any]) ?? [:]
        return ((user?["display_name"] as? String) ?? (obj["display_name"] as? String),
                (user?["username"] as? String) ?? (obj["username"] as? String), prefs)
    }

    func saveDisplayName(base: String, name: String) async throws {
        _ = try await session.data(for: try request(base, "/api/profile", method: "POST", json: ["display_name": name]))
    }

    func setPreferences(base: String, _ kv: [String: Any]) async throws {
        _ = try await session.data(for: try request(base, "/api/me/preference", method: "POST", json: kv))
    }

    // MARK: 对话管理

    func renameChat(base: String, id: Int, title: String) async throws {
        _ = try await session.data(for: try request(base, "/api/tavern/chats/\(id)/rename", method: "POST", json: ["title": title]))
    }

    func archiveChat(base: String, id: Int, archived: Bool) async throws {
        _ = try await session.data(for: try request(base, "/api/tavern/chats/\(id)/archive", method: "PATCH", json: ["archived": archived]))
    }

    func deleteChat(base: String, id: Int) async throws {
        _ = try await session.data(for: try request(base, "/api/tavern/chats/\(id)", method: "DELETE"))
    }

    // MARK: 流式发送(SSE)

    /// POST /api/chat,逐 token 流式返回。后端 SSE: `event: <type>\ndata: <json>\n\n`。
    /// saveId 不为空时随 body 带上(剧情游戏);为空走当前激活会话(酒馆)。
    func streamChat(base: String, message: String, saveId: Int? = nil, attachments: [[String: Any]] = []) -> AsyncThrowingStream<ChatEvent, Error> {
        var body: [String: Any] = ["message": message]
        if let saveId { body["save_id"] = saveId }
        if !attachments.isEmpty { body["attachments"] = attachments }
        return streamSSE(base: base, path: "/api/chat", body: body)
    }

    /// 保存资料(昵称/简介)。
    func saveProfile(base: String, displayName: String, bio: String, username: String? = nil) async throws {
        // 用户名走 PATCH /api/me/profile(同时可改 display_name);bio + display_name 走 /api/profile。
        if let u = username, !u.isEmpty {
            _ = try? await session.data(for: try request(base, "/api/me/profile", method: "PATCH", json: ["username": u, "display_name": displayName]))
        }
        _ = try await session.data(for: try request(base, "/api/profile", method: "POST", json: ["display_name": displayName, "bio": bio]))
    }
    /// 公开个人主页开关(visibility 自由 jsonb,用 public_profile 键)。
    func setProfileVisibility(base: String, isPublic: Bool) async throws {
        _ = try await session.data(for: try request(base, "/api/profile/visibility", method: "POST", json: ["public_profile": isPublic]))
    }
    /// 成就列表 GET /api/me/achievements → items[]。
    func achievements(base: String) async -> [[String: Any]] {
        guard let (data, resp) = try? await session.data(for: try request(base, "/api/me/achievements")),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [] }
        return (obj["items"] as? [[String: Any]]) ?? (obj["achievements"] as? [[String: Any]]) ?? []
    }
    /// 分支树 GET /api/branches/{saveId} → (nodes, activeCommitId)。
    ///
    /// 必须翻页拉完:后端 tree() 默认 page_limit=1000 且 `order by id`(**升序**),不跟
    /// page.next_cursor 就只拿到**最老的** 1000 个 commit,新的全被截掉。群反馈
    /// (行者无疆 2026-07-27,save 268 有 1035 个 commit):第 1 页停在 turn 878、玩家已在
    /// turn 909,当前活跃 commit 根本不在第 1 页里 → 看起来像「进度没存下来」,其实一条没丢。
    /// 孪生:frontend/src/api-client.js 的 branches.list、mobile/src/api/index.ts 的
    /// branches.list —— 三端同批次修。上限 20 页(=2 万 commit)。
    func branchTree(base: String, saveId: Int) async -> (nodes: [[String: Any]], activeId: String?) {
        var nodes: [[String: Any]] = []
        var activeId: String? = nil
        var cursor: String? = nil
        for _ in 0..<20 {
            var path = "/api/branches/\(saveId)"
            if let c = cursor,
               let esc = c.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
                path += "?cursor=\(esc)"
            }
            guard let (data, resp) = try? await session.data(for: try request(base, path)),
                  (resp as? HTTPURLResponse)?.statusCode == 200,
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            else { break }
            let page = (obj["nodes"] as? [[String: Any]]) ?? (obj["commits"] as? [[String: Any]]) ?? []
            nodes.append(contentsOf: page)
            if activeId == nil {
                activeId = (obj["active_commit_id"] as? String) ?? (obj["active_branch_node_id"] as? String)
                    ?? (obj["active_commit_id"] as? NSNumber).map { $0.stringValue }
            }
            let pageInfo = obj["page"] as? [String: Any]
            cursor = pageInfo?["next_cursor"] as? String
                ?? (pageInfo?["next_cursor"] as? NSNumber).map { $0.stringValue }
            if cursor == nil || page.isEmpty { break }
        }
        return (nodes, activeId)
    }
    func branchActivate(base: String, saveId: Int, commitId: String, nodeId: String) async throws {
        // 后端只读 node_id 且做 int();发 Int(数值串)避免空/非数字串触发 500。save_id/commit_id 后端忽略,不发。
        _ = try await session.data(for: try request(base, "/api/branches/activate", method: "POST",
            json: ["node_id": Int(nodeId) ?? nodeId]))
    }
    func branchDelete(base: String, saveId: Int, nodeId: String) async throws {
        _ = try await session.data(for: try request(base, "/api/branches/delete", method: "POST",
            json: ["save_id": saveId, "node_id": nodeId]))
    }

    /// 用量统计 GET /api/me/usage(返回原始 dict)。
    func usage(base: String, days: Int = 30) async -> [String: Any] {
        guard let (data, resp) = try? await session.data(for: try request(base, "/api/me/usage?days=\(days)")),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return [:] }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
    }

    // MARK: 游戏台深交互(待确认 / 记忆 / 分支 / 存档)

    /// 审核模式:批准/拒绝一条 pending write。
    func pendingWrite(base: String, id: String, index: Int, action: String) async {
        _ = try? await session.data(for: try request(base, "/api/permissions/pending-write", method: "POST",
            json: ["id": id, "index": index, "action": action]))
    }
    /// 清除一条 GM 询问(玩家做出选择)。choice 随后作为玩家输入发送。
    func clearQuestion(base: String, id: String, index: Int, choice: String) async {
        _ = try? await session.data(for: try request(base, "/api/questions/clear", method: "POST",
            json: ["id": id, "index": index, "choice": choice]))
    }
    /// 记忆模式 normal/deep/off。
    func memoryMode(base: String, mode: String) async {
        _ = try? await session.data(for: try request(base, "/api/memory/mode", method: "POST", json: ["mode": mode]))
    }
    /// 手动保存当前存档。
    func saveGame(base: String) async {
        _ = try? await session.data(for: try request(base, "/api/save", method: "POST", json: [:]))
    }
    /// 回滚到指定消息(删除该消息及之后)。
    func rollback(base: String, saveId: Int, messageIndex: Int) async throws {
        let req = try request(base, "/api/branches/rollback", method: "POST", json: ["save_id": saveId, "message_index": messageIndex])
        _ = try await session.data(for: req)
    }
    /// 从指定消息分叉(继续新分支)。
    func branchContinue(base: String, saveId: Int, messageIndex: Int) async throws {
        let req = try request(base, "/api/branches/continue", method: "POST", json: ["save_id": saveId, "message_index": messageIndex])
        _ = try await session.data(for: req)
    }
    /// 激活/切换存档为当前。委托到检查状态码的实现(原实现不检查 → 401/404/500 静默吞,调用方误以为成功)。
    func activateSave(base: String, saveId: Int) async throws {
        try await activateSave(base: base, id: saveId)
    }
    func renameSave(base: String, saveId: Int, title: String) async throws {
        _ = try await session.data(for: try request(base, "/api/saves/\(saveId)/rename", method: "POST", json: ["title": title]))
    }
    func deleteSave(base: String, saveId: Int) async throws {
        _ = try await session.data(for: try request(base, "/api/saves/\(saveId)/delete", method: "POST", json: [:]))
    }

    // MARK: 生图(POST /api/images/generate → 轮询 GET /api/images/{id})

    /// 入队一次聊天生图,返回 image_id。失败(含配额/缺凭据)抛 APIError.message。
    func generateImage(base: String, prompt: String, saveId: Int?, size: String?) async throws -> String {
        try await enqueueImage(base: base, prompt: prompt, kind: "chat", size: size, saveId: saveId, attach: nil)
    }

    /// 通用生图入队:kind(chat/cover/avatar/game/persona)+ 可选 attach(自动回写目标)。
    /// attach 形态:{type:"user_avatar"} / {type:"card_avatar",card_id,script_id?} / {type:"script_cover",script_id}
    func enqueueImage(base: String, prompt: String, kind: String, size: String?, saveId: Int? = nil, attach: [String: Any]? = nil) async throws -> String {
        var body: [String: Any] = ["prompt": prompt, "kind": kind]
        if let saveId { body["save_id"] = saveId }
        if let size, !size.isEmpty { body["size"] = size }
        if let attach { body["attach"] = attach }
        let (data, resp) = try await session.data(for: try request(base, "/api/images/generate", method: "POST", json: body, timeout: 60))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            let code2 = (obj["code"] as? String) ?? ""
            if code2 == "quota_exceeded" { throw APIError.message("生图额度已用完") }
            if code2 == "credentials_required" { throw APIError.message("请先在「我的 → 模型与密钥」配置生图模型与 Key") }
            throw APIError.message((obj["error"] as? String) ?? (obj["detail"] as? String) ?? (code2.isEmpty ? "生图失败" : code2))
        }
        if let i = obj["image_id"] as? Int { return String(i) }
        if let s = obj["image_id"] as? String, !s.isEmpty { return s }
        throw APIError.message("生图未返回 image_id")
    }

    func imageStatus(base: String, id: String) async throws -> (status: String, url: String?, error: String?) {
        let (data, _) = try await session.data(for: try request(base, "/api/images/\(id)"))
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        return ((obj["status"] as? String) ?? "pending", obj["url"] as? String, obj["error"] as? String)
    }

    /// 轮询直到完成/失败,返回最终 url(失败抛错)。
    func awaitImage(base: String, id: String, maxTries: Int = 60) async throws -> String {
        // 后端 ai_images 终态:done(成功) / failed / cancelled。succeeded/success/error 为冗余兼容值。
        var doneNoUrlSeen = 0
        for _ in 0..<maxTries {
            let r = try await imageStatus(base: base, id: id)
            if r.status == "done" || r.status == "succeeded" || r.status == "success" {
                if let u = r.url, !u.isEmpty { return u }
                // [round-3-P2] 终态 done 却无 url = 后端不一致;再给一拍宽限,仍空则快速失败,
                //   避免白等满 maxTries(原行为:静默轮询到超时)。
                doneNoUrlSeen += 1
                if doneNoUrlSeen >= 2 { throw APIError.message(r.error ?? "生图已完成但未返回图片地址") }
            }
            if r.status == "failed" || r.status == "error" || r.status == "cancelled" {
                throw APIError.message(r.error ?? "生图失败")
            }
            try await Task.sleep(nanoseconds: 2_000_000_000)  // 不吞 CancellationError:取消时立即中止轮询
        }
        throw APIError.message("生图超时")
    }

    // MARK: 封面 / 头像 上传(multipart)+ 便捷封装
    /// 通用图片 multipart 上传。返回服务端相对 url(读 url 或 avatar_url 字段)。
    func uploadImageMultipart(base: String, path: String, data: Data, filename: String, mime: String) async throws -> String {
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: try makeURL(base, path))
        req.httpMethod = "POST"; req.timeoutInterval = 90
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(mime)\r\n\r\n")
        body.append(data); append("\r\n--\(boundary)--\r\n")
        req.httpBody = body
        let (rdata, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: rdata)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            throw APIError.message((obj["error"] as? String) ?? serverError(from: rdata) ?? "上传失败")
        }
        if let u = obj["url"] as? String, !u.isEmpty { return u }
        if let u = obj["avatar_url"] as? String, !u.isEmpty { return u }
        throw APIError.message("上传成功但未返回地址")
    }
    func uploadScriptCover(base: String, scriptId: Int, data: Data, mime: String) async throws -> String {
        try await uploadImageMultipart(base: base, path: "/api/scripts/\(scriptId)/cover", data: data, filename: "cover.jpg", mime: mime)
    }
    func uploadScriptCardAvatar(base: String, scriptId: Int, cardId: Int, data: Data, mime: String) async throws -> String {
        try await uploadImageMultipart(base: base, path: "/api/scripts/\(scriptId)/character-cards/\(cardId)/avatar", data: data, filename: "avatar.jpg", mime: mime)
    }
    func uploadUserCardAvatar(base: String, cardId: Int, data: Data, mime: String) async throws -> String {
        try await uploadImageMultipart(base: base, path: "/api/me/character-cards/\(cardId)/avatar", data: data, filename: "avatar.jpg", mime: mime)
    }

    // MARK: 人设图(persona images,与「头像」是两套:头像=avatar_path 缩略;人设图=完整图,独立历史)
    /// 列出某用户卡的人设图(完整图);返回数组,is_current 标记当前。
    func personaImages(base: String, cardId: Int) async throws -> [PersonaImage] {
        let (data, resp) = try await session.data(for: try request(base, "/api/me/character-cards/\(cardId)/persona-images"))
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return [] }
        if let arr = try? JSONDecoder().decode([PersonaImage].self, from: data) { return arr }
        // 兼容 {items:[...]}
        struct W: Decodable { let items: [PersonaImage]? }
        return (try? JSONDecoder().decode(W.self, from: data))?.items ?? []
    }
    /// AI 生成人设图(完整图)。返回 image_id 供轮询。
    func generatePersonaImage(base: String, cardId: Int, prompt: String) async throws -> String {
        let body: [String: Any] = prompt.isEmpty ? [:] : ["prompt": prompt]
        let (data, resp) = try await session.data(for: try request(base, "/api/me/character-cards/\(cardId)/generate-persona-image", method: "POST", json: body, timeout: 60))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if code < 200 || code >= 300 || obj["ok"] as? Bool == false {
            let c = (obj["error"] as? String) ?? ""
            if c == "quota_exceeded" { throw APIError.message("生图额度已用完") }
            if c == "credentials_required" { throw APIError.message("请先在「我的 → 模型与密钥」配置生图模型与 Key") }
            throw APIError.message(c.isEmpty ? "生成失败" : c)
        }
        if let i = obj["image_id"] as? Int { return String(i) }
        if let s = obj["image_id"] as? String, !s.isEmpty { return s }
        throw APIError.message("未返回 image_id")
    }
    /// 上传人设图(完整图)。返回 url。
    func uploadPersonaImage(base: String, cardId: Int, data: Data, mime: String) async throws -> String {
        try await uploadImageMultipart(base: base, path: "/api/me/character-cards/\(cardId)/persona-images/upload", data: data, filename: "persona.jpg", mime: mime)
    }
    /// 把某张历史人设图设为当前(同时更新头像)。
    func setCurrentPersona(base: String, cardId: Int, imageId: Int) async throws {
        try await postExpectOK(base, "/api/me/character-cards/\(cardId)/persona-images/\(imageId)/set-current", [:], fail: "设置失败")
    }
    func uploadProfileAvatar(base: String, data: Data, mime: String) async throws -> String {
        try await uploadImageMultipart(base: base, path: "/api/profile/avatar", data: data, filename: "avatar.jpg", mime: mime)
    }
    func resetProfileAvatar(base: String) async throws {
        _ = try await session.data(for: try request(base, "/api/profile/avatar/reset", method: "POST", json: [:]))
    }

    /// 探活某服务器(扫码连接用):/api/health 返 2xx-4xx 即视为可连。返回 (ok, 版本/错误).
    func probeServer(base: String) async -> (ok: Bool, info: String) {
        guard let url = try? makeURL(base, "/api/health") else { return (false, "地址无效") }
        var req = URLRequest(url: url); req.timeoutInterval = 8
        guard let (data, resp) = try? await session.data(for: req) else { return (false, "无法连接,请检查地址与网络") }
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code >= 200 && code < 500 else { return (false, "服务器返回 \(code)") }
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let ver = (obj["app_version"] as? String) ?? (obj["version"] as? String) ?? ""
        return (true, ver.isEmpty ? "已连接" : "v\(ver)")
    }

    /// 扫码免登录:消费桌面端 desktop-login token → URLSession 自动存 session cookie → 返回登录用户。
    /// 桌面端「局域网二维码 / 复制登录链接」编码的就是 /api/auth/desktop-login?token=…
    func desktopLogin(base: String, token: String) async throws -> APIUser? {
        let enc = token.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? token
        // GET 会 302 + Set-Cookie;跟随重定向无妨,只取 cookie。失败不抛,交给 me() 判定真伪。
        _ = try? await session.data(for: try request(base, "/api/auth/desktop-login?token=\(enc)", timeout: 20))
        return try await me(base: base)
    }

    /// 邀请链接轻量注册(自部署,无邮箱):用户名 + 密码 → 置 session cookie → 返回新用户。
    func registerInvite(base: String, invite: String, username: String, password: String) async throws -> APIUser? {
        let req = try request(base, "/api/local/register", method: "POST", json: [
            "invite": invite, "username": username, "password": password, "age_confirmed": true,
        ], timeout: 30)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { throw APIError.http(code, serverError(from: data) ?? "注册失败") }
        return try await me(base: base)
    }

    /// POST /api/opening —— 剧情开场流(新存档无历史时)。
    func streamOpening(base: String, saveId: Int? = nil) -> AsyncThrowingStream<ChatEvent, Error> {
        var body: [String: Any] = [:]
        if let saveId { body["save_id"] = saveId }
        return streamSSE(base: base, path: "/api/opening", body: body)
    }

    private func streamSSE(base: String, path: String, body: [String: Any]) -> AsyncThrowingStream<ChatEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let req = try request(base, path, method: "POST", json: body, timeout: 300)
                    let (bytes, resp) = try await session.bytes(for: req)
                    let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                    if code < 200 || code >= 300 {
                        // 读取错误响应体(原来一律空串,用户只看到光秃秃的状态码)。错误体很小,收集后解析 {error}。
                        var bodyStr = ""
                        for try await line in bytes.lines { bodyStr += line + "\n"; if bodyStr.utf8.count > 4096 { break } }
                        let msg = serverError(from: Data(bodyStr.utf8)) ?? (bodyStr.isEmpty ? "" : String(bodyStr.prefix(300)))
                        continuation.finish(throwing: APIError.http(code, msg))
                        return
                    }
                    var event = "message"
                    for try await line in bytes.lines {
                        if line.isEmpty { event = "message"; continue }
                        if line.hasPrefix("event:") {
                            event = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
                        } else if line.hasPrefix("data:") {
                            let raw = String(line.dropFirst(5).trimmingCharacters(in: .whitespaces))
                            handle(event: event, dataLine: raw, into: continuation)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func handle(event: String, dataLine: String,
                        into c: AsyncThrowingStream<ChatEvent, Error>.Continuation) {
        let data = Data(dataLine.utf8)
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        switch event {
        case "token":
            if let t = obj?["text"] as? String { c.yield(.token(t)) }
        case "stage":
            let l = (obj?["message"] as? String) ?? (obj?["label"] as? String) ?? (obj?["phase"] as? String)
            if let l, !l.isEmpty { c.yield(.stage(l)) }
        case "thinking":
            if let t = obj?["text"] as? String, !t.isEmpty { c.yield(.stage(t)) }
        case "usage":
            if let pct = doubleVal(obj?["context_pct"]) {
                c.yield(.usage(pct))
            } else if let used = doubleVal(obj?["context_used"]), let max = doubleVal(obj?["context_max"]), max > 0 {
                c.yield(.usage(used / max * 100))
            }
        case "system_receipt":
            // 斜杠命令(/time /loc /rel /var 等)的确定性回执 → 状态行提示,对齐各端「设定已更新」反馈。
            if let t = obj?["text"] as? String, !t.isEmpty {
                let first = t.replacingOccurrences(of: "```", with: "").split(separator: "\n").first.map(String.init) ?? t
                c.yield(.stage("✓ " + first.trimmingCharacters(in: .whitespaces)))
            }
        case "updates":
            // /set 等 directive 的确定性回执(pre_llm)→ 状态行提示。
            // 失败项(后端惯例:「X 失败:」「X 被拒绝:」「X 未生效:」)不该被标成「已更新」,与 web 端同款拆分。
            if (obj?["stage"] as? String) == "pre_llm", let items = obj?["items"] as? [String], !items.isEmpty {
                let fails = items.filter { $0.contains("失败") || $0.contains("被拒绝") || $0.contains("未生效") }
                if fails.isEmpty {
                    c.yield(.stage("✓ 设定已更新：" + items.joined(separator: "；")))
                } else {
                    let okCount = items.count - fails.count
                    c.yield(.stage("⚠ 设定已应用 \(okCount) 项，\(fails.count) 项未生效：" + items.joined(separator: "；")))
                }
            }
        case "error":
            c.yield(.error((obj?["message"] as? String) ?? "生成出错"))
        case "done":
            // 后端把清洗后的正文落在 status.history 末条 assistant(strip_leaked_scaffold 已应用);
            // 用它替换流式累计的原始文本,杜绝 ```json ops 块泄漏到气泡。
            var final: String?
            if let hist = (obj?["status"] as? [String: Any])?["history"] as? [[String: Any]] {
                for m in hist.reversed() where (m["role"] as? String) == "assistant" {
                    if let ct = m["content"] as? String, !ct.isEmpty { final = ct; break }
                }
            }
            c.yield(.done(final))
        default:
            break
        }
    }
}
