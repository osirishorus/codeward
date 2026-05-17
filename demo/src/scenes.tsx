import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {
  mapOutput,
  preflightOutput,
  routesOutput,
  symbolOutput,
} from "./captures";

// ────────────────────────────────────────────────────────────────────────────
// Design tokens. Single source of truth — every scene reads these so the
// whole video shifts visually if we change one value here.
// ────────────────────────────────────────────────────────────────────────────

const BG = "#0d1117";          // GitHub dark
const PANEL = "#161b22";
const BORDER = "#30363d";
const FG = "#c9d1d9";
const MUTED = "#8b949e";
const ACCENT = "#7ee787";      // green (success / verbs)
const WARN = "#f0883e";        // orange (HIGH blast radius)
const BRAND = "#a371f7";       // purple (Codeward)
const MONO = "ui-monospace, 'JetBrains Mono', 'Fira Code', 'SF Mono', monospace";
const SANS = "Inter, -apple-system, system-ui, sans-serif";

// ────────────────────────────────────────────────────────────────────────────
// Animation helpers
// ────────────────────────────────────────────────────────────────────────────

const EASE = Easing.bezier(0.16, 1, 0.3, 1);

/** Fade + slide-up over `dur` seconds starting at `delay` seconds. */
const useEntrance = (delay: number, dur: number) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = interpolate(
    frame,
    [delay * fps, (delay + dur) * fps],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE },
  );
  return { opacity: t, transform: `translateY(${(1 - t) * 12}px)` };
};

/**
 * Typewriter reveal: returns the leading slice of `text` matching the current
 * frame, plus a blinking cursor. `cps` = chars per second. The reveal starts
 * at `delay` (seconds). After the text is fully revealed, the cursor keeps
 * blinking until `hideCursorAfter` seconds.
 */
const useTyped = (
  text: string,
  delay: number,
  cps: number,
  hideCursorAfter?: number,
) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const elapsed = Math.max(0, frame / fps - delay);
  const shown = Math.min(text.length, Math.floor(elapsed * cps));
  const done = shown >= text.length;
  const cursorOn = Math.floor((frame / fps) * 2) % 2 === 0;
  const cursorVisible =
    hideCursorAfter == null ? true : frame / fps < hideCursorAfter;
  return {
    body: text.slice(0, shown),
    cursor: cursorVisible && (cursorOn || !done) ? "▍" : " ",
  };
};

// ────────────────────────────────────────────────────────────────────────────
// Shared chrome
// ────────────────────────────────────────────────────────────────────────────

const TerminalWindow: React.FC<{
  children: React.ReactNode;
  title?: string;
}> = ({ children, title = "codeward — fastapi" }) => (
  <div
    style={{
      width: "82%",
      maxWidth: 1500,
      background: PANEL,
      border: `1px solid ${BORDER}`,
      borderRadius: 12,
      overflow: "hidden",
      boxShadow: "0 24px 60px rgba(0,0,0,0.45)",
      fontFamily: MONO,
    }}
  >
    <div
      style={{
        height: 40,
        background: "#0b1018",
        borderBottom: `1px solid ${BORDER}`,
        display: "flex",
        alignItems: "center",
        paddingLeft: 16,
        gap: 8,
      }}
    >
      <span style={{ width: 12, height: 12, borderRadius: 6, background: "#ff5f56" }} />
      <span style={{ width: 12, height: 12, borderRadius: 6, background: "#ffbd2e" }} />
      <span style={{ width: 12, height: 12, borderRadius: 6, background: "#27c93f" }} />
      <span style={{ marginLeft: 16, fontSize: 13, color: MUTED }}>{title}</span>
    </div>
    <div style={{ padding: "28px 32px", color: FG, fontSize: 22, lineHeight: 1.45 }}>
      {children}
    </div>
  </div>
);

const Caption: React.FC<{ text: string; delay?: number }> = ({
  text,
  delay = 0,
}) => {
  const s = useEntrance(delay, 0.6);
  return (
    <div
      style={{
        ...s,
        marginTop: 32,
        fontFamily: SANS,
        fontSize: 24,
        color: MUTED,
        letterSpacing: 0.2,
      }}
    >
      {text}
    </div>
  );
};

