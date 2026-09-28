import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

// 游戏台 —— 剧情角色扮演的核心对话(对齐 web MobileGame)。
// 全屏呈现:顶栏 + GM 散文/玩家消息 + 富输入框(附件+生图)+ 右侧世界面板。
struct GameConsoleView: View {
    @EnvironmentObject var store: AppStore
    let launch: GameLaunch

    @State private var snap = GameSnapshot.empty
    @State private var messages: [ChatMessage] = []
    @State private var attachments: [ChatAttachment] = []
    @State private var photoItem: PhotosPickerItem?
    @State private var showPhotoPicker = false
    @State private var showFileImporter = false
    @State private var showGenImage = false
    @State private var loading = true
    @State private var text = ""
    @State private var running = false
    @State private var runLabel = "正在生成"
    @State private var contextPct = 0
    @State private var permission = "full_access"
    @State private var modelLabel = "模型"
    @State private var error: String?
    @State private var needsOpening = false

    @State private var showPanels = false
    @State private var showLeftDrawer = false
    @State private var sheet: Sheet?
    @State private var providers: [PickerProvider] = []
    @State private var currentModelId = ""
    @State private var savesList: [SaveItem] = []
    @State private var pendingExpanded = true
    @State private var rollbackTarget: Int?
    @State private var peekExpanded = false
    @State private var runStart = Date()

    @State private var streamTask: Task<Void, Never>?
    @State private var answering = false   // [round-3-P2] answerChoice 同步重入闸
    @State private var didInitLargePanels = false   // 大屏:世界面板默认展开,只设一次
    @Environment(\.horizontalSizeClass) private var hsc   // 大屏适配:regular=iPad/Mac 持久侧栏面板
    @FocusState private var inputFocused: Bool

    enum Sheet: Identifiable { case model, permission, slash, context, menu
        var id: Int { hashValue } }

    // 主内容列(对话 + 输入),手机/大屏共用。
    private var mainColumn: some View {
        VStack(spacing: 0) {
            topBar
            Divider().overlay(Theme.lineSoft)
            scenePeekBar
            chatArea
            if !running { confirmZone }
            if !running { suggestionChips }
            composerArea
        }
    }

