/* «Мой ИИ»: логика интерфейса. Разметка в index.html, оформление в css/style.css, темы в themes/. */
const TOKEN=window.APP.token, DEFAULT_MODEL=window.APP.model;
const $=s=>document.querySelector(s);
const msgsEl=$('#msgs'), scrollEl=$('#scroll'), input=$('#input'), sendBtn=$('#send'), modelSel=$('#model'), statusEl=$('#status');
const uid=()=>Date.now().toString(36)+Math.random().toString(36).slice(2,6);
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!==undefined)e.innerHTML=h;return e};

async function api(path,opt={}){
  opt.headers=Object.assign({'X-App-Token':TOKEN,'Content-Type':'application/json'},opt.headers||{});
  const r=await fetch(path,opt);
  if(!r.ok){let t=await r.text(),m=t;try{m=JSON.parse(t).error||t}catch(e){}throw new Error(m)}
  return r.json();
}
async function copyText(t){
  try{await navigator.clipboard.writeText(t);return true}
  catch(e){try{const a=document.createElement('textarea');a.value=t;a.style.cssText='position:fixed;opacity:0';document.body.append(a);a.select();const ok=document.execCommand('copy');a.remove();return ok}catch(_){return false}}
}

/* ================= Настройки, темы, оформление ================= */
const DEFAULTS={theme:'auto',overrides:{},font:'sys',fontCustom:'',fontSize:15,width:780,radius:18,glow:true,system:'',model:'',collapsed:false};
let settings=Object.assign({},DEFAULTS), themes=[];
const FALLBACK={id:'midnight',name:'Полночь',custom:false,vars:{'--bg':'#0e1014','--side':'#12151b','--surface':'#171b23','--raised':'#1e232d','--line':'#262c38','--text':'#e8eaf0','--muted':'#8a93a6','--bubble':'#222838','--accent':'#8b9bff','--accent2':'#c58bff','--code':'#0b0d11'}};
const VARS=[['--bg','Фон'],['--side','Боковая панель'],['--surface','Поля и карточки'],['--raised','Кнопки'],['--line','Рамки'],['--text','Текст'],['--muted','Вторичный текст'],['--bubble','Ваши сообщения'],['--accent','Акцент'],['--accent2','Второй акцент'],['--code','Блоки кода']];
const FONTS={
  sys:['Системный (Segoe UI)','"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif'],
  serif:['С засечками (Georgia)','Georgia,"Times New Roman",serif'],
  round:['Округлый (Trebuchet)','"Trebuchet MS","Segoe UI",sans-serif'],
  verdana:['Широкий (Verdana)','Verdana,Geneva,sans-serif'],
  mono:['Моноширинный (Cascadia)','"Cascadia Code","Cascadia Mono",Consolas,monospace']
};
const darkMQ=matchMedia('(prefers-color-scheme: dark)');
let saveTimer=null;
function scheduleSave(){clearTimeout(saveTimer);saveTimer=setTimeout(()=>api('/api/settings',{method:'PUT',body:JSON.stringify(settings)}).catch(()=>{}),400)}

