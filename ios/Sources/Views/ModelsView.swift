import SwiftUI

// 模型与密钥(BYOK)—— 对齐 web MobileSettings「模型」:provider 列表 + 自配 API key/base_url/代理 + 测试/删除。
struct ModelsView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss

    @State private var providers: [ProviderAPI] = []
    @State private var creds: [String: CredItem] = [:]
    @State private var loading = true
    @State private var detail: ProviderRef?
    @State private var showAdd = false
    @State private var error: String?

    // 把自定义凭据(不在 catalog)也作为行
    struct ProviderRef: Identifiable { let id: String; let title: String; let baseURL: String; let models: [ModelEntry]; var isCustom: Bool = false }

    private func configured(_ id: String) -> Bool { creds[id]?.configured ?? false }

    /// 主列表:只显示「已配置 key」的(用户要求:没 key 的不显示,放进添加流程当选项)。
    private var rows: [ProviderRef] {
        var out: [ProviderRef] = providers
            .filter { configured($0.id) }
            .map { ProviderRef(id: $0.id, title: $0.title, baseURL: $0.baseUrl ?? "", models: $0.list) }
        let known = Set(providers.map { $0.id })
        for (cid, c) in creds where !known.contains(cid) && c.configured {
            out.append(ProviderRef(id: cid, title: cid, baseURL: c.baseURL, models: [], isCustom: true))
        }
        return out
    }
    /// 添加流程的候选:catalog 里尚未配置 key 的服务商。
    private var supported: [ProviderRef] {
        providers.filter { !configured($0.id) }
            .map { ProviderRef(id: $0.id, title: $0.title, baseURL: $0.baseUrl ?? "", models: $0.list) }
    }

    var body: some View {
        NavigationStack {
            ZStack {
                WarmBackground()
                if loading && providers.isEmpty {
                    ProgressView().tint(Theme.accent)
                } else if let error, providers.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "exclamationmark.triangle").font(.system(size: 36)).foregroundStyle(Theme.muted2)
                        Text(error).font(Theme.ui(13)).foregroundStyle(Theme.danger).multilineTextAlignment(.center)
                        Button("重试") { Task { await reload() } }.font(Theme.ui(14, .semibold)).foregroundStyle(Theme.accent)
                    }.padding(32)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("为各供应商填入你自己的 API Key(BYOK)。Key 直接保存到所连服务器,本机不留存明文。")
                                .font(Theme.ui(12)).foregroundStyle(Theme.muted).padding(.horizontal, 4).padding(.bottom, 2)
                            if rows.isEmpty {
                                VStack(spacing: 8) {
                                    Image(systemName: "key.horizontal").font(.system(size: 34)).foregroundStyle(Theme.muted2)
                                    Text(loc: "还没有配置任何 API").font(Theme.ui(14, .medium)).foregroundStyle(Theme.textQuiet)
                                    Text(loc: "点下方「添加 API」,选择服务商或自定义中转站。").font(Theme.ui(12)).foregroundStyle(Theme.muted)
                                }.frame(maxWidth: .infinity).padding(.vertical, 28)
                            } else {
                                ForEach(Array(rows.enumerated()), id: \.element.id) { i, p in
                                    Button { detail = p } label: { row(p) }.buttonStyle(PressableStyle())
                                        .transition(.move(edge: .top).combined(with: .opacity))
                                }
                            }
                            addButton
                        }.padding(16).padding(.bottom, 24)
                        .animation(.spring(response: 0.4, dampingFraction: 0.82), value: rows.count)
                    }
                }
            }
            .navigationTitle("模型与密钥").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() }.foregroundStyle(Theme.accent) } }
            .task {
                await reload()
                if ProcessInfo.processInfo.environment["STELLATRIX_OPEN_PROVIDER"] == "1" { detail = rows.first }
                if ProcessInfo.processInfo.environment["STELLATRIX_OPEN_ADD"] == "1" { showAdd = true }
            }
            .sheet(item: $detail) { p in
                ProviderDetailView(provider: p, cred: creds[p.id]) { Task { await reload() } }
                    .environmentObject(store)
            }
            .sheet(isPresented: $showAdd) {
                AddAPIView(supported: supported,
                           onPick: { ref in showAdd = false; DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { detail = ref } },
                           onChanged: { Task { await reload() } })
                    .environmentObject(store)
            }
        }
    }

    private var addButton: some View {
        Button { showAdd = true } label: {
            HStack(spacing: 8) {
                Image(systemName: "plus.circle.fill").font(.system(size: 17))
                Text(loc: "添加 API").font(Theme.ui(15, .semibold))
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.accent.opacity(0.6))
            }
            .foregroundStyle(Theme.accent).padding(.horizontal, 14).padding(.vertical, 14)
            .background(RoundedRectangle(cornerRadius: 14).fill(Theme.accentSoft))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.accentEdge, lineWidth: 1))
        }.buttonStyle(PressableStyle()).padding(.top, 4)   // demo 也可打开(查看流程),保存在内部按 demo 守卫
    }

    private func row(_ p: ProviderRef) -> some View {
        let c = creds[p.id]
        let configured = c?.configured ?? false
        return HStack(spacing: 12) {
            ZStack {
                Circle().fill(configured ? Theme.accentSoft : Theme.panel2)
                Image(systemName: configured ? "key.fill" : "key").font(.system(size: 14)).foregroundStyle(configured ? Theme.accent : Theme.muted)
            }.frame(width: 38, height: 38)
            VStack(alignment: .leading, spacing: 3) {
                Text(p.title).font(Theme.ui(15, .medium)).foregroundStyle(Theme.text)
                HStack(spacing: 6) {
                    if configured {
                        Text("已配置" + (c?.key_hint.map { " ••••\($0)" } ?? "")).font(Theme.ui(11)).foregroundStyle(Color(red: 0.45, green: 0.72, blue: 0.5))
                    } else {
                        Text("未配置").font(Theme.ui(11)).foregroundStyle(Theme.muted2)
                    }
                    if !p.models.isEmpty { Text("· \(p.models.count) 模型").font(Theme.ui(11)).foregroundStyle(Theme.muted2) }
                }
            }
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.muted2)
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.line, lineWidth: 1))
        .contentShape(Rectangle())
    }

    private func reload() async {
        loading = true
        defer { loading = false }
        if store.demo {
            providers = []   // 演示无真 provider 列表;用 picker 的演示数据近似
            providers = DemoData.providers.map { dp in
                ProviderAPI(apiId: dp.id, idField: dp.id, name: dp.title, displayName: dp.title,
                            enabled: true, baseUrl: "https://api.\(dp.id).com",
                            keyHint: dp.id == "anthropic" ? "a1b2" : nil,
                            models: dp.models.map { ModelEntry(realName: $0.id, idField: $0.id, modelId: $0.id, label: $0.display, capabilities: nil, caps: nil, enabled: true, hidden: false) },
                            entries: nil)
            }
            creds = ["anthropic": CredItem(api_id: "anthropic", enabled: true, base_url_override: "", key_hint: "a1b2", proxy_url: "", has_key: true, has_credential: true)]
            return
        }
        error = nil
        do {
            providers = try await store.api.models(base: store.serverURL).providers
        } catch {
            self.error = (error as? LocalizedError)?.errorDescription ?? "加载失败,请检查网络或服务器"
        }
        let list = (try? await store.api.credentialsList(base: store.serverURL)) ?? []
        creds = Dictionary(uniqueKeysWithValues: list.map { ($0.id, $0) })
    }
}

