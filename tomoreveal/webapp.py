"""The same controls in a web browser (optional; python -m tomoreveal app)."""
import json
import os
import re
import shutil
import sys
import tempfile
import threading

from .common import UserError, __version__
from .studio import Studio


def make_handler(studio):
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs, unquote

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers(); self.wfile.write(body)

        def _json(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _guard(self, fn):
            try:
                self._send(200, fn())
            except UserError as exc:
                self._send(400, dict(error=str(exc)))
            except Exception as exc:
                self._send(500, dict(error=f"{type(exc).__name__}: {exc}"))

        def _allowed(self):
            """Only TomoReveal's own page: Host is this server, and Origin (when sent) is this server too.
            Also refuses a Content-Length that is not a whole number (a negative one would block, then still run)."""
            own = (f"127.0.0.1:{self.server.server_address[1]}", f"localhost:{self.server.server_address[1]}")
            origin = self.headers.get("Origin")
            if not (self.headers.get("Host") in own and (origin is None or origin in tuple("http://" + h for h in own))):
                code, why = 403, "refused: this request did not come from TomoReveal's own page"
            elif not (self.headers.get("Content-Length") or "0").strip().isdigit():
                code, why = 400, "refused: the Content-Length is not a whole number"
            else:
                return True
            self.close_connection = True  # the unread body must never be taken as a second request
            self._send(code, dict(error=why), extra={"Connection": "close"})
            return False

        def do_GET(self):
            if not self._allowed():
                return
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self._guard(lambda: dict(info=studio.info(), job=studio.progress()))
            if u.path == "/api/progress":
                return self._guard(studio.progress)
            if u.path == "/api/lastframe":
                return self._send(200, studio.thumb or b"", "image/jpeg")
            if u.path.startswith("/out/"):
                return self._file(os.path.join(studio.outdir, os.path.basename(unquote(u.path[5:]))))
            self._send(404, dict(error="not found"))

        def _file(self, path):
            if not os.path.isfile(path):
                return self._send(404, dict(error="not found"))
            size = os.path.getsize(path); start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    return self._send(416, dict(error="that byte range is not in the file"), extra={"Content-Range": f"bytes */{size}"})
                self.send_response(206); self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", "video/mp4" if path.lower().endswith(".mp4") else "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Length", str(end - start + 1)); self.end_headers()
            with open(path, "rb") as f:
                f.seek(start); left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def do_POST(self):
            if not self._allowed():
                return
            u = urlparse(self.path)
            if u.path == "/api/upload":
                name = os.path.basename(unquote(parse_qs(u.query).get("name", ["upload.bin"])[0])) or "upload.bin"
                n = int(self.headers.get("Content-Length") or 0); folder = tempfile.mkdtemp(dir=studio.work, prefix="upload_")
                dest = os.path.join(folder, name)  # a folder per upload: two files with one name are both kept
                try:
                    with open(dest, "wb") as f:
                        while n > 0:
                            chunk = self.rfile.read(min(1 << 20, n))
                            if not chunk:
                                break
                            f.write(chunk); n -= len(chunk)
                except OSError as exc:
                    shutil.rmtree(folder, True)
                    return self._send(500, dict(error=f"could not store the file: {exc}"))
                if n > 0:  # the browser stopped sending before the end
                    shutil.rmtree(folder, True); self.close_connection = True
                    return self._send(400, dict(error="the upload was cut off"), extra={"Connection": "close"})
                return self._send(200, dict(path=dest))
            try:
                body = self._json()
            except ValueError:
                return self._send(400, dict(error="the request is not valid JSON"))
            busy = studio.job.get("state") == "rendering"
            if u.path == "/api/load":
                if busy:
                    return self._send(409, dict(error="busy rendering"))
                return self._guard(lambda: studio.call(studio.load, body.get("paths") or [], body.get("roi"), body.get("image")))
            if u.path in ("/api/tomogram", "/api/segmentation", "/api/remove"):
                if busy:
                    return self._send(409, dict(error="busy rendering"))
                fn = {"/api/tomogram": lambda: studio.set_tomogram_source(body.get("key") or None),
                      "/api/segmentation": lambda: studio.set_segmentation_enabled(body["key"], bool(body.get("on"))),
                      "/api/remove": lambda: studio.remove_source(body["key"])}[u.path]
                return self._guard(lambda: studio.call(fn))
            if u.path == "/api/preview":
                if busy:
                    return self._send(409, dict(error="busy rendering"))
                return self._guard(lambda: studio.call(studio.preview, body["spec"], body))
            if u.path == "/api/pick":
                if busy:
                    return self._send(409, dict(error="busy rendering"))
                return self._guard(lambda: studio.call(studio.pick, body["spec"], body))
            if u.path == "/api/threshold":
                if busy:
                    return self._send(409, dict(error="busy rendering"))
                return self._guard(lambda: studio.call(studio.set_threshold, body["class"], body["value"]))
            if u.path == "/api/reset":
                if busy:
                    return self._send(409, dict(error="busy rendering"))
                return self._guard(lambda: studio.call(studio.reset))
            if u.path == "/api/render":
                return self._guard(lambda: studio.start_render(body["spec"], body.get("name")))
            if u.path == "/api/cancel":
                studio._cancel = True
                return self._send(200, dict(ok=True))
            self._send(404, dict(error="not found"))

    return Handler


def _bind(handler, port):
    """A server on the first free port from `port` on (30 tries) -> (server, port), or (None, None)."""
    from http.server import ThreadingHTTPServer

    class Server(ThreadingHTTPServer):
        allow_reuse_address = sys.platform != "win32"  # on Windows it lets a second server take a busy port
    for p in range(port, port + 30):
        try:
            return Server(("127.0.0.1", p), handler), p
        except OSError:
            continue
    return None, None


def cmd_app(args):
    import webbrowser
    studio = Studio(headless=args.headless, outdir=args.outdir)
    httpd, port = _bind(make_handler(studio), args.port)
    if httpd is None:
        sys.exit("tomoreveal: no free port found")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"TomoReveal {__version__} is running at {url}\nMovies are saved in {studio.outdir}\nPress Ctrl+C here to quit.")
    if args.files:
        try:
            studio.load(args.files)
        except UserError as exc:
            print(f"note: {exc}")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        studio.loop()
    except KeyboardInterrupt:
        print("\nbye")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>TomoReveal</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#14161a;--card:#1e2127;--line:#2f343d;--fg:#e6e8eb;--dim:#9aa3af;--acc:#4da3ff;--ok:#3fb950;--err:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,Helvetica,Arial,sans-serif}