/**
 * Color-pass the rendered ANSI-ish text. We keep this naive: highlight the
 * `$ ` prompt + command, headings starting with `#`, HTTP verbs, arrow `→`,
 * the `HIGH` blast-radius badge, and parenthetical file:line refs. Anything
 * not matched stays foreground color.
 */
const Pretty: React.FC<{ text: string }> = ({ text }) => {
  const lines = text.split("\n");
  return (
    <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: MONO }}>
      {lines.map((line, i) => (
        <div key={i}>{colorize(line)}</div>
      ))}
    </pre>
  );
};

const colorize = (line: string): React.ReactNode => {
  if (line.startsWith("$ ")) {
    return (
      <>
        <span style={{ color: ACCENT }}>$ </span>
        <span style={{ color: FG }}>{line.slice(2)}</span>
      </>
    );
  }
  if (line.startsWith("# ")) {
    return <span style={{ color: BRAND, fontWeight: 600 }}>{line}</span>;
  }
  // Verb highlighting + → arrow + file:line
  const verbMatch = /^(\s+)(GET|POST|PUT|PATCH|DELETE|ANY|HEAD|OPTIONS)\b/.exec(line);
  if (verbMatch) {
    const rest = line.slice(verbMatch[0].length);
    return (
      <>
        <span>{verbMatch[1]}</span>
        <span style={{ color: ACCENT, fontWeight: 600 }}>{verbMatch[2]}</span>
        {colorRest(rest)}
      </>
    );
  }
  return <span>{colorRest(line)}</span>;
};

const colorRest = (s: string): React.ReactNode => {
  // split on file:line refs and arrow
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  const re = /(→)|(\(([^)]+:\d+)\))|(\bHIGH\b)|(blast_radius=)|(\bsymbols=)|(\bdependents \(\d+\))|(\blikely tests:)|(\brecommended:)|(\bDefined:)|(\bCallers:)|(\bMethods:)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    if (m.index > cursor) parts.push(s.slice(cursor, m.index));
    if (m[1]) parts.push(<span style={{ color: BRAND }}>{m[1]}</span>);
    else if (m[2]) parts.push(<span style={{ color: MUTED }}>{m[2]}</span>);
    else if (m[4]) parts.push(<span style={{ color: WARN, fontWeight: 700 }}>{m[4]}</span>);
    else parts.push(<span style={{ color: MUTED }}>{m[0]}</span>);
    cursor = m.index + m[0].length;
  }
  if (cursor < s.length) parts.push(s.slice(cursor));
  return parts;
};

// ────────────────────────────────────────────────────────────────────────────
// Scenes
// ────────────────────────────────────────────────────────────────────────────

export const TitleScene: React.FC = () => {
  const titleStyle = useEntrance(0.2, 0.7);
  const subStyle = useEntrance(0.7, 0.7);
  const targetStyle = useEntrance(1.3, 0.6);
  return (
    <AbsoluteFill
      style={{
        background: BG,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          ...titleStyle,
          fontFamily: SANS,
          fontSize: 128,
          fontWeight: 800,
          color: FG,
          letterSpacing: -2,
        }}
      >
        Codeward
      </div>
      <div
        style={{
          ...subStyle,
          marginTop: 18,
          fontFamily: SANS,
          fontSize: 32,
          color: MUTED,
          maxWidth: 1100,
          textAlign: "center",
          lineHeight: 1.35,
        }}
      >
        Symbol-level code intelligence for AI coding agents.
      </div>
      <div
        style={{
          ...targetStyle,
          marginTop: 56,
          fontFamily: MONO,
          fontSize: 22,
          color: ACCENT,
        }}
      >
        Demo target: <span style={{ color: FG }}>fastapi/fastapi</span>
        <span style={{ color: MUTED }}>  ·  1,129 files  ·  ~50k LOC</span>
      </div>
    </AbsoluteFill>
  );
};

export const MapScene: React.FC = () => {
  const { body, cursor } = useTyped("codeward map", 0.2, 16, 1.5);
  const out = useEntrance(1.5, 0.5);
  return (
    <AbsoluteFill
      style={{
        background: BG,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
      }}
    >
      <TerminalWindow>
        <div>
          <span style={{ color: ACCENT }}>$ </span>
          {body}
          <span style={{ color: BRAND }}>{cursor}</span>
        </div>
        <div style={{ ...out, marginTop: 18 }}>
          <Pretty text={mapOutput} />
        </div>
      </TerminalWindow>
      <Caption text="Orientation in one call. 1129 files indexed by symbol density and dependents." delay={2.4} />
    </AbsoluteFill>
  );
};

