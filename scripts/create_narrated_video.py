"""Prepare and locally render a narrated ChunkVault explainer in a browser."""
from __future__ import annotations

import argparse
import http.server
import json
import math
import os
import socketserver
import subprocess
import threading
import wave
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "docs" / ".video_work"
VIDEO = ROOT / "docs" / "ChunkVault_End_to_End.webm"
PORT = 8765

SEGMENTS = [
    ("The storage problem", "ChunkVault is a local storage engine built to reduce repeated file data. Instead of treating every upload as one indivisible blob, it looks inside the file, finds reusable pieces, and stores each distinct piece once. The result is a file archive whose metadata can point many files to the same chunk bytes."),
    ("From browser to backend", "A user selects a file in the React interface. The browser sends it as a multipart request to the FastAPI service. The API validates the request and hands the stream to the upload orchestrator. ChunkVault runs as one sequential backend process on one machine; the browser is only the control surface, while storage logic stays on the server."),
    ("Stage, then find boundaries", "Before processing chunks, the upload is streamed into a staging file. While it is copied, ChunkVault enforces its upload limit and computes a SHA-256 digest for the whole file. A rolling Rabin-style fingerprint then scans a sliding window of bytes and chooses content-defined chunk boundaries. Because boundaries follow content, a small insertion does not shift every later boundary."),
    ("Identify and deduplicate", "Each chunk receives its own SHA-256 hash. That hash is the chunk's stable identity and later verifies its integrity. The deduplication manager checks the metadata and verifies bytes already in the store. If the content exists, the upload reuses it. If it is new, ChunkVault stages the chunk, flushes it to disk, and atomically publishes it under a path derived from its hash."),
    ("Commit safely across two stores", "ChunkVault separates payload bytes from metadata: bytes live in the local content-addressed chunk store, and relationships live in SQLite. Since a filesystem operation and a SQLite transaction cannot commit together, upload ordering matters. The bytes are published first. Only after every required chunk is present does one SQLite transaction register chunks, add the ordered file manifest, mark the file complete, and finish the operation."),
    ("The manifest links it all", "The files table records the filename, original size, whole-file digest, and status. The chunks table records each unique hash, its size, and its reference count. The file_chunks table maps a file to chunk hashes in order. When another file reuses a chunk, its manifest points to the same hash and the reference count increases. Thus repeated content adds references instead of another payload copy."),
    ("Restore and verify", "To restore, the service loads the file's ordered manifest and reads each chunk from the store. Every chunk's size and SHA-256 are checked before its bytes are written to the output. As the file is reassembled, ChunkVault also checks the final size and whole-file digest. A missing chunk, damaged chunk, or mismatched manifest causes a failed restore, so corrupted content is not reported as a successful download."),
    ("Delete and recover", "Deleting a file removes its manifest references and updates counts. Shared chunks remain available to other files; payloads with no remaining references can be removed. A read-only integrity audit compares SQLite with the chunk store, and an explicit reconciliation step cleans up crash leftovers. Upload failure handling also removes staged data and unreferenced chunks it created."),
    ("What this project is", "The interface also exposes chunk inspection, comparison, storage statistics, integrity checks, benchmarks, and a capacity simulation. Those tools explain and measure the same pipeline. ChunkVault is a single-machine prototype with one writer and no distributed mode. Its central idea is simple: detect content boundaries, address chunks by cryptographic hash, and verify the bytes whenever files are rebuilt."),
]

