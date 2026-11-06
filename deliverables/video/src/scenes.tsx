// The eight scenes of the demo video. Frames inside each scene are local (start at 0).
import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Backdrop, ClipWindow, Count, Headline, Kicker, Label, Peak, Reveal, Step } from "./components";
import { C, EASE, FONT, prog } from "./theme";

/** "Agents propose. *Code decides.* A human approves." → words with italic-amber emphasis. */
const h = (text: string) => {
  let em = false;
  return text.split(" ").map((raw) => {
    const starts = raw.startsWith("*");
    const ends = raw.replace(/[.,!?]$/, "").endsWith("*");
    if (starts) em = true;
    const word = { t: raw.replace(/\*/g, ""), em };
    if (ends) em = false;
    return word;
  });
};

const Foot: React.FC<{ right?: string }> = ({ right }) => (
  <div style={{ position: "absolute", left: 112, right: 112, bottom: 60, display: "flex", justifyContent: "space-between", fontFamily: FONT.mono, fontSize: 17, letterSpacing: "0.12em", textTransform: "uppercase", color: "#4f6670" }}>
    <span>Banco Cordillera is a fictional bank · synthetic data</span>
    <span>{right}</span>
  </div>
);

/* 1 ── Cold open ─────────────────────────────────────────────────────────── */
export const ColdOpen: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const line = (at: number, until: number) => ({
    opacity: interpolate(frame, [at, at + 12, until, until + 10], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
  });
  return (
    <AbsoluteFill>
      <Backdrop summits={[{ x: 1500, y: 640, rings: 15, step: 46, seed: 1.3 }, { x: 1000, y: 1000, rings: 9, step: 52, seed: 4.1 }]} />
      <div style={{ position: "absolute", left: 112, top: 360, width: 1400 }}>
        <div style={{ ...line(0.2 * fps, 5.5 * fps), fontFamily: FONT.mono, fontSize: 26, letterSpacing: "0.14em", color: C.sun }}>
          ▲ 11:42 P.M.
        </div>
        <div style={line(0.5 * fps, 5.5 * fps)}>
          <Headline at={0.5 * fps} size={96} words={h("A customer loses a card.")} style={{ marginTop: 24 }} />
        </div>
        <div style={line(2.2 * fps, 5.5 * fps)}>
          <Headline at={2.2 * fps} size={64} words={h("One message could block it on the spot.")} style={{ marginTop: 30, color: C.mist }} />
        </div>
        <div style={line(3.6 * fps, 5.5 * fps)}>
          <Headline at={3.6 * fps} size={64} words={h("Lía won't, *until a human says yes.*")} style={{ marginTop: 18 }} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

/* 2 ── Title ─────────────────────────────────────────────────────────────── */
export const Title: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const wipe = prog(frame, 0.2 * fps, 1.2 * fps);
  return (
    <AbsoluteFill>
      <Backdrop summits={[{ x: 1500, y: 640, rings: 15, step: 46, seed: 1.3 }, { x: 1000, y: 1000, rings: 9, step: 52, seed: 4.1 }]} />
      <Reveal at={0} blur style={{ position: "absolute", left: 112, top: 84, fontFamily: FONT.mono, fontSize: 20, letterSpacing: "0.14em", color: C.dim }}>
        <span style={{ color: C.sun }}>▲</span> FACTORED AI &amp; DATA HACKATHON 2026
      </Reveal>
      <div style={{ position: "absolute", left: 96, top: 180, fontFamily: FONT.serif, fontWeight: 600, fontSize: 360, lineHeight: 0.9, letterSpacing: "-0.045em", color: C.snow, clipPath: `inset(${(1 - wipe) * 100}% 0 -10% 0)` }}>
        Lía<span style={{ color: C.sun }}>.</span>
      </div>
      <Reveal at={1.2 * fps} style={{ position: "absolute", left: 112, top: 600, width: 900, fontFamily: FONT.serif, fontSize: 50, lineHeight: 1.2, color: C.mist }}>
        A card-support assistant for Banco Cordillera that resolves on its own, and <span style={{ fontStyle: "italic", color: C.sun }}>stops for a human</span> before every card block.
      </Reveal>
      <Peak at={2.2 * fps} value="97%" label="handled correctly · 32/33 live" style={{ left: 1190, top: 240 }} />
      <Peak at={2.6 * fps} value="0 / 33" label="unsafe outcomes · baseline 3" style={{ left: 1420, top: 480 }} />
      <Peak at={3.0 * fps} value="100%" label="blocks approved by a human" style={{ left: 1100, top: 720 }} />
      <Foot right="ES · PT" />
    </AbsoluteFill>
  );
};

/* 3 ── Why cards ─────────────────────────────────────────────────────────── */
export const WhyCards: React.FC = () => {
  const { fps } = useVideoConfig();
  const row = (at: number, big: React.ReactNode, text: React.ReactNode) => (
    <Reveal at={at} style={{ display: "grid", gridTemplateColumns: "300px 1fr", alignItems: "baseline", padding: "30px 0", borderTop: `1px solid ${C.rule}` }}>
      <div style={{ fontFamily: FONT.serif, fontWeight: 600, fontSize: 110, color: C.sun, lineHeight: 1 }}>{big}</div>
      <div style={{ fontFamily: FONT.sans, fontSize: 36, color: C.mist, lineHeight: 1.35 }}>{text}</div>
    </Reveal>
  );
  return (
    <AbsoluteFill>
      <Backdrop summits={[{ x: 300, y: 1040, rings: 12, step: 48, seed: 2.2 }, { x: 1840, y: 120, rings: 7, step: 44, seed: 0.7 }]} glowX="20%" />
      <Kicker n="01" label="Why card support" />
      <Headline at={0.2 * fps} size={78} words={h("A third of the bank runs on cards, and *one wrong write* costs trust.")} style={{ position: "absolute", left: 112, top: 160, width: 1650 }} />
      <div style={{ position: "absolute", left: 112, top: 440, width: 1500 }}>
        {row(1.2 * fps, <Count to={35} at={1.2 * fps} suffix="%" />, <>of products are cards: <b style={{ color: C.snow, fontWeight: 500 }}>140,040 of 400,000</b></>)}
        {row(1.8 * fps, <Count to={35} at={1.8 * fps} suffix="%" />, <>of transactions run on cards: <b style={{ color: C.snow, fontWeight: 500 }}>1.55M of 4.43M</b></>)}
        {row(2.4 * fps, "1", <>write the assistant may request, <b style={{ color: C.snow, fontWeight: 500 }}>a card block</b>. No money ever moves.</>)}
      </div>
      <Foot right="LATAM Bank dataset · synthetic" />
    </AbsoluteFill>
  );
};

/* 4 ── Demo: the chat (reads are automatic) ──────────────────────────────── */
export const DemoChat: React.FC = () => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill>
      <Backdrop summits={[{ x: 1800, y: 1080, rings: 10, step: 50, seed: 3.3 }]} glowX="85%" />
      <Kicker n="02" label="Live demo · the public deployment" />
      <ClipWindow
        src="clips/chat-status.mp4"
        trimBefore={8.5 * fps}
        playbackRate={1.3}
        url="carlosortiz-21.github.io/…/cordillera"
        left={112}
        top={170}
        width={1100}
        height={619}
        cams={[
          { at: 0, x: 900, y: 100, w: 900 },
          { at: 5 * fps, x: 900, y: 100, w: 900 },
          { at: 11 * fps, x: 940, y: 250, w: 760 },
        ]}
      />
      <div style={{ position: "absolute", left: 1290, top: 200, width: 540 }}>
        <Headline at={0.6 * fps} size={58} words={h("Reads are *automatic.*")} />
        <div style={{ marginTop: 40 }}>
          <Step at={2 * fps} n="·" title="Asked in plain Spanish" body="Status, transactions, policies. Portuguese too." />
          <Step at={6 * fps} n="·" title="Looked up, not guessed" body="Read-only tools, each checking the card is yours." />
          <Step at={9 * fps} n="·" title="Masked on the way out" body="•••• 1382 — the model never saw the number." />
        </div>
      </div>
      <Foot right="Real recording · sped up 1.3×" />
    </AbsoluteFill>
  );
};

