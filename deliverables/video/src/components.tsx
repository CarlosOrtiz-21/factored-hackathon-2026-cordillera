// Reusable building blocks. Every animation is driven by useCurrentFrame()
// (CSS transitions don't render in Remotion).
import { Video } from "@remotion/media";
import React from "react";
import {
  AbsoluteFill,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { C, CLIP, EASE, FONT, prog } from "./theme";

/* ---------- Background: night + sunrise glow + drawn contour lines ---------- */

type Summit = { x: number; y: number; rings: number; step: number; seed: number };

const ringPath = (p: Summit, r: number) => {
  const R = 40 + r * p.step;
  const pts: string[] = [];
  for (let a = 0; a <= Math.PI * 2 + 0.001; a += Math.PI / 90) {
    const n =
      Math.sin(a * 3 + p.seed + r * 0.35) * 0.09 +
      Math.sin(a * 5 - p.seed * 2 + r * 0.2) * 0.05 +
      Math.cos(a * 2 + r * 0.15) * 0.07;
    pts.push(
      `${(p.x + Math.cos(a) * R * (1 + n) * 1.35).toFixed(1)} ${(p.y + Math.sin(a) * R * (1 + n) * 0.78).toFixed(1)}`,
    );
  }
  return `M${pts.join(" L")} Z`;
};

export const Backdrop: React.FC<{ summits: Summit[]; glowX?: string; drawAt?: number }> = ({
  summits,
  glowX = "74%",
  drawAt = 0,
}) => {
  const frame = useCurrentFrame();
  const drift = interpolate(frame, [0, 900], [0, -40]); // slow parallax so stills breathe
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(ellipse 1000px 560px at ${glowX} 112%, rgba(242,169,59,0.24), transparent 70%), radial-gradient(ellipse at 15% -10%, #10222e, transparent 60%), ${C.bg}`,
      }}
    >
      <svg width={1920} height={1080} style={{ position: "absolute", translate: `0px ${drift}px` }}>
        {summits.flatMap((p, pi) =>
          Array.from({ length: p.rings }, (_, r) => {
            const t = prog(frame, drawAt + pi * 10 + r * 2, 80);
            return (
              <path
                key={`${pi}-${r}`}
                d={ringPath(p, r)}
                pathLength={1}
                fill="none"
                stroke={r % 4 === 0 ? "rgba(127,211,208,0.38)" : "rgba(127,211,208,0.2)"}
                strokeWidth={r % 4 === 0 ? 1.8 : 1.3}
                strokeDasharray={1}
                strokeDashoffset={1 - t}
              />
            );
          }),
        )}
      </svg>
    </AbsoluteFill>
  );
};

/* ---------- Text entrances ---------- */