// provider 详情:配置/测试/删除 key
struct ProviderDetailView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let provider: ModelsView.ProviderRef
    let cred: CredItem?
    var onChanged: () -> Void

    @State private var apiKey = ""
    @State private var baseURL = ""
    @State private var proxy = ""
    @State private var showAdvanced = false
    @State private var saving = false
    @State private var testing = false
    @State private var testMsg: String?
    @State private var testOK = false
    @State private var err: String?
    @State private var showDeleteKey = false
    @State private var hiddenModels: Set<String> = []
    @State private var modelErr: String?
    @State private var didInitHidden = false

    var body: some View {
        NavigationStack {
            ZStack {
                WarmBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        infoCard
                        keyCard
                        if !provider.models.isEmpty { modelsCard }
                    }.padding(16).padding(.bottom, 24)
                }
            }
            .navigationTitle(provider.title).navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() }.foregroundStyle(Theme.accent) } }
            .onAppear {
                baseURL = cred?.baseURL ?? ""; proxy = cred?.proxy ?? ""; showAdvanced = !(cred?.proxy.isEmpty ?? true)
                if !didInitHidden { didInitHidden = true; hiddenModels = Set(provider.models.filter { !$0.visible }.map { $0.id }) }
            }
        }
    }

    private var infoCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            kv("供应商", provider.id)
            if let h = cred?.key_hint, !h.isEmpty { kv("当前 Key", "••••\(h)") }
            else { kv("当前 Key", cred?.configured == true ? "已配置" : "未配置") }
            if !provider.baseURL.isEmpty { kv("默认 Base URL", provider.baseURL) }
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.line, lineWidth: 1))
    }

    private var keyCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("配置 API Key").font(Theme.ui(11, .semibold)).foregroundStyle(Theme.accent).tracking(1.2)
            field("API Key", text: $apiKey, placeholder: cred?.configured == true ? "留空则保留现有" : "粘贴你的 key", secure: true)
            field("Base URL(可选)", text: $baseURL, placeholder: "中转站/自建端点,留空用默认")
            Button { withAnimation { showAdvanced.toggle() } } label: {
                HStack(spacing: 4) {
                    Text(loc: showAdvanced ? "隐藏高级" : "高级(代理)").font(Theme.ui(12)).foregroundStyle(Theme.muted)
                    Image(systemName: showAdvanced ? "chevron.up" : "chevron.down").font(.system(size: 9)).foregroundStyle(Theme.muted2)
                }
            }
            if showAdvanced { field("出站代理(可选)", text: $proxy, placeholder: "仅本地/自托管生效") }

            if store.demo {
                Text("演示模式不可配置;登录后可填入你自己的 Key。").font(Theme.ui(11.5)).foregroundStyle(Theme.muted2)
            }
            if let err { Text(err).font(Theme.ui(12)).foregroundStyle(Theme.danger) }
            if let testMsg { Text(testMsg).font(Theme.ui(12)).foregroundStyle(testOK ? Color(red: 0.45, green: 0.72, blue: 0.5) : Theme.danger) }

            HStack(spacing: 10) {
                Button { Task { await save() } } label: {
                    HStack(spacing: 5) { if saving { ProgressView().tint(Theme.onAccent).scaleEffect(0.7) }
                        Text(loc: saving ? "保存中…" : "保存").font(Theme.ui(14, .semibold)) }
                    .foregroundStyle(Theme.onAccent).frame(maxWidth: .infinity).padding(.vertical, 11).background(Theme.accent, in: Capsule())
                }.disabled(saving || store.demo)
                Button { Task { await test() } } label: {
                    HStack(spacing: 5) { if testing { ProgressView().tint(Theme.accent).scaleEffect(0.7) }
                        Text("测试").font(Theme.ui(14, .medium)) }
                    .foregroundStyle(Theme.text).frame(maxWidth: .infinity).padding(.vertical, 11)
                    .background(Theme.panel2, in: Capsule()).overlay(Capsule().stroke(Theme.line, lineWidth: 1))
                }.disabled(testing || store.demo || !(cred?.configured ?? false))
            }
            if cred?.configured ?? false {
                Button(role: .destructive) { showDeleteKey = true } label: {
                    Text("删除密钥").font(Theme.ui(13)).foregroundStyle(Theme.danger).frame(maxWidth: .infinity).padding(.vertical, 8)
                }.disabled(store.demo)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.line, lineWidth: 1))
        .confirmationDialog("删除「\(provider.title)」的 API Key?", isPresented: $showDeleteKey, titleVisibility: .visible) {
            Button("删除密钥", role: .destructive) { Task { await remove() } }
            Button("取消", role: .cancel) {}
        }
    }

    private var modelsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("模型(\(provider.models.count))").font(Theme.ui(11, .semibold)).foregroundStyle(Theme.accent).tracking(1.2)
                Spacer()
                Text(loc: "精选").font(Theme.ui(10.5)).foregroundStyle(Theme.muted2)
            }
            ForEach(provider.models) { m in
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(m.display).font(Theme.ui(13.5)).foregroundStyle(hiddenModels.contains(m.id) ? Theme.muted2 : Theme.text)
                        Text(m.id).font(Theme.ui(10.5).monospaced()).foregroundStyle(Theme.muted2).lineLimit(1)
                    }
                    Spacer()
                    Toggle("", isOn: Binding(
                        get: { !hiddenModels.contains(m.id) },
                        set: { on in toggleModel(m, visible: on) }
                    )).labelsHidden().tint(Theme.accent).disabled(store.demo)
                }.padding(.vertical, 2)
            }
            if let modelErr { Text(modelErr).font(Theme.ui(11.5)).foregroundStyle(Theme.danger) }
            Text("关掉的模型不会出现在模型选择器里(仅影响你自己,不动其他人或全局目录)。")
                .font(Theme.ui(11)).foregroundStyle(Theme.muted2)
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.line, lineWidth: 1))
    }

    // picker 安全:不允许把本 provider 最后一个可见模型也关掉;乐观更新+失败回滚。
    private func toggleModel(_ m: ModelEntry, visible: Bool) {
        if store.demo { return }
        if !visible {
            let remaining = provider.models.filter { !hiddenModels.contains($0.id) && $0.id != m.id }
            if remaining.isEmpty { modelErr = "至少保留一个可见模型"; return }
        }
        modelErr = nil
        let prev = hiddenModels
        if visible { hiddenModels.remove(m.id) } else { hiddenModels.insert(m.id) }
        Task {
            do {
                try await store.api.setModelVisibility(base: store.serverURL, apiId: provider.id, model: m.id, visible: visible)
                onChanged()
            } catch {
                hiddenModels = prev
                modelErr = (error as? LocalizedError)?.errorDescription ?? "更新失败"
            }
        }
    }

    private func kv(_ k: String, _ v: String) -> some View {
        HStack(alignment: .top) {
            Text(k).font(Theme.ui(12.5)).foregroundStyle(Theme.muted).frame(width: 88, alignment: .leading)
            Text(v).font(Theme.ui(13)).foregroundStyle(Theme.text).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
        }
    }
    @ViewBuilder private func field(_ label: String, text: Binding<String>, placeholder: String, secure: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(loc: label).font(Theme.ui(12.5)).foregroundStyle(Theme.muted)
            Group {
                if secure { SecureField(placeholder, text: text) }
                else { TextField(placeholder, text: text) }
            }
            .font(Theme.ui(13.5)).foregroundStyle(Theme.text).tint(Theme.accent)
            .autocorrectionDisabled().textInputAutocapitalization(.never)
            .padding(.horizontal, 10).padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 8).fill(Theme.panel2))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.line, lineWidth: 1))
        }
    }

    private func save() async {
        err = nil; testMsg = nil
        let key = apiKey.trimmingCharacters(in: .whitespaces)
        // Key 框留空:已配置的供应商 = 保留现有密钥、只改地址 / 代理(必须带 keep_key,
        // 否则后端把空 key 当成删除);还没配置的供应商没有可保留的密钥,直接提示先填。
        let hasStoredKey = cred?.configured == true
        if key.isEmpty && !hasStoredKey { err = tr("请先填写 API Key"); return }
        saving = true
        defer { saving = false }
        do {
            try await store.api.setCredential(base: store.serverURL, apiId: provider.id,
                key: key,
                baseURL: baseURL.trimmingCharacters(in: .whitespaces),
                proxy: proxy.trimmingCharacters(in: .whitespaces),
                keepKey: key.isEmpty && hasStoredKey)
            apiKey = ""
            onChanged()
            testMsg = "已保存"; testOK = true
        } catch { err = (error as? LocalizedError)?.errorDescription ?? "保存失败" }
    }
    private func test() async {
        err = nil; testMsg = nil; testing = true
        defer { testing = false }
        do { let r = try await store.api.testCredential(base: store.serverURL, apiId: provider.id)
            testMsg = r.message; testOK = r.ok
        } catch { testMsg = (error as? LocalizedError)?.errorDescription ?? "测试失败"; testOK = false }
    }
    private func remove() async {
        err = nil
        do { try await store.api.deleteCredential(base: store.serverURL, apiId: provider.id); onChanged(); dismiss() }
        catch { err = (error as? LocalizedError)?.errorDescription ?? "删除失败" }
    }
}