/* 5 ── How it works: the graph ───────────────────────────────────────────── */
type Node = { x: number; y: number; w: number; tag: string; name: string; hot?: "human" | "code" };
const NODES: Node[] = [
  { x: 112, y: 465, w: 230, tag: "No LLM", name: "PII guard" },
  { x: 382, y: 465, w: 230, tag: "Haiku 4.5", name: "Triage" },
  { x: 652, y: 375, w: 250, tag: "Sonnet 5 · read tools", name: "Executor" },
  { x: 652, y: 555, w: 250, tag: "Sonnet 5 · cited", name: "Policy RAG" },
  { x: 952, y: 465, w: 230, tag: "Fails closed", name: "Critic" },
  { x: 1232, y: 465, w: 190, tag: "Code rule", name: "HITL gate", hot: "code" },
  { x: 1472, y: 465, w: 300, tag: "interrupt() · auditor", name: "Human approval", hot: "human" },
  { x: 1472, y: 745, w: 300, tag: "No LLM · idempotent", name: "Tool executor" },
  { x: 1232, y: 745, w: 190, tag: "Re-read", name: "Verify" },
  { x: 952, y: 745, w: 230, tag: "Masked", name: "Respond" },
];
const WIRES = [
  "342,520 382,520", "612,520 632,520 632,430 652,430", "612,520 632,520 632,610 652,610",
  "902,430 927,430 927,520 952,520", "902,610 927,610 927,520 952,520", "1182,520 1232,520",
  "1422,520 1472,520", "1622,575 1622,745", "1472,800 1422,800", "1232,800 1182,800",
];
const ROUTE: [number, number][] = [[497, 520], [632, 520], [632, 430], [927, 430], [927, 520], [1622, 520], [1622, 800], [1067, 800]];

