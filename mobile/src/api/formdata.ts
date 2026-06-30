/**
 * Centralized helpers for building FormData payloads compatible with expo-fetch (the
 * default fetch implementation in Expo SDK 56+).
 *
 * expo-fetch only accepts these FormData part types:
 *   - string
 *   - Blob / File (native, not JS-polyfilled)
 *   - { bytes: Uint8Array | ArrayBuffer, name?: string, type?: string }
 *
 * The legacy RN shape { uri, name, type } is NOT supported by expo-fetch and throws
 * "Unsupported FormDataPart implementation" in release builds.
 *
 * Our strategy: read the picked file into a Uint8Array via expo-file-system, then
 * append it as { bytes, name, type }.  This bypasses all Blob/File compatibility
 * issues across Android and iOS.
 */
import * as FileSystem from "expo-file-system";

/**
 * Append a picked file (from DocumentPicker / ImagePicker) to a FormData under the
 * given field name as the { bytes, name, type } format that expo-fetch expects.
 *
 * This is async because we must read the file content from disk first.
 */
export async function appendFile(
  form: FormData,
  field: string,
  asset: { uri: string; name?: string | null; mimeType?: string | null; type?: string | null },
): Promise<void> {
  const filename = asset.name || (asset.uri.split("/").pop() ?? "upload.bin");
  const mime = asset.mimeType || asset.type || "application/octet-stream";

  try {
    // Read the file as base64, then decode to a raw Uint8Array.
    const b64 = await FileSystem.readAsStringAsync(asset.uri, {
      encoding: FileSystem.EncodingType.Base64,
    });

    // Decode base64 → binary
    const binaryStr = atob(b64);
    const bytes = new Uint8Array(binaryStr.length);
    for (let i = 0; i < binaryStr.length; i++) {
      bytes[i] = binaryStr.charCodeAt(i);
    }

    // { bytes, name, type } is the canonical expo-fetch FormData part format.
    form.append(field, { bytes, name: filename, type: mime } as any);
  } catch {
    // If FileSystem can't read the URI (e.g. content:// URI without permission),
    // fall back to the legacy RN shape.  This will still fail with expo-fetch,
    // but gives the caller a clearer error than a silent no-op.
    form.append(field, { uri: asset.uri, name: filename, type: mime } as any);
  }
}
