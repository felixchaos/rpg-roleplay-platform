/**
 * Persistent app config + secure session storage.
 * - serverUrl: the bring-your-own-server base (e.g. http://192.168.1.5:7860)
 * - rpg_session cookie value: stored in secure-store, attached manually to every request
 *   because RN's implicit cookie jar is unreliable across restarts / arbitrary hosts.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

const K_SERVER = "rpg.serverUrl";
const K_SESSION = "rpg.session";

export function normalizeBaseUrl(raw: string): string {
  let u = (raw || "").trim();
  if (!u) return "";
  if (!/^https?:\/\//i.test(u)) u = "https://" + u;
  return u.replace(/\/+$/, "");
}

/** The official cloud instance — used by default so the app opens straight to login. */
export const DEFAULT_SERVER_URL = "https://rpg-roleplay.stellatrix.icu";

export async function getServerUrl(): Promise<string | null> {
  const stored = await AsyncStorage.getItem(K_SERVER);
  return stored || DEFAULT_SERVER_URL;
}

export async function setServerUrl(url: string): Promise<void> {
  await AsyncStorage.setItem(K_SERVER, normalizeBaseUrl(url));
}

export async function getSessionCookie(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(K_SESSION);
  } catch {
    return null;
  }
}

export async function setSessionCookie(value: string | null): Promise<void> {
  try {
    if (value) await SecureStore.setItemAsync(K_SESSION, value);
    else await SecureStore.deleteItemAsync(K_SESSION);
  } catch {
    /* secure store may be unavailable on some emulators; non-fatal */
  }
}
