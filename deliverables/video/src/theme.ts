// Visual system shared with the slide deck: "Andean night" world (contours, sun glow)
// + editorial briefing voice (Source Serif 4 / DM Sans / IBM Plex Mono).
import { loadFont as loadSerif } from "@remotion/google-fonts/SourceSerif4";
import { loadFont as loadSans } from "@remotion/google-fonts/DMSans";
import { loadFont as loadMono } from "@remotion/google-fonts/IBMPlexMono";
import { Easing, interpolate } from "remotion";

const serif = loadSerif("normal", { weights: ["400", "600"], subsets: ["latin"] });
loadSerif("italic", { weights: ["400"], subsets: ["latin"] });
const sans = loadSans("normal", { weights: ["400", "500"], subsets: ["latin"] });
const mono = loadMono("normal", { weights: ["400", "500"], subsets: ["latin"] });

export const FONT = {
  serif: serif.fontFamily,
  sans: sans.fontFamily,
  mono: mono.fontFamily,
};

export const C = {
  bg: "#0a141c",
  snow: "#eef3f1",
  mist: "#b9c8c8",
  dim: "#6f8a94",
  glacier: "#7fd3d0",
  sun: "#f2a93b",
  danger: "#e8735a",
  rule: "rgba(238, 243, 241, 0.14)",
  panel: "rgba(238, 243, 241, 0.03)",
};

// The deck's ease-out-expo, used for every entrance.
export const EASE = Easing.bezier(0.16, 1, 0.3, 1);

/** 0 → 1 between `start` and `start + dur` frames, eased and clamped. */
export const prog = (frame: number, start: number, dur: number) =>
  interpolate(frame, [start, start + dur], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASE,
  });

/** Source recordings: 2560×1392 screen captures of the public demo. */
export const CLIP = { width: 2560, height: 1392 };
