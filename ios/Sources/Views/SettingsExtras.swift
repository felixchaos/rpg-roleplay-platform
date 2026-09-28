import SwiftUI

// 偏好 / 记忆 / 权限 —— 对齐 web MobileSettings 对应分区,全部写 POST /api/me/preference。

// MARK: 偏好
struct PreferencesView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    // [round-3-P2] 移除死 @State language:段控以 store.language 为唯一真相,该字段从不被读取,
    //   留着会让人误以为它是语言状态源。
    @State private var serif = true
    @State private var autosave = true
    @State private var blackSwan = false
    @State private var curator = 0.6
    @State private var loaded = false

    var body: some View {
        SettingsScaffold(title: "偏好") {
            card("界面") {
                rowLabel("界面语言")
                seg(["zh-CN": "简体", "zh-TW": "繁體", "en": "EN"], order: ["zh-CN", "zh-TW", "en"], sel: store.language) {
                    store.setLanguage($0)   // 即时切换 UI 语言 + 同步后端(store.language 为唯一真相)
                }
                toggle("叙事衬线字体", $serif) { save("pref.serif", serif) }
                toggle("自动保存", $autosave) { save("pref.autosave", autosave) }
            }
            card("智能体") {
                toggle("黑天鹅事件代理", $blackSwan) { save("black_swan.enabled", blackSwan) }
                sliderRow("策展置信阈值", $curator, 0...1, 0.05, "%.2f") { save("curator.confidence_threshold", curator) }
            }
        } onLoad: { await load() }
    }
    private func save(_ k: String, _ v: Any) { guard loaded, !store.demo else { return }; Task { try? await store.api.setPreferences(base: store.serverURL, [k: v]) } }
    private func load() async {
        if store.demo { loaded = true; return }
        guard let p = try? await store.api.profile(base: store.serverURL) else { loaded = true; return }
        let pr = p.prefs
        serif = readBool(pr, ["pref.serif", "serif"], true)
        autosave = readBool(pr, ["pref.autosave", "autosave"], true)
        blackSwan = readBool(pr, ["black_swan.enabled"], false)
        curator = readDbl(pr, ["curator.confidence_threshold"], 0.6)
        loaded = true
    }
}

// MARK: 记忆
struct MemoryView: View {
    @EnvironmentObject var store: AppStore
    // 初值和读取回退值 = 后端 MemorySettings 的默认值(守卫 test_memory_settings_display_defaults.py)
    @State private var recall = 5.0
    @State private var summary = 10.0
    @State private var budget = 800.0
    @State private var archive = 50.0
    @State private var pinnedMax = 20.0
    @State private var bPinned = true
    @State private var bWorld = true
    @State private var bChar = true
    @State private var loaded = false

    var body: some View {
        SettingsScaffold(title: "记忆") {
            card("检索与摘要") {
                sliderRow("召回深度", $recall, 2...20, 1, "%.0f") { save("memory.recall_depth", Int(recall)) }
                sliderRow("摘要窗口", $summary, 3...20, 1, "%.0f") { save("memory.summary_window", Int(summary)) }
                sliderRow("Token 预算", $budget, 200...2000, 50, "%.0f") { save("memory.token_budget", Int(budget)) }
                sliderRow("自动归档回合", $archive, 10...200, 5, "%.0f") { save("memory.auto_archive_after_turns", Int(archive)) }
                sliderRow("固定记忆上限", $pinnedMax, 5...100, 1, "%.0f") { save("memory.pinned_max", Int(pinnedMax)) }
            }
            card("记忆桶") {
                toggle("固定记忆", $bPinned) { save("memory.bucket_pinned_enabled", bPinned) }
                toggle("世界知识", $bWorld) { save("memory.bucket_world_enabled", bWorld) }
                toggle("角色知识", $bChar) { save("memory.bucket_character_enabled", bChar) }
            }
        } onLoad: { await load() }
    }
    private func save(_ k: String, _ v: Any) { guard loaded, !store.demo else { return }; Task { try? await store.api.setPreferences(base: store.serverURL, [k: v]) } }
    private func load() async {
        if store.demo { loaded = true; return }
        guard let p = try? await store.api.profile(base: store.serverURL) else { loaded = true; return }
        let pr = p.prefs
        recall = readDbl(pr, ["memory.recall_depth", "settings.召回深度"], 5)
        summary = readDbl(pr, ["memory.summary_window", "settings.摘要窗口"], 10)
        budget = readDbl(pr, ["memory.token_budget"], 800)
        archive = readDbl(pr, ["memory.auto_archive_after_turns"], 50)
        pinnedMax = readDbl(pr, ["memory.pinned_max", "settings.固定记忆上限"], 20)
        bPinned = readBool(pr, ["memory.bucket_pinned_enabled"], true)
        bWorld = readBool(pr, ["memory.bucket_world_enabled"], true)
        bChar = readBool(pr, ["memory.bucket_character_enabled"], true)
        loaded = true
    }
}