export const RoutesScene: React.FC = () => {
  const { body, cursor } = useTyped(
    "codeward routes --filter /items --method GET docs_src/security",
    0.2,
    24,
    1.8,
  );
  const out = useEntrance(1.8, 0.5);
  return (
    <AbsoluteFill
      style={{
        background: BG,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
      }}
    >
      <TerminalWindow>
        <div>
          <span style={{ color: ACCENT }}>$ </span>
          {body}
          <span style={{ color: BRAND }}>{cursor}</span>
        </div>
        <div style={{ ...out, marginTop: 18 }}>
          <Pretty text={routesOutput.split("\n").slice(2).join("\n")} />
        </div>
      </TerminalWindow>
      <Caption text="Framework-aware: FastAPI, Flask, Django, Express, NestJS, Spring, Gin, Rails, Laravel, ASP.NET." delay={2.7} />
    </AbsoluteFill>
  );
};

export const PreflightScene: React.FC = () => {
  // Two-panel: top is the model's intent, bottom is what codeward injects.
  const intent = useEntrance(0.2, 0.5);
  const injected = useEntrance(1.4, 0.7);
  return (
    <AbsoluteFill
      style={{
        background: BG,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 20,
      }}
    >
      <div
        style={{
          ...intent,
          width: "82%",
          maxWidth: 1500,
          background: "#1f1f29",
          border: `1px solid ${BORDER}`,
          borderRadius: 12,
          padding: "20px 28px",
          fontFamily: SANS,
          color: MUTED,
          fontSize: 22,
        }}
      >
        <span style={{ color: BRAND, fontWeight: 600 }}>Agent (Claude/Codex) →</span>{" "}
        <span style={{ color: FG, fontFamily: MONO }}>
          apply_patch fastapi/routing.py
        </span>
      </div>
      <div style={{ ...injected }}>
        <TerminalWindow title="codeward preflight (auto-injected via PreToolUse hook)">
          <Pretty text={preflightOutput} />
        </TerminalWindow>
      </div>
      <Caption
        text="The headline feature: dependents, tests, side-effects, and blast-radius pushed in BEFORE the edit happens."
        delay={2.5}
      />
    </AbsoluteFill>
  );
};

export const SymbolScene: React.FC = () => {
  const { body, cursor } = useTyped("codeward symbol APIRoute", 0.2, 18, 1.8);
  const out = useEntrance(1.8, 0.5);
  return (
    <AbsoluteFill
      style={{
        background: BG,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
      }}
    >
      <TerminalWindow>
        <div>
          <span style={{ color: ACCENT }}>$ </span>
          {body}
          <span style={{ color: BRAND }}>{cursor}</span>
        </div>
        <div style={{ ...out, marginTop: 18 }}>
          <Pretty text={symbolOutput} />
        </div>
      </TerminalWindow>
      <Caption text="Definition + 11 ranked callers across 7 files. No grep, no cat, no guessing." delay={2.6} />
    </AbsoluteFill>
  );
};

export const OutroScene: React.FC = () => {
  const fade = useEntrance(0.2, 0.7);
  const install = useEntrance(0.9, 0.6);
  const link = useEntrance(1.5, 0.6);
  return (
    <AbsoluteFill
      style={{
        background: BG,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          ...fade,
          fontFamily: SANS,
          fontSize: 56,
          fontWeight: 700,
          color: FG,
          letterSpacing: -0.5,
        }}
      >
        Install in one command
      </div>
      <div
        style={{
          ...install,
          marginTop: 36,
          background: PANEL,
          border: `1px solid ${BORDER}`,
          borderRadius: 10,
          padding: "20px 32px",
          fontFamily: MONO,
          fontSize: 30,
        }}
      >
        <span style={{ color: ACCENT }}>$ </span>
        <span style={{ color: FG }}>pipx install codeward</span>
      </div>
      <div
        style={{
          ...link,
          marginTop: 48,
          fontFamily: SANS,
          fontSize: 24,
          color: MUTED,
        }}
      >
        github.com/osirishorus/codeward
      </div>
    </AbsoluteFill>
  );
};