const pointOnRoute = (t: number): [number, number] => {
  const seg = ROUTE.slice(1).map((p, i) => Math.hypot(p[0] - ROUTE[i][0], p[1] - ROUTE[i][1]));
  let d = t * seg.reduce((a, b) => a + b, 0);
  for (let i = 0; i < seg.length; i++) {
    if (d <= seg[i]) {
      const k = d / seg[i];
      return [ROUTE[i][0] + (ROUTE[i + 1][0] - ROUTE[i][0]) * k, ROUTE[i][1] + (ROUTE[i + 1][1] - ROUTE[i][1]) * k];
    }
    d -= seg[i];
  }
  return ROUTE[ROUTE.length - 1];
};

export const HowItWorks: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // Pulse: travel to the human, wait there (the approval), then finish.
  const t = interpolate(frame, [4.5 * fps, 8 * fps, 9.6 * fps, 12 * fps], [0, 0.6, 0.6, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE });
  const [px, py] = pointOnRoute(t);
  const waiting = frame > 8 * fps && frame < 9.6 * fps;
  return (
    <AbsoluteFill>
      <Backdrop summits={[{ x: 960, y: 1180, rings: 13, step: 52, seed: 5.2 }]} glowX="50%" />
      <Kicker n="03" label="How it works" />
      <Headline at={0.2 * fps} size={68} words={h("Agents propose. *Code decides.* A human approves.")} style={{ position: "absolute", left: 112, top: 160, width: 1700 }} />
      <svg width={1920} height={1080} style={{ position: "absolute" }}>
        {WIRES.map((pts, i) => (
          <polyline key={pts} points={pts} pathLength={1} fill="none" strokeWidth={2.5}
            stroke={i >= 5 ? "rgba(242,169,59,0.75)" : "rgba(238,243,241,0.38)"}
            strokeDasharray={1} strokeDashoffset={1 - prog(frame, 1.2 * fps + i * 5, 20)} />
        ))}
      </svg>
      {NODES.map((n, i) => {
        const e = prog(frame, 0.9 * fps + i * 5, 18);
        const glow = n.hot === "human" && waiting ? 1 : 0;
        return (
          <div key={n.name} style={{
            position: "absolute", left: n.x, top: n.y, width: n.w, height: 110, padding: "16px 20px", boxSizing: "border-box",
            opacity: e, translate: `0px ${(1 - e) * 16}px`, background: "rgba(10,20,28,0.92)",
            border: n.hot ? `${n.hot === "code" ? "2px dashed" : "2px solid"} ${C.sun}` : `1px solid ${C.rule}`,
            boxShadow: n.hot === "human" ? `0 0 ${30 + glow * 50}px rgba(242,169,59,${0.2 + glow * 0.35})` : undefined,
          }}>
            <div style={{ fontFamily: FONT.mono, fontSize: 15, letterSpacing: "0.06em", textTransform: "uppercase", color: n.hot ? C.sun : C.glacier, whiteSpace: "nowrap" }}>{n.tag}</div>
            <div style={{ marginTop: 10, fontFamily: FONT.serif, fontWeight: 600, fontSize: 32, color: C.snow, whiteSpace: "nowrap" }}>{n.name}</div>
          </div>
        );
      })}
      <div style={{ position: "absolute", left: px - 11, top: py - 11, width: 22, height: 22, borderRadius: 11, background: C.sun, boxShadow: "0 0 26px 8px rgba(242,169,59,0.6)", opacity: frame > 4.5 * fps && t < 1 ? 1 : 0 }} />
      <Reveal at={8 * fps} style={{ position: "absolute", left: 1472, top: 395, fontFamily: FONT.mono, fontSize: 20, color: C.sun, letterSpacing: "0.04em" }}>
        waits for an auditor ≠ the requester
      </Reveal>
      <Reveal at={10 * fps} style={{ position: "absolute", left: 112, top: 930, width: 1500, fontFamily: FONT.sans, fontSize: 30, color: C.mist }}>
        The model never sees raw personal data, and every step lands in an <b style={{ color: C.snow, fontWeight: 500 }}>append-only audit trail</b>.
      </Reveal>
    </AbsoluteFill>
  );
};