export const Reveal: React.FC<{
  at: number;
  dur?: number;
  y?: number;
  blur?: boolean;
  style?: React.CSSProperties;
  children: React.ReactNode;
}> = ({ at, dur = 22, y = 22, blur = false, style, children }) => {
  const frame = useCurrentFrame();
  const t = prog(frame, at, dur);
  return (
    <div
      style={{
        opacity: t,
        translate: `0px ${(1 - t) * y}px`,
        filter: blur ? `blur(${(1 - t) * 8}px)` : undefined,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

/** Serif headline revealed word by word; `em` words are italic amber. */
export const Headline: React.FC<{
  words: { t: string; em?: boolean }[];
  at: number;
  size?: number;
  style?: React.CSSProperties;
}> = ({ words, at, size = 76, style }) => {
  const frame = useCurrentFrame();
  return (
    <div style={{ fontFamily: FONT.serif, fontSize: size, lineHeight: 1.08, color: C.snow, letterSpacing: "-0.012em", ...style }}>
      {words.map((w, i) => {
        const t = prog(frame, at + i * 2.5, 20);
        return (
          <span
            key={i}
            style={{
              display: "inline-block",
              marginRight: "0.25em",
              opacity: t,
              translate: `0px ${(1 - t) * 14}px`,
              filter: `blur(${(1 - t) * 5}px)`,
              fontStyle: w.em ? "italic" : "normal",
              color: w.em ? C.sun : C.snow,
            }}
          >
            {w.t}
          </span>
        );
      })}
    </div>
  );
};

export const words = (text: string, emphasis: string[] = []) =>
  text.split(" ").map((t) => ({ t, em: emphasis.some((e) => e.split(" ").includes(t.replace(/[.,]/g, ""))) }));

export const Kicker: React.FC<{ n: string; label: string; at?: number }> = ({ n, label, at = 0 }) => (
  <Reveal at={at} blur style={{ position: "absolute", left: 112, top: 84, fontFamily: FONT.mono, fontSize: 20, letterSpacing: "0.16em", textTransform: "uppercase", color: C.dim }}>
    <span style={{ color: C.sun }}>{n}</span> · {label}
  </Reveal>
);

export const Label: React.FC<{ children: React.ReactNode; color?: string; style?: React.CSSProperties }> = ({ children, color = C.glacier, style }) => (
  <div style={{ fontFamily: FONT.mono, fontSize: 18, letterSpacing: "0.14em", textTransform: "uppercase", color, ...style }}>{children}</div>
);

/** Counts up to `to` between `at` and `at + dur`. */
export const Count: React.FC<{ to: number; at: number; dur?: number; suffix?: string; style?: React.CSSProperties }> = ({ to, at, dur = 40, suffix = "", style }) => {
  const frame = useCurrentFrame();
  return <span style={style}>{Math.round(to * prog(frame, at, dur))}{suffix}</span>;
};

export const Peak: React.FC<{ at: number; value: React.ReactNode; label: string; style: React.CSSProperties }> = ({ at, value, label, style }) => (
  <Reveal at={at} style={{ position: "absolute", display: "flex", gap: 20, ...style }}>
    <div style={{ marginTop: 20, width: 0, height: 0, borderLeft: "15px solid transparent", borderRight: "15px solid transparent", borderBottom: `26px solid ${C.sun}`, filter: "drop-shadow(0 0 14px rgba(242,169,59,0.8))" }} />
    <div>
      <div style={{ fontFamily: FONT.serif, fontWeight: 600, fontSize: 92, lineHeight: 1, color: C.snow }}>{value}</div>
      <div style={{ marginTop: 12, fontFamily: FONT.mono, fontSize: 21, color: C.glacier, maxWidth: 360, lineHeight: 1.35 }}>{label}</div>
    </div>
  </Reveal>
);

/* ---------- A browser-like window showing a screen recording with a moving camera ---------- */

export type Cam = { at: number; x: number; y: number; w: number };

/**
 * Shows the region {x, y, w} of the 2560×1392 recording inside the window; the camera
 * eases between keyframes (frames relative to this component). Cropping also hides the
 * recorder's own browser chrome and tabs.
 */
export const ClipWindow: React.FC<{
  src: string;
  trimBefore: number;
  playbackRate?: number;
  cams: Cam[];
  left: number;
  top: number;
  width: number;
  height: number;
  url: string;
}> = ({ src, trimBefore, playbackRate = 1, cams, left, top, width, height, url }) => {
  const frame = useCurrentFrame();
  const at = cams.map((c) => c.at);
  const cam = (k: "x" | "y" | "w") =>
    cams.length === 1
      ? cams[0][k]
      : interpolate(frame, at, cams.map((c) => c[k]), { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE });
  const s = width / cam("w");
  const enter = prog(frame, 0, 24);
  const bar = 46;
  return (
    <div
      style={{
        position: "absolute",
        left,
        top,
        width,
        height: height + bar,
        opacity: enter,
        scale: `${0.94 + 0.06 * enter}`,
        borderRadius: 18,
        overflow: "hidden",
        border: `1px solid ${C.rule}`,
        boxShadow: "0 40px 120px rgba(0,0,0,0.55), 0 0 0 1px rgba(127,211,208,0.12)",
        background: "#f3f5f6",
      }}
    >
      <div style={{ height: bar, background: "#101c25", display: "flex", alignItems: "center", gap: 10, padding: "0 18px" }}>
        {["#e8735a", "#f2a93b", "#7fd3d0"].map((c) => (
          <div key={c} style={{ width: 12, height: 12, borderRadius: 6, background: c, opacity: 0.85 }} />
        ))}
        <div style={{ marginLeft: 14, fontFamily: FONT.mono, fontSize: 17, color: C.dim, letterSpacing: "0.02em" }}>{url}</div>
      </div>
      <div style={{ position: "relative", width, height, overflow: "hidden" }}>
        <Video
          src={staticFile(src)}
          trimBefore={trimBefore}
          playbackRate={playbackRate}
          muted
          style={{
            position: "absolute",
            left: -cam("x") * s,
            top: -cam("y") * s,
            width: CLIP.width * s,
            height: CLIP.height * s,
          }}
        />
      </div>
    </div>
  );
};

/** A small "step" caption used beside the demo clips. */
export const Step: React.FC<{ at: number; n: string; title: string; body: string; active?: boolean }> = ({ at, n, title, body, active = true }) => (
  <Reveal at={at} style={{ marginBottom: 34, opacity: undefined }}>
    <div style={{ display: "flex", gap: 18, alignItems: "baseline", opacity: active ? 1 : 0.38 }}>
      <div style={{ fontFamily: FONT.serif, fontWeight: 600, fontSize: 40, color: C.sun, width: 44, flexShrink: 0 }}>{n}</div>
      <div>
        <div style={{ fontFamily: FONT.serif, fontWeight: 600, fontSize: 36, color: C.snow, lineHeight: 1.1 }}>{title}</div>
        <div style={{ marginTop: 8, fontFamily: FONT.sans, fontSize: 24, color: C.mist, lineHeight: 1.38 }}>{body}</div>
      </div>
    </div>
  </Reveal>
);
