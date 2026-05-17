import "./index.css";
import { Composition } from "remotion";
import { MyComposition, TOTAL_SECONDS } from "./Composition";

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="CodewardDemo"
        component={MyComposition}
        durationInFrames={Math.round(TOTAL_SECONDS * FPS)}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
      />
    </>
  );
};
