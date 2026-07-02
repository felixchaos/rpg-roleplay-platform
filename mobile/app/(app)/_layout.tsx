import React, { useEffect, useState } from "react";
import { View } from "react-native";
import { Stack } from "expo-router";
import { theme } from "@/theme/theme";
import { compliance } from "@/api";
import { ThresholdOath } from "@/components/ThresholdOath";
import { EmberWatch } from "@/components/EmberWatch";

export default function AppLayout() {
  // null = checking, true = gate required, false = cleared
  const [needsOath, setNeedsOath] = useState<boolean | null>(null);
  const [version, setVersion] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await compliance.splashStatus();
        if (!alive) return;
        setVersion(r?.current_version || "");
        setNeedsOath(!r?.acked);
      } catch {
        // If the status check fails (older server without the endpoint), don't hard-block.
        if (alive) setNeedsOath(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (needsOath === null) {
    return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  }
  if (needsOath) {
    return <ThresholdOath version={version} onAcked={() => setNeedsOath(false)} />;
  }

  return (
    <>
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: theme.color.bg },
        animation: "slide_from_right",
      }}
    >
      <Stack.Screen name="chats" />
      <Stack.Screen name="console" />
      <Stack.Screen name="new-chat" options={{ presentation: "modal" }} />
      <Stack.Screen name="chat/[id]" />
      <Stack.Screen name="tree/[id]" />
      <Stack.Screen name="worldline/[id]" />
      <Stack.Screen name="personas" />
      <Stack.Screen name="profile" />
      <Stack.Screen name="card-edit" />
      <Stack.Screen name="gm-style" />
      <Stack.Screen name="save-settings" />
      <Stack.Screen name="script/[id]" />
      <Stack.Screen name="script-audit/[id]" />
      <Stack.Screen name="preferences" />
      <Stack.Screen name="account" />
      <Stack.Screen name="reliquary" />
      <Stack.Screen name="advanced" />
      <Stack.Screen name="memory-settings" />
      <Stack.Screen name="modules" />
      <Stack.Screen name="model-params" />
      <Stack.Screen name="aviary" />
      <Stack.Screen name="apparatus" />
      <Stack.Screen name="distillery" />
      <Stack.Screen name="help" />
      <Stack.Screen name="settings" />
    </Stack>
    <EmberWatch />
    </>
  );
}