header{padding:10px 18px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:14px}
header b{font-size:18px}header span{color:var(--dim)}
main{display:grid;grid-template-columns:minmax(340px,430px) 1fr;gap:14px;padding:14px;align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:12px}
.card h3{margin:0 0 8px;font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--dim)}
#drop{border:2px dashed #4b5563;text-align:center;padding:22px 14px;cursor:pointer;transition:.15s}
#drop.over{border-color:var(--acc);background:#1b2b40}#drop p{margin:4px 0}#drop small{color:var(--dim)}
button,select,input[type=text],input[type=number]{background:#2a2f38;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 9px;font:inherit}
button{cursor:pointer}button:hover{border-color:var(--acc)}button.primary{background:var(--acc);border-color:var(--acc);color:#04121f;font-weight:600;padding:8px 16px}
button.on{background:#27507c;border-color:var(--acc)}button:disabled{opacity:.45;cursor:default}
input[type=number]{width:62px}input[type=color]{width:30px;height:24px;padding:0;border:1px solid var(--line);background:none;border-radius:4px}
input[type=range]{width:100%}label{color:var(--dim)}
.row{display:flex;gap:8px;align-items:center;margin:6px 0;flex-wrap:wrap}.row>label{min-width:70px;flex:none}.grow{flex:1;min-width:0}select{max-width:100%;text-overflow:ellipsis}
table{width:100%;border-collapse:collapse}td{padding:3px 4px;vertical-align:middle}td.n{color:var(--dim);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td input[type=text]{width:100%}tr.off td input[type=text]{opacity:.5}
.shot{border:1px solid var(--line);border-radius:8px;padding:6px 8px;margin:6px 0;background:#232730}
.shot .row{margin:3px 0}.shot select,.shot input{padding:3px 6px}
#stage{position:relative;background:#000;border-radius:10px;overflow:hidden;border:1px solid var(--line);min-height:200px}
#prev{display:block;width:100%;height:auto}#stage.pick #prev{cursor:crosshair}
#hint{position:absolute;left:0;right:0;top:40%;text-align:center;color:var(--dim);padding:0 20px}
#spin{position:absolute;right:10px;bottom:8px;font-size:12px;color:var(--dim)}
#pickbar{position:absolute;left:0;right:0;top:0;background:#27507cdd;padding:6px 10px;text-align:center;display:none}
#stage.pick #pickbar{display:block}
#marks{position:relative;height:18px;font-size:11px;color:var(--dim)}#marks span{position:absolute;white-space:nowrap;border-left:1px solid #4b5563;padding-left:3px}
#msg{min-height:20px;margin:6px 0;color:var(--dim)}#msg.err{color:var(--err)}#msg.ok{color:var(--ok)}
progress{width:100%;height:14px}video{width:100%;border-radius:8px;margin-top:8px;background:#000}
.hidden{display:none!important}a{color:var(--acc)}code{background:#2a2f38;padding:1px 5px;border-radius:4px}
details summary{cursor:pointer;color:var(--dim)}
</style></head><body>
<header><b>TomoReveal</b><span>tomogram slice-sweep and segmentation-reveal movies &middot; runs only on this computer</span></header>
<main>
<section>
 <div class="card" id="drop">
  <p><b>Drop your tomogram and segmentation here</b></p>
  <p><small>tomogram: .mrc .rec .map .tif &nbsp;&middot;&nbsp; segmentation: Dragonfly .ORSSession or a label .mrc/.tif<br>
  easymode / Ais: drop the <code>name__feature.mrc</code> files, one structure each; they are added to what is loaded.<br>
  A Dragonfly session on its own is enough: its stored image is used as the tomogram.</small></p>
  <p><button id="browse">Choose files&hellip;</button></p>
  <input type="file" id="file" multiple class="hidden">
 </div>
 <details class="card"><summary>Large files? Paste their paths instead (no copying)</summary>
  <div class="row"><input type="text" id="path1" class="grow" placeholder="/full/path/to/tomogram.mrc"></div>
  <div class="row"><input type="text" id="path2" class="grow" placeholder="/full/path/to/session.ORSSession"><button id="openpaths">Open</button></div>
 </details>
 <div id="msg"></div>
 <div class="card hidden" id="datacard"><h3>Data</h3>
  <div class="row"><label>Tomogram</label><select id="image" class="grow"></select></div>
  <div id="segs"></div>
  <div id="dims" style="color:var(--dim);font-size:12px"></div>
  <div id="filesline" style="color:var(--dim);font-size:12px"></div>
  <div class="row"><button id="startover" title="Unload everything">Start over</button></div>
 </div>
 <div class="card hidden" id="classcard"><h3>Structures</h3>
  <table id="classes"></table>
  <div id="noseg" class="hidden" style="color:var(--dim)">No segmentation loaded. Drop one, or switch one on under Data.</div>
  <div style="color:var(--dim);font-size:12px;margin-top:6px">Untick to hide. <b>Zoom</b> adds a close-up of that structure to the movie.</div>
 </div>
 <div class="card hidden" id="viewcard"><h3>Main view</h3>
  <div class="row" id="presets"></div>
  <div class="row"><label>Tilt</label><input type="range" id="tilt" min="0" max="85" step="1" class="grow"><span id="tiltv"></span></div>
  <div class="row"><label>Rotate</label><input type="range" id="az" min="-180" max="180" step="1" class="grow"><span id="azv"></span></div>
  <div class="row"><label>Zoom</label><input type="range" id="zoom" min="0.5" max="3" step="0.05" class="grow"><span id="zoomv"></span></div>
 </div>
 <div class="card hidden" id="storycard"><h3>Movie</h3>
  <div class="row"><input type="checkbox" id="s0on"><span class="grow">1. Slide up through the slices</span><input type="number" id="s0sec" min="1" max="60" step="0.5"><label>s</label></div>
  <div class="row"><input type="checkbox" id="s1on"><span class="grow">2. Slide back, revealing the segmentation</span><input type="number" id="s1sec" min="1" max="60" step="0.5"><label>s</label></div>
  <div class="row"><input type="checkbox" id="s2on"><span class="grow">3. Overview <select id="s2motion"><option value="rock">rock</option><option value="spin">full spin</option><option value="none">still</option></select></span><input type="number" id="s2sec" min="1" max="60" step="0.5"><label>s</label></div>
  <div style="margin-top:8px"><b>Close-ups</b> <span style="color:var(--dim)">(played after the overview)</span></div>
  <div id="shots"></div>
  <div class="row"><button id="pickbtn">+ Click a structure in the preview</button></div>
  <div class="row"><input type="checkbox" id="rto"><span>Return to the overview at the end</span></div>
 </div>
 <div class="card hidden" id="lookcard"><h3>Look</h3>
  <div class="row"><input type="checkbox" id="legend"><span>Legend</span>&nbsp;<input type="checkbox" id="slabel"><span>Slice number</span>&nbsp;<input type="checkbox" id="invert"><span>Invert contrast</span></div>
  <div class="row"><label>Background</label><select id="bg"><option value="#000000">black</option><option value="#ffffff">white</option><option value="#1c1f26">dark grey</option></select>
   <label>Contrast</label><input type="range" id="contrast" min="0" max="10" step="0.5" style="width:110px"></div>
  <div class="row"><label>Title</label><input type="text" id="title" class="grow" placeholder="optional text, bottom left"></div>
 </div>
</section>
<section>
 <div id="stage"><img id="prev" alt=""><div id="hint">The live preview appears here once a tomogram and a segmentation are loaded.</div>
  <div id="pickbar">Click the structure you want a close-up of &nbsp; <button id="pickcancel">cancel</button></div><div id="spin"></div></div>
 <div class="hidden" id="scrubwrap"><input type="range" id="scrub" min="0" max="100" value="0"><div id="marks"></div>
  <div id="frameinfo" style="color:var(--dim);font-size:12px"></div></div>
 <div class="card hidden" id="rendercard" style="margin-top:12px"><h3>Make the movie</h3>
  <div class="row"><label>Quality</label><select id="quality"><option value="960x540">Draft 960&times;540 (fast)</option><option value="1920x1080" selected>HD 1920&times;1080</option><option value="3840x2160">4K 3840&times;2160 (slow)</option><option value="1080x1080">Square 1080&times;1080</option><option value="1080x1920">Portrait 1080&times;1920</option></select>
   <label>Name</label><input type="text" id="name" class="grow"></div>
  <div class="row"><button class="primary" id="go">Make movie</button><button id="cancel" class="hidden">Cancel</button><span id="jobmsg" style="color:var(--dim)"></span></div>
  <progress id="bar" class="hidden" max="1" value="0"></progress>
  <div id="result"></div>
 </div>
</section>
</main>
<script>
var $=s=>document.querySelector(s);
var S={info:null,spec:null,frame:0,total:1,pick:false,inflight:false,pending:false,req:null,timer:null,poll:null};
function say(t,cls){const m=$('#msg');m.textContent=t||'';m.className=cls||'';}
async function api(path,body){
  const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  let j={};try{j=await r.json();}catch(e){}
  if(!r.ok||j.error)throw new Error(j.error||r.statusText);return j;}
function upload(file,onp){return new Promise((res,rej)=>{const x=new XMLHttpRequest();x.open('POST','/api/upload?name='+encodeURIComponent(file.name));
  x.upload.onprogress=e=>{if(e.lengthComputable)onp(e.loaded/e.total);};
  x.onload=()=>{try{const j=JSON.parse(x.responseText);if(x.status==200)res(j.path);else rej(new Error(j.error||'upload failed'));}catch(e){rej(e);}};
  x.onerror=()=>rej(new Error('upload failed'));x.send(file);});}
async function handleFiles(files){
  try{const paths=[];
    for(const f of files){say('Copying '+f.name+' ... 0%');paths.push(await upload(f,p=>say('Copying '+f.name+' ... '+Math.round(p*100)+'%')));}
    await load({paths});}catch(e){say(e.message,'err');}}
async function load(body){
  say('Reading the files and building 3D surfaces. This can take a minute for large tomograms ...');
  try{const info=await api('/api/load',body);applyInfo(info);}catch(e){say(e.message,'err');}}
function applyInfo(info){
  const prev=S.info;const sameSeg=prev&&prev.can_show&&info.can_show&&S.spec;
  S.info=info;
  if(!info.can_show){fillData(info);$('#datacard').classList.toggle('hidden',!(info.segmentation_sources||[]).length);say(info.message||'');return;}
  if(!sameSeg){S.spec=info.spec;S.req={at:{shot:2,u:0}};}
  else{ // keep the user's settings of every structure that is still there (keyed by its stable key)
    if(String(prev.dims)!==String(info.dims)){S.spec.clip=null;S.spec.z_range=null;}
    const keys=new Set(info.structures.map(c=>c.key));const merged={};
    for(const [k,v] of Object.entries(info.spec.classes))merged[k]=S.spec.classes[k]||v;
    S.spec.classes={...S.spec.classes,...merged};S.spec.shots=S.spec.shots.filter(s=>s.type!=='focus'||keys.has(s.target.class));}
  say('Loaded. Adjust anything on the left; the preview updates as you go.','ok');
  for(const id of ['datacard','classcard','viewcard','storycard','lookcard','rendercard','scrubwrap'])$('#'+id).classList.remove('hidden');
  $('#hint').classList.add('hidden');
  if(!$('#name').value||!sameSeg)$('#name').value=(info.tomo_name||'tomogram').replace(/\.[^.]+$/,'').slice(0,60)+'_reveal';
  buildUI();preview();}
function opt(sel,list,val){sel.innerHTML='';for(const o of list){const e=document.createElement('option');e.value=o.id;e.textContent=o.label;sel.appendChild(e);}sel.value=val;}
function fillData(info){
  const im=$('#image');im.innerHTML='';for(const o of info.tomogram_choices){const e=document.createElement('option');e.value=o.key||'';e.textContent=o.label;im.appendChild(e);}
  im.value=info.tomogram||'';
  const box=$('#segs');box.innerHTML='';let group=null;
  for(const s of info.segmentation_sources){
    if(s.group!==group){group=s.group;const h=el('div',{});h.style.color='var(--dim)';h.style.marginTop='6px';h.textContent=group;box.appendChild(h);}
    const row=el('label',{});row.style.display='block';const cb=el('input',{type:'checkbox'});cb.checked=s.enabled;cb.disabled=!s.usable;
    cb.onchange=async()=>{try{say('Updating ...');applyInfo(await api('/api/segmentation',{key:s.key,on:cb.checked}));}catch(e){say(e.message,'err');}};
    row.append(cb,document.createTextNode(' '+s.label+(s.kind!=='scores'?' ('+s.count+' structures)':'')));row.title=s.reason||s.names.join(', ');box.appendChild(row);
    const n=el('div',{});n.style.color='var(--dim)';n.style.fontSize='12px';n.style.marginLeft='22px';
    n.textContent=s.usable?(s.kind!=='scores'?s.names.slice(0,6).join(', ')+(s.names.length>6?', ...':''):''):s.reason;if(n.textContent)box.appendChild(n);}}
function changed(){clearTimeout(S.timer);S.timer=setTimeout(preview,120);}
function buildUI(){
  const info=S.info,sp=S.spec;
  fillData(info);
  $('#dims').textContent=info.dims.join(' x ')+' voxels'+(info.voxel?' · '+(info.voxel/10).toFixed(2)+' nm per voxel':'');
  $('#filesline').textContent=info.match_note||'';
  const tb=$('#classes');tb.innerHTML='';$('#noseg').classList.toggle('hidden',info.structures.length>0);
  for(const c of info.structures){const k=c.key,sc=sp.classes[k];const tr=document.createElement('tr');
    if(c.background){tr.innerHTML='<td></td><td></td><td style="color:var(--dim)">'+esc(c.name)+' (background, not drawn)</td><td class="n">'+c.voxels.toLocaleString()+'</td><td></td>';tb.appendChild(tr);continue;}
    tr.className=sc.show?'':'off';
    const cb=el('input',{type:'checkbox'});cb.checked=sc.show;cb.onchange=()=>{sc.show=cb.checked;tr.className=sc.show?'':'off';changed();};
    const col=el('input',{type:'color',value:sc.color});col.oninput=()=>{sc.color=col.value;changed();};
    const nm=el('input',{type:'text',value:sc.name});nm.oninput=()=>{sc.name=nm.value;changed();};
    const zb=el('button',{});zb.textContent='Zoom';zb.title='Add a close-up of this structure';zb.onclick=()=>addFocus({class:c.key},sc.name);
    for(const x of [cb,col,nm]){const td=document.createElement('td');td.appendChild(x);tr.appendChild(td);}
    const tn=document.createElement('td');tn.className='n';tn.textContent=c.voxels.toLocaleString();tr.appendChild(tn);
    const tz=document.createElement('td');tz.appendChild(zb);tr.appendChild(tz);tb.appendChild(tr);
    if(c.kind==='score'){ // easymode / Ais score volume: the surface is drawn where the score exceeds this threshold
      const tr2=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.style.paddingLeft='62px';
      const lb=el('label',{});lb.textContent='threshold ';
      const thr=el('input',{type:'range',min:0.05,max:0.95,step:0.05,value:c.threshold});thr.style.width='150px';thr.style.verticalAlign='middle';
      const tv=el('span',{});tv.style.color='var(--dim)';
      const show=(v,mesh)=>{tv.textContent=' '+(+v).toFixed(2)+(mesh?'':' (nothing above the threshold)');};show(c.threshold,c.mesh);
      thr.oninput=()=>show(thr.value,true);
      thr.onchange=async()=>{try{say('Rebuilding '+sc.name+' ...');const r=await api('/api/threshold',{class:c.id,value:+thr.value});
        c.threshold=r.threshold;c.voxels=r.voxels;c.mesh=r.mesh;tn.textContent=r.voxels.toLocaleString();show(r.threshold,r.mesh);
        S.spec.shots=S.spec.shots.filter(s=>!(s.type==='focus'&&s.target.class===c.key&&s.target.component!=null));buildShots();
        say('Threshold updated.','ok');preview();}catch(e){say(e.message,'err');}};
      td.append(lb,thr,tv);tr2.appendChild(td);tb.appendChild(tr2);}}
  const pr=$('#presets');pr.innerHTML='';
  for(const [name,v] of Object.entries(info.views)){const b=el('button',{});b.textContent=name;b.onclick=()=>{sp.view.tilt=v[0];sp.view.az=v[1];syncView();changed();};pr.appendChild(b);}
  syncView();
  bind('#tilt',v=>{sp.view.tilt=+v;syncView();});bind('#az',v=>{sp.view.az=+v;syncView();});bind('#zoom',v=>{sp.view.zoom=+v;syncView();});
  for(const i of [0,1,2]){const sh=sp.shots[i];const on=$('#s'+i+'on'),sec=$('#s'+i+'sec');on.checked=sh.on;sec.value=sh.seconds;
    on.onchange=()=>{sh.on=on.checked;changed();};sec.oninput=()=>{sh.seconds=Math.max(0.5,+sec.value||1);changed();};}
  $('#s2motion').value=sp.shots[2].motion;$('#s2motion').onchange=e=>{sp.shots[2].motion=e.target.value;changed();};
  $('#rto').checked=sp.return_to_overview;$('#rto').onchange=e=>{sp.return_to_overview=e.target.checked;changed();};
  const st=sp.style;
  $('#legend').checked=st.legend;$('#legend').onchange=e=>{st.legend=e.target.checked;changed();};
  $('#slabel').checked=st.slice_label;$('#slabel').onchange=e=>{st.slice_label=e.target.checked;changed();};
  $('#invert').checked=st.invert;$('#invert').onchange=e=>{st.invert=e.target.checked;changed();};
  $('#bg').value=st.bg;$('#bg').onchange=e=>{st.bg=e.target.value;changed();};
  $('#contrast').value=st.contrast[0];$('#contrast').oninput=e=>{st.contrast=[+e.target.value,100-(+e.target.value)];changed();};
  $('#title').value=st.title;$('#title').oninput=e=>{st.title=e.target.value;changed();};
  buildShots();}
function el(tag,attrs){const e=document.createElement(tag);for(const k in attrs)e.setAttribute(k,attrs[k]);return e;}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function bind(sel,fn){$(sel).oninput=e=>{fn(e.target.value);changed();};}
function syncView(){const v=S.spec.view;$('#tilt').value=v.tilt;$('#az').value=v.az;$('#zoom').value=v.zoom;
  $('#tiltv').textContent=Math.round(v.tilt)+'°';$('#azv').textContent=Math.round(v.az)+'°';$('#zoomv').textContent=(+v.zoom).toFixed(2)+'x';}
function addFocus(target,name){
  S.spec.shots.push({type:'focus',on:true,target:target,caption:name,others:'grey',motion:'orbit',seconds:5,zoom:1,slice:'keep',angle:30});
  S.req={at:{shot:S.spec.shots.length-1,u:0.08}};buildShots();preview();}
function buildShots(){
  const box=$('#shots');box.innerHTML='';
  S.spec.shots.forEach((sh,i)=>{if(sh.type!=='focus')return;
    const d=el('div',{class:'shot'});
    const r1=el('div',{class:'row'});
    const cap=el('input',{type:'text',value:sh.caption||'',placeholder:'caption'});cap.className='grow';cap.oninput=()=>{sh.caption=cap.value;changed();};
    const show=el('button',{});show.textContent='show';show.title='Show this close-up in the preview';show.onclick=()=>{S.req={at:{shot:i,u:0.08}};preview();};
    const rm=el('button',{});rm.textContent='remove';rm.onclick=()=>{S.spec.shots.splice(i,1);buildShots();changed();};
    r1.append(cap,show,rm);
    const r2=el('div',{class:'row'});
    const oth=sel([['grey','grey out the rest'],['hide','hide the rest'],['keep','keep the rest']],sh.others,v=>{sh.others=v;});
    const mot=sel([['orbit','orbit around it'],['rock','rock'],['none','hold still']],sh.motion,v=>{sh.motion=v;});
    const sl=sel([['keep','keep slice'],['hide','hide slice']],sh.slice,v=>{sh.slice=v;});
    r2.append(oth,mot,sl);
    const r3=el('div',{class:'row'});
    const sec=el('input',{type:'number',min:1,max:60,step:0.5,value:sh.seconds});sec.oninput=()=>{sh.seconds=Math.max(0.5,+sec.value||1);changed();};
    const zm=el('input',{type:'number',min:0.3,max:4,step:0.1,value:sh.zoom});zm.oninput=()=>{sh.zoom=Math.max(0.2,+zm.value||1);changed();};
    const l1=el('label',{});l1.textContent='seconds';const l2=el('label',{});l2.textContent='zoom';
    const what=el('span',{});what.style.color='var(--dim)';what.textContent=sh.target.component!=null?'one object':'whole class';
    r3.append(l1,sec,l2,zm,what);
    d.append(r1,r2,r3);box.appendChild(d);});
  if(!box.children.length){const p=el('div',{});p.style.color='var(--dim)';p.style.margin='6px 0';p.textContent='None yet. Use a Zoom button above, or click a structure in the preview.';box.appendChild(p);}}
function sel(opts,val,fn){const s=el('select',{});for(const [v,t] of opts){const o=el('option',{value:v});o.textContent=t;s.appendChild(o);}s.value=val;s.onchange=()=>{fn(s.value);changed();};return s;}
async function preview(){
  if(!S.spec)return;
  if(S.inflight){S.pending=true;return;}
  S.inflight=true;$('#spin').textContent='updating ...';
  const body=Object.assign({spec:specFor()},S.req||{t:S.total>1?S.frame/(S.total-1):0.6});S.req=null;
  try{const r=await api('/api/preview',body);
    $('#prev').src=r.img;S.frame=r.frame;S.total=r.total;
    const sc=$('#scrub');sc.max=r.total-1;sc.value=r.frame;
    const mk=$('#marks');mk.innerHTML='';for(const m of r.marks){const s=el('span',{});s.style.left=(100*m.frame/Math.max(1,r.total-1))+'%';s.textContent=m.label;mk.appendChild(s);}
    $('#frameinfo').textContent='Drag to scrub through the movie · '+(r.frame/30).toFixed(1)+' s of '+r.seconds+' s';
  }catch(e){if(e.message!=='busy rendering')say(e.message,'err');}
  S.inflight=false;$('#spin').textContent='';
  if(S.pending){S.pending=false;preview();}}
function specFor(size){const sp=JSON.parse(JSON.stringify(S.spec));const q=(size||$('#quality').value).split('x').map(Number);sp.output.size=q;return sp;}
$('#scrub').oninput=e=>{S.req={frame:+e.target.value};preview();};
$('#quality').onchange=()=>changed();
function setPick(on){S.pick=on;$('#stage').classList.toggle('pick',on);$('#pickbtn').classList.toggle('on',on);}
$('#pickbtn').onclick=()=>{if(!S.pick){S.req={at:{shot:2,u:0}};preview();}setPick(!S.pick);};
$('#pickcancel').onclick=e=>{e.stopPropagation();setPick(false);};
$('#prev').onclick=async e=>{
  if(!S.pick||!S.spec)return;
  const r=e.target.getBoundingClientRect();const x=(e.clientX-r.left)/r.width,y=(e.clientY-r.top)/r.height;
  try{const p=await api('/api/pick',{spec:specFor(),frame:S.frame,x:x,y:y});
    if(!p.found){say('Nothing there. Click directly on a coloured structure (move the slider to a frame where the segmentation is visible).','err');return;}
    setPick(false);say('Added a close-up of '+p.name+(p.component!=null?' (one object, '+p.voxels.toLocaleString()+' voxels)':'')+'.','ok');
    addFocus(p.component!=null?{class:p.class,component:p.component}:{class:p.class},p.name);
  }catch(err){say(err.message,'err');}};
const drop=$('#drop');
drop.onclick=e=>{if(e.target.id!=='browse')$('#file').click();};$('#browse').onclick=()=>$('#file').click();
$('#file').onchange=e=>{if(e.target.files.length)handleFiles([...e.target.files]);e.target.value='';};
for(const ev of ['dragenter','dragover'])document.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('over');});
for(const ev of ['dragleave','drop'])document.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('over');});
document.addEventListener('drop',e=>{const fs=[...(e.dataTransfer?e.dataTransfer.files:[])];if(fs.length)handleFiles(fs);});
$('#openpaths').onclick=()=>{const p=[$('#path1').value,$('#path2').value].map(s=>s.trim()).filter(Boolean);if(p.length)load({paths:p});};
$('#startover').onclick=async()=>{try{await api('/api/reset',{});}catch(e){}location.reload();};
$('#image').onchange=async e=>{try{say('Changing the tomogram ...');applyInfo(await api('/api/tomogram',{key:e.target.value}));}catch(err){say(err.message,'err');}};
$('#go').onclick=async()=>{
  try{await api('/api/render',{spec:specFor(),name:$('#name').value});$('#result').innerHTML='';startPoll();}catch(e){say(e.message,'err');}};
$('#cancel').onclick=()=>api('/api/cancel',{});
function startPoll(){clearInterval(S.poll);S.poll=setInterval(poll,700);poll();}
async function poll(){
  let j;try{j=await api('/api/progress');}catch(e){return;}
  const busy=j.state==='rendering';
  $('#go').disabled=busy;$('#cancel').classList.toggle('hidden',!busy);$('#bar').classList.toggle('hidden',!busy);
  if(busy){$('#bar').value=j.done/Math.max(1,j.total);
    $('#jobmsg').textContent='frame '+j.done+' of '+j.total+(j.eta!=null?' · about '+(j.eta>90?Math.round(j.eta/60)+' min':j.eta+' s')+' left':'');
    if(j.done>0)$('#prev').src='/api/lastframe?'+Date.now();return;}
  clearInterval(S.poll);
  if(j.state==='done'){$('#jobmsg').textContent='Finished in '+j.elapsed+' s';
    $('#result').innerHTML='<video controls loop src="/out/'+encodeURIComponent(j.file)+'?'+Date.now()+'"></video><div class="row"><a href="/out/'+encodeURIComponent(j.file)+'" download>Download '+esc(j.file)+'</a><span style="color:var(--dim)">'+j.seconds+' s · '+j.mb+' MB · saved as <code>'+esc(j.path)+'</code></span></div>';
    preview();}
  else if(j.state==='error'){$('#jobmsg').textContent='';say('Rendering failed: '+j.message,'err');preview();}
  else if(j.state==='cancelled'){$('#jobmsg').textContent='Cancelled';preview();}}
(async()=>{try{const s=await api('/api/state');if(s.info.can_show||(s.info.segmentation_sources||[]).length)applyInfo(s.info);if(s.job.state==='rendering')startPoll();}catch(e){}})();
</script></body></html>
"""
