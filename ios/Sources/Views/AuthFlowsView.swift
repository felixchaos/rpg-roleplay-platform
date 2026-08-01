import SwiftUI

// ─────────────────────────────────────────────────────────────────────────────
// 应用内账号流程 —— 注册 / 邮箱验证码 / 验证码登录 / 找回密码。
// 全部对接后端既有端点(register / verify-email / login-code / forgot-password)。
// 成功置 session cookie → 设 store.user → RootView 自动进入 app。
// ─────────────────────────────────────────────────────────────────────────────

// 复用的输入框(对齐 LoginView 风格)
private struct AuthField<Content: View>: View {
    let icon: String; let placeholder: String; let isEmpty: Bool
    @ViewBuilder var content: Content
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon).font(.system(size: 14)).foregroundStyle(Theme.muted).frame(width: 18)
            ZStack(alignment: .leading) {
                if isEmpty { Text(loc: placeholder).font(Theme.ui(15)).foregroundStyle(Theme.muted2) }
                content.font(Theme.ui(15)).foregroundStyle(Theme.text).tint(Theme.accent)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 14)
        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.line, lineWidth: 1))
    }
}

private struct AuthPrimaryButton: View {
    let title: String; let busy: Bool; let enabled: Bool; let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if busy { ProgressView().tint(Theme.onAccent) }
                Text(loc: title).font(Theme.ui(16, .semibold))
            }
            .foregroundStyle(Theme.onAccent).frame(maxWidth: .infinity).padding(.vertical, 14)
            .background(enabled ? Theme.accent : Theme.panel3, in: RoundedRectangle(cornerRadius: 14))
        }.disabled(!enabled || busy)
    }
}

private struct AuthScaffold<Content: View>: View {
    @Environment(\.dismiss) var dismiss
    let title: String; let subtitle: String
    @ViewBuilder var content: Content
    var body: some View {
        NavigationStack {
            ZStack {
                WarmBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(loc: subtitle).font(Theme.ui(13.5)).foregroundStyle(Theme.muted).lineSpacing(3)
                            .padding(.top, 4).padding(.bottom, 6)
                        content
                        Spacer(minLength: 20)
                    }.padding(20).frame(maxWidth: 460).frame(maxWidth: .infinity)
                }
            }
            .navigationTitle(Text(loc: title)).navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button(tr("取消")) { dismiss() }.foregroundStyle(Theme.textQuiet) } }
        }
    }
}

// ── 注册 ──
struct RegisterView: View {
    @EnvironmentObject var store: AppStore
    @State private var username = ""; @State private var email = ""; @State private var password = ""
    @State private var birthday = Calendar.current.date(byAdding: .year, value: -18, to: Date()) ?? Date()
    @State private var agree = false
    @State private var busy = false; @State private var err: String?
    @State private var pending: PendingEmail?
    private struct PendingEmail: Identifiable { let id = UUID(); let email: String }

    var body: some View {
        AuthScaffold(title: "注册账号", subtitle: "创建账号后即可在所有设备同步剧本、存档与角色。") {
            AuthField(icon: "person", placeholder: "用户名", isEmpty: username.isEmpty) {
                TextField("", text: $username).textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            AuthField(icon: "envelope", placeholder: "邮箱", isEmpty: email.isEmpty) {
                TextField("", text: $email).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            AuthField(icon: "lock", placeholder: "密码(至少 8 位)", isEmpty: password.isEmpty) {
                SecureField("", text: $password)
            }
            // 出生日期:后端强制要 + 算 ≥18(缺了直接 400);DatePicker 不便塞进 AuthField,单独一行同款样式。
            HStack(spacing: 10) {
                Image(systemName: "calendar").font(.system(size: 14)).foregroundStyle(Theme.muted).frame(width: 18)
                Text(loc: "出生日期").font(Theme.ui(15)).foregroundStyle(Theme.muted)
                Spacer()
                DatePicker("", selection: $birthday, in: ...Date(), displayedComponents: .date)
                    .labelsHidden().tint(Theme.accent)
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.line, lineWidth: 1))
            Button { agree.toggle() } label: {
                HStack(alignment: .top, spacing: 9) {
                    Image(systemName: agree ? "checkmark.square.fill" : "square").font(.system(size: 17)).foregroundStyle(agree ? Theme.accent : Theme.muted2)
                    Text(loc: "我已年满 18 周岁,并同意服务条款与隐私政策。")
                        .font(Theme.ui(12.5)).foregroundStyle(Theme.muted).multilineTextAlignment(.leading)
                    Spacer()
                }
            }.buttonStyle(.plain).padding(.vertical, 2)
            if let err { Text(err).font(Theme.ui(13)).foregroundStyle(Theme.danger) }
            AuthPrimaryButton(title: "注册", busy: busy, enabled: canSubmit) { Task { await submit() } }
        }
        .sheet(item: $pending) { p in
            EmailVerifyView(email: p.email).environmentObject(store)
        }
    }
    private var age: Int { Calendar.current.dateComponents([.year], from: birthday, to: Date()).year ?? 0 }
    private var canSubmit: Bool {
        !username.isEmpty && email.contains("@") && password.count >= 8 && agree && age >= 18
    }
    private func submit() async {
        busy = true; err = nil; defer { busy = false }
        if age < 18 { err = "你必须年满 18 周岁才能注册"; return }
        let fmt = DateFormatter(); fmt.dateFormat = "yyyy-MM-dd"; fmt.locale = Locale(identifier: "en_US_POSIX")
        let bday = fmt.string(from: birthday)
        do {
            let r = try await store.api.register(base: store.serverURL, username: username, password: password, email: email, displayName: username, birthday: bday)
            if let u = r.user { store.user = u }                          // 自动登录
            else { pending = PendingEmail(email: r.pendingEmail ?? email) } // 需邮箱验证码
        } catch { self.err = (error as? LocalizedError)?.errorDescription ?? "注册失败" }
    }
}

// ── 邮箱验证码(注册后)──
struct EmailVerifyView: View {
    @EnvironmentObject var store: AppStore
    let email: String
    @State private var code = ""; @State private var busy = false; @State private var err: String?
    @State private var resendHint: String?

