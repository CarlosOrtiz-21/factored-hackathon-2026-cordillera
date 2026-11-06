// The full demo video: eight scenes joined with short crossfades.
// Durations live here, on each sequence, so they can be retimed in one place (or in Studio).
import { Audio } from "@remotion/media";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import React from "react";
import { AbsoluteFill, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Close, ColdOpen, DemoChat, DemoHitl, HowItWorks, Results, Title, WhyCards } from "./scenes";

export const FPS = 30;
const XF = 15; // crossfade length in frames
const SCENES = [6, 6, 8, 14, 14, 8, 10, 9, 12, 8]; // seconds, in order (DemoHitl is 3 parts)
export const TOTAL_FRAMES = SCENES.reduce((a, s) => a + s * FPS, 0) - (SCENES.length - 1) * XF;

export type LiaVideoProps = { music: string | null };

const Music: React.FC<{ src: string }> = ({ src }) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();
  return (
    <Audio
      src={staticFile(src)}
      volume={interpolate(frame, [0, 2 * fps, durationInFrames - 3 * fps, durationInFrames], [0, 0.55, 0.55, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })}
    />
  );
};

export const LiaVideo: React.FC<LiaVideoProps> = ({ music }) => {
  const fadeT = <TransitionSeries.Transition presentation={fade()} timing={linearTiming({ durationInFrames: XF })} />;
  return (
    <AbsoluteFill style={{ backgroundColor: "#0a141c" }}>
      <TransitionSeries>
        <TransitionSeries.Sequence name="Cold open" durationInFrames={SCENES[0] * FPS}><ColdOpen /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="Title" durationInFrames={SCENES[1] * FPS}><Title /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="Why cards" durationInFrames={SCENES[2] * FPS}><WhyCards /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="Demo · chat" durationInFrames={SCENES[3] * FPS}><DemoChat /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="How it works" durationInFrames={SCENES[4] * FPS}><HowItWorks /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="Demo · requested" durationInFrames={SCENES[5] * FPS}><DemoHitl part={1} /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="Demo · auditor" durationInFrames={SCENES[6] * FPS}><DemoHitl part={2} /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="Demo · confirmed" durationInFrames={SCENES[7] * FPS}><DemoHitl part={3} /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="Results" durationInFrames={SCENES[8] * FPS}><Results /></TransitionSeries.Sequence>
        {fadeT}
        <TransitionSeries.Sequence name="Close" durationInFrames={SCENES[9] * FPS}><Close /></TransitionSeries.Sequence>
      </TransitionSeries>
      {music ? <Music src={music} /> : null}
    </AbsoluteFill>
  );
};
