import SwiftUI

// 编辑资料 —— 对齐 web「编辑资料」核心字段(昵称 + 简介)。
struct EditProfileView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var displayName = ""
    @State private var username = ""
    @State private var bio = ""
    @State private var avatarURL: String?
    @State private var isPublic = false
    @State private var saving = false

    var body: some View {
        NavigationStack {
            ZStack {
                WarmBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        // 头像
                        HStack {
                            Spacer()
                            ImageSetControl(
                                base: store.serverURL, currentURL: avatarURL, style: .avatarCircle, width: 88,
                                canEdit: !store.demo, placeholderIcon: "person.fill",
                                upload: { data, mime in try await store.api.uploadProfileAvatar(base: store.serverURL, data: data, mime: mime) },
                                generate: { prompt, size in
                                    let id = try await store.api.enqueueImage(base: store.serverURL, prompt: prompt, kind: "avatar", size: size, attach: ["type": "user_avatar"])
                                    return try await store.api.awaitImage(base: store.serverURL, id: id)
                                },
                                remove: { try await store.api.resetProfileAvatar(base: store.serverURL) },
                                onUpdated: { v in avatarURL = v; store.user?.avatarURL = v })
                            Spacer()
                        }
                        card("昵称") { field($displayName, "你的显示名") }
                        card("用户名") {
                            field($username, "登录用户名(英文/数字)")
                            Text(loc: "用户名用于登录,修改后请用新用户名登录。").font(Theme.ui(11)).foregroundStyle(Theme.muted2)
                        }
                        card("简介") {
                            ZStack(alignment: .topLeading) {
                                if bio.isEmpty { Text(loc: "一句话介绍自己(可选)").font(Theme.ui(14)).foregroundStyle(Theme.muted2).padding(.vertical, 4) }
                                TextEditor(text: $bio).font(Theme.ui(14)).foregroundStyle(Theme.text).tint(Theme.accent).scrollContentBackground(.hidden).frame(height: 80)
                            }
                        }
                        card("隐私") {
                            Toggle(isOn: $isPublic) { Text(loc: "公开个人主页").font(Theme.ui(14)).foregroundStyle(Theme.text) }
                                .tint(Theme.accent).disabled(store.demo)
                                // 写偏好 public_profile(成就墙读的就是它)。以前写的是 /api/profile/visibility
                                // (profile_extras.visibility),没有任何后端读方,开关拨了等于没拨。
                                .onChange(of: isPublic) { _, v in if !store.demo { Task { try? await store.api.setPreferences(base: store.serverURL, ["public_profile": v]) } } }
                            Text(loc: "开启后,他人可通过你的公开主页查看成就墙等。").font(Theme.ui(11)).foregroundStyle(Theme.muted2)
                        }
                    }.padding(16)
                }
            }
            .navigationTitle("编辑资料").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.foregroundStyle(Theme.textQuiet) }
                ToolbarItem(placement: .confirmationAction) { Button(saving ? "保存中…" : "保存") { Task { await save() } }.foregroundStyle(Theme.accent).disabled(saving) }
            }
            .task { await load() }
        }
    }
    private func field(_ t: Binding<String>, _ ph: String) -> some View {
        TextField(ph, text: t).font(Theme.ui(15)).foregroundStyle(Theme.text).tint(Theme.accent)
            .autocorrectionDisabled()
            .padding(.horizontal, 11).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 10).fill(Theme.panel2)).overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.line, lineWidth: 1))
    }
    private func load() async {
        displayName = store.user?.displayName ?? ""
        username = store.user?.username ?? ""
        avatarURL = store.user?.avatarURL
        if store.demo { return }
        if let p = try? await store.api.profile(base: store.serverURL) {
            if let n = p.displayName { displayName = n }
            if let u = p.username { username = u }
            if let b = p.prefs["bio"] as? String { bio = b }
            if let pub = p.prefs["public_profile"] as? Bool { isPublic = pub }
        }
    }
    private func save() async {
        saving = true; defer { saving = false; dismiss() }
        if store.demo { return }
        try? await store.api.saveProfile(base: store.serverURL, displayName: displayName.trimmingCharacters(in: .whitespaces), bio: bio,
                                         username: username.trimmingCharacters(in: .whitespaces))
    }
}

