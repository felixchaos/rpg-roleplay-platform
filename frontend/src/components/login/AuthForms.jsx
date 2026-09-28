// AuthForms.jsx — the seven auth-mode <form> bodies, mechanically split from login-app.jsx.
// Presentational only: every closure-entangled handler (handleVerify/submit/...) stays defined in
// the LoginApp shell and is threaded in as a prop. JSX bodies are byte-for-byte identical to the
// originals; the inline setErr + pl-auth-error role=alert '非静默' error UI is preserved verbatim.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { OtpInput } from './OtpInput.jsx';
import { SchemaField } from './SchemaField.jsx';

function VerifyForm({ handleVerify, pendingEmail, verifyCode, setVerifyCode, busy, err, notice, resendCooldown, setMode, setErr, setNotice, handleResend }) {
  const { t } = useTranslation();
  return (
          <form className="pl-auth-form" onSubmit={handleVerify}>
            <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
              {t('auth.verify.sent_to')} <strong>{pendingEmail}</strong>{t('auth.verify.expires')}
            </div>
            <div className="pl-field">
              <label htmlFor="verify_code">{t('auth.verify.code_label')}</label>
              <OtpInput
                value={verifyCode}
                onChange={setVerifyCode}
                onComplete={(code) => handleVerify(null, code)}
                disabled={busy}
                autoFocus
                label={t('auth.verify.code_label')}
              />
            </div>
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary" disabled={busy || verifyCode.length !== 6}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy ? t('auth.verify.verifying') : t('auth.verify.verify_btn')}
            </button>
            <div className="pl-auth-foot" style={{justifyContent: 'space-between'}}>
              <button type="button" style={{background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 13, padding: 0}}
                      onClick={() => { setMode('register'); setErr(''); setNotice(''); }}>
                {t('auth.verify.back')}
              </button>
              <button type="button"
                      disabled={resendCooldown > 0 || busy}
                      style={{background: 'none', border: 'none', color: resendCooldown > 0 ? 'var(--muted)' : 'var(--accent)', cursor: resendCooldown > 0 ? 'default' : 'pointer', fontSize: 13, padding: 0}}
                      onClick={handleResend}>
                {resendCooldown > 0 ? t('auth.verify.resend_cooldown', { s: resendCooldown }) : t('auth.verify.resend')}
              </button>
            </div>
          </form>
  );
}

function MagicOtpForm({ handleMagicOtpVerify, magicEmail, magicCode, setMagicCode, busy, err, notice }) {
  const { t } = useTranslation();
  return (
          <form className="pl-auth-form" onSubmit={handleMagicOtpVerify}>
            <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
              {t('auth.app.magic_otp_sent_to', { email: magicEmail })}
            </div>
            <div className="pl-field">
              <label htmlFor="magic_otp_code">{t('auth.app.otp_code_label')}</label>
              <OtpInput
                value={magicCode}
                onChange={setMagicCode}
                onComplete={(code) => handleMagicOtpVerify(null, code)}
                disabled={busy}
                autoFocus
                label={t('auth.app.otp_code_label')}
              />
            </div>
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary" disabled={busy || magicCode.length !== 6}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy ? t('auth.app.magic_otp_verifying') : t('auth.app.magic_otp_submit')}
            </button>
          </form>
  );
}

function NeedsProfileForm({ handleProfileSubmit, profileUsername, setProfileUsername, profileDisplayName, setProfileDisplayName, busy, err, notice }) {
  const { t } = useTranslation();
  return (
          <form className="pl-auth-form" onSubmit={handleProfileSubmit}>
            <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
              {t('auth.app.needs_profile_desc')}
            </div>
            <div className="pl-field">
              <label htmlFor="profile_username">{t('auth.app.username_label')} <span className="pl-field-req">*</span></label>
              <input
                id="profile_username"
                type="text"
                autoComplete="username"
                value={profileUsername}
                onChange={(e) => setProfileUsername(e.target.value)}
                autoFocus
                maxLength={32}
              />
            </div>
            <div className="pl-field">
              <label htmlFor="profile_display_name">{t('auth.app.display_name_label')}</label>
              <input
                id="profile_display_name"
                type="text"
                autoComplete="nickname"
                value={profileDisplayName}
                onChange={(e) => setProfileDisplayName(e.target.value)}
                maxLength={64}
              />
            </div>
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary"
                    disabled={busy || !profileUsername.trim()}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy ? t('auth.app.profile_saving') : t('auth.app.profile_submit')}
            </button>
          </form>
  );
}