    var body: some View {
        AuthScaffold(title: "邮箱验证", subtitle: "验证码已发送到 \(email),请输入收到的 6 位验证码。") {
            AuthField(icon: "number", placeholder: "6 位验证码", isEmpty: code.isEmpty) {
                TextField("", text: $code).keyboardType(.numberPad)
            }
            if let err { Text(err).font(Theme.ui(13)).foregroundStyle(Theme.danger) }
            if let resendHint { Text(resendHint).font(Theme.ui(12.5)).foregroundStyle(Theme.accent) }
            AuthPrimaryButton(title: "验证并进入", busy: busy, enabled: code.count >= 4) { Task { await submit() } }
            Button { Task { await resend() } } label: {
                Text(loc: "没收到?重新发送").font(Theme.ui(13, .medium)).foregroundStyle(Theme.accent)
            }.frame(maxWidth: .infinity).padding(.top, 4)
        }
    }
    private func submit() async {
        busy = true; err = nil; defer { busy = false }
        do { store.user = try await store.api.verifyEmail(base: store.serverURL, email: email, code: code.trimmingCharacters(in: .whitespaces)) }
        catch { self.err = (error as? LocalizedError)?.errorDescription ?? "验证失败" }
    }
    private func resend() async {
        err = nil; resendHint = nil
        do { try await store.api.resendCode(base: store.serverURL, email: email); resendHint = tr("验证码已重发,请查收邮件") }
        catch { self.err = (error as? LocalizedError)?.errorDescription ?? "重发失败" }
    }
}

// ── 验证码登录(免密)──
struct OTPLoginView: View {
    @EnvironmentObject var store: AppStore
    @State private var email = ""; @State private var code = ""
    @State private var sent = false; @State private var busy = false; @State private var err: String?

    var body: some View {
        AuthScaffold(title: "验证码登录", subtitle: sent ? "验证码已发送到 \(email),请输入。" : "输入邮箱,我们会发送一次性登录验证码,无需密码。") {
            AuthField(icon: "envelope", placeholder: "邮箱", isEmpty: email.isEmpty) {
                TextField("", text: $email).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled().disabled(sent)
            }
            if sent {
                AuthField(icon: "number", placeholder: "6 位验证码", isEmpty: code.isEmpty) {
                    TextField("", text: $code).keyboardType(.numberPad)
                }
            }
            if let err { Text(err).font(Theme.ui(13)).foregroundStyle(Theme.danger) }
            if sent {
                AuthPrimaryButton(title: "登录", busy: busy, enabled: code.count >= 4) { Task { await verify() } }
                Button { Task { await sendCode() } } label: { Text(loc: "重新发送").font(Theme.ui(13, .medium)).foregroundStyle(Theme.accent) }
                    .frame(maxWidth: .infinity).padding(.top, 4)
            } else {
                AuthPrimaryButton(title: "发送验证码", busy: busy, enabled: email.contains("@")) { Task { await sendCode() } }
            }
        }
    }
    private func sendCode() async {
        busy = true; err = nil; defer { busy = false }
        do {
            // 邮箱没注册时服务端不发信 —— 必须说破,否则用户对着验证码框等一封不会到的邮件。
            let registered = try await store.api.requestLoginCode(base: store.serverURL, email: email)
            if registered { sent = true } else { self.err = "这个邮箱还没有账号,请先注册。" }
        }
        catch { self.err = (error as? LocalizedError)?.errorDescription ?? "发送失败" }
    }
    private func verify() async {
        busy = true; err = nil; defer { busy = false }
        do { store.user = try await store.api.verifyLoginCode(base: store.serverURL, email: email, code: code.trimmingCharacters(in: .whitespaces)) }
        catch { self.err = (error as? LocalizedError)?.errorDescription ?? "验证失败" }
    }
}

// ── 忘记密码(触发重置邮件)──
struct ForgotPasswordView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var email = ""; @State private var busy = false; @State private var done = false

    var body: some View {
        AuthScaffold(title: "找回密码", subtitle: done ? "如果该邮箱已注册,我们已发送重置链接。请到邮箱点击链接设置新密码,然后回来登录。" : "输入注册邮箱,我们会发送密码重置链接。") {
            if !done {
                AuthField(icon: "envelope", placeholder: "邮箱", isEmpty: email.isEmpty) {
                    TextField("", text: $email).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                AuthPrimaryButton(title: "发送重置链接", busy: busy, enabled: email.contains("@")) { Task { await submit() } }
            } else {
                AuthPrimaryButton(title: "完成", busy: false, enabled: true) { dismiss() }
            }
        }
    }
    private func submit() async {
        busy = true; defer { busy = false }
        try? await store.api.forgotPassword(base: store.serverURL, email: email)
        done = true   // 防枚举:无论是否注册都提示已发送
    }
}
