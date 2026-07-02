import { useFonts } from "expo-font";
import {
  Cinzel_600SemiBold,
  Cinzel_700Bold,
} from "@expo-google-fonts/cinzel";
import {
  Spectral_400Regular,
  Spectral_400Regular_Italic,
  Spectral_500Medium,
  Spectral_600SemiBold,
} from "@expo-google-fonts/spectral";
import { JetBrainsMono_400Regular } from "@expo-google-fonts/jetbrains-mono";

/** Loads the Candlelit Grimoire type families. Returns true once ready. */
export function useGrimoireFonts(): boolean {
  const [loaded] = useFonts({
    Cinzel_600SemiBold,
    Cinzel_700Bold,
    Spectral_400Regular,
    Spectral_400Regular_Italic,
    Spectral_500Medium,
    Spectral_600SemiBold,
    JetBrainsMono_400Regular,
  });
  return loaded;
}
