const fs=require('fs'), vm=require('vm'), path=require('path');
const html=fs.readFileSync(path.join(__dirname,'..','androcam_host','web','index.html'),'utf8');
const src=html.match(/<script>([\s\S]*)<\/script>/)[1].replace(/^tickLoop\(\);\s*$/m,'');   // don't start the poll loop
function mkEl(tag){ const e={tag,children:[],style:{},attrs:{},_cls:"",
  classList:{toggle(c,on){e._cls=on?c:''}}, set className(v){e._cls=v}, get className(){return e._cls},
  setAttribute(k,v){e.attrs[k]=v}, removeAttribute(k){delete e.attrs[k]}, append(...k){e.children.push(...k)}, replaceChildren(...k){e.children=k},
  addEventListener(){}, cget(){}, onclick:null, onchange:null, disabled:false, value:"", checked:false, textContent:"", src:"" };
  Object.defineProperty(e,'src',{get(){return e.attrs.src||""},set(v){e.attrs.src=v}}); return e; }
const els={}; const doc={getElementById:id=>els[id]||(els[id]=mkEl(id)), createElement:mkEl, createTextNode:t=>({nodeType:3,text:String(t)}), activeElement:null, body:mkEl('body')};
const sb={document:doc, console, setTimeout:()=>0, Date, JSON, Math, Array, String, Number, Object, fetch:async()=>({json:async()=>({})}), confirm:()=>true, window:{}};
vm.createContext(sb); vm.runInContext(src,sb);
const state=(c,vc)=>({version:"x",pc_name:"PC",ips:["192.168.1.4"],pair_code:"123456",phone_port:47801,download_port:47802,apk:true,first_run:true,wizard_step:0,
  phone:{connected:c,info:c?{name:"<img src=x onerror=alert(1)>",os:"android"}:{},status:c?{torch:true,facing:"back"}:{},address:"1.2.3.4"},
  video:{fps:c?29.7:0,canvas:[1280,720],mirror:false,frames:5,errors:0,last_error:"",seq:1},audio:{level:.4,muted:false},
  vcam:{installed:vc,files:true,supported:true},mic_driver:{installed:false,available:false},autostart:false,auth_failures:0});
function text(n){ return n.nodeType===3?n.text:(n.children||[]).map(text).join(" ") }
let fails=0; const ok=(c,m)=>{ if(!c){console.log("FAIL:",m);fails++} };
vm.runInContext(`
  globalThis.__run=function(S0){ S=S0; renderDash(); wizOpen=true; for(let i=0;i<STEPS;i++){ step=i; renderWiz.page=-1; renderWiz(); } };`,sb);
for (const [c,vc] of [[false,false],[true,false],[true,true]]) { try { sb.__run(state(c,vc)); } catch(e){ console.log("THROW",c,vc,e.stack); fails++ } }
// content checks per page
vm.runInContext(`S=${JSON.stringify(state(true,false))}; globalThis.pages=PAGES.map(f=>f());`,sb);
const P=sb.pages.map(p=>p.map(text).join(" "));
ok(P[1].includes("http://192.168.1.4:47802"),"download URL shown");
ok(P[2].includes("123456")&&P[2].includes("Connected"),"pairing code + connected tick");
ok(P[4].includes("not installed yet"),"vcam state");
ok(P[5].includes("driver"),"mic step mentions driver honestly");
// XSS: phone-supplied name must only ever be a text node, never markup
ok(P[2].includes("<img src=x onerror=alert(1)>"),"name rendered as literal text");
function hasHtmlNode(n){ return n.tag==='img' && n.attrs && n.attrs.onerror }
sb.pages.flat().forEach(function w(n){ ok(!hasHtmlNode(n),"no injected element"); (n.children||[]).forEach(w) });
const d=els=>doc.getElementById(els);
sb.__run(state(true,true)); ok(d("pilltxt").textContent==="Phone connected","pill text"); ok(d("btnTorch").textContent==="Flash: on","torch label");
ok(d("phName").textContent.includes("<img"),"dashboard name via textContent"); ok(d("meter").style.width==="40%","meter width");
sb.__run(state(false,false)); ok(d("pilltxt").textContent==="Waiting for phone","pill waiting"); ok(d("btnFlip").disabled===true,"controls disabled when no phone");
console.log(fails? "UI TEST FAILED":"UI LOGIC TEST PASSED"); process.exit(fails?1:0);