/* 6 ── Demo: human in the loop (three steps of one recording) ──────────── */
const HitlCaptions: React.FC<{ active: 1 | 2 | 3 }> = ({ active }) => (
  <div style={{ position: "absolute", left: 1290, top: 240, width: 540 }}>
    <Step at={0} n="1" active={active === 1} title="Requested, then paused" body="Lía proposes the block and tells the customer it awaits a supervisor." />
    <Step at={0} n="2" active={active === 2} title="A human approves" body="Auditor role, never the requester, within 10 minutes." />
    <Step at={0} n="3" active={active === 3} title="Executed and verified" body="Code runs the block, re-reads the card, then confirms." />
  </div>
);

export const DemoHitl: React.FC<{ part: 1 | 2 | 3 }> = ({ part }) => {
  const { fps } = useVideoConfig();
  const common = { src: "clips/hitl-block.mp4", left: 112, top: 210, width: 1100, height: 619 } as const;
  return (
    <AbsoluteFill>
      <Backdrop summits={[{ x: 1800, y: 1080, rings: 10, step: 50, seed: 3.3 }]} glowX="85%" drawAt={-120} />
      <Kicker n="04" label="Live demo · a card block, end to end" />
      <Headline at={0} size={58} words={h("The write *waits for a human.*")} style={{ position: "absolute", left: 112, top: 120, width: 1300 }} />
      {part === 1 && (
        <ClipWindow {...common} url="…/cordillera · customer" trimBefore={0.4 * fps} cams={[
          { at: 0, x: 880, y: 80, w: 920 }, { at: 7 * fps, x: 920, y: 150, w: 780 },
        ]} />
      )}
      {part === 2 && (
        <ClipWindow {...common} url="…/cordillera/auditor · auditor" trimBefore={19.5 * fps} playbackRate={2.2} cams={[
          { at: 0, x: 720, y: 90, w: 780 }, { at: 8 * fps, x: 740, y: 150, w: 640 },
        ]} />
      )}
      {part === 3 && (
        <ClipWindow {...common} url="…/cordillera · customer" trimBefore={45.5 * fps} cams={[
          { at: 0, x: 900, y: 90, w: 900 }, { at: 4 * fps, x: 940, y: 330, w: 760 },
        ]} />
      )}
      <HitlCaptions active={part} />
      <Foot right={part === 2 ? "Real recording · sped up 2.2×" : "Real recording"} />
    </AbsoluteFill>
  );
};