// 成就墙 —— GET /api/me/achievements。
struct AchievementsView: View {
    @EnvironmentObject var store: AppStore
    @State private var items: [[String: Any]] = []
    @State private var loading = true

    private var unlocked: Int { items.filter { ($0["unlocked"] as? Bool) == true }.count }

    var body: some View {
        NavigationStack {
            ZStack {
                WarmBackground()
                if loading { ProgressView().tint(Theme.accent) }
                else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            Text("已解锁 \(unlocked) / \(items.count)").font(Theme.ui(13)).foregroundStyle(Theme.muted)
                            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                                ForEach(Array(items.enumerated()), id: \.offset) { _, a in cell(a) }
                            }
                        }.padding(16)
                    }
                }
            }
            .navigationTitle("成就").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
            .task { if store.demo { items = demoItems; loading = false } else { items = await store.api.achievements(base: store.serverURL); loading = false } }
        }
    }
    private func cell(_ a: [String: Any]) -> some View {
        let on = (a["unlocked"] as? Bool) == true
        let name = (a["name"] as? String) ?? "成就"
        let desc = (a["desc"] as? String) ?? (a["description"] as? String) ?? ""
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: on ? "rosette" : "lock.fill").font(.system(size: 16)).foregroundStyle(on ? tierColor(a["tier"] as? String) : Theme.muted2)
                Spacer()
            }
            Text(name).font(Theme.ui(13.5, .semibold)).foregroundStyle(on ? Theme.text : Theme.muted)
            Text(desc).font(Theme.ui(11)).foregroundStyle(Theme.muted2).lineLimit(3)
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(on ? Theme.panel : Theme.panel.opacity(0.5)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(on ? Theme.accentEdge : Theme.line, lineWidth: 1))
    }
    private func tierColor(_ t: String?) -> Color {
        switch t { case "gold": return Color(red: 0.85, green: 0.68, blue: 0.3)
        case "silver": return Color(red: 0.7, green: 0.72, blue: 0.75)
        case "bronze": return Color(red: 0.72, green: 0.5, blue: 0.35); default: return Theme.accent }
    }
    private var demoItems: [[String: Any]] {
        [["name": "初次开局", "desc": "创建第一个存档", "tier": "bronze", "unlocked": true],
         ["name": "百回合", "desc": "单档对话满 100 回合", "tier": "silver", "unlocked": false]]
    }
}