function resolvedTheme(){
  let id=settings.theme;if(id==='auto')id=darkMQ.matches?'midnight':'daylight';
  return themes.find(t=>t.id===id)||themes.find(t=>t.id==='midnight')||themes[0];
}
function lum(hex){const n=parseInt(hex.slice(1),16);return (0.2126*((n>>16)&255)+0.7152*((n>>8)&255)+0.0722*(n&255))/255}
function currentVars(){const t=resolvedTheme();return Object.assign({},t.vars,settings.overrides||{})}
function applyTheme(){
  const v=currentVars(),root=document.documentElement;
  VARS.forEach(([k])=>{if(v[k])root.style.setProperty(k,v[k])});
  root.style.setProperty('--on',lum(v['--accent'])>0.6?'#111':'#fff');
  root.style.colorScheme=lum(v['--bg'])<0.5?'dark':'light';
  let f=(FONTS[settings.font]||FONTS.sys)[1];
  const custom=(settings.fontCustom||'').trim().replace(/["\\;{}]/g,'');
  if(custom)f='"'+custom+'",'+f;
  root.style.setProperty('--font',f);
  root.style.setProperty('--fs',settings.fontSize+'px');
  root.style.setProperty('--w',settings.width+'px');
  root.style.setProperty('--r',settings.radius+'px');
  root.classList.toggle('glow',!!settings.glow);
}
darkMQ.addEventListener('change',()=>{if(settings.theme==='auto'){applyTheme();if(dlg.open)renderThemeTabs()}});

/* --- окно настроек --- */
const dlg=$('#dlg');
function swatch(id,name,bg,text,a1,a2,custom){
  const d=el('div','sw'+(settings.theme===id?' on':''));
  d.style.background=bg;d.style.color=text;
  d.innerHTML='<b>'+esc(name)+'</b><div class="dots3"><i style="background:'+a1+'"></i><i style="background:'+a2+'"></i><i style="background:'+text+'"></i></div>'+(custom?'<small>моя</small>':'');
  d.onclick=()=>{settings.theme=id;settings.overrides={};applyTheme();scheduleSave();renderThemeTabs()};
  return d;
}
function renderThemeTabs(){
  const box=$('#swatches');box.innerHTML='';
  const auto=swatch('auto','Как в Windows','linear-gradient(135deg,#0e1014 50%,#f6f7fa 50%)','#8b9bff','#8b9bff','#c58bff');
  auto.style.color='#fff';auto.querySelector('b').style.cssText='background:#0e1014;padding:1px 8px;border-radius:6px;align-self:flex-start;color:#e8eaf0';
  box.append(auto);
  themes.forEach(t=>box.append(swatch(t.id,t.name,t.vars['--bg'],t.vars['--text'],t.vars['--accent'],t.vars['--accent2'],t.custom)));
  const cols=$('#colors');cols.innerHTML='';const v=currentVars();
  VARS.forEach(([k,label])=>{
    const l=el('label','',esc(label)),i=document.createElement('input');
    i.type='color';i.value=v[k];
    i.oninput=()=>{
      if(settings.theme==='auto')settings.theme=resolvedTheme().id; // правки привязываем к текущей теме
      settings.overrides=Object.assign({},settings.overrides,{[k]:i.value});
      applyTheme();scheduleSave();syncColorButtons();
    };
    l.append(i);cols.append(l);
  });
  syncColorButtons();
}
function syncColorButtons(){
  $('#resetColors').disabled=!Object.keys(settings.overrides||{}).length;
  const t=themes.find(x=>x.id===settings.theme);$('#delTheme').hidden=!(t&&t.custom);
}
function syncViewTab(){
  const sel=$('#fontSel');sel.innerHTML='';
  Object.entries(FONTS).forEach(([k,[n]])=>sel.append(new Option(n,k)));sel.value=settings.font;
  $('#fontCustom').value=settings.fontCustom||'';
  $('#fsRange').value=settings.fontSize;$('#fsVal').textContent=settings.fontSize+' px';
  $('#wRange').value=settings.width;$('#wVal').textContent=settings.width+' px';
  $('#rRange').value=settings.radius;$('#rVal').textContent=settings.radius+' px';
  $('#glow').checked=!!settings.glow;
}
function bindView(){
  const upd=()=>{applyTheme();scheduleSave()};
  $('#fontSel').onchange=e=>{settings.font=e.target.value;upd()};
  $('#fontCustom').oninput=e=>{settings.fontCustom=e.target.value;upd()};
  $('#fsRange').oninput=e=>{settings.fontSize=+e.target.value;$('#fsVal').textContent=e.target.value+' px';upd()};
  $('#wRange').oninput=e=>{settings.width=+e.target.value;$('#wVal').textContent=e.target.value+' px';upd()};
  $('#rRange').oninput=e=>{settings.radius=+e.target.value;$('#rVal').textContent=e.target.value+' px';upd()};
  $('#glow').onchange=e=>{settings.glow=e.target.checked;upd()};
  $('#resetView').onclick=()=>{Object.assign(settings,{font:DEFAULTS.font,fontCustom:'',fontSize:DEFAULTS.fontSize,width:DEFAULTS.width,radius:DEFAULTS.radius,glow:DEFAULTS.glow});syncViewTab();upd()};
  $('#sys').oninput=e=>{settings.system=e.target.value;scheduleSave()};
  $('#resetColors').onclick=()=>{settings.overrides={};applyTheme();scheduleSave();renderThemeTabs()};
  $('#saveTheme').onclick=async()=>{
    const name=(prompt('Название темы:','Моя тема')||'').trim();if(!name)return;
    const id='u'+uid(),vars=currentVars();
    try{
      await api('/api/themes/'+id,{method:'PUT',body:JSON.stringify({name,vars})});
      themes=await api('/api/themes');settings.theme=id;settings.overrides={};
      applyTheme();scheduleSave();renderThemeTabs();
    }catch(e){alert('Не удалось сохранить тему: '+e.message)}
  };
  $('#delTheme').onclick=async()=>{
    const t=themes.find(x=>x.id===settings.theme);if(!t||!t.custom||!confirm('Удалить тему «'+t.name+'»?'))return;
    try{await api('/api/themes/'+t.id,{method:'DELETE'})}catch(e){}
    themes=await api('/api/themes');settings.theme='auto';settings.overrides={};
    applyTheme();scheduleSave();renderThemeTabs();
  };
  $('#tabs').onclick=e=>{
    const b=e.target.closest('button[data-tab]');if(!b)return;
    document.querySelectorAll('#tabs button').forEach(x=>x.classList.toggle('on',x===b));
    document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.id==='tab-'+b.dataset.tab));
  };
  $('#dlgClose').onclick=()=>dlg.close();
  dlg.addEventListener('click',e=>{if(e.target===dlg)dlg.close()});
}
function openSettings(){renderThemeTabs();syncViewTab();$('#sys').value=settings.system||'';dlg.showModal()}