/* 7 ── Results ───────────────────────────────────────────────────────────── */
const ResultRow: React.FC<{ at: number; label: string; sys: string; base: string; sysW: number; baseW: number; bad?: boolean }> = ({ at, label, sys, base, sysW, baseW, bad }) => {
  const frame = useCurrentFrame();
  const g = prog(frame, at + 8, 40);
  const bar = (w: number, color: string) => (
    <div style={{ height: 16, background: "rgba(238,243,241,0.06)", marginTop: 10 }}>
      <div style={{ height: "100%", width: `${Math.max(w * g, w > 0 ? 0 : 0.6)}%`, background: color }} />
    </div>
  );
  return (
    <Reveal at={at} style={{ display: "grid", gridTemplateColumns: "560px 1fr 1fr", gap: 60, alignItems: "end", padding: "18px 0", borderTop: `1px solid ${C.rule}` }}>
      <div style={{ fontFamily: FONT.sans, fontSize: 34, color: C.mist }}>{label}</div>
      <div>
        <div style={{ fontFamily: FONT.serif, fontWeight: 600, fontSize: 48, color: C.sun }}>{sys}</div>
        {bar(sysW, C.sun)}
      </div>
      <div>
        <div style={{ fontFamily: FONT.serif, fontWeight: 600, fontSize: 48, color: bad ? C.danger : C.mist }}>{base}</div>
        {bar(baseW, bad ? C.danger : "rgba(185,200,200,0.45)")}
      </div>
    </Reveal>
  );
};

export const Results: React.FC = () => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill>
      <Backdrop summits={[{ x: 1560, y: 900, rings: 11, step: 46, seed: 0.4 }]} glowX="30%" />
      <Kicker n="05" label="Results · same 33 held-out cases, live" />
      <Headline at={0.2 * fps} size={78} words={h("Same cases. *Zero unsafe outcomes.*")} style={{ position: "absolute", left: 112, top: 160, width: 1700 }} />
      <div style={{ position: "absolute", left: 112, right: 112, top: 310 }}>
        <Reveal at={0.8 * fps} style={{ display: "grid", gridTemplateColumns: "560px 1fr 1fr", gap: 60, paddingBottom: 14 }}>
          <Label>Metric</Label><Label>Lía (system)</Label><Label color={C.dim}>One LLM, all tools</Label>
        </Reveal>
        <ResultRow at={1.2 * fps} label="Handled correctly" sys="97%" base="79%" sysW={97} baseW={79} />
        <ResultRow at={2.0 * fps} label="Unsafe outcomes" sys="0 / 33" base="3 / 33" sysW={0} baseW={9.1} bad />
        <ResultRow at={2.8 * fps} label="Blocks approved by a human" sys="100%" base="0%" sysW={100} baseW={0} bad />
        <ResultRow at={3.6 * fps} label="Missed escalations" sys="0 / 9" base="3 / 9" sysW={0} baseW={33} bad />
      </div>
      <Reveal at={5 * fps} style={{ position: "absolute", left: 112, bottom: 104, fontFamily: FONT.mono, fontSize: 20, color: C.dim, letterSpacing: "0.02em" }}>
        One live run · 120-case golden set (31% Portuguese) · deterministic scoring · details in docs/EVAL_REPORT.md
      </Reveal>
      <Foot right="eval/ · golden_v1" />
    </AbsoluteFill>
  );
};

/* 8 ── Close ─────────────────────────────────────────────────────────────── */
export const Close: React.FC = () => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill>
      <Backdrop summits={[{ x: 160, y: 980, rings: 12, step: 48, seed: 6.0 }, { x: 1700, y: 1100, rings: 8, step: 50, seed: 2.9 }]} glowX="70%" />
      <Headline at={0.3 * fps} size={104} words={h("Automation where it's safe, *a human where it matters.*")} style={{ position: "absolute", left: 112, top: 300, width: 1600 }} />
      <Reveal at={2.2 * fps} style={{ position: "absolute", left: 112, top: 700, fontFamily: FONT.mono, fontSize: 28, lineHeight: 1.8, color: C.glacier }}>
        <span style={{ color: C.dim }}>demo </span>carlosortiz-21.github.io/factored-hackathon-2026-cordillera<br />
        <span style={{ color: C.dim }}>code </span>github.com/CarlosOrtiz-21/factored-hackathon-2026-cordillera
      </Reveal>
      <Reveal at={3 * fps} blur style={{ position: "absolute", right: 112, top: 84, fontFamily: FONT.serif, fontWeight: 600, fontSize: 64, color: C.snow }}>
        Lía<span style={{ color: C.sun }}>.</span>
      </Reveal>
      <Foot right="Factored AI & Data Hackathon 2026" />
    </AbsoluteFill>
  );
};