// 添加 API —— 选支持的服务商(尚未配 key)当选项,或自定义中转站 API。
struct AddAPIView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let supported: [ModelsView.ProviderRef]
    var onPick: (ModelsView.ProviderRef) -> Void
    var onChanged: () -> Void

    @State private var customOpen = false
    @State private var name = ""
    @State private var baseURL = ""
    @State private var apiKey = ""
    @State private var saving = false
    @State private var err: String?
    @State private var appeared = false
    @State private var testMsg: String?
    @State private var testOK = false
    @State private var done = false

    var body: some View {
        NavigationStack {
            ZStack {
                WarmBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        customSection
                        if !supported.isEmpty {
                            VStack(alignment: .leading, spacing: 9) {
                                Text(loc: "支持的服务商").font(Theme.ui(11, .semibold)).foregroundStyle(Theme.accent).tracking(1.2)
                                VStack(spacing: 8) {
                                    ForEach(Array(supported.enumerated()), id: \.element.id) { i, p in
                                        Button { onPick(p); dismiss() } label: { providerRow(p) }
                                            .buttonStyle(PressableStyle())
                                            .opacity(appeared ? 1 : 0)
                                            .offset(y: appeared ? 0 : 12)
                                            .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(Double(i) * 0.04), value: appeared)
                                    }
                                }
                            }
                        }
                    }.padding(16).padding(.bottom, 24)
                }
            }
            .navigationTitle("添加 API").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.foregroundStyle(Theme.textQuiet) } }
            .onAppear { appeared = true }
        }
    }

    private var customSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button { withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) { customOpen.toggle() } } label: {
                HStack(spacing: 10) {
                    ZStack { RoundedRectangle(cornerRadius: 11).fill(Theme.accentSoft)
                        Image(systemName: "wand.and.stars").font(.system(size: 16)).foregroundStyle(Theme.accent) }.frame(width: 42, height: 42)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(loc: "自定义 API / 中转站").font(Theme.ui(15, .medium)).foregroundStyle(Theme.text)
                        Text(loc: "填入兼容 OpenAI 的中转站地址 + Key").font(Theme.ui(11.5)).foregroundStyle(Theme.muted)
                    }
                    Spacer()
                    Image(systemName: customOpen ? "chevron.up" : "chevron.down").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.muted2)
                }
            }.buttonStyle(PressableStyle())
            if customOpen {
                field("名称", $name, "如:我的中转站")
                field("Base URL", $baseURL, "https://中转站/v1")
                field("API Key", $apiKey, "粘贴你的 key", secure: true)
                if let err { Text(err).font(Theme.ui(12)).foregroundStyle(Theme.danger) }
                if let testMsg { HStack(spacing: 5) {
                    if saving { ProgressView().tint(Theme.accent).scaleEffect(0.7) }
                    Text(testMsg).font(Theme.ui(12.5)).foregroundStyle(testOK ? Color(red: 0.45, green: 0.72, blue: 0.5) : Theme.danger)
                } }
                if done {
                    Button { dismiss() } label: {
                        Text(loc: "完成").font(Theme.ui(14.5, .semibold)).foregroundStyle(Theme.onAccent)
                            .frame(maxWidth: .infinity).padding(.vertical, 12).background(Theme.accent, in: Capsule())
                    }
                } else {
                    Button { Task { await saveCustom() } } label: {
                        HStack(spacing: 5) { if saving { ProgressView().tint(Theme.onAccent).scaleEffect(0.7) }
                            Text(loc: saving ? "添加并测试中…" : "添加并测试连接").font(Theme.ui(14.5, .semibold)) }
                        .foregroundStyle(Theme.onAccent).frame(maxWidth: .infinity).padding(.vertical, 12)
                        .background(canSave ? Theme.accent : Theme.panel3, in: Capsule())
                    }.disabled(!canSave || saving)
                }
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(customOpen ? Theme.accentEdge : Theme.line, lineWidth: 1))
    }
    private func providerRow(_ p: ModelsView.ProviderRef) -> some View {
        HStack(spacing: 12) {
            ZStack { Circle().fill(Theme.panel2); Image(systemName: "key").font(.system(size: 13)).foregroundStyle(Theme.muted) }.frame(width: 36, height: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(p.title).font(Theme.ui(14.5, .medium)).foregroundStyle(Theme.text)
                if !p.models.isEmpty { Text("\(p.models.count) 模型").font(Theme.ui(11)).foregroundStyle(Theme.muted2) }
            }
            Spacer()
            Image(systemName: "plus").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.accent)
        }
        .padding(.horizontal, 13).padding(.vertical, 11)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel)).overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.line, lineWidth: 1))
        .contentShape(Rectangle())
    }
    private func field(_ label: String, _ t: Binding<String>, _ ph: String, secure: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(loc: label).font(Theme.ui(12.5)).foregroundStyle(Theme.muted)
            Group { if secure { SecureField(ph, text: t) } else { TextField(ph, text: t) } }
                .font(Theme.ui(13.5)).foregroundStyle(Theme.text).tint(Theme.accent).autocorrectionDisabled().textInputAutocapitalization(.never)
                .padding(.horizontal, 10).padding(.vertical, 9)
                .background(RoundedRectangle(cornerRadius: 8).fill(Theme.panel2)).overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.line, lineWidth: 1))
        }
    }
    private var canSave: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty && baseURL.contains("http") && !apiKey.trimmingCharacters(in: .whitespaces).isEmpty
    }
    private func saveCustom() async {
        err = nil; testMsg = nil; saving = true; defer { saving = false }
        if store.demo { err = "演示模式不可配置;登录后可添加自定义 API。"; return }
        let apiId = name.trimmingCharacters(in: .whitespaces)
        do {
            try await store.api.setCredential(base: store.serverURL, apiId: apiId,
                key: apiKey.trimmingCharacters(in: .whitespaces),
                baseURL: baseURL.trimmingCharacters(in: .whitespaces), proxy: "")
            // 通达测试 + 延迟(对齐网页:加完即测连接,显示 ms)
            testMsg = tr("正在测试连接…")
            let r = try await store.api.testCredential(base: store.serverURL, apiId: apiId)
            testMsg = r.message; testOK = r.ok
            onChanged(); done = true
        } catch { err = (error as? LocalizedError)?.errorDescription ?? "添加失败" }
    }
}
