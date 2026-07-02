/**
 * Root index — placeholder route that the route guard redirects away from on mount.
 * Without this file, expo-router falls back to its built-in Sitemap page (which also
 * crashes in release builds due to a `URL.origin` reference that doesn't resolve in the
 * hermes bundle). The guard in `_layout.tsx` flips us to /(auth)/server or /(app)/chats
 * based on auth state as soon as `ready` is true.
 *
 * While we wait for the guard to fire, show the KindlingSplash so the user isn't staring
 * at a black void during the auth-init handshake.
 */
import React from "react";
import { KindlingSplash } from "@/components/KindlingSplash";

export default function Index() {
  return <KindlingSplash tagline="正在合卷…" />;
}
