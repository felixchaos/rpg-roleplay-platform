/**
 * Authenticated file download + native share. Used for exporting tavern chats to
 * SillyTavern JSONL. We must attach the rpg_session cookie manually (the backend
 * streams the file as an attachment behind auth), so we can't just hand a URL to
 * the OS — we download into the app cache first, then open the share sheet.
 *
 * The downloaded file is cleaned from cache after sharing (or immediately if
 * sharing is unavailable) to prevent cache bloat.
 */
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { baseUrl } from "./http";
import { getSessionCookie } from "./storage";

export async function downloadAndShare(
  path: string,
  filename: string,
  mimeType = "application/jsonl",
): Promise<void> {
  const base = await baseUrl();
  const cookie = await getSessionCookie();
  const url = path.startsWith("http") ? path : base + path;
  const target = FileSystem.cacheDirectory + filename;

  const res = await FileSystem.downloadAsync(url, target, {
    headers: cookie ? { Cookie: cookie } : undefined,
  });
  if (res.status >= 400) {
    // Clean up the partial/temp file on error
    await FileSystem.deleteAsync(res.uri, { idempotent: true }).catch(() => {});
    throw new Error(`下载失败 (HTTP ${res.status})`);
  }

  const canShare = await Sharing.isAvailableAsync();
  if (canShare) {
    await Sharing.shareAsync(res.uri, { mimeType, dialogTitle: filename, UTI: "public.data" });
  }

  // Always clean up the temp file from cache after sharing (or if sharing failed)
  await FileSystem.deleteAsync(res.uri, { idempotent: true }).catch(() => {});
}
