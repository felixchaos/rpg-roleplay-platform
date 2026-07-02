/**
 * PromptDialog — a cross-platform text-input dialog. Alert.prompt is iOS-only (silent
 * no-op on Android), so rename actions did nothing on phones. This themed modal fills
 * that gap: a single text field with confirm/cancel, styled in the Candlelit Grimoire
 * palette. Imperative-friendly via a tiny hook so callers can `await promptText(...)`.
 */
import React, { useEffect, useState } from "react";
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { BlurView } from "expo-blur";
import { EmberButton } from "@/components/ui";
import { theme } from "@/theme/theme";

export type PromptConfig = {
  title: string;
  placeholder?: string;
  initialValue?: string;
  confirmLabel?: string;
  onConfirm: (value: string) => void;
};

export function PromptDialog({
  visible,
  config,
  onClose,
}: {
  visible: boolean;
  config: PromptConfig | null;
  onClose: () => void;
}) {
  const [value, setValue] = useState("");

  useEffect(() => {
    if (visible) setValue(config?.initialValue ?? "");
  }, [visible, config]);

  const submit = () => {
    const v = value.trim();
    if (!v) return;
    config?.onConfirm(v);
    onClose();
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <KeyboardAvoidingView
        style={styles.center}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        pointerEvents="box-none"
      >
        <View style={styles.card}>
          <BlurView intensity={28} tint="dark" style={styles.fill} />
          <Text style={styles.title}>{config?.title}</Text>
          <TextInput
            value={value}
            onChangeText={setValue}
            placeholder={config?.placeholder}
            placeholderTextColor={theme.color.textFaint}
            style={styles.input}
            autoFocus
            onSubmitEditing={submit}
            returnKeyType="done"
          />
          <View style={styles.actions}>
            <EmberButton label="取消" variant="ghost" onPress={onClose} style={{ flex: 1 }} />
            <EmberButton label={config?.confirmLabel || "确定"} onPress={submit} style={{ flex: 1 }} />
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

/** Hook that wires a single PromptDialog instance you render once per screen. */
export function usePrompt() {
  const [visible, setVisible] = useState(false);
  const [config, setConfig] = useState<PromptConfig | null>(null);

  const prompt = (cfg: PromptConfig) => {
    setConfig(cfg);
    setVisible(true);
  };

  const node = (
    <PromptDialog visible={visible} config={config} onClose={() => setVisible(false)} />
  );

  return { prompt, promptNode: node };
}

const styles = StyleSheet.create({
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  center: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: theme.space(8) },
  card: {
    width: "100%",
    backgroundColor: "rgba(20,16,12,0.95)",
    borderRadius: theme.radius.lg,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    padding: theme.space(5),
    gap: theme.space(4),
  },
  title: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 0.5 },
  input: {
    backgroundColor: theme.color.bgInput,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    paddingHorizontal: theme.space(4),
    paddingVertical: theme.space(3.5),
    color: theme.color.text,
    fontFamily: theme.font.prose,
    fontSize: theme.size.md,
  },
  actions: { flexDirection: "row", gap: theme.space(3) },
});