// MARK: 权限
struct PermissionsView: View {
    @EnvironmentObject var store: AppStore
    @State private var mode = "review"
    @State private var highRisk: Set<String> = []
    @State private var custom: [String] = []
    @State private var newEntry = ""
    @State private var loaded = false

    private let highRiskAll = ["timeline.pending_jump", "player.background", "world.constraints", "relationships.*.tone"]

    var body: some View {
        SettingsScaffold(title: "权限") {
            card("默认权限模式") {
                seg(["default": "默认", "review": "审核", "full_access": "完全"], order: ["default", "review", "full_access"], sel: mode) {
                    mode = $0; save("perm.default_mode", $0)
                }
                Text("控制 GM 写状态前是否需要你确认。").font(Theme.ui(11.5)).foregroundStyle(Theme.muted2)
            }
            card("高风险字段白名单") {
                ForEach(highRiskAll, id: \.self) { k in
                    Toggle(isOn: Binding(get: { highRisk.contains(k) }, set: { on in
                        if on { highRisk.insert(k) } else { highRisk.remove(k) }
                        save("perm.high_risk_whitelist", Array(highRisk))
                    })) { Text(k).font(Theme.ui(12.5).monospaced()).foregroundStyle(Theme.text) }.tint(Theme.accent)
                }
            }
            card("自定义白名单") {
                ForEach(custom, id: \.self) { e in
                    HStack {
                        Text(e).font(Theme.ui(12.5).monospaced()).foregroundStyle(Theme.text)
                        Spacer()
                        Button { custom.removeAll { $0 == e }; save("permissions.custom_whitelist", custom) } label: {
                            Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.muted2)
                        }
                    }
                }
                HStack(spacing: 8) {
                    TextField("如 world.weather", text: $newEntry).font(Theme.ui(13)).foregroundStyle(Theme.text).tint(Theme.accent)
                        .autocorrectionDisabled().textInputAutocapitalization(.never)
                        .padding(.horizontal, 10).padding(.vertical, 8)
                        .background(RoundedRectangle(cornerRadius: 8).fill(Theme.panel2)).overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.line, lineWidth: 1))
                    Button("添加") { addCustom() }.font(Theme.ui(13, .semibold)).foregroundStyle(Theme.accent)
                }
            }
        } onLoad: { await load() }
    }
    private func addCustom() {
        let e = newEntry.trimmingCharacters(in: .whitespaces)
        guard !e.isEmpty, !custom.contains(e) else { return }
        custom.append(e); newEntry = ""; save("permissions.custom_whitelist", custom)
    }
    private func save(_ k: String, _ v: Any) { guard loaded, !store.demo else { return }; Task { try? await store.api.setPreferences(base: store.serverURL, [k: v]) } }
    private func load() async {
        if store.demo { custom = ["world.weather"]; highRisk = ["player.background"]; loaded = true; return }
        guard let p = try? await store.api.profile(base: store.serverURL) else { loaded = true; return }
        let pr = p.prefs
        mode = readStr(pr, ["perm.default_mode"], "review")
        if let arr = (pr["perm.high_risk_whitelist"] as? [String]) { highRisk = Set(arr) }
        if let arr = (pr["permissions.custom_whitelist"] as? [String]) ?? (pr["perm.custom_whitelist"] as? [String]) { custom = arr }
        loaded = true
    }
}