    var body: some View {
        ZStack(alignment: .trailing) {
            WarmBackground()
            if hsc == .regular {
                // 大屏(iPad/Mac):对话 + 世界面板并排常驻(web 风),不再用覆盖抽屉。
                HStack(spacing: 0) {
                    mainColumn.frame(maxWidth: .infinity)
                    if showPanels {
                        Divider().overlay(Theme.lineSoft)
                        GamePanelsDrawer(snap: snap, contextPct: contextPct, modelLabel: modelLabel)
                            .frame(width: 360)
                            .transition(.move(edge: .trailing))
                    }
                }
            } else {
                // 手机:对话满宽 + 右侧世界面板覆盖抽屉。
                mainColumn
                if showPanels {
                    Color.black.opacity(0.45).ignoresSafeArea()
                        .onTapGesture { withAnimation(.easeOut(duration: 0.2)) { showPanels = false } }
                    GamePanelsDrawer(snap: snap, contextPct: contextPct, modelLabel: modelLabel)
                        .frame(width: 320).transition(.move(edge: .trailing))
                }
            }
            // 左侧抽屉:存档切换 / 手动保存(两端都用覆盖抽屉,偶发操作)
            if showLeftDrawer {
                Color.black.opacity(0.45).ignoresSafeArea()
                    .onTapGesture { withAnimation(.easeOut(duration: 0.2)) { showLeftDrawer = false } }
                HStack(spacing: 0) { leftDrawer.frame(width: 300).transition(.move(edge: .leading)); Spacer() }
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .onDisappear { streamTask?.cancel() }   // 退出游戏台时取消 SSE 任务,避免任务继续访问已失效 @State
        .task {
            // 大屏默认展开世界面板(常驻列);只在首次进入设一次,不覆盖用户后续手动收起。
            if hsc == .regular && !didInitLargePanels { didInitLargePanels = true; showPanels = true }
            await load()
            switch ProcessInfo.processInfo.environment["STELLATRIX_GAMESHEET"] {
            case "menu": sheet = .menu
            case "gen": showGenImage = true
            case "drawer": await loadSaves(); showLeftDrawer = true
            default: break
            }
        }
        .sheet(item: $sheet) { which in sheetView(which) }
        .photosPicker(isPresented: $showPhotoPicker, selection: $photoItem, matching: .images)
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            Task { if let a = await AttachLoader.fromPhoto(item) { attachments.append(a) }; photoItem = nil }
        }
        .fileImporter(isPresented: $showFileImporter, allowedContentTypes: [.item], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first, let a = AttachLoader.fromFileURL(url) { attachments.append(a) }
        }
        .sheet(isPresented: $showGenImage) { GenImageSheet { prompt, size in generateImage(prompt, size) } }
        .confirmationDialog("回滚到此处?这将丢弃其后的所有对话,且不可撤销。", isPresented: Binding(get: { rollbackTarget != nil }, set: { if !$0 { rollbackTarget = nil } }), titleVisibility: .visible, presenting: rollbackTarget) { idx in
            Button("回滚", role: .destructive) { Task { await rollbackTo(idx) } }
            Button("取消", role: .cancel) {}
        }
    }

    // MARK: 顶栏
    private var topBar: some View {
        HStack(spacing: 8) {
            Button { store.exitGame() } label: {
                HStack(spacing: 3) {
                    Image(systemName: "chevron.left").font(.system(size: 13, weight: .semibold))
                    Text("应用").font(Theme.ui(13.5, .medium))
                }
                .foregroundStyle(Theme.accent).padding(.horizontal, 10).padding(.vertical, 6)
                .background(Capsule().fill(Theme.accentSoft))
            }
            Button { Task { await loadSaves() }; withAnimation(.easeOut(duration: 0.2)) { showLeftDrawer = true } } label: {
                Image(systemName: "line.3.horizontal").font(.system(size: 17)).foregroundStyle(Theme.textQuiet).frame(width: 44, height: 44)
            }.accessibilityLabel(Text("菜单"))
            VStack(spacing: 1) {
                Text(launch.title).font(Theme.ui(16, .semibold)).foregroundStyle(Theme.text).lineLimit(1)
                HStack(spacing: 4) {
                    Circle().fill(Theme.accent).frame(width: 5, height: 5)
                    Text(subtitle).font(Theme.ui(11)).foregroundStyle(Theme.muted).lineLimit(1)
                }
            }.frame(maxWidth: .infinity)
            Button { sheet = .menu } label: {
                Image(systemName: "ellipsis").font(.system(size: 17)).foregroundStyle(Theme.textQuiet)
                    .frame(width: 44, height: 44)
            }.accessibilityLabel(Text("更多选项"))
            Button { withAnimation(.easeOut(duration: 0.2)) { showPanels.toggle() } } label: {
                Image(systemName: "safari").font(.system(size: 17)).foregroundStyle(Theme.accent)
                    .frame(width: 38, height: 38).background(Circle().fill(Theme.accentSoft))
            }.accessibilityLabel(Text("世界面板"))
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
    }
    private var subtitle: String { launch.scriptTitle ?? "自由模式" }

    // MARK: 场景条(场景 peek bar:时间/天气/位置 + 点开看目标 + 完整状态)
    @ViewBuilder private var scenePeekBar: some View {
        let chips = peekChips
        let obj = snap.objective
        if !chips.isEmpty || (obj?.isEmpty == false) {
            VStack(spacing: 0) {
                Button { withAnimation(.easeOut(duration: 0.18)) { peekExpanded.toggle() } } label: {
                    HStack(spacing: 12) {
                        ForEach(chips, id: \.0) { chip in
                            HStack(spacing: 3) {
                                Image(systemName: chip.1).font(.system(size: 10)).foregroundStyle(Theme.accent)
                                Text(chip.2).font(Theme.ui(11.5)).foregroundStyle(Theme.muted).lineLimit(1)
                            }
                        }
                        Spacer()
                        Image(systemName: peekExpanded ? "chevron.up" : "chevron.down")
                            .font(.system(size: 10)).foregroundStyle(Theme.muted2)
                    }
                    .padding(.horizontal, 16).padding(.vertical, 7).contentShape(Rectangle())
                }.buttonStyle(.plain)
                if peekExpanded {
                    VStack(alignment: .leading, spacing: 8) {
                        if let obj, !obj.isEmpty {
                            HStack(alignment: .top, spacing: 6) {
                                Text("目标").font(Theme.ui(10.5, .semibold)).foregroundStyle(Theme.accent)
                                Text(obj).font(Theme.ui(12)).foregroundStyle(Theme.text)
                            }
                        }
                        Button { withAnimation { showPanels = true } } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "safari").font(.system(size: 10))
                                Text("查看完整状态").font(Theme.ui(11.5, .medium))
                            }.foregroundStyle(Theme.accent)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16).padding(.bottom, 9)
                }
                Divider().overlay(Theme.lineSoft)
            }
            .background(Theme.panel.opacity(0.4))
        }
    }
    private var peekChips: [(String, String, String)] {
        var out: [(String, String, String)] = []
        if let t = snap.sceneTime, !t.isEmpty { out.append(("time", "clock", t)) }
        if let w = snap.sceneWeather, !w.isEmpty { out.append(("weather", "cloud.sun", w)) }
        if let l = snap.sceneLocation, !l.isEmpty { out.append(("loc", "mappin.and.ellipse", l)) }
        return out
    }