HTML = r'''<!doctype html><meta charset="utf-8"><title>ChunkVault narrated explainer renderer</title>
<style>html,body{margin:0;background:#07101e;color:#dceaff;font:16px Segoe UI,Arial}body{display:grid;place-items:center;min-height:100vh}canvas{width:min(96vw,1280px);height:auto;aspect-ratio:16/9;background:#081321;box-shadow:0 18px 70px #0008}#status{position:fixed;bottom:12px;color:#92a8c5}button{position:fixed;top:14px;right:18px;padding:10px 18px;border:1px solid #40d7df;border-radius:8px;background:#102238;color:#dceaff;font-size:16px}</style>
<canvas id="v" width="1280" height="720"></canvas><button id="start">Render narrated video</button><div id="status">Ready · 1280×720 · voice-over included</div>
<script>
const cues=__CUES__;
const canvas=document.querySelector('#v'),ctx=canvas.getContext('2d'),btn=document.querySelector('#start'),status=document.querySelector('#status');
const W=1280,H=720,duration=cues[cues.length-1].end,colors=['#57a5ff','#46d6e5','#b293ff','#51dfa8','#ffbf67'];
let audio,audioCtx,source,recorder,chunks=[],began=0,finished=false;
function rr(x,y,w,h,r,fill,stroke){ctx.beginPath();ctx.roundRect(x,y,w,h,r);ctx.fillStyle=fill;ctx.fill();if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.stroke()}}
function text(s,x,y,size=24,color='#e7f1ff',weight=500,align='left'){ctx.font=`${weight} ${size}px Segoe UI,Arial`;ctx.fillStyle=color;ctx.textAlign=align;ctx.fillText(s,x,y)}
function wrap(s,x,y,max,size,color,align='center'){ctx.font=`500 ${size}px Segoe UI,Arial`;const words=s.split(' '),lines=[];let line='';for(const w of words){const q=(line+' '+w).trim();if(ctx.measureText(q).width>max&&line){lines.push(line);line=w}else line=q}if(line)lines.push(line);lines.forEach((t,i)=>text(t,x,y+i*(size+9),size,color,500,align));return lines.length*(size+9)}
function curve(points,color,width=4){ctx.beginPath();ctx.moveTo(points[0][0],points[0][1]);for(let i=1;i<points.length;i++)ctx.lineTo(points[i][0],points[i][1]);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.lineCap='round';ctx.stroke()}
function arrow(x1,y1,x2,y2,c){curve([[x1,y1],[x2,y2]],c,4);const a=Math.atan2(y2-y1,x2-x1);ctx.beginPath();ctx.moveTo(x2,y2);ctx.lineTo(x2-13*Math.cos(a-.55),y2-13*Math.sin(a-.55));ctx.lineTo(x2-13*Math.cos(a+.55),y2-13*Math.sin(a+.55));ctx.closePath();ctx.fillStyle=c;ctx.fill()}
function pill(label,x,y,c){rr(x,y,150,42,20,'#14253a',c);text(label,x+75,y+27,15,c,700,'center')}
function chunk(x,y,w,c,label){rr(x,y,w,54,10,'#112239',c);text(label,x+w/2,y+33,16,'#e8f1ff',600,'center')}
function node(x,y,w,h,title,sub,c){rr(x,y,w,h,16,'#102039',c);text(title,x+w/2,y+34,18,c,700,'center');text(sub,x+w/2,y+66,14,'#d5e2f3',500,'center')}
function frame(ms){const t=Math.min(Math.max(0,(ms-began)/1000),duration);let idx=0;for(let i=0;i<cues.length;i++){if(t>=cues[i].start)idx=i}const cue=cues[idx],u=Math.max(0,Math.min(1,(t-cue.start)/(cue.end-cue.start))),scene=idx;
ctx.clearRect(0,0,W,H);const g=ctx.createLinearGradient(0,0,W,H);g.addColorStop(0,'#091424');g.addColorStop(1,'#07101c');ctx.fillStyle=g;ctx.fillRect(0,0,W,H);
// Low contrast moving particles create a continuous technical backdrop.
for(let i=0;i<58;i++){const x=((i*197+t*13)%W),y=(i*83%570)+22;ctx.globalAlpha=.12+(i%3)*.035;ctx.fillStyle=colors[i%5];ctx.beginPath();ctx.arc(x,y,i%4===0?2:1,0,7);ctx.fill()}ctx.globalAlpha=1;
text('CHUNKVAULT',54,54,17,'#54d9e5',700);text('END-TO-END SYSTEM WALKTHROUGH',54,82,12,'#829ab8',600);
// Persistent animated byte stream crosses the upper field; chunks join it as the story progresses.
const flowX=(t*110)%1500;for(let k=0;k<16;k++){const x=((k*105-flowX+1600)%1500)-100;rr(x,120+(k%2)*8,50,18,6,k%3===0?'#18324e':'#10243a',colors[k%5])}
const titles=['The storage problem','From browser to backend','Stage, then find boundaries','Identify and deduplicate','Commit safely across two stores','The manifest links it all','Restore and verify','Delete and recover','What this project is'];
text(titles[scene],W/2,196,36,'#f1f6ff',700,'center');
// Scenes are stages of one connected flow; the animated marker advances as narration progresses.
const stageX=[120,250,390,530,670,810,950,1090,1190];
curve(stageX.map((x,i)=>[x,284+(i%2)*7]),'#29415b',3);
for(let i=0;i<stageX.length;i++){const active=i<=scene;const c=active?colors[i%5]:'#344962';ctx.beginPath();ctx.arc(stageX[i],284+(i%2)*7,active?10:6,0,7);ctx.fillStyle=c;ctx.fill();if(i<scene)pill(['UI','API','CDC','HASH','STORE','DB','RESTORE','AUDIT','DONE'][i],stageX[i],308,c)}
const marker=stageX[scene]+(scene<stageX.length-1?(stageX[scene+1]-stageX[scene])*u:0);ctx.beginPath();ctx.arc(marker,284+(scene%2)*7,15,0,7);ctx.fillStyle=colors[scene%5];ctx.shadowColor=colors[scene%5];ctx.shadowBlur=22;ctx.fill();ctx.shadowBlur=0;
// Draw active system animation for each stage.
if(scene===0){
  for(let i=0;i<3;i++){const x=300+i*260+Math.sin(t*1.4+i)*18;rr(x,390,170,100,13,'#12233a',colors[i]);text('file 0'+(i+1),x+85,430,18,'#e8f1ff',700,'center');for(let j=0;j<3;j++)rr(x+25+j*40,452,28,18,5,colors[(i+j)%5])}
  wrap('Repeated files contain repeated bytes.',W/2,550,720,22,'#9bb0ca');
} else if(scene===1){
  node(135,390,190,94,'React UI','choose + upload',colors[0]);node(410,390,190,94,'FastAPI','validate + route',colors[1]);node(685,390,210,94,'Uploader','coordinate pipeline',colors[2]);node(985,350,190,74,'SQLite','metadata',colors[3]);node(985,455,190,74,'Chunk store','payload bytes',colors[4]);arrow(325,435,410,435,colors[1]);arrow(600,435,685,435,colors[2]);arrow(895,420,985,385,colors[3]);arrow(895,448,985,492,colors[4]);
} else if(scene===2){
  node(100,390,180,88,'Staging file','stream + whole hash',colors[0]);arrow(280,434,350,434,colors[1]);
  const starts=[350,485,670,800,990],widths=[135,185,130,190,150];for(let i=0;i<starts.length;i++){const x=starts[i],w=widths[i];chunk(x,407,w,colors[(i+1)%5],'chunk '+(i+1));for(let b=0;b<Math.floor(w/14);b++){let p=(b*19+t*55)%w;ctx.fillStyle=colors[(i+b)%5];ctx.globalAlpha=.45;ctx.fillRect(x+p,476,3,7);ctx.globalAlpha=1}}text('rolling fingerprint · variable boundaries',650,535,19,'#a9bdd5',600,'center');
} else if(scene===3){
  for(let i=0;i<5;i++){const x=155+i*205;chunk(x,396,150,colors[i],`chunk ${i+1}`);arrow(x+75,452,x+75,493,colors[i]);text('SHA-256',x+75,523,14,'#d8e7f9',700,'center');text(['a17f…','80d2…','a17f…','3c91…','b2ae…'][i],x+75,550,17,colors[i],700,'center')}
  rr(250,576,780,35,17,'#13283a',colors[3]);text('matching hash → reuse  ·  new hash → publish once',640,600,15,'#b7cadf',600,'center');
} else if(scene===4){
  node(125,390,230,84,'1  Publish bytes','fsync + atomic rename',colors[4]);node(525,390,230,84,'2  Check presence','all required chunks',colors[1]);node(925,390,230,84,'3  Commit SQLite','manifest + complete',colors[3]);arrow(355,432,525,432,colors[1]);arrow(755,432,925,432,colors[3]);
  // Visual two-phase commit ribbon
  curve([[150,525],[350,525],[350,555],[930,555],[930,525],[1130,525]],'#536d88',4);text('payloads become durable before the metadata transaction commits',640,590,16,'#a9bdd5',500,'center');
} else if(scene===5){
  node(130,385,250,150,'files','name · size · file SHA-256',colors[0]);node(515,385,250,150,'file_chunks','ordered hash references',colors[1]);node(900,385,250,150,'chunks','unique bytes · ref_count',colors[4]);arrow(380,460,515,460,colors[1]);arrow(765,460,900,460,colors[4]);
  for(let i=0;i<4;i++){const y=408+i*31;ctx.beginPath();ctx.arc(870,y,5,0,7);ctx.fillStyle=colors[i];ctx.fill();curve([[870,y],[900,y]],colors[i],2)}
  text('many file manifests can point to the same chunk identity',640,575,17,'#a9bdd5',500,'center');
} else if(scene===6){
  const xs=[190,380,570,760,950];for(let i=0;i<xs.length;i++){const bob=Math.sin(t*2+i)*7;chunk(xs[i],405+bob,150,colors[i],`chunk ${i+1}`);if(i<xs.length-1)arrow(xs[i]+150,432+bob,xs[i+1],432,colors[i])}
  rr(250,520,780,44,20,'#102a33',colors[3]);text('verify every chunk  →  verify size + whole-file digest',640,549,17,'#a9f0d4',700,'center');
} else if(scene===7){
  node(135,390,220,110,'delete file','remove manifest links',colors[0]);node(520,370,230,80,'shared chunk','ref_count stays positive',colors[1]);node(520,470,230,80,'exclusive chunk','remove when unused',colors[4]);node(925,390,220,110,'integrity audit','detect + reconcile',colors[2]);arrow(355,430,520,410,colors[1]);arrow(355,455,520,510,colors[4]);arrow(750,410,925,430,colors[2]);
} else {
  const labels=['Inspect chunks','Compare files','Storage stats','Benchmarks','Capacity model'];for(let i=0;i<labels.length;i++){const x=95+i*220, yy=390+Math.sin(t*1.5+i)*16;rr(x,yy,185,78,18,'#12243b',colors[i]);text(labels[i],x+92,yy+48,16,colors[i],700,'center')}
  text('single machine  ·  one writer  ·  no distributed mode',640,525,18,'#b8cbe0',600,'center');
}
// Lower-third narration caption and synced progress.
rr(64,624,1152,60,12,'#0e1c2d','#203650');wrap(cue.caption,W/2,651,1090,17,'#e1ebf7');rr(64,700,1152,6,3,'#1a2b40');rr(64,700,1152*t/duration,6,3,'#4bd7df');
if(recorder&&recorder.state==='recording'&&!finished){if(t>=duration){finished=true;recorder.stop();audio.pause();status.textContent='Finalizing narrated video…'}else status.textContent=`Rendering narrated video · ${Math.floor(t)} / ${Math.floor(duration)} seconds`}
if(!finished)requestAnimationFrame(frame);
}
async function start(){btn.disabled=true;status.textContent='Starting narration and animation…';audioCtx=new AudioContext();audio= new Audio('/narration.wav');await audioCtx.resume();await new Promise((resolve,reject)=>{audio.oncanplaythrough=resolve;audio.onerror=()=>reject(Error('Voice-over audio did not load'));audio.load()});
const audioStream=audioCtx.createMediaStreamDestination();source=audioCtx.createMediaElementSource(audio);source.connect(audioStream);source.connect(audioCtx.destination);
const videoStream=canvas.captureStream(30),combined=new MediaStream([...videoStream.getVideoTracks(),...audioStream.stream.getAudioTracks()]);
const mime=['video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus','video/webm'].find(x=>MediaRecorder.isTypeSupported(x));recorder=new MediaRecorder(combined,{mimeType:mime,videoBitsPerSecond:4500000,audioBitsPerSecond:128000});chunks=[];recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};recorder.onstop=async()=>{const blob=new Blob(chunks,{type:mime});status.textContent='Saving the video…';try{const response=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:blob});const result=await response.json();status.textContent=`Video ready · ${result.size_mb} MB · voice-over included`;btn.textContent='Render again';btn.disabled=false}catch(e){status.textContent='Could not save video: '+e.message;btn.disabled=false}};
began=performance.now();recorder.start(1000);audio.currentTime=0;await audio.play();requestAnimationFrame(frame)}
btn.addEventListener('click',()=>start().catch(e=>{status.textContent='Render error: '+e.message;btn.disabled=false}));
</script>'''