/* ================= Markdown (маленький и безопасный) ================= */
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function inline(s){
  const keep=[];
  s=s.replace(/`([^`\n]+)`/g,(_,c)=>{keep.push('<code>'+esc(c)+'</code>');return '\u0000'+(keep.length-1)+'\u0000'});
  s=esc(s);
  s=s.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
  s=s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g,'$1<em>$2</em>');
  s=s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s.replace(/\u0000(\d+)\u0000/g,(_,i)=>keep[+i]);
}
function codeBlock(code,lang){
  return '<div class="code"><div class="code-h"><span>'+esc(lang||'код')+'</span><button class="copy">Копировать</button></div><pre><code>'+esc(code)+'</code></pre></div>';
}
function md(src){
  const L=src.replace(/\r/g,'').split('\n'),out=[];let i=0;
  const isBlock=l=>/^(```|#{1,4}\s|\s*[-*]\s|\s*\d+[.)]\s|>)/.test(l);
  while(i<L.length){
    const l=L[i];let m;
    if((m=l.match(/^```(\S*)/))){const c=[];i++;while(i<L.length&&!/^```/.test(L[i]))c.push(L[i++]);i++;out.push(codeBlock(c.join('\n'),m[1]));continue}
    if((m=l.match(/^(#{1,4})\s+(.*)/))){const n=m[1].length+1;out.push('<h'+n+'>'+inline(m[2])+'</h'+n+'>');i++;continue}
    if(/^\s*[-*]\s+/.test(l)){const it=[];while(i<L.length&&/^\s*[-*]\s+/.test(L[i]))it.push(inline(L[i++].replace(/^\s*[-*]\s+/,'')));out.push('<ul>'+it.map(x=>'<li>'+x+'</li>').join('')+'</ul>');continue}
    if(/^\s*\d+[.)]\s+/.test(l)){const it=[];while(i<L.length&&/^\s*\d+[.)]\s+/.test(L[i]))it.push(inline(L[i++].replace(/^\s*\d+[.)]\s+/,'')));out.push('<ol>'+it.map(x=>'<li>'+x+'</li>').join('')+'</ol>');continue}
    if(/^>/.test(l)){const q=[];while(i<L.length&&/^>/.test(L[i]))q.push(L[i++].replace(/^>\s?/,''));out.push('<blockquote>'+inline(q.join(' '))+'</blockquote>');continue}
    if(/^\s*(---+|\*\*\*+)\s*$/.test(l)){out.push('<hr>');i++;continue}
    if(!l.trim()){i++;continue}
    const p=[];while(i<L.length&&L[i].trim()&&!isBlock(L[i]))p.push(L[i++]);
    if(!p.length)p.push(L[i++]);
    out.push('<p>'+inline(p.join('\n')).replace(/\n/g,'<br>')+'</p>');
  }
  return out.join('');
}

/* ================= Чаты ================= */
let chats=[],cur=null,busy=false,ctrl=null;

function splitThink(m){
  let think=m.thinking||'',ans=m.content||'';
  const t=ans.match(/^\s*<think>([\s\S]*?)(<\/think>|$)/);
  if(t){think=(think?think+'\n':'')+t[1];ans=ans.slice(t[0].length)}
  return {think:think.trim(),ans};
}
function fillAssistant(row,m,streaming){
  const {th,body,acts}=row._r,{think,ans}=splitThink(m);
  th.hidden=!think;
  th.classList.toggle('live',streaming&&!ans.trim()&&!!think);
  th.querySelector('.tlabel').textContent=(streaming&&!ans.trim())?'Думаю…':'Размышления';
  th.querySelector('.tbody').textContent=think;
  let html=ans.trim()?md(ans):((streaming&&!think)?'<span class="typing"><i></i><i></i><i></i></span>':'');
  if(m.error)html+='<div class="err">'+esc(m.error)+'</div>';
  body.innerHTML=html;
  acts.innerHTML='';
  if(!streaming&&ans.trim()){
    const c=el('button','','Копировать');
    c.onclick=async()=>{const ok=await copyText(ans.trim());c.textContent=ok?'Скопировано':'Не удалось';setTimeout(()=>c.textContent='Копировать',1200)};
    acts.append(c);
    if(cur.messages[cur.messages.length-1]===m){const r=el('button','','Повторить');r.onclick=regenerate;acts.append(r)}
  }else if(!streaming&&m.error){
    const r=el('button','','Повторить');r.onclick=regenerate;acts.append(r);
  }
}
function addMsg(m){
  const row=el('div','msg '+m.role);
  if(m.role==='user'){row.append(el('div','bubble',esc(m.content).replace(/\n/g,'<br>')))}
  else{
    row.append(el('div','avatar','✦'));
    const col=el('div','col'),th=el('details','think','<summary><span class="dots"></span><span class="tlabel">Размышления</span></summary><div class="tbody"></div>'),
      body=el('div','body'),acts=el('div','acts');
    th.hidden=true;col.append(th,body,acts);row.append(col);row._r={th,body,acts};
    fillAssistant(row,m,false);
  }
  msgsEl.append(row);return row;
}
function renderChat(){
  msgsEl.innerHTML='';
  if(!cur.messages.length){
    msgsEl.innerHTML='<div class="empty"><div class="logo">✦</div><h1>Чем помочь?</h1><p>'+esc(modelSel.value||DEFAULT_MODEL)+'</p></div>';
  }else cur.messages.forEach(addMsg);
  renderSide();scrollDown(true);
}
function scrollDown(force){
  const near=scrollEl.scrollHeight-scrollEl.scrollTop-scrollEl.clientHeight<140;
  if(force||near)scrollEl.scrollTo({top:scrollEl.scrollHeight,behavior:force?'auto':'smooth'});
}
function renderSide(){
  const list=$('#list');list.innerHTML='';
  chats.forEach(c=>{
    const it=el('div','item'+(cur&&c.id===cur.id?' active':''));
    it.append(el('span','',esc(c.title)));
    const d=el('button','del','×');d.title='Удалить';
    d.onclick=async e=>{e.stopPropagation();if(!confirm('Удалить чат «'+c.title+'»?'))return;
      await api('/api/chats/'+c.id,{method:'DELETE'});chats=chats.filter(x=>x.id!==c.id);
      if(cur&&cur.id===c.id)newChat();else renderSide()};
    it.append(d);it.onclick=()=>openChat(c.id);list.append(it);
  });
}
function newChat(){
  cur={id:uid(),title:'Новый чат',model:modelSel.value,messages:[],updated:Date.now()};
  renderChat();input.focus();
}
async function openChat(id){
  if(busy)return;
  try{cur=await api('/api/chats/'+id)}catch(e){return}
  if(cur.model&&[...modelSel.options].some(o=>o.value===cur.model))modelSel.value=cur.model;
  renderChat();
}
async function save(){
  cur.updated=Date.now();
  try{
    await api('/api/chats/'+cur.id,{method:'PUT',body:JSON.stringify(cur)});
    chats=await api('/api/chats');renderSide();
  }catch(e){}
}

/* ================= Отправка и потоковый ответ ================= */
function setBusy(b){
  busy=b;sendBtn.textContent=b?'■':'↑';sendBtn.title=b?'Остановить':'Отправить';sendBtn.disabled=false;
}
function friendly(msg){
  if(/Failed to fetch|NetworkError/i.test(msg))return 'Потеряна связь с программой. Закройте окно и откройте приложение заново.';
  return msg;
}
async function generate(m){
  setBusy(true);ctrl=new AbortController();
  const row=msgsEl.lastElementChild;let pending=false,finished=false;
  const upd=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;if(finished)return;fillAssistant(row,m,true);scrollDown()})};
  fillAssistant(row,m,true);
  try{
    const hist=cur.messages.slice(0,-1).map(x=>({role:x.role,content:x.content})).filter(x=>x.content.trim());
    const r=await fetch('/api/chat',{method:'POST',signal:ctrl.signal,headers:{'X-App-Token':TOKEN,'Content-Type':'application/json'},
      body:JSON.stringify({model:cur.model,messages:hist,system:settings.system||''})});
    if(!r.ok){let t=await r.text(),e=t;try{e=JSON.parse(t).error}catch(_){}throw new Error(e)}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){
      const {done,value}=await rd.read();if(done)break;
      buf+=dec.decode(value,{stream:true});let nl;
      while((nl=buf.indexOf('\n'))>=0){
        const line=buf.slice(0,nl).trim();buf=buf.slice(nl+1);if(!line)continue;
        let o;try{o=JSON.parse(line)}catch(_){continue}
        if(o.error)throw new Error(o.error);
        const mm=o.message||{};
        if(mm.thinking)m.thinking=(m.thinking||'')+mm.thinking;
        if(mm.content)m.content=(m.content||'')+mm.content;
        upd();
      }
    }
  }catch(e){if(e.name!=='AbortError')m.error=friendly(e.message||String(e))}
  finally{
    finished=true;ctrl=null;setBusy(false);fillAssistant(row,m,false);scrollDown();
    if(!m.content&&!m.thinking&&!m.error)cur.messages.pop(); // ничего не пришло (остановили сразу)
    await save();input.focus();
  }
}
async function send(){
  if(busy){if(ctrl)ctrl.abort();return}
  const text=input.value.trim();if(!text)return;
  input.value='';autosize();
  cur.model=modelSel.value;
  cur.messages.push({role:'user',content:text});
  if(cur.messages.length===1)cur.title=text.replace(/\s+/g,' ').slice(0,48);
  const m={role:'assistant',content:'',thinking:''};
  cur.messages.push(m);renderChat();
  await generate(m);
}
async function regenerate(){
  if(busy)return;
  const last=cur.messages[cur.messages.length-1];
  if(!last||last.role!=='assistant')return;
  cur.messages.pop();
  const m={role:'assistant',content:'',thinking:''};
  cur.messages.push(m);renderChat();
  await generate(m);
}