    // MARK: 建议 chips(GM 给出的下一步选项,点按填入输入框)
    @ViewBuilder private var suggestionChips: some View {
        let sg = snap.suggestions
        if !sg.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(sg, id: \.self) { s in
                        Button { text = s; inputFocused = true } label: {
                            Text(s).font(Theme.ui(12.5)).foregroundStyle(Theme.accent).lineLimit(1)
                                .padding(.horizontal, 12).padding(.vertical, 7)
                                .background(Capsule().fill(Theme.accentSoft))
                                .overlay(Capsule().stroke(Theme.accentEdge, lineWidth: 1))
                        }
                    }
                }.padding(.horizontal, 14)
            }
            .padding(.bottom, 4)
        }
    }

    // MARK: 对话区
    private var chatArea: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    if loading && messages.isEmpty {
                        ProgressView().tint(Theme.accent).frame(maxWidth: .infinity).padding(.top, 40)
                    }
                    if needsOpening && messages.isEmpty {
                        openingPrompt
                    }
                    ForEach(messages) { m in messageView(m).id(m.id) }
                    if running { runningIndicator }
                    if let error, !running { errorBar(error) }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 16).padding(.vertical, 16)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: messages.last?.content) { _, _ in withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo("bottom", anchor: .bottom) } }
            .onChange(of: running) { _, _ in withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } }
        }
    }

    @ViewBuilder private func messageView(_ m: ChatMessage) -> some View {
        if m.role == .assistant {
            if m.generating || m.imageURL != nil {
                VStack(alignment: .leading, spacing: 8) {
                    Text("生图").font(Theme.ui(10, .semibold)).foregroundStyle(Theme.accent).tracking(1.5)
                    if !m.content.isEmpty {
                        Text(m.content).font(Theme.ui(12.5)).italic().foregroundStyle(Theme.muted).lineLimit(3)
                    }
                    if m.generating {
                        HStack(spacing: 8) { ProgressView().tint(Theme.accent).scaleEffect(0.8); Text("生成中…").font(Theme.ui(12.5)).foregroundStyle(Theme.muted) }
                            .frame(width: 200, height: 120).background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel2))
                    } else if let url = m.imageURL {
                        ServerImageView(base: store.serverURL, path: url)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    Text("GM").font(Theme.ui(10, .semibold)).foregroundStyle(Theme.muted2).tracking(1.5)
                    Text(m.content.isEmpty ? " " : m.content)
                        .font(Theme.serif(16.5)).foregroundStyle(Theme.text)
                        .lineSpacing(7).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .contextMenu { msgMenu(m) }
            }
        } else {
            HStack {
                Spacer(minLength: 40)
                VStack(alignment: .trailing, spacing: 6) {
                    if let t = m.attachThumbs { AttachThumbsView(thumbs: t) }
                    Text(m.content).font(Theme.ui(15)).foregroundStyle(Theme.text)
                        .padding(.horizontal, 14).padding(.vertical, 10)
                        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.accentSoft))
                        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.accentEdge, lineWidth: 1))
                        .textSelection(.enabled)
                }
            }
            .contextMenu { msgMenu(m) }
        }
    }

    private var runningIndicator: some View {
        HStack(spacing: 10) {
            ProgressView().tint(Theme.accent).scaleEffect(0.8)
            Text(runLabel).font(Theme.ui(12.5)).foregroundStyle(Theme.muted)
            TimelineView(.periodic(from: runStart, by: 0.1)) { ctx in
                Text(String(format: "%.1fs", max(0, ctx.date.timeIntervalSince(runStart))))
                    .font(Theme.ui(11).monospacedDigit()).foregroundStyle(Theme.muted2)
            }
        }.padding(.vertical, 2)
    }
    private func errorBar(_ e: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 13)).foregroundStyle(Theme.danger)
            Text(e).font(Theme.ui(12.5)).foregroundStyle(Theme.danger)
            Spacer()
            Button("重试") { Task { await retry() } }.font(Theme.ui(12.5, .semibold)).foregroundStyle(Theme.accent)
        }
        .padding(12).background(RoundedRectangle(cornerRadius: 10).fill(Theme.danger.opacity(0.1)))
    }
    private var openingPrompt: some View {
        VStack(spacing: 12) {
            Text("这个存档还没有开场").font(Theme.serif(17)).foregroundStyle(Theme.textQuiet)
            Button { Task { await runOpening() } } label: {
                Label("生成开场", systemImage: "sparkles").font(Theme.ui(14.5, .semibold))
                    .foregroundStyle(Theme.onAccent).padding(.horizontal, 18).padding(.vertical, 11)
                    .background(Theme.accent, in: Capsule())
            }
        }.frame(maxWidth: .infinity).padding(.top, 30)
    }

    // MARK: 输入区(统一为酒馆式卡片:输入行 + 卡内 chip 行)
    private var composerArea: some View {
        VStack(spacing: 8) {
            if !attachments.isEmpty {
                AttachChipsStrip(attachments: attachments) { a in attachments.removeAll { $0.id == a.id } }
            }
            HStack(alignment: .bottom, spacing: 8) {
                Button { sheet = .menu } label: {
                    Image(systemName: "plus").font(.system(size: 19)).foregroundStyle(Theme.muted)
                        .frame(width: 44, height: 44).contentShape(Rectangle())
                }.accessibilityLabel(Text("添加"))
                TextField("此刻你做什么…", text: $text, axis: .vertical)
                    .font(Theme.ui(15)).foregroundStyle(Theme.text).tint(Theme.accent)
                    .lineLimit(1...6).focused($inputFocused).padding(.vertical, 6)
                Button { running ? stop() : send() } label: {
                    Image(systemName: running ? "stop.fill" : "arrow.up")
                        .font(.system(size: 15, weight: .bold)).foregroundStyle(Theme.onAccent)
                        .frame(width: 34, height: 34)
                        .background(Circle().fill((canSend || running) ? Theme.accent : Theme.muted2))
                        .frame(width: 44, height: 44).contentShape(Rectangle())   // 视觉圆点 34,触控区 44
                }.disabled(!canSend && !running).accessibilityLabel(Text(running ? "停止生成" : "发送"))
            }
            // 卡内 chip 行:模型 / 斜杠 / 权限 + 上下文
            HStack(spacing: 7) {
                Button { sheet = .model } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "sparkles").font(.system(size: 11))
                        Text(modelLabel).font(Theme.ui(12)).lineLimit(1)
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold))
                    }
                    .foregroundStyle(Theme.muted).padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Theme.panel2, in: Capsule()).overlay(Capsule().stroke(Theme.lineSoft, lineWidth: 1))
                }
                iconChip("slash.circle") { sheet = .slash }
                Button { sheet = .permission } label: {
                    HStack(spacing: 4) {
                        Image(systemName: permIcon).font(.system(size: 12)).foregroundStyle(permColor)
                        Text(loc: permShort).font(Theme.ui(11)).foregroundStyle(Theme.muted)   // 不止靠颜色区分(色盲可辨)
                    }
                    .padding(.horizontal, 9).frame(height: 30).background(Theme.panel2, in: Capsule())
                    .overlay(Capsule().stroke(Theme.lineSoft, lineWidth: 1))
                }.accessibilityLabel(Text("权限模式"))
                Spacer()
                Button { sheet = .context } label: {
                    HStack(spacing: 5) {
                        ZStack {
                            Circle().stroke(Theme.line, lineWidth: 2.5).frame(width: 15, height: 15)
                            Circle().trim(from: 0, to: CGFloat(contextPct) / 100)
                                .stroke(Theme.accent, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                                .rotationEffect(.degrees(-90)).frame(width: 15, height: 15)
                        }
                        Text("\(contextPct)%").font(Theme.ui(11)).foregroundStyle(Theme.muted).monospacedDigit()
                    }
                }
            }
        }
        .padding(.horizontal, 8).padding(.top, 7).padding(.bottom, 8)
        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 22))
        .overlay(RoundedRectangle(cornerRadius: 22).stroke(inputFocused ? Theme.accentEdge : Theme.line, lineWidth: 1))
        .frame(maxWidth: 720).frame(maxWidth: .infinity)
        .padding(.horizontal, 14).padding(.top, 8).padding(.bottom, 12)
    }
    private var canSend: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty }

    private func iconChip(_ icon: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 13)).foregroundStyle(Theme.muted)
                .frame(width: 34, height: 30).background(Theme.panel2, in: Capsule())
                .overlay(Capsule().stroke(Theme.lineSoft, lineWidth: 1))
        }.accessibilityLabel(Text("快捷命令"))
    }
    private var permIcon: String {
        switch permission { case "read_only": return "eye"; case "review": return "shield"; default: return "lock.open" }
    }
    private var permShort: String {
        switch permission { case "read_only": return "只读"; case "review": return "审核"; default: return "完全" }
    }
    private var permColor: Color {
        switch permission { case "read_only": return Theme.muted; case "review": return Theme.accent; default: return Color(red: 0.45, green: 0.72, blue: 0.5) }
    }

    // MARK: sheets
    @ViewBuilder private func sheetView(_ which: Sheet) -> some View {
        switch which {
        case .model:
            ModelPickerView(providers: providers, currentId: currentModelId) { m in
                modelLabel = m.display; currentModelId = m.id
                if !store.demo { Task { try? await store.api.selectModel(base: store.serverURL, apiId: m.apiId, modelId: m.id, saveId: launch.id) } }
            }
        case .permission: permissionSheet
        case .slash: slashSheet
        case .context: contextSheet
        case .menu: menuSheet
        }
    }

    private var permissionSheet: some View {
        sheetShell("权限模式") {
            VStack(spacing: 0) {
                permRow("read_only", "只读", "eye", "GM 不修改任何状态")
                divider; permRow("review", "审核", "shield", "状态写入需你确认")
                divider; permRow("full_access", "完全访问", "lock.open", "GM 可直接写状态")
            }.background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
        }
    }
    private func permRow(_ id: String, _ title: String, _ icon: String, _ desc: String) -> some View {
        Button {
            permission = id
            if !store.demo { Task { try? await store.api.setPermission(base: store.serverURL, mode: id) } }
            sheet = nil
        } label: {
            HStack(spacing: 12) {
                Image(systemName: icon).font(.system(size: 16)).foregroundStyle(permission == id ? Theme.accent : Theme.muted).frame(width: 24)
                VStack(alignment: .leading, spacing: 2) {
                    Text(loc: title).font(Theme.ui(15, .medium)).foregroundStyle(Theme.text)
                    Text(desc).font(Theme.ui(11.5)).foregroundStyle(Theme.muted)
                }
                Spacer()
                if permission == id { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
            }.padding(14).contentShape(Rectangle())
        }.buttonStyle(.plain)
    }

    private var slashSheet: some View {
        sheetShell("快捷命令") {
            VStack(spacing: 0) {
                slashRow("/status", "查看完整状态") { withAnimation { showPanels = true } }
                divider; slashRow("/save", "手动保存") { Task { await manualSave() } }
                divider; slashRow("/retry", "重试上一回合") { Task { await retry() } }
                divider; slashRow("/set ", "修改状态(插入)") { text += "/set " }
                divider; slashRow("/loc ", "设定位置(插入)") { text += "/loc " }
                divider; slashRow("/time ", "设定时间(插入)") { text += "/time " }
                divider; slashRow("/rel ", "设定关系(插入)") { text += "/rel " }
                divider; slashRow("/var ", "设定变量(插入)") { text += "/var " }
                divider; slashRow("/pin ", "钉住记忆(插入)") { text += "/pin " }
                divider; slashRow("/note ", "记笔记(插入)") { text += "/note " }
            }.background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
        }
    }
    private func slashRow(_ cmd: String, _ desc: String, _ action: @escaping () -> Void) -> some View {
        Button { action(); sheet = nil; inputFocused = true } label: {
            HStack {
                Text(cmd).font(Theme.ui(14, .medium).monospaced()).foregroundStyle(Theme.accent)
                Text(desc).font(Theme.ui(12.5)).foregroundStyle(Theme.muted)
                Spacer()
            }.padding(14).contentShape(Rectangle())
        }.buttonStyle(.plain)
    }

    private var contextSheet: some View {
        sheetShell("上下文用量") {
            VStack(alignment: .leading, spacing: 12) {
                HStack { Text("已用").foregroundStyle(Theme.muted); Spacer(); Text("\(contextPct)%").foregroundStyle(Theme.text).monospacedDigit() }
                    .font(Theme.ui(14))
                ProgressView(value: Double(contextPct), total: 100).tint(Theme.accent)
                if snap.contextWindow > 0 {
                    HStack { Text("窗口上限").foregroundStyle(Theme.muted); Spacer(); Text("\(snap.contextWindow) tokens").foregroundStyle(Theme.text).monospacedDigit() }
                        .font(Theme.ui(13))
                }
                Text("圆环反映当前输入占模型上下文窗口的比例。").font(Theme.ui(11.5)).foregroundStyle(Theme.muted2)
            }.padding(16).background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
        }
    }

    private var menuSheet: some View {
        sheetShell("更多") {
            VStack(spacing: 0) {
                menuRow("图片", "photo") { sheet = nil; showPhotoPicker = true }
                divider; menuRow("文件", "doc") { sheet = nil; showFileImporter = true }
                divider; menuRow("生成图片", "sparkles") { sheet = nil; showGenImage = true }
                divider; menuRow("世界面板", "safari") { sheet = nil; withAnimation { showPanels = true } }
                divider; menuRow("退出到应用", "rectangle.portrait.and.arrow.right") { sheet = nil; store.exitGame() }
            }.background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
        }
    }
    private func menuRow(_ t: String, _ icon: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: icon).font(.system(size: 15)).foregroundStyle(Theme.accent).frame(width: 24)
                Text(loc: t).font(Theme.ui(15)).foregroundStyle(Theme.text); Spacer()
            }.padding(14).contentShape(Rectangle())
        }.buttonStyle(.plain)
    }

    private func sheetShell<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
        ZStack {
            WarmBackground()
            VStack(alignment: .leading, spacing: 14) {
                Text(loc: title).font(Theme.serif(20, .semibold)).foregroundStyle(Theme.text).padding(.top, 18).padding(.horizontal, 16)
                content().padding(.horizontal, 16)
                Spacer()
            }
        }
        .presentationDetents([.medium, .large])
        .presentationBackground(Theme.bg)
    }
    private var divider: some View { Rectangle().fill(Theme.lineSoft).frame(height: 1).padding(.leading, 14) }

    @ViewBuilder private func msgMenu(_ m: ChatMessage) -> some View {
        Button { UIPasteboard.general.string = m.content } label: { Label("复制", systemImage: "doc.on.doc") }
        if let idx = messages.firstIndex(where: { $0.id == m.id }) {
            if m.role == .assistant && idx == (messages.lastIndex(where: { $0.role == .assistant }) ?? -1) {
                Button { Task { await retry() } } label: { Label("重新生成", systemImage: "arrow.clockwise") }
            }
            // 回滚/分叉用服务端原始下标(本地数组跳过空回合,本地下标会错位);无则退回本地下标。
            let serverIdx = m.serverIndex ?? idx
            Button { Task { await branchAt(serverIdx) } } label: { Label("从这里分叉", systemImage: "arrow.triangle.branch") }
            Button(role: .destructive) { rollbackTarget = serverIdx } label: { Label("回滚到此", systemImage: "arrow.uturn.backward") }
        }
    }

    // MARK: 待确认区(GM 选项 chips + 审核模式写入)
    @ViewBuilder private var confirmZone: some View {
        let qs = snap.pendingQuestions
        let ws = snap.pendingWrites
        if !qs.isEmpty || !ws.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(qs) { q in
                    VStack(alignment: .leading, spacing: 6) {
                        if !q.text.isEmpty { Text(q.text).font(Theme.ui(13)).foregroundStyle(Theme.text) }
                        if !q.options.isEmpty {
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(spacing: 8) {
                                    ForEach(q.options, id: \.self) { opt in
                                        Button { answerChoice(q, opt) } label: {
                                            Text(opt).font(Theme.ui(12.5, .medium)).foregroundStyle(Theme.onAccent).lineLimit(1)
                                                .padding(.horizontal, 12).padding(.vertical, 7).background(Capsule().fill(Theme.accent))
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                ForEach(ws) { w in
                    HStack(alignment: .top, spacing: 8) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(w.path).font(Theme.ui(11).monospaced()).foregroundStyle(riskColor(w.risk))
                            Text("→ \(w.to)").font(Theme.ui(12)).foregroundStyle(Theme.text).lineLimit(2)
                            if !w.reason.isEmpty { Text(w.reason).font(Theme.ui(10.5)).foregroundStyle(Theme.muted).lineLimit(2) }
                        }
                        Spacer()
                        Button("允许") { Task { await approveWrite(w) } }.font(Theme.ui(12, .semibold)).foregroundStyle(Theme.accent)
                        Button("拒绝") { Task { await rejectWrite(w) } }.font(Theme.ui(12)).foregroundStyle(Theme.danger)
                    }
                    .padding(8).background(RoundedRectangle(cornerRadius: 8).fill(Theme.panel2))
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 10).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel.opacity(0.65))
        }
    }
    private func riskColor(_ r: String) -> Color {
        switch r { case "high": return Theme.danger; case "medium": return Theme.accent; default: return Theme.muted }
    }

    // MARK: 左抽屉(存档切换 / 手动保存)
    private var leftDrawer: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 14) {
                Text("存档与记忆").font(Theme.serif(18, .semibold)).foregroundStyle(Theme.text).padding(.top, 54).padding(.horizontal, 16)
                Button { Task { await manualSave() } } label: {
                    HStack(spacing: 10) { Image(systemName: "tray.and.arrow.down").foregroundStyle(Theme.accent).frame(width: 22)
                        Text("手动保存").font(Theme.ui(15)).foregroundStyle(Theme.text); Spacer() }
                        .padding(.horizontal, 16).padding(.vertical, 8).contentShape(Rectangle())
                }
                if !snap.structuredUpdates.isEmpty {
                    Divider().overlay(Theme.lineSoft).padding(.horizontal, 12)
                    Text("本轮结构化更新").font(Theme.ui(11, .semibold)).foregroundStyle(Theme.muted).tracking(1).padding(.horizontal, 16)
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(snap.structuredUpdates.prefix(8).enumerated()), id: \.offset) { _, u in
                            Text(u).font(Theme.ui(10.5).monospaced()).foregroundStyle(Theme.muted).lineLimit(2)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }.padding(.horizontal, 16)
                }
                if !snap.forcedSetVars.isEmpty {
                    Divider().overlay(Theme.lineSoft).padding(.horizontal, 12)
                    Text("强制设定（/set）").font(Theme.ui(11, .semibold)).foregroundStyle(Theme.muted).tracking(1).padding(.horizontal, 16)
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(snap.forcedSetVars.enumerated()), id: \.offset) { _, kv in
                            HStack(spacing: 8) {
                                Text(kv.1.isEmpty ? kv.0 : "\(kv.0) = \(kv.1)")
                                    .font(Theme.ui(10.5).monospaced()).foregroundStyle(Theme.muted).lineLimit(2)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Button {
                                    Task {
                                        await store.api.worldlineVariableRemove(base: store.serverURL, key: kv.0)
                                        snap = (try? await store.api.rawState(base: store.serverURL)) ?? snap
                                    }
                                } label: {
                                    Image(systemName: "trash").font(.system(size: 11)).foregroundStyle(Theme.muted)
                                }
                            }
                        }
                    }.padding(.horizontal, 16)
                }
                Divider().overlay(Theme.lineSoft).padding(.horizontal, 12)
                Text("切换存档").font(Theme.ui(11, .semibold)).foregroundStyle(Theme.muted).tracking(1).padding(.horizontal, 16)
                ScrollView {
                    LazyVStack(spacing: 6) {
                        ForEach(savesList.filter { !$0.isTavern }) { s in
                            Button { Task { await switchSave(s) } } label: {
                                HStack(spacing: 8) {
                                    Image(systemName: s.id == launch.id ? "play.fill" : "bookmark.fill")
                                        .font(.system(size: 12)).foregroundStyle(s.id == launch.id ? Theme.accent : Theme.muted).frame(width: 18)
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(s.display).font(Theme.ui(13.5, .medium)).foregroundStyle(Theme.text).lineLimit(1)
                                        if let st = s.scriptTitle { Text(st).font(Theme.ui(10.5)).foregroundStyle(Theme.muted2).lineLimit(1) }
                                    }
                                    Spacer()
                                    if s.id == launch.id { Image(systemName: "checkmark").font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.accent) }
                                }
                                .padding(.horizontal, 10).padding(.vertical, 8)
                                .background(RoundedRectangle(cornerRadius: 8).fill(s.id == launch.id ? Theme.accentSoft : Theme.panel))
                                .contentShape(Rectangle())
                            }.buttonStyle(.plain)
                        }
                    }.padding(.horizontal, 12)
                }
                Spacer()
            }
        }
    }

    // MARK: 待确认/抽屉 handlers
    private func answerChoice(_ q: PendingQuestion, _ opt: String) {
        // [round-3-P2] 原仅 guard !running:clearQuestion 是异步,两次快速点击在 send() 翻起
        //   running 之前都能穿过两道 !running 闸 → 并发 send。改用同步 answering 闸(视图方法在
        //   主线程,设标志后再起 Task 即原子),阻断重入;defer 复位,后续靠 running 守。
        guard !running, !answering else { return }
        answering = true
        Task { @MainActor in
            defer { answering = false }
            if !store.demo { await store.api.clearQuestion(base: store.serverURL, id: q.id, index: q.index, choice: opt) }
            guard !running else { return }
            text = opt; send()
        }
    }
    private func approveWrite(_ w: PendingWrite) async {
        guard !store.demo else { return }
        await store.api.pendingWrite(base: store.serverURL, id: w.id, index: w.index, action: "approve")
        snap = (try? await store.api.rawState(base: store.serverURL)) ?? snap
    }
    private func rejectWrite(_ w: PendingWrite) async {
        guard !store.demo else { return }
        await store.api.pendingWrite(base: store.serverURL, id: w.id, index: w.index, action: "reject")
        snap = (try? await store.api.rawState(base: store.serverURL)) ?? snap
    }
    private func loadSaves() async {
        if store.demo { savesList = DemoData.saves; return }
        savesList = (try? await store.api.savesList(base: store.serverURL)) ?? []
    }
    private func manualSave() async {
        if !store.demo { await store.api.saveGame(base: store.serverURL) }
        withAnimation { showLeftDrawer = false }
    }
    private func switchSave(_ s: SaveItem) async {
        withAnimation { showLeftDrawer = false }
        guard s.id != launch.id else { return }
        await store.launchGame(s)
    }
    private func branchAt(_ idx: Int) async {
        guard !store.demo else { return }
        try? await store.api.branchContinue(base: store.serverURL, saveId: launch.id, messageIndex: idx)
        snap = (try? await store.api.rawState(base: store.serverURL)) ?? snap
        rebuildFromSnapshot()
    }
    private func rollbackTo(_ idx: Int) async {
        guard !store.demo else { return }
        try? await store.api.rollback(base: store.serverURL, saveId: launch.id, messageIndex: idx)
        snap = (try? await store.api.rawState(base: store.serverURL)) ?? snap
        rebuildFromSnapshot()
    }

    // MARK: 逻辑
    private func load() async {
        loading = true
        defer { loading = false }
        if store.demo {
            snap = DemoData.gameSnapshot(launch)
        } else {
            do { snap = try await store.api.rawState(base: store.serverURL) }
            catch { self.error = (error as? LocalizedError)?.errorDescription }
        }
        rebuildFromSnapshot()
        await loadModels()
    }
    private func rebuildFromSnapshot() {
        // GM 历史正文统一去残留 ops 块(对齐 web stripNarrativeOps);清洗后为空的回合不渲染。
        messages = snap.history.compactMap { t -> ChatMessage? in
            if t.role == "user" { return ChatMessage(role: .user, content: t.content, serverIndex: t.index) }
            let cleaned = cleanNarrative(t.content)
            guard !cleaned.isEmpty else { return nil }
            return ChatMessage(role: .assistant, content: cleaned, serverIndex: t.index)
        }
        permission = snap.permission
        if let m = snap.modelLabel { modelLabel = m }
        needsOpening = messages.isEmpty
    }
    private func loadModels() async {
        if store.demo { providers = DemoData.providers; currentModelId = DemoData.selectedModelId; modelLabel = DemoData.selectedModelDisplay; return }
        if let r = try? await store.api.models(base: store.serverURL) {
            providers = r.pickerProviders
            if let sel = r.selected { currentModelId = sel.modelName; if !sel.display.isEmpty { modelLabel = sel.display } }
        }
    }

    private func send() {
        let msg = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!msg.isEmpty || !attachments.isEmpty), !running else { return }
        let atts = attachments
        text = ""; attachments = []; error = nil; inputFocused = false; needsOpening = false
        let thumbs = atts.filter { $0.isImage }.map { $0.dataURL }
        messages.append(ChatMessage(role: .user, content: msg.isEmpty ? "(附件)" : msg, attachThumbs: thumbs.isEmpty ? nil : thumbs))
        let stream = store.demo
            ? DemoData.gameStream(msg)
            : store.api.streamChat(base: store.serverURL, message: msg, saveId: launch.id, attachments: atts.map { $0.bodyDict })
        startStream(stream)
    }

    // 生图:占位消息 → 入队 → 轮询 → 落 imageURL
    private func generateImage(_ prompt: String, _ size: String) {
        needsOpening = false
        messages.append(ChatMessage(role: .assistant, content: prompt, generating: true))
        let idx = messages.count - 1
        Task { @MainActor in   // 显式 MainActor:回调改 @State messages,必须主线程
            if store.demo {
                try? await Task.sleep(nanoseconds: 1_200_000_000)
                if idx < messages.count { messages[idx].generating = false; messages[idx].content = "(演示模式不出真实图片,登录后配置生图模型即可)" }
                return
            }
            do {
                let id = try await store.api.generateImage(base: store.serverURL, prompt: prompt, saveId: launch.id, size: size)
                let url = try await store.api.awaitImage(base: store.serverURL, id: id)
                if idx < messages.count { messages[idx].generating = false; messages[idx].imageURL = url; messages[idx].content = prompt }
            } catch {
                if idx < messages.count { messages[idx].generating = false; messages[idx].content = "生图失败:" + ((error as? LocalizedError)?.errorDescription ?? "未知错误") }
            }
        }
    }
    private func runOpening() async {
        guard !running else { return }
        error = nil; needsOpening = false
        startStream(store.demo ? DemoData.gameStream("__opening__") : store.api.streamOpening(base: store.serverURL, saveId: launch.id))
    }
    private func startStream(_ stream: AsyncThrowingStream<ChatEvent, Error>) {
        runStart = Date(); running = true; runLabel = "正在生成"
        messages.append(ChatMessage(role: .assistant, content: "", streaming: true))
        let idx = messages.count - 1
        let msgId = messages[idx].id  // [round-4-P1] 稳定 id:收尾按 id 定位本条助手消息,防 retry 重排后 idx 错位毁掉新消息
        var raw = ""   // 累计原始 token;显示用 cleanNarrative 实时去 ops 块
        var gotCleanedDone = false   // done 已带服务端清洗正文 → 不再二次 cleanNarrative(避免误截断合法正文)
        streamTask = Task { @MainActor in   // 显式 MainActor:流式回调改 @State,必须在主线程
            do {
                for try await ev in stream {
                    switch ev {
                    case .stage(let l): runLabel = l
                    case .token(let t): raw += t; if idx < messages.count { messages[idx].content = cleanNarrative(raw) }
                    case .usage(let pct): contextPct = max(0, min(100, Int(pct.rounded())))
                    case .error(let e): error = e
                    case .done(let final): if let final, !final.isEmpty, idx < messages.count { messages[idx].content = final; gotCleanedDone = true }
                    }
                }
            } catch is CancellationError {
                // 用户主动停止(stop()/退出)不算错误,不显示「生成中断」错误条
            } catch {
                if self.error == nil { self.error = (error as? LocalizedError)?.errorDescription ?? "生成中断" }
            }
            // [round-4-P1] 按 id 收尾:被 retry/重发取代后本条可能已被移走/重排,idx 会指向别的消息。
            if let i = messages.firstIndex(where: { $0.id == msgId }) {
                messages[i].streaming = false
                if !gotCleanedDone { messages[i].content = cleanNarrative(messages[i].content) }
                if messages[i].content.isEmpty { messages.remove(at: i) }
            }
            running = false
            // [round-3-P2] 被 stop()/退出取消后不再发收尾的 rawState 请求(原来取消仍打一次网络)。
            if !store.demo && !Task.isCancelled { snap = (try? await store.api.rawState(base: store.serverURL)) ?? snap }
        }
    }
    private func stop() {
        streamTask?.cancel()
        if !store.demo { Task { await store.api.stopGeneration(base: store.serverURL) } }
        running = false
    }
    private func retry() async {
        guard !running else { return }   // 防与进行中的流式重入
        streamTask?.cancel()             // 确保上一个流任务已死再重发
        guard let lastUser = messages.last(where: { $0.role == .user })?.content else { return }
        // 删除末尾 assistant + 该 user,再重发(对齐「重试=删本轮输出重发输入」)
        while let last = messages.last, last.role == .assistant { messages.removeLast() }
        if let last = messages.last, last.role == .user { messages.removeLast() }
        text = lastUser; send()
    }
}
