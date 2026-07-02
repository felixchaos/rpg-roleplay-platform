/**
 * TurnstileGate — Cloudflare Turnstile human-verification, rendered in a WebView since RN
 * has no native widget. We load a minimal HTML page hosting the Turnstile script, bind the
 * server-provided sitekey, and relay the solved token back to RN via postMessage. The host
 * (register screen) only mounts this when /api/auth/schema reports a sitekey, and blocks
 * submission until a token arrives.
 */
import React, { useMemo } from "react";
import { StyleSheet, View } from "react-native";
import { WebView } from "react-native-webview";
import { theme } from "@/theme/theme";

function buildHtml(sitekey: string): string {
  // The page posts {token} on success and {expired}/{error} otherwise. Dark theme to
  // match the app; transparent body so the grimoire backdrop shows through.
  return `<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<style>
  html,body{margin:0;padding:0;background:transparent;}
  #wrap{display:flex;align-items:center;justify-content:center;padding:6px 0;}
</style>
</head><body>
<div id="wrap"><div id="ts"></div></div>
<script>
  function send(m){ if(window.ReactNativeWebView) window.ReactNativeWebView.postMessage(JSON.stringify(m)); }
  function render(){
    if(!window.turnstile){ return setTimeout(render, 200); }
    window.turnstile.render('#ts', {
      sitekey: ${JSON.stringify(sitekey)},
      theme: 'dark',
      callback: function(token){ send({type:'token', token:token}); },
      'expired-callback': function(){ send({type:'expired'}); },
      'error-callback': function(){ send({type:'error'}); }
    });
  }
  render();
</script>
</body></html>`;
}

export function TurnstileGate({
  sitekey,
  onToken,
  onExpire,
}: {
  sitekey: string;
  onToken: (token: string) => void;
  onExpire?: () => void;
}) {
  const html = useMemo(() => buildHtml(sitekey), [sitekey]);

  return (
    <View style={styles.wrap}>
      <WebView
        originWhitelist={["*"]}
        source={{ html, baseUrl: "https://challenges.cloudflare.com" }}
        style={styles.web}
        scrollEnabled={false}
        javaScriptEnabled
        domStorageEnabled
        // Cloudflare needs a normal-looking origin; an https baseUrl above satisfies it.
        onMessage={(e) => {
          try {
            const msg = JSON.parse(e.nativeEvent.data);
            if (msg.type === "token" && msg.token) onToken(msg.token);
            else if (msg.type === "expired" || msg.type === "error") onExpire?.();
          } catch {
            /* ignore malformed */
          }
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    height: 86,
    borderRadius: theme.radius.md,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    backgroundColor: theme.color.bgInput,
  },
  web: { flex: 1, backgroundColor: "transparent" },
});