// 存档详情 —— 概况 + 分支树(激活/删除)。
struct SaveDetailView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let save: SaveItem
    var onChanged: () -> Void
    @State private var nodes: [[String: Any]] = []
    @State private var activeId: String?
    @State private var loading = true
    @State private var showExport = false
    @State private var exporting = false
    @State private var shareItem: ShareItem?
    @State private var showDelete = false
    @State private var nodeDeleteTarget: String?
    @State private var err: String?

    struct ShareItem: Identifiable { let id = UUID(); let url: URL }

    var body: some View {
        NavigationStack {
            ZStack {
                WarmBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        card("概况") {
                            kv("剧本", save.scriptTitle ?? "—")
                            if let p = save.player_name, !p.isEmpty { kv("角色", p) }
                            kv("回合", "\(save.turns)")
                            kv("分支", "\(save.branches)")
                            if let wt = save.world_time, !wt.isEmpty { kv("世界时间", wt) }
                            if let u = save.updated { kv("最近游玩", u) }
                        }
                        if let snip = save.snippet, !snip.isEmpty {
                            Text(snip).font(Theme.serif(13.5)).italic().foregroundStyle(Theme.muted)
                                .frame(maxWidth: .infinity, alignment: .leading).padding(12)
                                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.panel2))
                        }
                        Button { Task { await store.launchGame(save); dismiss() } } label: {
                            Label("进入游戏", systemImage: "play.fill").font(Theme.ui(15, .semibold)).foregroundStyle(Theme.onAccent)
                                .frame(maxWidth: .infinity).padding(.vertical, 12).background(Theme.accent, in: Capsule())
                        }
                        HStack(spacing: 10) {
                            Button { showExport = true } label: {
                                HStack(spacing: 5) { if exporting { ProgressView().tint(Theme.accent).scaleEffect(0.7) }
                                    Label("导出", systemImage: "square.and.arrow.up").labelStyle(.titleAndIcon) }
                                .font(Theme.ui(13.5, .medium)).foregroundStyle(Theme.text).frame(maxWidth: .infinity).padding(.vertical, 10)
                                .background(Theme.panel2, in: Capsule()).overlay(Capsule().stroke(Theme.line, lineWidth: 1))
                            }.disabled(exporting || store.demo)
                            Button(role: .destructive) { showDelete = true } label: {
                                Label("删除", systemImage: "trash").font(Theme.ui(13.5, .medium)).foregroundStyle(Theme.danger)
                                    .frame(maxWidth: .infinity).padding(.vertical, 10)
                                    .background(Theme.panel2, in: Capsule()).overlay(Capsule().stroke(Theme.line, lineWidth: 1))
                            }.disabled(store.demo)
                        }
                        if let err { Text(err).font(Theme.ui(12)).foregroundStyle(Theme.danger) }
                        Text("分支").font(Theme.ui(11, .semibold)).foregroundStyle(Theme.accent).tracking(1.2)
                        if loading { ProgressView().tint(Theme.accent).frame(maxWidth: .infinity).padding(.vertical, 16) }
                        else if nodes.isEmpty { Text("暂无分支节点。").font(Theme.ui(13)).foregroundStyle(Theme.muted) }
                        else { ForEach(Array(nodes.enumerated()), id: \.offset) { _, n in branchRow(n) } }
                    }.padding(16)
                }
            }
            .navigationTitle(save.display).navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("关闭") { dismiss() }.foregroundStyle(Theme.textQuiet) } }
            .task { let r = await store.api.branchTree(base: store.serverURL, saveId: save.id); nodes = r.nodes; activeId = r.activeId; loading = false }
            .confirmationDialog("导出存档", isPresented: $showExport, titleVisibility: .visible) {
                Button("不含向量(更小)") { Task { await export("no_vectors") } }
                Button("完整(含向量)") { Task { await export("full") } }
                Button("取消", role: .cancel) {}
            }
            .confirmationDialog("删除存档「\(save.display)」?此操作不可撤销。", isPresented: $showDelete, titleVisibility: .visible) {
                Button("删除", role: .destructive) { Task { await deleteSaveAction() } }
                Button("取消", role: .cancel) {}
            }
            .confirmationDialog("删除该分支节点?其下的分支也会一并删除。", isPresented: Binding(get: { nodeDeleteTarget != nil }, set: { if !$0 { nodeDeleteTarget = nil } }), titleVisibility: .visible, presenting: nodeDeleteTarget) { nid in
                Button("删除", role: .destructive) { Task { await deleteNode(nid) } }
                Button("取消", role: .cancel) {}
            }
            .sheet(item: $shareItem) { item in ActivityView(items: [item.url]) }
        }
    }
    private func export(_ tier: String) async {
        err = nil; exporting = true; defer { exporting = false }
        do { let url = try await store.api.saveExportBundle(base: store.serverURL, saveId: save.id, tier: tier); shareItem = ShareItem(url: url) }
        catch { err = (error as? LocalizedError)?.errorDescription ?? "导出失败" }
    }
    private func deleteSaveAction() async {
        do { try await store.api.deleteSave(base: store.serverURL, saveId: save.id); onChanged(); dismiss() }
        catch { err = (error as? LocalizedError)?.errorDescription ?? "删除失败" }
    }
    private func deleteNode(_ nid: String) async {
        do { try await store.api.branchDelete(base: store.serverURL, saveId: save.id, nodeId: nid); await refresh() }
        catch { err = (error as? LocalizedError)?.errorDescription ?? "删除失败" }
    }
    private func branchRow(_ n: [String: Any]) -> some View {
        let nid = (n["id"] as? String) ?? (n["id"] as? NSNumber).map { $0.stringValue } ?? ""
        let cid = (n["commit_id"] as? String) ?? nid
        let summary = (n["summary"] as? String) ?? (n["message"] as? String) ?? (n["content_preview"] as? String) ?? "节点"
        let turn = (n["turn_index"] as? Int) ?? (n["turn"] as? Int)
        let isActive = nid == activeId || cid == activeId
        return HStack(alignment: .top, spacing: 8) {
            Image(systemName: isActive ? "circle.fill" : "circle").font(.system(size: 9)).foregroundStyle(isActive ? Theme.accent : Theme.muted2).padding(.top, 4)
            VStack(alignment: .leading, spacing: 2) {
                if let t = turn { Text("第 \(t) 回合").font(Theme.ui(10.5)).foregroundStyle(Theme.muted2) }
                Text(summary).font(Theme.ui(12.5)).foregroundStyle(Theme.text).lineLimit(2)
            }
            Spacer()
            if !isActive {
                HStack(spacing: 12) {
                    Button("激活") { Task { try? await store.api.branchActivate(base: store.serverURL, saveId: save.id, commitId: cid, nodeId: nid); await refresh() } }
                        .font(Theme.ui(12, .semibold)).foregroundStyle(Theme.accent)
                    Button { nodeDeleteTarget = nid } label: { Image(systemName: "trash").font(.system(size: 12)).foregroundStyle(Theme.danger) }
                        .disabled(store.demo)
                }
            } else { Text("当前").font(Theme.ui(11)).foregroundStyle(Theme.accent) }
        }
        .padding(10).background(RoundedRectangle(cornerRadius: 10).fill(isActive ? Theme.accentSoft : Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(isActive ? Theme.accentEdge : Theme.line, lineWidth: 1))
    }
    private func refresh() async { let r = await store.api.branchTree(base: store.serverURL, saveId: save.id); nodes = r.nodes; activeId = r.activeId; onChanged() }
    private func kv(_ k: String, _ v: String) -> some View {
        HStack { Text(k).font(Theme.ui(12.5)).foregroundStyle(Theme.muted).frame(width: 72, alignment: .leading)
            Text(v).font(Theme.ui(13)).foregroundStyle(Theme.text); Spacer() }
    }
}