function CodeLoginForm({ loginCodeSent, handleLoginCodeVerify, requestLoginCode, loginCodeEmail, setLoginCodeEmail, loginCodeEmailMask, loginCode, setLoginCode, busy, err, notice, resendCooldown, setLoginCodeSent, setErr, setNotice, handleResend }) {
  const { t } = useTranslation();
  return (
          <form className="pl-auth-form" onSubmit={(e) => loginCodeSent ? handleLoginCodeVerify(e) : (e.preventDefault(), requestLoginCode(loginCodeEmail))}>
            {!loginCodeSent ? (
              <>
                <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
                  {t('auth.login_code.desc')}
                </div>
                <div className="pl-field">
                  <label htmlFor="login_code_email">{t('auth.login_code.email_label')}</label>
                  <input
                    id="login_code_email"
                    type="email"
                    autoComplete="email"
                    value={loginCodeEmail}
                    onChange={(e) => setLoginCodeEmail(e.target.value)}
                    autoFocus
                  />
                </div>
              </>
            ) : (
              <>
                <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
                  {t('auth.verify.sent_to')} <strong>{loginCodeEmailMask}</strong>{t('auth.verify.expires')}
                </div>
                <div className="pl-field">
                  <label htmlFor="login_code">{t('auth.login_code.code_label')}</label>
                  <OtpInput
                    value={loginCode}
                    onChange={setLoginCode}
                    onComplete={(code) => handleLoginCodeVerify(null, code)}
                    disabled={busy}
                    autoFocus
                    label={t('auth.login_code.code_label')}
                  />
                </div>
              </>
            )}
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary"
                    disabled={busy || (loginCodeSent ? loginCode.length !== 6 : !loginCodeEmail.trim())}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy
                ? (loginCodeSent ? t('auth.login_code.verifying') : t('auth.login_code.sending'))
                : (loginCodeSent ? t('auth.login_code.verify_btn') : t('auth.login_code.send_btn'))}
            </button>
            {loginCodeSent && (
              <div className="pl-auth-foot" style={{justifyContent: 'space-between'}}>
                <button type="button" style={{background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 13, padding: 0}}
                        onClick={() => { setLoginCodeSent(false); setLoginCode(''); setErr(''); setNotice(''); }}>
                  {t('auth.login_code.back')}
                </button>
                <button type="button"
                        disabled={resendCooldown > 0 || busy}
                        style={{background: 'none', border: 'none', color: resendCooldown > 0 ? 'var(--muted)' : 'var(--accent)', cursor: resendCooldown > 0 ? 'default' : 'pointer', fontSize: 13, padding: 0}}
                        onClick={handleResend}>
                  {resendCooldown > 0 ? t('auth.verify.resend_cooldown', { s: resendCooldown }) : t('auth.verify.resend')}
                </button>
              </div>
            )}
          </form>
  );
}

function ForgotForm({ handleForgot, forgotEmail, setForgotEmail, busy, err, notice, setMode, setErr, setNotice }) {
  const { t } = useTranslation();
  return (
          <form className="pl-auth-form" onSubmit={handleForgot}>
            <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
              {t('auth.forgot_desc')}
            </div>
            <div className="pl-field">
              <label htmlFor="forgot_email">{t('auth.forgot_email')}</label>
              <input
                id="forgot_email"
                type="email"
                autoComplete="email"
                value={forgotEmail}
                onChange={(e) => setForgotEmail(e.target.value)}
                autoFocus
              />
            </div>
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary" disabled={busy}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy ? t('auth.submitting') : t('auth.forgot_send')}
            </button>
            <div className="pl-auth-foot">
              <button type="button" style={{background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 13, padding: 0}}
                      onClick={() => { setMode('login'); setErr(''); setNotice(''); }}>
                {t('auth.forgot_back_to_login')}
              </button>
            </div>
          </form>
  );
}