/* ================= Интерфейс ================= */
function autosize(){input.style.height='auto';input.style.height=Math.min(input.scrollHeight,200)+'px'}
input.addEventListener('input',autosize);
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send()}});
sendBtn.onclick=send;
$('#newChat').onclick=()=>{if(!busy)newChat()};
$('#toggle').onclick=()=>{const a=$('#app');a.classList.toggle('collapsed');settings.collapsed=a.classList.contains('collapsed');scheduleSave()};
modelSel.onchange=()=>{settings.model=modelSel.value;scheduleSave();if(cur)cur.model=modelSel.value;if(!cur.messages.length)renderChat()};
msgsEl.addEventListener('click',async e=>{
  const b=e.target.closest('.copy');if(!b)return;
  const ok=await copyText(b.closest('.code').querySelector('code').textContent);
  b.textContent=ok?'Скопировано':'Не удалось';setTimeout(()=>b.textContent='Копировать',1200);
});
$('#gear').onclick=openSettings;
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='n'){e.preventDefault();if(!busy)newChat()}});

async function init(){
  try{settings=Object.assign({},DEFAULTS,await api('/api/settings'))}catch(e){}
  try{themes=await api('/api/themes')}catch(e){themes=[]}
  if(!themes.length)themes=[FALLBACK];
  applyTheme();bindView();
  if(settings.collapsed)$('#app').classList.add('collapsed');
  try{
    const r=await api('/api/models');
    if(r.offline){statusEl.textContent='Ollama не запущен';statusEl.classList.add('bad')}
    const names=r.models.length?r.models:[DEFAULT_MODEL];
    names.forEach(n=>modelSel.append(new Option(n,n)));
    const want=settings.model||DEFAULT_MODEL;
    modelSel.value=names.includes(want)?want:(names.includes(DEFAULT_MODEL)?DEFAULT_MODEL:names[0]);
  }catch(e){modelSel.append(new Option(DEFAULT_MODEL,DEFAULT_MODEL))}
  try{chats=await api('/api/chats')}catch(e){chats=[]}
  newChat();
  const ping=()=>fetch('/api/ping',{method:'POST',headers:{'X-App-Token':TOKEN}}).catch(()=>{});
  ping();setInterval(ping,5000);
  window.addEventListener('beforeunload',()=>navigator.sendBeacon('/api/bye?t='+TOKEN));
}
init();