// 账号与数据 —— 统一分类:清空存档 + 法律协议 + 注销账号(App Store 5.1.1(v) 应用内删除)。
struct AccountDataView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.openURL) private var openURL
    @State private var showClear = false
    @State private var clearing = false
    @State private var showDelete = false
    @State private var deleting = false
    @State private var deleteMsg: String?

    // 隐私开关:只保留后端真的会读的 public_profile(公开成就墙)。「允许被搜索」「分享匿名使用数据」
    // 「分享崩溃报告」「两步验证」「邮件通知」全站没有后端读方(没有用户搜索 / 用量上报 / 2FA 实现 /
    // 按偏好发的邮件),是装饰,已下线 —— 与 web / 手机端同批。
    @State private var privPublicProfile = false   // default false

    private var lang: String { store.language.hasPrefix("en") ? "en" : "zh-CN" }
    private let legalBase = "https://play.stellatrix.icu/legal"
    private var legalDocs: [(String, String)] {
        [("隐私政策", "privacy"), ("服务条款", "terms-of-service"), ("可接受使用政策", "acceptable-use"),
         ("Cookie 政策", "cookie"), ("DMCA 版权政策", "dmca"), ("成人内容声明", "adult-content-disclaimer")]
    }

    var body: some View {
        SettingsScaffold(title: "账号与数据") {
            card("数据") {
                Button(role: .destructive) { showClear = true } label: {
                    rowAction(clearing ? "清理中…" : "清空所有游戏存档", "trash", busy: clearing)
                }.disabled(clearing || store.demo)
            }
            card("隐私") {
                privToggle("公开主页",         isOn: $privPublicProfile, key: "public_profile")
            }
            card("法律与协议") {
                ForEach(Array(legalDocs.enumerated()), id: \.offset) { i, d in
                    if i > 0 { Divider().overlay(Theme.lineSoft) }
                    Button {
                        if let u = URL(string: "\(legalBase)/\(d.1).\(lang).html") { openURL(u) }
                    } label: {
                        HStack {
                            Text(loc: d.0).font(Theme.ui(14.5)).foregroundStyle(Theme.text)
                            Spacer()
                            Image(systemName: "arrow.up.right").font(.system(size: 11, weight: .semibold)).foregroundStyle(Theme.muted2)
                        }.contentShape(Rectangle())
                    }
                }
            }
            card("危险区") {
                Button(role: .destructive) { showDelete = true } label: {
                    rowAction(deleting ? "提交中…" : "注销账号", "person.crop.circle.badge.xmark", busy: deleting)
                }.disabled(deleting || store.demo)
                Text(loc: "注销将提交申请,宽限期内可登录撤销;到期后账号与全部数据永久删除。")
                    .font(Theme.ui(11.5)).foregroundStyle(Theme.muted)
            }
        } onLoad: {}
        .task { await loadPrivacy() }
        .confirmationDialog(tr("清空所有游戏存档?此操作不可撤销。"), isPresented: $showClear, titleVisibility: .visible) {
            Button(tr("全部删除"), role: .destructive) { Task { await clearAll() } }
            Button(tr("取消"), role: .cancel) {}
        }
        .confirmationDialog(tr("注销账号?"), isPresented: $showDelete, titleVisibility: .visible) {
            Button(tr("确认注销"), role: .destructive) { Task { await requestDelete() } }
            Button(tr("取消"), role: .cancel) {}
        } message: { Text(loc: "将提交账号注销申请,宽限期内可登录撤销;到期后账号与全部数据将被永久删除。") }
        .alert(tr("注销申请已提交"), isPresented: Binding(get: { deleteMsg != nil }, set: { if !$0 { deleteMsg = nil } })) {
            Button(tr("好")) { deleteMsg = nil; Task { await store.logout() } }
        } message: { Text(deleteMsg ?? "") }
    }

    // 隐私开关行:label + Toggle
    private func privToggle(_ label: String, isOn: Binding<Bool>, key: String) -> some View {
        Toggle(isOn: isOn) {
            Text(loc: label).font(Theme.ui(14.5)).foregroundStyle(Theme.text)
        }
        .tint(Theme.accent)
        .disabled(store.demo)
        .onChange(of: isOn.wrappedValue) { _, newVal in
            guard !store.demo else { return }
            Task { try? await store.api.setPreferences(base: store.serverURL, [key: newVal]) }
        }
    }

    private func rowAction(_ title: String, _ icon: String, busy: Bool) -> some View {
        HStack(spacing: 8) {
            if busy { ProgressView().tint(Theme.danger).scaleEffect(0.8) } else { Image(systemName: icon).font(.system(size: 14)) }
            Text(loc: title).font(Theme.ui(14.5, .medium)); Spacer()
        }.foregroundStyle(Theme.danger)
    }
    private func clearAll() async {
        guard !store.demo else { return }
        clearing = true; defer { clearing = false }
        let all = (try? await store.api.savesList(base: store.serverURL)) ?? []
        for s in all where !s.isTavern { try? await store.api.deleteSave(base: store.serverURL, saveId: s.id) }
    }
    private func requestDelete() async {
        guard !store.demo else { return }
        deleting = true; defer { deleting = false }
        do { deleteMsg = try await store.api.requestAccountDelete(base: store.serverURL) }
        catch { deleteMsg = (error as? LocalizedError)?.errorDescription ?? "注销申请失败" }
    }
    private func loadPrivacy() async {
        guard !store.demo else { return }
        guard let p = try? await store.api.profile(base: store.serverURL) else { return }
        let prefs = p.prefs
        if let v = prefs["public_profile"] as? Bool { privPublicProfile = v }
    }
}