function ResetForm({ handleReset, resetPw, setResetPw, resetPwConfirm, setResetPwConfirm, busy, err, notice }) {
  const { t } = useTranslation();
  return (
          <form className="pl-auth-form" onSubmit={handleReset}>
            <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
              {t('auth.reset_desc')}
            </div>
            <div className="pl-field">
              <label htmlFor="reset_pw">{t('auth.reset_new_pw')}</label>
              <input
                id="reset_pw"
                type="password"
                autoComplete="new-password"
                value={resetPw}
                onChange={(e) => setResetPw(e.target.value)}
                autoFocus
              />
            </div>
            <div className="pl-field">
              <label htmlFor="reset_pw_confirm">{t('auth.reset_confirm')}</label>
              <input
                id="reset_pw_confirm"
                type="password"
                autoComplete="new-password"
                value={resetPwConfirm}
                onChange={(e) => setResetPwConfirm(e.target.value)}
              />
            </div>
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary" disabled={busy}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy ? t('auth.submitting') : t('auth.reset_submit')}
            </button>
          </form>
  );
}

function MainAuthForm({ submit, schemaErr, schema, fields, values, setField, err, notice, mode, turnstileSitekey, tsRef, busy, minPw, setForgotEmail, setErr, setNotice, setMode }) {
  const { t } = useTranslation();
  return (
    <form className="pl-auth-form" onSubmit={submit}>
          {schemaErr && (
            <div className="pl-auth-error"
                 style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>
              {t('auth.schema_err')}{schemaErr}
            </div>
          )}

          {!schema && !schemaErr && (
            <div style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0'}}>
              {t('auth.schema_loading')}
            </div>
          )}

          {fields.map((f) => (
            <SchemaField key={f.key} field={f}
                         value={values[f.key]}
                         onChange={(v) => setField(f.key, v)} />
          ))}

          {err && (
            <div className="pl-auth-error" role="alert"
                 style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>
              {err}
            </div>
          )}

          {notice && (
            <div className="pl-auth-notice" role="status" aria-live="polite"
                 style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                         borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>
              {notice}
            </div>
          )}

          {mode === 'register' && turnstileSitekey && (
            <div ref={tsRef} className="pl-auth-turnstile"
                 style={{display: 'flex', justifyContent: 'center', margin: '2px 0'}} />
          )}

          <button type="submit" className="btn primary" disabled={busy || !schema}
                  style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
            {busy ? t('auth.submitting') : (mode === 'login' ? t('auth.login_btn') : t('auth.register_btn'))}
          </button>

          <div className="pl-auth-foot">
            <span>
              {schema?.notes?.first_user_is_admin
                ? t('auth.first_admin')
                : ''}
              {schema?.notes?.registration_closed
                ? t('auth.registration_closed_note')
                : schema?.notes?.invite_only
                  ? t('auth.invite_only_note')
                  : ''}
              {!schema?.notes?.registration_closed && !schema?.notes?.invite_only && !schema?.notes?.first_user_is_admin
                ? t('auth.min_password', { min: minPw })
                : ''}
            </span>
            <button
               onClick={() => {
                 setForgotEmail('');
                 setErr(''); setNotice('');
                 setMode('forgot');
               }}
               style={{background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 'inherit', padding: 0}}>{t('auth.forget_password')}</button>
          </div>
    </form>
  );
}

export { VerifyForm, MagicOtpForm, NeedsProfileForm, CodeLoginForm, ForgotForm, ResetForm, MainAuthForm };
