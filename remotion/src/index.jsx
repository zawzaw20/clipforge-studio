import React from "react";
import {registerRoot, Composition, AbsoluteFill, Video, useCurrentFrame, useVideoConfig} from "remotion";

const Captions = ({items=[]}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const active = items.filter(x => frame >= Math.floor(x.start*fps) && frame < Math.ceil(x.end*fps));
  const line = active.map(x => x.text).join(" ").trim();
  if (!line) return null;
  return <AbsoluteFill style={{justifyContent:"flex-end",alignItems:"center",paddingBottom:90,pointerEvents:"none"}}>
    <div style={{maxWidth:"88%",padding:"12px 24px",borderRadius:14,background:"rgba(0,0,0,.76)",color:"#fff",fontFamily:"Arial,sans-serif",fontWeight:900,fontSize:48,textAlign:"center",textShadow:"0 3px 8px #000"}}>{line}</div>
  </AbsoluteFill>;
};
const ClipForge = ({src,items=[],durationInFrames=900}) => <AbsoluteFill style={{background:"#000"}}>
  <Video src={src} style={{width:"100%",height:"100%",objectFit:"contain"}} />
  <Captions items={items}/>
</AbsoluteFill>;
const Root = () => <Composition id="ClipForge" component={ClipForge} width={1920} height={1080} fps={30} durationInFrames={900} defaultProps={{src:"",items:[],durationInFrames:900}} calculateMetadata={({props})=>({durationInFrames:props.durationInFrames||900})}/>;
registerRoot(Root);