// MARK: 复用脚手架 + 组件 + 读取助手
struct SettingsScaffold<C: View>: View {
    let title: String
    @ViewBuilder let content: () -> C
    let onLoad: () async -> Void
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ZStack {
                WarmBackground()
                ScrollView { VStack(alignment: .leading, spacing: 22) { content() }.padding(16).padding(.bottom, 24) }
            }
            .navigationTitle(LocalizedStringKey(title)).navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() }.foregroundStyle(Theme.accent) } }
            .task { await onLoad() }
        }
    }
}

@ViewBuilder func card<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
    VStack(alignment: .leading, spacing: 12) {
        Text(loc: title).font(Theme.ui(11, .semibold)).foregroundStyle(Theme.accent).tracking(1.2)
        VStack(alignment: .leading, spacing: 14) { content() }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.line, lineWidth: 1))
    }
}
func rowLabel(_ t: String) -> some View { Text(loc: t).font(Theme.ui(13.5)).foregroundStyle(Theme.text).frame(maxWidth: .infinity, alignment: .leading) }

func toggle(_ label: String, _ value: Binding<Bool>, _ onChange: @escaping () -> Void) -> some View {
    Toggle(isOn: value) { Text(loc: label).font(Theme.ui(14)).foregroundStyle(Theme.text) }
        .tint(Theme.accent).onChange(of: value.wrappedValue) { _, _ in onChange() }
}
func sliderRow(_ label: String, _ value: Binding<Double>, _ range: ClosedRange<Double>, _ step: Double, _ fmt: String, _ onCommit: @escaping () -> Void) -> some View {
    VStack(alignment: .leading, spacing: 6) {
        HStack { Text(loc: label).font(Theme.ui(13.5)).foregroundStyle(Theme.text); Spacer()
            Text(String(format: fmt, value.wrappedValue)).font(Theme.ui(13)).foregroundStyle(Theme.muted).monospacedDigit() }
        Slider(value: value, in: range, step: step) { editing in if !editing { onCommit() } }.tint(Theme.accent)
    }
}
func seg(_ map: [String: String], order: [String], sel: String, _ onPick: @escaping (String) -> Void) -> some View {
    HStack(spacing: 6) {
        ForEach(order, id: \.self) { k in
            Button { onPick(k) } label: {
                Text(loc: map[k] ?? k).font(Theme.ui(12.5, .medium)).foregroundStyle(sel == k ? Theme.onAccent : Theme.muted)
                    .frame(maxWidth: .infinity).padding(.vertical, 7)
                    .background(RoundedRectangle(cornerRadius: 8).fill(sel == k ? Theme.accent : Theme.panel2))
            }
        }
    }
}

func readStr(_ p: [String: Any], _ keys: [String], _ def: String) -> String {
    for k in keys { if let v = p[k] as? String, !v.isEmpty { return v } }; return def
}
func readBool(_ p: [String: Any], _ keys: [String], _ def: Bool) -> Bool {
    for k in keys { if let v = p[k] as? Bool { return v }; if let n = p[k] as? NSNumber { return n.boolValue } }; return def
}
func readDbl(_ p: [String: Any], _ keys: [String], _ def: Double) -> Double {
    for k in keys {
        if let v = p[k] as? Double { return v }; if let v = p[k] as? Int { return Double(v) }
        if let v = p[k] as? NSNumber { return v.doubleValue }; if let s = p[k] as? String, let d = Double(s) { return d }
    }; return def
}
