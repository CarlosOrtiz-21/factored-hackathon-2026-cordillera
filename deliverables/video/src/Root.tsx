import React from "react";
import { Composition } from "remotion";
import { FPS, LiaVideo, TOTAL_FRAMES } from "./LiaVideo";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="LiaDemo"
        component={LiaVideo}
        durationInFrames={TOTAL_FRAMES}
        fps={FPS}
        width={1920}
        height={1080}
        // Put a royalty-free track in public/ and set its name here, e.g. "music.mp3".
        defaultProps={{ music: null }}
      />
    </>
  );
};
