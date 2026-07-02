import "react-native-gesture-handler";
import React, { useEffect } from "react";
import { View } from "react-native";
import { Stack, useRouter, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import * as SystemUI from "expo-system-ui";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { AuthProvider, useAuth } from "@/state/auth";
import { useGrimoireFonts } from "@/theme/fonts";
import { KindlingSplash } from "@/components/KindlingSplash";
import { theme } from "@/theme/theme";

SystemUI.setBackgroundColorAsync(theme.color.bg).catch(() => {});

function RouteGuard() {
  const { ready, user, serverUrl } = useAuth();
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (!ready) return;
    const inAuth = segments[0] === "(auth)";
    const inApp = segments[0] === "(app)";
    const authed = !!user && !!serverUrl;
    if (!authed && !inAuth) router.replace("/(auth)/server");
    else if (authed && !inApp) router.replace("/(app)/chats");
  }, [ready, user, serverUrl, segments, router]);

  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: theme.color.bg },
        animation: "fade",
      }}
    >
      <Stack.Screen name="index" />
      <Stack.Screen name="(auth)" />
      <Stack.Screen name="(app)" />
    </Stack>
  );
}

export default function RootLayout() {
  const fontsLoaded = useGrimoireFonts();
  if (!fontsLoaded) return <KindlingSplash tagline="燃起符印…" />;
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <AuthProvider>
          <StatusBar style="light" />
          <RouteGuard />
        </AuthProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
