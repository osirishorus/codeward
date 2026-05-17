import { AbsoluteFill, Sequence, useVideoConfig } from "remotion";
import {
  MapScene,
  OutroScene,
  PreflightScene,
  RoutesScene,
  SymbolScene,
  TitleScene,
} from "./scenes";

// Scene durations in seconds. Sum is the composition length — keep in sync
// with `durationInFrames` in Root.tsx (= TOTAL_SECONDS * fps).
const TIMELINE: { component: React.FC; seconds: number }[] = [
  { component: TitleScene, seconds: 3 },
  { component: MapScene, seconds: 5 },
  { component: RoutesScene, seconds: 6 },
  { component: PreflightScene, seconds: 8 },
  { component: SymbolScene, seconds: 6 },
  { component: OutroScene, seconds: 3 },
];

export const TOTAL_SECONDS = TIMELINE.reduce((s, x) => s + x.seconds, 0);

export const MyComposition = () => {
  const { fps } = useVideoConfig();
  let cursor = 0;
  return (
    <AbsoluteFill>
      {TIMELINE.map(({ component: Scene, seconds }, i) => {
        const from = cursor * fps;
        const dur = seconds * fps;
        cursor += seconds;
        return (
          <Sequence key={i} from={from} durationInFrames={dur} layout="none">
            <Scene />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