def write_phrases() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    phrases = [text for _, text in SEGMENTS]
    phrase_file = WORK / "phrases.json"
    phrase_file.write_text(json.dumps(phrases, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote narration text to {phrase_file}")


def assemble() -> None:
    segments, params = [], None
    for i, (title, phrase) in enumerate(SEGMENTS):
        path = WORK / f"part-{i:02d}.wav"
        with wave.open(str(path), "rb") as wav:
            p = wav.getparams()
            if params is None:
                params = p
            elif (p.nchannels, p.sampwidth, p.framerate) != (params.nchannels, params.sampwidth, params.framerate):
                raise RuntimeError("TTS segments use different audio formats")
            raw = wav.readframes(wav.getnframes())
            segments.append((title, phrase, raw, wav.getnframes() / p.framerate))
    cues, payload, cursor = [], bytearray(), 0.0
    silence = b"\0" * int(params.framerate * 0.22) * params.nchannels * params.sampwidth
    for title, phrase, raw, seconds in segments:
        caption = phrase if len(phrase) <= 150 else phrase[:147].rsplit(" ", 1)[0] + "…"
        cues.append({"title": title, "text": phrase, "caption": caption, "start": cursor, "end": cursor + seconds})
        payload.extend(raw)
        payload.extend(silence)
        cursor += seconds + 0.22
    with wave.open(str(WORK / "narration.wav"), "wb") as out:
        out.setnchannels(params.nchannels); out.setsampwidth(params.sampwidth); out.setframerate(params.framerate)
        out.writeframes(bytes(payload))
    html = HTML.replace("__CUES__", json.dumps(cues, ensure_ascii=False))
    (WORK / "index.html").write_text(html, encoding="utf-8")
    print(f"Prepared {cursor:.1f} seconds of narration in {WORK}")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WORK), **kwargs)

    def do_POST(self):
        if urlparse(self.path).path != "/save":
            self.send_error(404); return
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 800_000_000:
            self.send_error(413); return
        data = self.rfile.read(size)
        VIDEO.write_bytes(data)
        body = json.dumps({"size_mb": round(len(data) / (1024 * 1024), 1)}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args):
        pass


def serve():
    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    httpd = Server(("127.0.0.1", PORT), Handler)
    print(f"Renderer ready at http://127.0.0.1:{PORT}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve()
    elif args.assemble:
        assemble()
    else:
        write_phrases()
