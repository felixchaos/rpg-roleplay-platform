import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

struct ChatView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let chatId: Int

    @State private var messages: [ChatMessage] = []
    @State private var input = ""
    @State private var attachments: [ChatAttachment] = []
    @State private var photoItem: PhotosPickerItem?
    @State private var showPhotoPicker = false
    @State private var showFileImporter = false
    @State private var showGenImage = false
    @State private var loading = true
    @State private var sending = false
    @State private var stage: String?
    @State private var immersive = false
    @State private var charName: String?
    @State private var scene: String?
    @State private var error: String?
    @State private var showMenu = false
    @State private var streamTask: Task<Void, Never>?
    @FocusState private var inputFocused: Bool
    // 模型
    @State private var providers: [PickerProvider] = []
    @State private var modelId = ""
    @State private var modelDisplay = ""
    @State private var showModel = false
    // 抽屉 / 权限 / 斜杠
    @State private var character: TavernCharacter?
    @State private var persona: PlayerState?
    @State private var systemPrompt = ""
    @State private var permission = "full_access"
    @State private var showDrawer = false
    @State private var showPerm = false
    @State private var showSlash = false
    @State private var shareItem: ShareItem?

    struct ShareItem: Identifiable { let id = UUID(); let url: URL }

    private var name: String { charName ?? "对话" }

    var body: some View {
        ZStack {
            WarmBackground()
            VStack(spacing: 0) {
                topBar
                thread
                composer
            }
        }
        .toolbar(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            await load()
            if ProcessInfo.processInfo.environment["STELLATRIX_TDRAWER"] == "1" { showDrawer = true }
        }
        .onDisappear { streamTask?.cancel() }
        .sheet(isPresented: $showMenu) { menuSheet }
        .sheet(isPresented: $showModel) {
            ModelPickerView(providers: providers, currentId: modelId) { pick in selectModel(pick) }
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showDrawer) {
            TavernDrawer(charName: charName, character: character, persona: persona,
                         immersive: $immersive, onToggleImmersive: { setImmersive($0) },
                         initialSystemPrompt: systemPrompt,
                         onSaveSystemPrompt: { saveSystemPrompt($0) }, canEdit: !store.demo,
                         serverBase: store.serverURL,
                         onBindCard: { role, cid in bindCard(role, cid) })
        }
        .sheet(item: $shareItem) { item in ActivityView(items: [item.url]) }
        .sheet(isPresented: $showPerm) { permSheet }
        .sheet(isPresented: $showSlash) { slashSheet }
        .sheet(isPresented: $showGenImage) { GenImageSheet { prompt, size in generateImage(prompt, size) } }
        .photosPicker(isPresented: $showPhotoPicker, selection: $photoItem, matching: .images)
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            Task { if let a = await AttachLoader.fromPhoto(item) { attachments.append(a) }; photoItem = nil }
        }
        .fileImporter(isPresented: $showFileImporter, allowedContentTypes: [.item], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first, let a = AttachLoader.fromFileURL(url) { attachments.append(a) }
        }
    }

    // MARK: 顶栏

    private var topBar: some View {
        HStack(spacing: 6) {
            Button { dismiss() } label: {
                HStack(spacing: 2) {
                    Image(systemName: "chevron.left").font(.system(size: 18, weight: .medium))
                    Text("游戏").font(Theme.ui(14, .medium))
                }.foregroundStyle(Theme.textQuiet).frame(height: 38).padding(.trailing, 4)
            }.accessibilityLabel(Text("返回"))
            Spacer()
            VStack(spacing: 2) {
                Text(name).font(Theme.serif(16.5, .medium)).foregroundStyle(Theme.text)
                HStack(spacing: 5) {
                    if !store.demo || charName != nil { PresenceDot() }
                    Text(statusLine).font(Theme.ui(10.5)).foregroundStyle(Theme.muted)
                }
            }
            Spacer()
            Button { showDrawer = true } label: {
                Image(systemName: "person.text.rectangle").font(.system(size: 16)).foregroundStyle(Theme.textQuiet)
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }.accessibilityLabel(Text("角色信息"))
        }
        .padding(.horizontal, 10).padding(.bottom, 10)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.lineSoft).frame(height: 1) }
    }

    private var statusLine: String {
        if immersive { return "沉浸中 · 在场" }
        return charName == nil ? "空白对话 · 即兴扮演" : "在场"
    }

    // MARK: 消息流

    private var thread: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    if loading { ProgressView().tint(Theme.accent).frame(maxWidth: .infinity).padding(.top, 40) }
                    if let scene, !messages.isEmpty { sceneDivider(scene) }
                    if messages.isEmpty && !loading { emptyHint }
                    ForEach(messages) { msg in
                        bubble(msg).id(msg.id)
                            .contextMenu {
                                Button { copyMessage(msg) } label: { Label("复制", systemImage: "doc.on.doc") }
                                if msg.id == lastAssistantId && !sending {
                                    Button { regenerate() } label: { Label("重新生成", systemImage: "arrow.clockwise") }
                                }
                            }
                    }
                    if sending {
                        HStack(spacing: 6) {
                            TypingDots()
                            Text(loc: stage ?? "正在落笔…").font(Theme.ui(12.5)).foregroundStyle(Theme.muted)
                        }.id("typing")
                    }
                    if let error {
                        Text(error).font(Theme.ui(13)).foregroundStyle(Theme.danger)
                            .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color(0xc8675d, 0.12), in: RoundedRectangle(cornerRadius: 10)).id("error")
                    }
                    Color.clear.frame(height: 6).id("bottom")
                }
                .frame(maxWidth: 720).frame(maxWidth: .infinity)
                .padding(.horizontal, 20).padding(.top, 18)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: messages.last?.content) { _, _ in down(proxy) }
            .onChange(of: messages.count) { _, _ in down(proxy) }
            .onChange(of: sending) { _, _ in down(proxy) }
        }
    }

    private func down(_ p: ScrollViewProxy) { withAnimation(.easeOut(duration: 0.18)) { p.scrollTo("bottom", anchor: .bottom) } }

    private func sceneDivider(_ s: String) -> some View {
        HStack(spacing: 10) {
            Rectangle().fill(Theme.line).frame(width: 26, height: 1)
            Text(s).font(Theme.ui(11)).foregroundStyle(Theme.muted2).tracking(2)
            Rectangle().fill(Theme.line).frame(width: 26, height: 1)
        }.frame(maxWidth: .infinity).padding(.bottom, 2)
    }

    @ViewBuilder
    private func bubble(_ msg: ChatMessage) -> some View {
        if msg.role == .user {
            HStack { Spacer(minLength: 48)
                VStack(alignment: .trailing, spacing: 6) {
                    if let t = msg.attachThumbs { AttachThumbsView(thumbs: t) }
                    Text(msg.content).font(Theme.ui(15)).foregroundStyle(Theme.text).textSelection(.enabled)
                        .padding(.horizontal, 15).padding(.vertical, 11)
                        .background(Theme.accentSoft, in: UnevenRoundedRectangle(topLeadingRadius: 18, bottomLeadingRadius: 18, bottomTrailingRadius: 6, topTrailingRadius: 18))
                        .overlay(UnevenRoundedRectangle(topLeadingRadius: 18, bottomLeadingRadius: 18, bottomTrailingRadius: 6, topTrailingRadius: 18).stroke(Theme.accentEdge, lineWidth: 0.5))
                }
            }
        } else if msg.generating || msg.imageURL != nil {
            VStack(alignment: .leading, spacing: 8) {
                Text("生图").font(Theme.ui(10, .semibold)).foregroundStyle(Theme.accent).tracking(1.5)
                if !msg.content.isEmpty { Text(msg.content).font(Theme.ui(12.5)).italic().foregroundStyle(Theme.muted).lineLimit(3) }
                if msg.generating {
                    HStack(spacing: 8) { ProgressView().tint(Theme.accent).scaleEffect(0.8); Text("生成中…").font(Theme.ui(12.5)).foregroundStyle(Theme.muted) }
                        .frame(width: 200, height: 120).background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel2))
                } else if let url = msg.imageURL {
                    ServerImageView(base: store.serverURL, path: url)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        } else {
            VStack(alignment: .leading, spacing: 7) {
                if let n = charName {
                    Text(n).font(Theme.ui(11, .medium)).tracking(1.5).foregroundStyle(Theme.accent.opacity(0.9))
                }
                Text(msg.content.isEmpty && msg.streaming ? "…" : msg.content)
                    .font(Theme.serif(16.5)).foregroundStyle(Theme.textQuiet)
                    .lineSpacing(7).textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var emptyHint: some View {
        VStack(spacing: 10) {
            Image(systemName: "quote.opening").font(.system(size: 28)).foregroundStyle(Theme.muted2)
            Text("对话尚未开始").font(Theme.serif(17)).foregroundStyle(Theme.textQuiet)
            Text("发条消息,开始你们的故事。").font(Theme.ui(13)).foregroundStyle(Theme.muted)
        }.frame(maxWidth: .infinity).padding(.top, 64)
    }

    // MARK: 输入框

    private var composer: some View {
        VStack(spacing: 8) {
            if !attachments.isEmpty {
                AttachChipsStrip(attachments: attachments) { a in attachments.removeAll { $0.id == a.id } }
            }
            HStack(alignment: .bottom, spacing: 8) {
                Button { showMenu = true } label: {
                    Image(systemName: "plus").font(.system(size: 19)).foregroundStyle(Theme.muted)
                        .frame(width: 34, height: 34)
                }
                TextField(charName.map { "给 \($0) 写点什么…" } ?? "写点什么…", text: $input, axis: .vertical)
                    .font(Theme.ui(15)).foregroundStyle(Theme.text).tint(Theme.accent)
                    .lineLimit(1...6).focused($inputFocused).padding(.vertical, 6)
                Button(action: sending ? stop : send) {
                    Image(systemName: sending ? "stop.fill" : "arrow.up").font(.system(size: 15, weight: .bold))
                        .foregroundStyle(Theme.onAccent).frame(width: 34, height: 34)
                        .background(Circle().fill((canSend || sending) ? Theme.accent : Theme.muted2))
                }.disabled(!canSend && !sending)
            }
            // 底部 chip 行(对齐网页输入框):模型 / 斜杠 / 权限
            HStack(spacing: 7) {
                Button { showModel = true } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "sparkles").font(.system(size: 11))
                        Text(loc: modelDisplay.isEmpty ? "模型" : modelDisplay).font(Theme.ui(12)).lineLimit(1)
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold))
                    }
                    .foregroundStyle(Theme.muted).padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Theme.panel2, in: Capsule()).overlay(Capsule().stroke(Theme.lineSoft, lineWidth: 1))
                }
                iconChip("slash.circle") { showSlash = true }
                iconChip(permIcon) { showPerm = true }
                Spacer()
            }
        }
        .padding(.horizontal, 8).padding(.top, 7).padding(.bottom, 8)
        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 22))
        .overlay(RoundedRectangle(cornerRadius: 22).stroke(inputFocused ? Theme.accentEdge : Theme.line, lineWidth: 1))
        .frame(maxWidth: 720).frame(maxWidth: .infinity)
        .padding(.horizontal, 14).padding(.top, 8).padding(.bottom, 12)
    }

    private var canSend: Bool { (!input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty) && !sending }

    // MARK: 菜单

    private var menuSheet: some View {
        VStack(spacing: 4) {
            Capsule().fill(Theme.lineStrong).frame(width: 38, height: 4).padding(.top, 10).padding(.bottom, 8)
            menuRow(icon: "sparkles", title: "AI 帮回", sub: "以你的角色生成一条回复填入输入框") { EmptyView() }
                .contentShape(Rectangle()).onTapGesture { aiReply() }
            menuRow(icon: "photo", title: "图片", sub: "从相册添加图片附件") { EmptyView() }
                .contentShape(Rectangle()).onTapGesture { showMenu = false; showPhotoPicker = true }
            menuRow(icon: "doc", title: "文件", sub: "添加文本/文件附件") { EmptyView() }
                .contentShape(Rectangle()).onTapGesture { showMenu = false; showFileImporter = true }
            menuRow(icon: "wand.and.stars", title: "生成图片", sub: "用生图模型为本场景出图") { EmptyView() }
                .contentShape(Rectangle()).onTapGesture { showMenu = false; showGenImage = true }
            menuRow(icon: "person.text.rectangle", title: "角色卡 / 我的角色 / 系统提示", sub: "查看与编辑") { EmptyView() }
                .contentShape(Rectangle()).onTapGesture { showMenu = false; showDrawer = true }
            menuRow(icon: "textformat.abc", title: "自动命名", sub: "让 AI 根据内容命名本对话") { EmptyView() }
                .contentShape(Rectangle()).onTapGesture { showMenu = false; autotitle() }
            menuRow(icon: "square.and.arrow.up", title: "导出对话(JSONL)", sub: "保存或分享本对话记录") { EmptyView() }
                .contentShape(Rectangle()).onTapGesture { showMenu = false; exportChat() }
            Spacer(minLength: 12)
        }
        .padding(.horizontal, 12).frame(maxWidth: .infinity, alignment: .leading)
        .presentationDetents([.height(500)]).presentationBackground(Theme.panel).presentationDragIndicator(.hidden)
    }

    // 权限 sheet
    private var permSheet: some View {
        let modes = [("full_access", "完全放行", "AI 直接写入状态", "lock.open"),
                     ("auto_review", "自动复核", "写入前自动校验", "eye"),
                     ("default", "需要确认", "写入需你确认", "checkmark.shield")]
        return VStack(spacing: 4) {
            Capsule().fill(Theme.lineStrong).frame(width: 38, height: 4).padding(.top, 10).padding(.bottom, 6)
            Text("写入权限").font(Theme.ui(12, .medium)).foregroundStyle(Theme.muted).frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 14)
            ForEach(modes, id: \.0) { m in
                Button { setPermission(m.0) } label: {
                    HStack(spacing: 12) {
                        Image(systemName: m.3).font(.system(size: 16)).foregroundStyle(Theme.accent).frame(width: 34)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(m.1).font(Theme.ui(14.5, .medium)).foregroundStyle(Theme.text)
                            Text(m.2).font(Theme.ui(11.5)).foregroundStyle(Theme.muted)
                        }
                        Spacer()
                        if permission == m.0 { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
                    }.padding(.horizontal, 14).padding(.vertical, 11).contentShape(Rectangle())
                }.buttonStyle(.plain)
            }
            Spacer(minLength: 12)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .presentationDetents([.height(280)]).presentationBackground(Theme.panel).presentationDragIndicator(.hidden)
    }

    // 斜杠命令 sheet(插入到输入框)
    private var slashSheet: some View {
        // 指令集对齐各端(状态写 + 记忆 + 模式);客户端命令(/status /debug /save)由 iOS 原生 UI 承担。
        let cmds = [("/set ", "改设定", "/set time=黄昏; location=旧城"),
                    ("/loc ", "改地点", "/loc <地点>"),
                    ("/time ", "改时间", "/time <时间>"),
                    ("/rel ", "改关系", "/rel <角色> <状态>"),
                    ("/var ", "设变量", "/var 变量=值"),
                    ("/pin ", "固定记忆", "/pin <文本>"),
                    ("/note ", "玩家笔记", "/note <文本>"),
                    ("/permission ", "权限模式", "/permission default|review|full_access"),
                    ("/retry", "重试本轮", "/retry")]
        return ScrollView {
            VStack(spacing: 0) {
                Capsule().fill(Theme.lineStrong).frame(width: 38, height: 4).padding(.top, 10).padding(.bottom, 8)
                ForEach(cmds, id: \.0) { c in
                    Button { input = c.0; showSlash = false; inputFocused = true } label: {
                        HStack(spacing: 12) {
                            Text(c.0.trimmingCharacters(in: .whitespaces)).font(Theme.ui(13.5, .medium)).foregroundStyle(Theme.accent).frame(width: 90, alignment: .leading).monospaced()
                            VStack(alignment: .leading, spacing: 1) {
                                Text(c.1).font(Theme.ui(14)).foregroundStyle(Theme.text)
                                Text(c.2).font(Theme.ui(11)).foregroundStyle(Theme.muted2).lineLimit(1)
                            }
                            Spacer()
                        }.padding(.horizontal, 14).padding(.vertical, 10).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                }
            }
        }
        .presentationDetents([.medium]).presentationBackground(Theme.panel).presentationDragIndicator(.hidden)
    }

    private func iconChip(_ icon: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 13)).foregroundStyle(Theme.muted)
                .frame(width: 30, height: 26).background(Theme.panel2, in: Capsule())
                .overlay(Capsule().stroke(Theme.lineSoft, lineWidth: 1))
        }
    }
    private var permIcon: String {
        permission == "full_access" ? "lock.open" : (permission == "auto_review" ? "eye" : "checkmark.shield")
    }
    private func setPermission(_ mode: String) {
        permission = mode; showPerm = false
        if store.demo { return }
        Task { try? await store.api.setPermission(base: store.serverURL, mode: mode) }
    }
    private func saveSystemPrompt(_ p: String) {
        systemPrompt = p
        if store.demo { return }
        Task { try? await store.api.setSystemPrompt(base: store.serverURL, id: chatId, prompt: p) }
    }

    private func menuRow<Trailing: View>(icon: String, title: String, sub: String, @ViewBuilder trailing: () -> Trailing) -> some View {
        HStack(spacing: 14) {
            Image(systemName: icon).font(.system(size: 17)).foregroundStyle(Theme.accent)
                .frame(width: 38, height: 38).background(Theme.panel2, in: RoundedRectangle(cornerRadius: 11))
            VStack(alignment: .leading, spacing: 2) {
                Text(loc: title).font(Theme.ui(14.5, .medium)).foregroundStyle(Theme.text)
                Text(sub).font(Theme.ui(12)).foregroundStyle(Theme.muted)
            }
            Spacer()
            trailing()
        }
        .padding(.horizontal, 12).padding(.vertical, 11)
    }

    // MARK: 逻辑

    private func load() async {
        loading = true; error = nil
        defer { loading = false }
        if store.demo {
            let s = DemoData.session(chatId)
            charName = s.name; scene = s.scene; messages = s.msgs
            character = DemoData.character(chatId); persona = DemoData.persona; systemPrompt = DemoData.systemPrompt
            providers = DemoData.providers; modelId = DemoData.selectedModelId; modelDisplay = DemoData.selectedModelDisplay
            return
        }
        do {
            try await store.api.tavernActivate(base: store.serverURL, id: chatId)
            let st = try await store.api.state(base: store.serverURL)
            messages = st.resolvedHistory.compactMap { e in
                guard let role = e.role, let content = e.content, !content.isEmpty else { return nil }
                return ChatMessage(role: role == "user" ? .user : .assistant, content: content)
            }
            let tav = st.resolvedTavern
            immersive = tav?.immersive ?? false
            charName = tav?.character?.name
            character = tav?.character
            persona = st.resolvedPlayer
            systemPrompt = tav?.system_prompt ?? ""
            permission = st.resolvedPermission
        } catch { self.error = (error as? LocalizedError)?.errorDescription ?? "加载失败" }
        // 模型目录(失败不阻断对话)
        if let m = try? await store.api.models(base: store.serverURL) {
            providers = m.pickerProviders
            if let sel = m.selected { modelId = sel.modelName; modelDisplay = sel.display }
        }
    }

    private func selectModel(_ pick: PickerModel) {
        modelId = pick.id; modelDisplay = pick.display
        if store.demo { return }
        Task { try? await store.api.selectModel(base: store.serverURL, apiId: pick.apiId, modelId: pick.id, saveId: chatId) }
    }

    private func copyMessage(_ m: ChatMessage) { UIPasteboard.general.string = m.content }

    private var lastAssistantId: UUID? {
        messages.last(where: { $0.role == .assistant && !$0.streaming })?.id
    }

    private func regenerate() {
        guard !sending else { return }
        // 取最后一条玩家输入,移除其后的回合(本地),重发
        guard let userText = messages.last(where: { $0.role == .user })?.content else { return }
        while let last = messages.last, last.role == .assistant { messages.removeLast() }
        if let last = messages.last, last.role == .user, last.content == userText { messages.removeLast() }
        input = userText; send()
    }

    private func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!text.isEmpty || !attachments.isEmpty), !sending else { return }
        let atts = attachments
        input = ""; attachments = []; error = nil; stage = nil
        let thumbs = atts.filter { $0.isImage }.map { $0.dataURL }
        messages.append(ChatMessage(role: .user, content: text.isEmpty ? "(附件)" : text, attachThumbs: thumbs.isEmpty ? nil : thumbs))
        let idx = messages.count
        messages.append(ChatMessage(role: .assistant, content: "", streaming: true))
        let msgId = messages[idx].id  // [round-4-P2] 稳定 id:收尾按 id 定位,防 regenerate 重排后 idx 误伤新消息
        sending = true
        streamTask = Task { @MainActor in
            let stream = store.demo ? DemoData.stream(text)
                                    : store.api.streamChat(base: store.serverURL, message: text, saveId: chatId, attachments: atts.map { $0.bodyDict })
            var raw = ""
            do {
                for try await ev in stream {
                    switch ev {
                    case .token(let t): raw += t; if idx < messages.count { messages[idx].content = cleanNarrative(raw) }
                    case .stage(let l): stage = l
                    case .usage: break
                    case .error(let m): error = m
                    case .done(let final): if let final, !final.isEmpty, idx < messages.count { messages[idx].content = final }
                    }
                }
            } catch is CancellationError {
            } catch { self.error = (error as? LocalizedError)?.errorDescription ?? "生成出错" }
            // [round-4-P2] 按 id 收尾,防 regenerate/重发重排后 idx 把别的消息标成非流式或删错。
            if let i = messages.firstIndex(where: { $0.id == msgId }) {
                messages[i].streaming = false
                messages[i].content = cleanNarrative(messages[i].content)
                if messages[i].content.isEmpty { messages.remove(at: i) }
            }
            sending = false; stage = nil
        }
    }

    private func stop() { streamTask?.cancel(); sending = false; stage = nil }

    private func generateImage(_ prompt: String, _ size: String) {
        messages.append(ChatMessage(role: .assistant, content: prompt, generating: true))
        let idx = messages.count - 1
        Task { @MainActor in
            if store.demo {
                try? await Task.sleep(nanoseconds: 1_200_000_000)
                if idx < messages.count { messages[idx].generating = false; messages[idx].content = "(演示模式不出真实图片,登录后配置生图模型即可)" }
                return
            }
            do {
                let id = try await store.api.generateImage(base: store.serverURL, prompt: prompt, saveId: chatId, size: size)
                let url = try await store.api.awaitImage(base: store.serverURL, id: id)
                if idx < messages.count { messages[idx].generating = false; messages[idx].imageURL = url; messages[idx].content = prompt }
            } catch {
                if idx < messages.count { messages[idx].generating = false; messages[idx].content = "生图失败:" + ((error as? LocalizedError)?.errorDescription ?? "未知错误") }
            }
        }
    }

    private func aiReply() {
        showMenu = false
        if store.demo {
            input = "我点点头,握紧了腰间的短刀。「一起。但丑话说前头——遇上麻烦,各自保命。」"
            inputFocused = true
            return
        }
        Task {
            do {
                let r = try await store.api.aiReply(base: store.serverURL, id: chatId)
                if !r.isEmpty { input = r; inputFocused = true }
            } catch {}
        }
    }

    private func setImmersive(_ on: Bool) {
        immersive = on
        if store.demo { return }
        Task {
            do { try await store.api.setImmersive(base: store.serverURL, id: chatId, enabled: on) }
            catch { immersive = !on }
        }
    }

    private func bindCard(_ role: String, _ cardId: Int) {
        if store.demo { return }
        Task {
            do { try await store.api.bindCard(base: store.serverURL, id: chatId, role: role, cardId: cardId); await load() }
            catch { self.error = (error as? LocalizedError)?.errorDescription ?? "更换角色卡失败" }
        }
    }

    private func autotitle() {
        if store.demo { return }
        Task { _ = try? await store.api.autotitle(base: store.serverURL, id: chatId) }
    }

    private func exportChat() {
        if store.demo { return }
        Task {
            do { let url = try await store.api.exportJsonl(base: store.serverURL, id: chatId); shareItem = ShareItem(url: url) }
            catch { self.error = (error as? LocalizedError)?.errorDescription ?? "导出失败" }
        }
    }
}

// UIActivityViewController 包装(分享导出的文件 / 文本)。
struct ActivityView: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

struct TypingDots: View {
    @State private var phase = 0
    let timer = Timer.publish(every: 0.35, on: .main, in: .common).autoconnect()
    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3) { i in
                Circle().fill(Theme.accent).frame(width: 6, height: 6).opacity(phase == i ? 1 : 0.3)
            }
        }.onReceive(timer) { _ in phase = (phase + 1) % 3 }
    }
}
