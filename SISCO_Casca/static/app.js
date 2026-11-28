"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const editable = ["classification", "equipment", "origin", "description", "start_date", "start_time", "end_date", "end_time", "maintenance_status", "forecast_date", "document_return_date", "generation_impact", "maintenance_type"];
  const model = { state: null, selected: null, renderedVersion: null, dirty: false, saving: false, syncing: false, loading: false, filter: "all", poll: null, requestEpoch: 0, view: "occurrences", plantKey: null, plantLabel: "" };
  let toastTimer, timelineReset = true, scrollFrame = null;
  let visibleIds = [];
  let returnRequest = null;
  const returnLabels = {historical:'Histórico de referência',pending:'Aguardando aplicação',applied:'Aplicado',ignored:'Sem alteração',error:'Dados inválidos',conflict:'Conferir divergência',superseded:'Resposta anterior',resolved:'Tratado manualmente'};
  let emailPreview = null, previewEpoch = 0, settingsLoaded = false, settingsDirty = false, settingsVersion = 0;
  const conclusion = (item) => item.workflow?.conclusion || item.values?.conclusion || "Não informada";
  const categoryMatches = (item, value) => value === "all" || (value === "pending" ? ["forms","documents"].includes(item.workflow?.category) : value === "overdue" ? item.workflow?.overdue && ["forms","documents"].includes(item.workflow?.category) : item.workflow?.category === value);

  const asText = (value) => value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
  const normalized = (value) => asText(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
  const current = () => model.state?.items.find((item) => String(item.id) === String(model.selected));
  const node = (tag, text, cls) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
  const dateTime = (value) => { if (!value) return "Ainda não registrada"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? asText(value) : parsed.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); };
  const shortDate = (value) => { const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(asText(value)); return match ? `${match[3]}/${match[2]}/${match[1]}` : asText(value) || "Sem data de início"; };
  const identity = (item) => item.legacy_id ? `Ocorrência ${item.legacy_id}` : item.forms_id ? `Forms ${item.forms_id}` : `Registro ${item.source_row || item.id}`;
  const status = (item) => asText(item.values?.status) || "Não informada";
  const statusTone = (value) => { const text = normalized(value); return /finaliz|encerr|conclu/.test(text) ? "green" : /abert|andamento/.test(text) ? "amber" : /contorn/.test(text) ? "blue" : "neutral"; };
  const makeBadge = (text, tone = "neutral") => node("span", text, `badge ${tone}`);
  const plantName = (item) => { const name = asText(item.values?.plant).trim().replace(/\s+/g, " "); return name === "-" ? "" : name; };
  const plantKey = (item) => normalized(plantName(item));
  const scopedItems = () => (model.state?.items || []).filter((item) => model.view !== "plant" || plantKey(item) === model.plantKey);

  function renderPlants() {
    if (!model.state) return;
    const groups = new Map();
    for (const item of model.state.items) {
      const key = plantKey(item);
      if (!groups.has(key)) groups.set(key, { key, name: plantName(item) || "Usina não informada", items: [] });
      groups.get(key).items.push(item);
    }
    // Carteira (estilo Asana): cada usina com a situação das ocorrências em aberto, as mais críticas primeiro.
    for (const group of groups.values()) group.health = plantHealth(group.items);
    const query = normalized($("plant-search").value).trim();
    const sorted = [...groups.values()].sort((a,b) => !a.key ? 1 : !b.key ? -1 : b.health.rank - a.health.rank || b.health.late - a.health.late || b.health.open - a.health.open || a.name.localeCompare(b.name, "pt-BR"));
    const visible = sorted.filter((group) => !query || normalized(group.name).includes(query));
    $("plants-count").textContent = `${visible.length} de ${groups.size} usinas · as que pedem atenção aparecem primeiro`;
    $("plants-empty").hidden = visible.length > 0;
    $("plants-empty").textContent = groups.size ? "Nenhuma usina encontrada. Ajuste sua pesquisa." : "As usinas aparecerão após a importação dos registros.";
    const sync = model.state.sync || {};
    $("plants-sync").textContent = sync.error ? `Leitura com aviso: ${explain(sync.error)}` : sync.last_success ? `Atualizado em ${dateTime(sync.last_success)} · Leitura automática a cada ${model.state.settings?.sync_seconds || 60} segundos` : "Aguardando a primeira importação.";
    const fragment = document.createDocumentFragment();
    for (const group of visible) {
      const health = group.health, card = node("button", undefined, `plant-card health-${health.tone}`); card.type = "button";
      card.setAttribute("aria-label", `Ver ocorrências de ${group.name}: ${health.label}`);
      const top = node("div", undefined, "plant-card-top");
      top.append(node("span", group.key ? health.label : "Para conferência", `plant-health ${health.tone}`), node("span", "↗", "plant-arrow"));
      const counts = node("div", undefined, "plant-card-counts");
      for (const [value, label, cls] of [[health.open, "em aberto", ""], [health.late, health.late === 1 ? "atrasada" : "atrasadas", health.late ? "bad" : ""], [health.recent, "em 30 dias", ""]]) {
        const cell = node("div", undefined, cls); cell.append(node("strong", String(value)), node("span", label)); counts.append(cell);
      }
      card.append(top, node("h2", group.name), counts);
      if (health.recurrence) card.append(node("p", `Reincidência: ${health.recurrence.name} · ${health.recurrence.count} em 60 dias`, "plant-recurrence"));
      card.append(node("span", "Ver ocorrências →", "plant-card-link"));
      card.addEventListener("click", () => openPlant(group)); fragment.append(card);
    }
    $("plants-grid").replaceChildren(fragment);
  }

  // Situação da usina e reincidência por equipamento (mesmo equipamento 3+ vezes em 60 dias).
  function plantHealth(items) {
    const open = items.filter((item) => item.workflow?.category && item.workflow.category !== "closed");
    const late = open.filter((item) => item.workflow?.overdue).length, stuck = open.filter(isStuck).length;
    const recent = items.filter((item) => { const days = daysFromToday(item.values?.start_date); return days != null && days <= 0 && days >= -30; }).length;
    const equipments = new Map(), compact = (text) => normalized(text).replace(/[^a-z0-9]/g, "");
    for (const item of items) {
      const days = daysFromToday(item.values?.start_date), name = asText(item.values?.equipment).trim().replace(/[.\s]+$/, ""), key = compact(name);
      // "Usina" ou o próprio nome da usina não são equipamento: não contam como reincidência.
      if (days == null || days > 0 || days < -60 || !key || ["usina", "naoinformado"].includes(key) || key === compact(plantName(item))) continue;
      if (!equipments.has(key)) equipments.set(key, { name, count: 0 });
      equipments.get(key).count++;
    }
    const recurrence = [...equipments.values()].filter((entry) => entry.count >= 3).sort((a, b) => b.count - a.count)[0] || null;
    const [tone, label, rank] = late ? ["red", "Atrasada", 3] : stuck ? ["amber", "Atenção", 2] : open.length ? ["blue", "Em andamento", 1] : ["green", "Em dia", 0];
    return { open: open.length, late, stuck, recent, recurrence, tone, label, rank };
  }

  // Gestão (kanban, no estilo Pipefy). As fases são as categorias calculadas em workflow.py: o card
  // muda de fase sozinho quando a etapa é concluída. Arrastar para uma fase à frente só abre o card
  // mostrando o que falta; o arraste não grava nada.
  // sla = meta de dias na fase (como o "tempo na fase" do Pipefy); acima dela o card fica "parado".
  const PHASES=[
    {key:'review',name:'Triagem',hint:'Classificar e revisar',color:'#8b6fd6',sla:1},
    {key:'monitoring',name:'Monitoramento 24h',hint:'Acompanhar a duração',color:'#e8962a',sla:1},
    {key:'sending',name:'Aguardando envio',hint:'Forms e registros prontos para sair',color:'#12a8a0',sla:1},
    {key:'forms',name:'Retorno da manutenção',hint:'Forms, previsão e cobranças',color:'#1f8bf4',sla:7},
    {key:'documents',name:'Registro da ocorrência',hint:'Word, envio e retorno',color:'#d9547a',sla:14},
    {key:'closed',name:'Concluídas',hint:'Encerradas · mais recentes primeiro',color:'#2f9e72',sla:null}];
  const COLLAPSED_KEY='sisco.fases.recolhidas';
  const collapsedPhases=()=>{try{const keys=JSON.parse(localStorage.getItem(COLLAPSED_KEY)||'[]');return Array.isArray(keys)?keys:[];}catch(_){return [];}};
  const saveCollapsed=(key,closed)=>{
    // Concluídas começam recolhidas; abri-las fica guardado à parte.
    if(key==='closed'){try{localStorage.setItem('sisco.fases.concluidas',closed?'recolhida':'aberta');}catch(_){/* só neste navegador */}return;}
    try{localStorage.setItem(COLLAPSED_KEY,JSON.stringify([...collapsedPhases().filter((entry)=>entry!==key),...(closed?[key]:[])]));}catch(_){/* só neste navegador */}};
  const isCollapsed=(key)=>{if(key!=='closed')return collapsedPhases().includes(key);try{return localStorage.getItem('sisco.fases.concluidas')!=='aberta';}catch(_){return true;}};
  // Ordem dos cards dentro de cada fase (as Concluídas seguem sempre das mais recentes para as mais antigas).
  const BOARD_SORTS={prioridade:'Prioridade',prazo:'Prazo mais próximo',parado:'Mais tempo na fase',recentes:'Abertura mais recente',antigas:'Abertura mais antiga'};
  let boardSort='prioridade';
  try{const saved=localStorage.getItem('sisco.fases.ordem');if(saved in BOARD_SORTS)boardSort=saved;}catch(_){/* começa por prioridade */}
  // Número que "conta" até o novo valor quando muda (ex.: total de uma fase).
  function countTo(element,value){
    const before=Number(element.dataset.value);element.dataset.value=value;
    if(!motionOk()||!Number.isFinite(before)||before===value){element.textContent=value.toLocaleString('pt-BR');return;}
    const start=performance.now(),span=Math.min(600,200+Math.abs(value-before)*8);
    const step=(now)=>{const t=Math.min(1,(now-start)/span),eased=1-Math.pow(1-t,3);element.textContent=Math.round(before+(value-before)*eased).toLocaleString('pt-BR');if(t<1&&element.dataset.value==value)requestAnimationFrame(step);};
    requestAnimationFrame(step);
  }
  function toggleTheme(){
    const dark=document.documentElement.dataset.theme!=='dark';
    document.documentElement.dataset.theme=dark?'dark':'light';
    try{localStorage.setItem('sisco.tema',dark?'escuro':'claro');}catch(_){/* só nesta sessão */}
    $("theme-toggle").setAttribute('aria-pressed',String(dark));
  }
  const daysInPhase=(item)=>{const days=daysFromToday(item.workflow?.phase_since);return days==null?null:Math.max(0,-days);};
  const isStuck=(item)=>{const sla=PHASES[phaseIndex(item)].sla,days=daysInPhase(item);return sla!=null&&days!=null&&days>sla;};
  const phaseAge=(item)=>{const days=daysInPhase(item);if(days==null)return '';const approx=item.workflow?.phase_since_estimated?'~':'';return days===0?'Nesta fase desde hoje':`Nesta fase há ${approx}${days} d`;};
  function syncPipeViews(){
    const pipe=['board','occurrences'].includes(model.view),active=model.view==='occurrences'?'list':board.mode;
    $("pipe-views").hidden=!pipe;renderShell();
    document.querySelectorAll("#pipe-views [data-pipe-mode]").forEach((button)=>button.setAttribute('aria-pressed',String(button.dataset.pipeMode===active)));
  }
  const CLOSED_PAGE=20;
  const board={closedLimit:CLOSED_PAGE,dragId:null,openPhase:null,mode:'board'};
  try{if(localStorage.getItem('sisco.gestao.modo')==='calendar')board.mode='calendar';}catch(_){/* Sem armazenamento: começa no quadro. */}
  const CLASS_TAGS={manutencao:['#fff0d9','#985a00'],ti:['#efe8fc','#6545bb'],operacional:['#e4effd','#2363c0'],distribuidora:['#dff4ef','#11796b'],civil:['#f5ece3','#87552d'],ambiental:['#e5f5e6','#2b7836'],informativo:['#edf1f4','#5b6f7d']};
  const itemById=(id)=>model.state?.items.find((item)=>String(item.id)===String(id));
  const phaseKey=(item)=>item?.workflow?.phase||item?.workflow?.category;
  const phaseIndex=(item)=>{const index=PHASES.findIndex((phase)=>phase.key===phaseKey(item));return index<0?1:index;};
  const dueOf=(item)=>{const wf=item.workflow||{};return wf.category==='documents'?(wf.document_deadline||item.values?.document_deadline_date):wf.due_at;};
  const daysFromToday=(value)=>{const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(asText(value));if(!m)return null;const t=new Date();return Math.round((new Date(+m[1],m[2]-1,+m[3])-new Date(t.getFullYear(),t.getMonth(),t.getDate()))/86400000);};
  const pressed=(id)=>$(id).getAttribute('aria-pressed')==='true';

  function classTag(name){
    const text=asText(name).trim(),colors=CLASS_TAGS[normalized(text)],tag=node('span',text||'Não classificada',`ktag${text?'':' ktag-empty'}`);
    if(colors){tag.style.setProperty('--tag-bg',colors[0]);tag.style.setProperty('--tag-fg',colors[1]);}
    return tag;
  }
  function dueChip(item){
    const due=dueOf(item);if(!due||item.workflow?.category==='closed')return null;
    const days=daysFromToday(due),late=item.workflow?.overdue;
    const text=late?(days<0?`Atrasada · ${-days} d`:'Atrasada'):days===0?'Vence hoje':days===1?'Vence amanhã':`Prazo ${shortDate(due).slice(0,5)}`;
    return node('span',text,`kdue${late?' late':days!=null&&days<=1?' soon':''}`);
  }
  function boardCard(item,phase){
    const v=item.values||{},wf=item.workflow||{},stuck=phase.key!=='closed'&&isStuck(item),card=node('button',undefined,`kcard${wf.overdue?' late':''}${stuck?' stuck':''}`);
    card.type='button';card.dataset.itemId=item.id;card.draggable=phase.key!=='closed';card.title=`${identity(item)} · ${conclusion(item)}`;
    card.style.setProperty('--phase',phase.color);
    const tags=node('div',undefined,'kcard-tags');
    if (v.classification) tags.append(classTag(v.classification));
    if(!item.reviewed)tags.append(node('span','A revisar','ktag ktag-review'));
    if(v.generation_impact==='Sim')tags.append(node('span','Afeta geração','ktag ktag-gen'));
    if(item.source_changed)tags.append(node('span','Fonte alterada','ktag ktag-changed'));
    if(stuck)tags.append(node('span',`Parado · meta ${phase.sla} d`,'ktag ktag-stuck'));
    const foot=node('div',undefined,'kcard-foot');
    foot.append(node('span',phase.key!=='closed'&&wf.phase_since?phaseAge(item):`Início ${v.start_date?shortDate(v.start_date):'não informado'}`,`kcard-age${stuck?' stuck':''}`));
    const chip=dueChip(item);if(chip)foot.append(chip);
    card.append(node('span',identity(item),'kcard-id'),node('strong',plantName(item)||'Usina não informada','kcard-title'),node('span',v.equipment||v.description||'Equipamento não informado','kcard-sub'),node('span',conclusion(item),'kcard-status'));
    if (tags.childElementCount) card.append(tags);
    card.append(foot);
    return card;
  }
  function boardColumns(){
    if($("board").children.length)return [...$("board").children];
    const columns=PHASES.map((phase,index)=>{
      const col=node('section',undefined,'kcol'),head=node('header',undefined,'kcol-head'),name=node('div',undefined,'kcol-name'),title=node('h3',phase.name),late=node('span','','kcol-late'),stuck=node('span','','kcol-stuck'),alerts=node('div',undefined,'kcol-alerts');
      col.dataset.phase=index;col.setAttribute('role','listitem');col.setAttribute('aria-labelledby',`kcol-title-${index}`);col.style.setProperty('--phase',phase.color);
      // Clique no cabeçalho recolhe a fase (fica uma faixa estreita com o nome e o total), como no Pipefy.
      col.classList.toggle('collapsed',isCollapsed(phase.key));
      head.tabIndex=0;head.setAttribute('role','button');head.setAttribute('aria-expanded',String(!col.classList.contains('collapsed')));head.title='Clique para recolher ou abrir a fase';
      const toggle=()=>{const closed=col.classList.toggle('collapsed');head.setAttribute('aria-expanded',String(!closed));saveCollapsed(phase.key,closed);};
      head.addEventListener('click',toggle);head.addEventListener('keydown',(event)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle();}});
      title.id=`kcol-title-${index}`;late.hidden=true;stuck.hidden=true;alerts.append(late,stuck);
      name.append(node('span',undefined,'kcol-dot'),title,node('span','0','kcol-count'));
      head.append(name,node('p',phase.sla?`${phase.hint} · meta ${phase.sla} d`:phase.hint,'kcol-hint'),alerts);
      col.append(head,node('div',undefined,'kcol-cards'));
      return col;
    });
    $("board").replaceChildren(...columns);
    return columns;
  }
  function sortedBoard(a,b){
    const start=(item)=>`${asText(item.values?.start_date)}T${asText(item.values?.start_time)}`;
    if(boardSort==='prazo'){const ad=asText(dueOf(a)),bd=asText(dueOf(b));if(ad!==bd)return !ad?1:!bd?-1:ad.localeCompare(bd);return boardOrder(a,b);}
    if(boardSort==='parado'){const diff=(daysInPhase(b)??-1)-(daysInPhase(a)??-1);return diff||boardOrder(a,b);}
    if(boardSort==='recentes')return start(b).localeCompare(start(a));
    if(boardSort==='antigas')return start(a).localeCompare(start(b));
    return boardOrder(a,b);
  }
  function boardOrder(a,b){
    const late=Number(Boolean(b.workflow?.overdue))-Number(Boolean(a.workflow?.overdue));if(late)return late;
    const ad=asText(dueOf(a)),bd=asText(dueOf(b));
    if(ad!==bd)return !ad?1:!bd?-1:ad.localeCompare(bd);
    return chronological(b,a);
  }
  function boardItems(){
    const query=normalized($("board-search").value).trim(),classification=$("board-classification").value,plant=$("board-plant").value;
    return model.state.items.filter((item)=>{
      const v=item.values||{};
      if(classification&&asText(v.classification)!==classification)return false;
      if(plant&&plantName(item)!==plant)return false;
      if(pressed("board-overdue")&&!item.workflow?.overdue)return false;
      if(pressed("board-pending")&&item.reviewed)return false;
      return !query||normalized([item.legacy_id,item.forms_id,v.plant,v.equipment,v.description,v.classification,conclusion(item)].join(" ")).includes(query);
    });
  }
  function renderBoard(){
    // Durante o arraste o quadro não é refeito (a leitura automática apagaria o card arrastado).
    if(!model.state||model.view!=='board'||board.dragId!=null)return;
    const all=model.state.items;
    updateOptions("board-classification",all.map((item)=>item.values?.classification),"Todas as classificações");
    updateOptions("board-plant",all.map(plantName),"Todas as usinas");
    const extraFilters = [$("board-classification").value, $("board-plant").value].filter(Boolean);
    $("board-filter-label").textContent = extraFilters.length ? `Filtros (${extraFilters.length})` : "Filtros";
    const calendar=board.mode==='calendar';
    $("board").hidden=calendar;$("calendar").hidden=!calendar;
    syncPipeViews();
    if(calendar){renderCalendar(boardItems());if($("card-dialog").open)renderCardPhases();return;}
    const items=boardItems(),groups=PHASES.map(()=>[]);
    items.forEach((item)=>groups[phaseIndex(item)].push(item));
    boardColumns().forEach((col,index)=>{
      const phase=PHASES[index],list=groups[index],closed=phase.key==='closed';
      list.sort(closed?(a,b)=>chronological(b,a):sortedBoard);
      const shown=closed?list.slice(0,board.closedLimit):list,late=list.filter((item)=>item.workflow?.overdue).length;
      countTo(col.querySelector('.kcol-count'),list.length);
      const lateTag=col.querySelector('.kcol-late');lateTag.hidden=!late;lateTag.textContent=`${late} atrasada${late>1?'s':''}`;
      const stuckCount=closed?0:list.filter(isStuck).length,stuckTag=col.querySelector('.kcol-stuck');
      stuckTag.hidden=!stuckCount;stuckTag.textContent=`${stuckCount} parada${stuckCount>1?'s':''}`;
      const box=col.querySelector('.kcol-cards'),top=box.scrollTop,fragment=document.createDocumentFragment();
      shown.forEach((item)=>fragment.append(boardCard(item,phase)));
      if(!list.length)fragment.append(node('p',items.length===all.length?'Nenhum card nesta fase.':'Nenhum card com estes filtros.','kcol-empty'));
      if(shown.length<list.length){const more=node('button',`Mostrar mais · ${list.length-shown.length} restantes`,'button secondary kcol-more');more.type='button';more.dataset.more='1';fragment.append(more);}
      box.replaceChildren(fragment);box.scrollTop=top;
    });
    $("board-count").textContent=items.length===all.length?`${all.length} cards`:`${items.length} de ${all.length} cards`;
    if($("card-dialog").open)renderCardPhases();
  }
  // Calendário da Gestão: cada ocorrência no dia da próxima ação (cobrança do Forms, previsão informada,
  // cobrança do registro) ou no dia de início. Mesmos filtros e mesmo card do quadro.
  const cal={month:null,basis:'due',frame:0,returnDay:null};
  const dayKey=(date)=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  const calDate=(item)=>asText(cal.basis==='start'?item.values?.start_date:item.workflow?.category==='closed'?'':item.workflow?.due_at||dueOf(item)).slice(0,10);
  const calOrder=(a,b)=>Number(Boolean(b.workflow?.overdue))-Number(Boolean(a.workflow?.overdue))||phaseIndex(a)-phaseIndex(b)||chronological(a,b);
  function calChip(item){
    const wf=item.workflow||{},plant=plantName(item)||'Usina não informada';
    const chip=node('button',`${item.legacy_id||(item.forms_id?`Forms ${item.forms_id}`:item.id)} · ${plant}`,`cal-chip${wf.overdue?' late':''}`);
    chip.type='button';chip.dataset.itemId=item.id;chip.style.setProperty('--phase',PHASES[phaseIndex(item)].color);
    const time=cal.basis==='start'?asText(item.values?.start_time):asText(wf.due_at).slice(11,16);
    chip.title=`${identity(item)} · ${plant}\n${conclusion(item)}${wf.overdue?' · atrasada':''}${time&&time!=='00:00'?` · ${time}`:''}`;
    return chip;
  }
  function calendarDays(items){
    const byDay=new Map();let undated=0;
    for(const item of items){
      const key=calDate(item);
      if(!key){if(cal.basis==='due'&&item.workflow?.category!=='closed')undated++;continue;}
      if(!byDay.has(key))byDay.set(key,[]);
      byDay.get(key).push(item);
    }
    byDay.forEach((list)=>list.sort(calOrder));
    return {byDay,undated};
  }
  function renderCalendar(items){
    const today=new Date(),first=cal.month||new Date(today.getFullYear(),today.getMonth(),1);cal.month=first;
    const offset=(first.getDay()+6)%7,length=new Date(first.getFullYear(),first.getMonth()+1,0).getDate(),weeks=Math.ceil((offset+length)/7);
    const {byDay,undated}=calendarDays(items),month=dayKey(first).slice(0,7),todayKey=dayKey(today);
    let inMonth=0;byDay.forEach((list,key)=>{if(key.startsWith(month))inMonth+=list.length;});
    $("cal-title").textContent=first.toLocaleDateString('pt-BR',{month:'long',year:'numeric'});
    document.querySelectorAll(".cal-basis button").forEach((button)=>button.setAttribute('aria-pressed',String(button.dataset.basis===cal.basis)));
    $("board-count").textContent=`${inMonth} ${cal.basis==='due'?(inMonth===1?'ação':'ações'):(inMonth===1?'ocorrência':'ocorrências')} no mês`;
    $("cal-undated").textContent=undated?`${undated} em aberto sem data de ação (ex.: Triagem)`:'';
    const legend=PHASES.filter((phase)=>cal.basis==='start'||phase.key!=='closed').map((phase)=>{const entry=node('span',phase.name);entry.style.setProperty('--phase',phase.color);return entry;});
    const late=node('span','Atrasada','late');legend.push(late);
    $("cal-legend").replaceChildren(...legend);
    const grid=$("cal-grid"),narrow=window.matchMedia('(max-width:760px)').matches;
    $("calendar").classList.toggle('cal-agenda',narrow);
    if(narrow){
      // Tela estreita: agenda com os dias do mês que têm ocorrências.
      const fragment=document.createDocumentFragment();
      for(let day=1;day<=length;day++){
        const date=new Date(first.getFullYear(),first.getMonth(),day),key=dayKey(date),list=byDay.get(key);if(!list)continue;
        const block=node('section',undefined,`cal-agenda-day${key===todayKey?' today':''}`);
        block.append(node('h3',date.toLocaleDateString('pt-BR',{weekday:'short',day:'numeric',month:'short'})),...list.map(calChip));
        fragment.append(block);
      }
      if(!fragment.childNodes.length)fragment.append(node('p','Nenhuma ocorrência neste mês com estes filtros.','cal-empty'));
      grid.replaceChildren(fragment);
      return;
    }
    grid.style.setProperty('--weeks',weeks);
    const cells=[];
    for(let index=0;index<weeks*7;index++){
      const date=new Date(first.getFullYear(),first.getMonth(),1-offset+index),key=dayKey(date),cell=node('div',undefined,'cal-day');
      cell.classList.toggle('out',date.getMonth()!==first.getMonth());cell.classList.toggle('weekend',index%7>4);
      cell.classList.toggle('today',key===todayKey);cell.classList.toggle('past',key<todayKey);
      cell.dataset.day=key;cell.append(node('span',String(date.getDate()),'cal-num'),node('div',undefined,'cal-events'));
      cells.push(cell);
    }
    grid.replaceChildren(...cells);
    // Quantos cards cabem por dia depende da altura da tela; o excedente vira "+N mais".
    const fit=Math.max(1,Math.floor((cells[0].querySelector('.cal-events').clientHeight+4)/24));
    cells.forEach((cell)=>{
      const list=byDay.get(cell.dataset.day);if(!list)return;
      const box=cell.querySelector('.cal-events'),shown=list.length>fit?fit-1:list.length;
      list.slice(0,shown).forEach((item)=>box.append(calChip(item)));
      if(list.length>shown){const more=node('button',shown?`+${list.length-shown} mais`:`${list.length} ocorrências`,'cal-more');more.type='button';more.dataset.dayMore=cell.dataset.day;box.append(more);}
    });
  }
  function openDay(key){
    const list=calendarDays(boardItems()).byDay.get(key)||[],[y,m,d]=key.split('-').map(Number);
    $("day-title").textContent=new Date(y,m-1,d).toLocaleDateString('pt-BR',{weekday:'long',day:'numeric',month:'long'});
    $("day-list").replaceChildren(...list.map((item)=>{const row=calChip(item);row.classList.add('day-row');row.append(node('small',conclusion(item)));return row;}));
    cal.returnDay=key;
    $("day-dialog").showModal();
  }
  // Acompanhamento mensal: mesmo recorte do relatório mensal do Power BI, linhas do CONTROLE pelo mês da
  // Data Início. As linhas com ID repetido que o importador não transforma em card (ambiguous_legacy)
  // entram só nas contagens, para o total bater com o Excel.
  const monthly={month:null};
  const MONTHS=['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
  const monthLabel=(key,long=false)=>{const [y,m]=key.split('-').map(Number);return long?new Date(y,m-1,1).toLocaleDateString('pt-BR',{month:'long',year:'numeric'}):`${MONTHS[m-1]}/${String(y).slice(2)}`;};
  const shiftMonthKey=(key,step)=>{const [y,m]=key.split('-').map(Number);return dayKey(new Date(y,m-1+step,1)).slice(0,7);};
  const excelDay=(serial)=>typeof serial==='number'&&serial>0?dayKey(new Date(1899,11,30+Math.floor(serial))):'';
  const percent=(part,total)=>total?`${(part/total*100).toLocaleString('pt-BR',{maximumFractionDigits:1})}%`:'—';
  const monthOf=(row)=>asText(row.values?.start_date).slice(0,7);
  const isAbove24=(row)=>normalized(row.values?.sla)==='superior 24h';
  function monthlyRows(){
    const rows=model.state.items.filter((item)=>item.control_row);
    for(const issue of model.state.sync?.issues||[]){
      if(issue.kind!=='ambiguous_legacy')continue;
      const raw=issue.raw_fields?.controle||{},col=(letter)=>{const key=Object.keys(raw).find((k)=>k.startsWith(`${letter} ·`));return key?raw[key]:'';};
      rows.push({pseudo:true,legacy_id:asText(col('C')),values:{classification:asText(col('D')),plant:asText(col('M')),start_date:excelDay(col('F')),status:asText(col('H')),sla:asText(col('S')),word_status:asText(col('W')),document_sent:asText(col('X')),document_return_date:excelDay(col('AD'))},
        workflow:{category:normalized(col('Z'))==='encerrada'?'closed':'monitoring',conclusion:asText(col('Z'))}});
    }
    return rows;
  }
  // Etapa do registro (Word) pelas colunas W, X e AD do CONTROLE.
  function registroStage(row){
    const v=row.values||{},word=normalized(v.word_status);
    if(!word||word==='-'||word.includes('nao se aplica'))return null;
    if(v.document_return_date)return 'received';
    if(normalized(v.document_sent).includes('enviado'))return row.workflow?.overdue&&row.workflow?.category==='documents'?'late':'waiting';
    return word.includes('gerado')?'send':'generate';
  }
  function countBy(rows,key){
    const counts=new Map();
    rows.forEach((row)=>{const label=key(row)||'Não informado';counts.set(label,(counts.get(label)||0)+1);});
    return [...counts.entries()].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0],'pt-BR'));
  }
  function mcard(title,cls=''){const card=node('section',undefined,`mcard ${cls}`);card.append(node('h3',title));return card;}
  function barList(entries,total,limit=entries.length){
    const list=node('div',undefined,'mbars'),max=Math.max(1,...entries.map((entry)=>entry[1]));
    entries.slice(0,limit).forEach(([label,count,tone])=>{
      const row=node('div',undefined,`mbar${tone?` ${tone}`:''}`),track=node('span',undefined,'mbar-track'),fill=node('span',undefined,'mbar-fill');
      fill.style.width=`${count/max*100}%`;track.append(fill);
      row.append(node('span',label,'mbar-label'),track,node('strong',String(count)),node('small',percent(count,total)));
      list.append(row);
    });
    return list;
  }
  // Cartão no estilo do relatório do Power BI: faixa cinza com o título e o número centralizado.
  function biCard(title,value,detail){
    const card=node('section',undefined,'mbi'),body=node('div',undefined,'mbi-body');
    body.append(node('strong',value,'mbi-value'));
    if(detail)body.append(detail instanceof Node?detail:node('p',detail,'mkpi-detail'));
    card.append(node('h3',title,'mbi-head'),body);
    return card;
  }
  function renderMonthly(){
    if(!model.state||model.view!=='monthly')return;
    const rows=monthlyRows(),todayKey=dayKey(new Date()),current=todayKey.slice(0,7),day=Number(todayKey.slice(8,10));
    const keys=rows.map(monthOf).filter((key)=>/^\d{4}-\d{2}$/.test(key)).sort();
    if(!keys.length){$("monthly-body").replaceChildren(node('p','Nenhuma ocorrência importada.','cal-empty'));return;}
    const last=keys[keys.length-1]>current?keys[keys.length-1]:current,months=[];
    for(let key=keys[0];key<=last;key=shiftMonthKey(key,1))months.push(key);
    // Sem escolha, abre o último mês fechado.
    if(!monthly.month||!months.includes(monthly.month))monthly.month=months.includes(shiftMonthKey(current,-1))?shiftMonthKey(current,-1):last;
    const key=monthly.month,partial=key===current,sel=rows.filter((row)=>monthOf(row)===key),total=sel.length;
    const select=$("month-select");
    if(select.options.length!==months.length||select.options[0].value!==last)select.replaceChildren(...[...months].reverse().map((month)=>new Option(monthLabel(month,true),month)));
    select.value=key;$("month-prev").disabled=key===months[0];$("month-next").disabled=key===last;
    $("month-title").textContent=monthLabel(key,true);
    $("month-partial").hidden=!partial;$("month-partial").textContent=`Mês em andamento · até ${String(day).padStart(2,'0')}/${key.slice(5)}`;

    // Comparação: mês anterior inteiro, ou o mesmo período dele quando o mês ainda não acabou.
    const prevKey=shiftMonthKey(key,-1),prev=rows.filter((row)=>monthOf(row)===prevKey&&(!partial||Number(asText(row.values.start_date).slice(8,10))<=day)).length,diff=total-prev;
    const trend=node('p',undefined,'mkpi-detail');
    trend.append(node('span',diff===0?'= igual':`${diff>0?'▲ +':'▼ '}${diff}`,`mdelta${diff>0?' up':diff<0?' down':''}`),document.createTextNode(` vs ${monthLabel(prevKey)}${partial?' no mesmo período':''} (${prev})`));
    const above=sel.filter(isAbove24).length,analyzing=sel.filter((row)=>normalized(row.values?.sla)==='analisar').length;
    const stages={generate:0,send:0,waiting:0,late:0,received:0};
    sel.forEach((row)=>{const stage=registroStage(row);if(stage)stages[stage]++;});
    const required=Object.values(stages).reduce((a,b)=>a+b,0);

    const kpis=node('div',undefined,'mkpi-stack');
    kpis.append(biCard('Ocorrências no mês',total.toLocaleString('pt-BR'),trend),
      biCard('Ocorrências acima 24h',`${above}`,`${percent(above,total)} do mês · ${analyzing} ${analyzing===1?'aberta':'abertas'} há +24h`));

    // Tendência: 12 meses até o mês atual (ou a partir do escolhido, se for mais antigo).
    const end=key>current?key:key<shiftMonthKey(current,-11)?shiftMonthKey(key,11):current,windowKeys=[];
    for(let step=11;step>=0;step--)windowKeys.push(shiftMonthKey(end,-step));
    const series=windowKeys.map((month)=>{const list=rows.filter((row)=>monthOf(row)===month);return {month,total:list.length,above:list.filter(isAbove24).length};});
    const peak=Math.max(1,...series.map((entry)=>entry.total)),chart=node('div',undefined,'mtrend');
    chart.setAttribute('role','group');chart.setAttribute('aria-label','Ocorrências por mês nos últimos 12 meses');
    for(const entry of series){
      const col=node('button',undefined,`mtrend-col${entry.month===key?' selected':''}${entry.month===current?' partial':''}`),area=node('span',undefined,'mtrend-area'),bar=node('span',undefined,'mtrend-bar'),hot=node('span',undefined,'mtrend-hot');
      col.type='button';col.dataset.month=entry.month;
      col.title=`${monthLabel(entry.month,true)}: ${entry.total} ocorrências, ${entry.above} acima de 24h${entry.month===current?' (em andamento)':''}`;
      bar.style.height=`${entry.total/peak*100}%`;hot.style.height=`${entry.total?entry.above/entry.total*100:0}%`;bar.append(hot);
      area.append(node('span',String(entry.total),'mtrend-value'),bar);
      col.append(area,node('span',monthLabel(entry.month),'mtrend-label'));
      chart.append(col);
    }
    const trendCard=mcard('Ocorrências por mês','mspan-9'),legend=node('p',undefined,'mlegend');
    legend.append(node('span','Ocorrências','mlegend-total'),node('span','Acima de 24h','mlegend-hot'),node('span','Clique num mês para abrir','mlegend-hint'));
    trendCard.append(chart,legend);

    const classCard=mcard('Classificação no mês');
    classCard.append(total?barList(countBy(sel,(row)=>asText(row.values?.classification).trim()||'Não classificada'),total):node('p','Sem ocorrências neste mês.','muted'));
    const plants=countBy(sel,(row)=>plantName(row)||'Usina não informada'),plantCard=mcard('Usinas com mais ocorrências');
    plantCard.append(total?barList(plants,total,8):node('p','Sem ocorrências neste mês.','muted'));
    if(plants.length>8)plantCard.append(node('p',`+ ${plants.length-8} outras usinas (${plants.slice(8).reduce((sum,entry)=>sum+entry[1],0)} ocorrências)`,'mcard-foot'));
    const regCard=mcard('Registros (Word) do mês');
    regCard.append(required?barList([['A gerar o Word',stages.generate],['A enviar',stages.send],['Aguardando retorno',stages.waiting],['Retorno vencido',stages.late,'late'],['Recebidos',stages.received,'done']],required):node('p','Nenhuma ocorrência do mês exige registro.','muted'));

    // Pendências de hoje, de todos os meses: o que precisa de ação agora.
    const pendCard=mcard('Pendências hoje','mpend');
    pendCard.append(node('p','Todas as ocorrências em aberto, de qualquer mês.','mcard-sub'));
    for(const phase of PHASES.filter((entry)=>entry.key!=='closed')){
      const list=model.state.items.filter((item)=>phaseKey(item)===phase.key),late=list.filter((item)=>item.workflow?.overdue).length;
      const row=node('button',undefined,'mpend-row');row.type='button';row.dataset.goBoard='1';row.style.setProperty('--phase',phase.color);
      row.append(node('span',phase.name),node('strong',String(list.length)));
      if(late)row.append(node('span',`${late} ${late===1?'atrasada':'atrasadas'}`,'kcol-late'));
      pendCard.append(row);
    }

    const openRows=sel.filter((row)=>!row.pseudo&&row.workflow?.category!=='closed').sort(boardOrder);
    const listCard=mcard(`Ocorrências do mês ainda em aberto · ${openRows.length}`,'mspan-12');
    if(openRows.length){
      const table=node('table',undefined,'mtable'),head=node('tr'),body=node('tbody');
      ['Ocorrência','Início','Usina','Classificação','Etapa atual','Prazo'].forEach((label)=>{const th=node('th',label);th.scope='col';head.append(th);});
      const thead=node('thead');thead.append(head);
      for(const row of openRows){
        const tr=node('tr'),open=node('button',identity(row),'mtable-open'),due=dueChip(row),status=node('td',undefined,'mtable-status');
        open.type='button';open.dataset.openItem=row.id;tr.dataset.openItem=row.id;
        status.style.setProperty('--phase',PHASES[phaseIndex(row)].color);status.append(node('span',conclusion(row)));
        const first=node('td');first.append(open);const dueCell=node('td');if(due)dueCell.append(due);
        tr.append(first,node('td',shortDate(row.values?.start_date)),node('td',plantName(row)||'Usina não informada'),node('td',asText(row.values?.classification)||'Não classificada'),status,dueCell);
        body.append(tr);
      }
      table.append(thead,body);
      const wrap=node('div',undefined,'mtable-wrap');wrap.append(table);listCard.append(wrap);
    } else listCard.append(node('p','Todas as ocorrências deste mês estão concluídas.','muted'));

    $("monthly-body").replaceChildren(kpis,trendCard,classCard,plantCard,regCard,pendCard,listCard);
    const pseudo=sel.filter((row)=>row.pseudo).map((row)=>row.legacy_id),formsOnly=model.state.items.filter((item)=>!item.control_row&&monthOf(item)===key).length,sync=model.state.sync||{};
    $("monthly-note").textContent=[
      'Recorte: linhas da aba CONTROLE pela Data Início, o mesmo critério do Power BI. Acima de 24h = coluna S (Controle 24h).',
      pseudo.length?`Inclui ${pseudo.length} ${pseudo.length===1?'linha':'linhas'} com ID repetido (${pseudo.join(', ')}), que ${pseudo.length===1?'não vira':'não viram'} card.`:'',
      formsOnly?`${formsOnly} ${formsOnly===1?'resposta':'respostas'} do Forms deste mês ainda sem linha no CONTROLE não ${formsOnly===1?'entra':'entram'}.`:'',
      sync.source_saved_at?`Planilha salva em ${dateTime(sync.source_saved_at)}.`:''].filter(Boolean).join(' ');
    if($("card-dialog").open)renderCardPhases();
  }
  // ---------- KPIs da gestão: indicador com meta, tendência, leitura e achados (calculados em indicadores.py) ----------
  // ---------- Casca limpa (estilo Pipefy): sem barra lateral; cada ambiente tem o próprio menu no topo ----------
  const ENVS={
    occurrences:{name:'Ocorrências',desc:'Gestão do processo, da triagem ao registro',icon:'board',views:['board','occurrences','plant','plants'],tabs:[['board','Fases'],['list','Lista'],['calendar','Calendário'],['plants','Usinas']]},
    generation:{name:'Geração',desc:'Situação dos grupos geradores',icon:'generation',views:['generation'],tabs:[]},
    kpis:{name:'KPIs',desc:'Indicadores, diagnóstico e plano de ação',icon:'kpis',views:['kpis'],tabs:[['overview','Visão geral'],['indicators','Indicadores'],['diagnosis','Diagnóstico'],['plan','Plano de ação']]},
    monthly:{name:'Relatórios',desc:'Acompanhamento mensal',icon:'monthly',views:['monthly'],tabs:[]},
    rules:{name:'Flow Studio',desc:'Mapa, BPMN, decisões e regras do fluxo',icon:'rules',views:['rules'],tabs:[['map','Mapa'],['bpmn','BPMN'],['editor','Editor do fluxo'],['dmn','Decisões (DMN)'],['sim','Simulador']]},
    import:{name:'Sincronização',icon:'import',views:['import'],tabs:[]},
    home:{name:'Painel Power BI',icon:'monthly',views:['home'],tabs:[]}};
  const envOf=(view)=>Object.keys(ENVS).find((key)=>ENVS[key].views.includes(view))||null;
  const envIcon=(key)=>document.querySelector(`.sidebar .nav-item[data-view="${ENVS[key].icon}"] svg`)?.cloneNode(true)||node('span');
  function activeTab(env){
    if(env==='occurrences')return model.view==='occurrences'?'list':['plants','plant'].includes(model.view)?'plants':board.mode;
    return env==='kpis'?kpiUI.tab:env==='rules'?rulesUI.mode:'';
  }
  function openTab(env,tab){
    if(env==='occurrences'){
      if(tab==='list')return showView('occurrences');
      if(tab==='plants')return showView('plants');
      showView('board');if(model.view==='board')setBoardMode(tab);return;
    }
    if(env==='kpis'){kpiUI.tab=tab;renderKpis();window.scrollTo({top:0,behavior:'instant'});}
    if(env==='rules')document.querySelector(`[data-rules-mode="${tab}"]`)?.click();
    renderShell();
  }
  function renderShell(){
    const env=envOf(model.view),tabs=$("env-tabs"),current=env?activeTab(env):'';
    document.body.classList.toggle('at-start',model.view==='start');
    $("shell-crumb").textContent=env?`›  ${ENVS[env].name}`:'';
    tabs.hidden=!env||!ENVS[env].tabs.length;
    tabs.replaceChildren(...(env?ENVS[env].tabs:[]).map(([key,label])=>{const button=node('button',label);button.type='button';button.dataset.envTab=key;button.setAttribute('aria-pressed',String(key===current));return button;}));
  }
  // Início: os ambientes (cada um com um número vivo) e as prioridades de hoje.
  function renderStart(){
    if(!model.state||model.view!=='start')return;
    const items=model.state.items,kpis=model.state.kpis||{months:[]},gen=model.state.generation||{},management=model.state.management||{actions:[]};
    const now=new Date(),today=dayKey(now),month=today.slice(0,7),FOLLOW=['monitoring','sending','forms','documents'];
    const following=items.filter((item)=>FOLLOW.includes(phaseKey(item))),late=following.filter((item)=>item.workflow?.overdue);
    const ofMonth=items.filter((item)=>item.control_row&&monthOf(item)===month);
    const plants=gen.usinas||[],units=plants.flatMap((plant)=>plant.unidades),running=units.filter((unit)=>unit.cor==='verde').length,unexplained=plants.filter((plant)=>plant.unidades.some((unit)=>unit.cor==='amarelo'));
    const closedKey=kpis.months.includes(shiftMonthKey(month,-1))?shiftMonthKey(month,-1):kpis.months[kpis.months.length-1],closed=closedKey?kpis.by_month[closedKey]:null;
    const current=kpis.by_month?.[month],lateActions=management.actions.filter((action)=>action.late),plantCount=new Set(items.map(plantKey).filter(Boolean)).size;
    const icon=(env)=>{const box=node('span',undefined,'start-icon');box.append(envIcon(env));return box;};
    startActions=[];
    const go=(element,run)=>{element.dataset.startRow=startActions.length;startActions.push(run);return element;};
    const toKpis=(tab)=>()=>{kpiUI.tab=tab;showView('kpis');};

    const head=node('header',undefined,'start-head'),text=node('div');
    text.append(node('h2','Bem-vindo ao SISCO'),node('p','Gestão operacional das ocorrências e da geração das usinas.'));text.firstChild.id='start-title';head.append(text);

    // Faixa de números: o retrato do momento, cada um levando ao seu ambiente.
    const strip=node('div',undefined,'start-strip');
    const stat=(env,value,label,detail,run,tone='')=>{const tile=go(node('button',undefined,`start-stat ${tone}`),run);tile.type='button';const words=node('span',undefined,'start-stat-text');const line=node('strong');line.append(node('b',String(value)),document.createTextNode(` ${label}`));words.append(line,node('small',detail));tile.append(icon(env),words,node('span','›','start-go'));strip.append(tile);};
    stat('generation',plants.length,plants.length===1?'usina':'usinas',units.length?`${running} de ${units.length} grupos gerando`:'com relatório diário',()=>showView('generation'));
    const base=current?.volume.baseline,delta=base?Math.round((ofMonth.length/base-1)*100):null;
    stat('monthly',ofMonth.length,'ocorrências no mês',delta==null?monthLabel(month,true):`${delta>=0?'+':''}${delta}% contra a média de 3 meses (mês em andamento)`,()=>showView('monthly'));
    stat('kpis',closed?closed.score.bad:0,closed&&closed.score.bad===1?'meta crítica':'metas críticas',closedKey?`fora da meta em ${monthLabel(closedKey)}`:'sem mês fechado',toKpis('indicators'),closed&&closed.score.bad?'bad':'');
    stat('occurrences',following.length,'em andamento',late.length?`${late.length} com prazo vencido`:'nenhuma com prazo vencido',()=>openTab('occurrences','board'));

    // Seus módulos.
    const modules=node('section',undefined,'start-panel'),grid=node('div',undefined,'start-grid');
    modules.append(node('h3','Seus processos'),node('p','Cada módulo tem a própria área de trabalho, com os dados integrados ao SISCO.','start-sub'),grid);
    // Cada processo tem a própria cor suave no ícone.
    const card=(env,name,desc,foot,run,tone='info')=>{const tile=go(node('button',undefined,`start-card tone-${tone}`),run),top=node('span',undefined,'start-card-top'),words=node('span',undefined,'start-card-text'),bottom=node('span',undefined,'start-live');tile.type='button';
      words.append(node('strong',name),node('span',desc,'start-desc'));top.append(icon(env),words);bottom.append(node('span',foot),node('span','›','start-go'));tile.append(top,bottom);grid.append(tile);};
    card('occurrences','Ocorrências','Registro e acompanhamento das ocorrências.',`${ofMonth.length} no mês · ${following.length} em andamento`,()=>openTab('occurrences','board'),'info');
    card('generation','Geração','Situação dos grupos geradores.',units.length?`${plants.length} usinas · ${running} gerando`:'Aguardando a leitura dos relatórios',()=>showView('generation'),'ok');
    card('kpis','KPIs da Gestão','Metas, diagnóstico e plano de ação.',closed?`${closed.score.ok} de ${closed.score.ok+closed.score.warn+closed.score.bad} metas cumpridas em ${monthLabel(closedKey)}`:'6 indicadores',toKpis('overview'),'bad');
    card('monthly','Relatórios','Evolução da operação, mês a mês.','Relatório mensal',()=>showView('monthly'),'indigo');
    card('import','Usinas','Situação de cada usina.',`${plantCount} usinas na carteira`,()=>openTab('occurrences','plants'),'neutral');
    card('rules','Regras do fluxo','Fluxos, classificações e prazos.',`Versão ${model.state.configuration?.version??0}${model.state.rules?.draft?' · rascunho aberto':' publicada'}`,()=>showView('rules'),'violet');

    // Prioridades de hoje: só o que pede ação.
    const list=node('section',undefined,'start-panel start-priorities'),rows=[];
    list.append(node('h3','Prioridades de hoje'),node('p','Pontos que pedem atenção na operação.','start-sub'));
    unexplained.forEach((plant)=>{const stopped=plant.unidades.filter((unit)=>unit.cor==='amarelo');rows.push(['bad','Crítico',`${plant.usina}: ${stopped.length} ${stopped.length===1?'gerador parado':'geradores parados'} sem ocorrência que explique`,stopped.map((unit)=>unit.nome).join(' e '),()=>showView('generation')]);});
    if(late.length)rows.push(['bad','Crítico',`${late.length} ${late.length===1?'ocorrência com prazo vencido':'ocorrências com prazo vencido'}`,late.slice(0,3).map((item)=>`${identity(item).replace('Ocorrência ','')} · ${plantName(item)}`).join('  ·  '),()=>openTab('occurrences','board')]);
    if(closed)kpis.definitions.filter((def)=>closed.kpis[def.key].status==='bad').slice(0,2).forEach((def)=>rows.push(['info','Indicador',def.name,`${kpiValue(closed.kpis[def.key].value,def.unit)} em ${monthLabel(closedKey)} (${kpiTarget(def)})`,toKpis('indicators')]));
    (closed?closed.findings.filter((found)=>found.level===3).slice(0,1):[]).forEach((found)=>rows.push(['warn','Atenção',found.title,found.action,toKpis('overview')]));
    if(lateActions.length)rows.push(['warn','Atenção',`${lateActions.length} ${lateActions.length===1?'ação de melhoria atrasada':'ações de melhoria atrasadas'}`,lateActions[0].title,toKpis('plan')]);
    if(!rows.length)list.append(node('p','Nada pedindo atenção agora.','muted'));
    rows.slice(0,4).forEach(([tone,label,title,detail,run])=>{const row=go(node('button',undefined,`start-row ${tone}`),run);row.type='button';const words=node('span',undefined,'start-row-text');words.append(node('span',label,`start-badge ${tone}`),node('strong',title),node('small',detail));row.append(words,node('span','›','start-go'));list.append(row);});
    if(rows.length){const all=go(node('button',rows.length>4?`Ver todas as prioridades (${rows.length})`:'Ver todas as prioridades','start-link'),toKpis('overview'));all.type='button';list.append(all);}

    // Minha fila: o que depende de uma ação do COG agora.
    const queue=node('section',undefined,'start-panel start-activity'),review=items.filter((item)=>phaseKey(item)==='review').length;
    const unsent=items.filter((item)=>item.values?.end_date&&!['','-','nao se aplica'].includes(normalized(item.values?.word_status))&&!normalized(item.values?.document_sent).includes('enviado')&&!item.values?.document_return_date);
    const dueSoon=following.filter((item)=>!item.workflow?.overdue&&asText(item.workflow?.due_at).slice(0,10)===today);
    queue.append(node('h3','Minha fila'),node('p','O que depende de uma ação sua agora.','start-sub'));
    const queueRow=(kind,text,tag,run,id)=>{const row=node('button',undefined,'start-event');row.type='button';if(id)row.dataset.openItem=id;else go(row,run);row.append(node('span',kind,'start-event-kind'),node('strong',text),node('span',tag,'start-tag'),node('span','›','start-when'));queue.append(row);};
    late.slice(0,3).forEach((item)=>queueRow('!',`${identity(item)} · ${conclusion(item)}`,'prazo vencido',null,item.id));
    dueSoon.slice(0,2).forEach((item)=>queueRow('•',`${identity(item)} · ${conclusion(item)}`,'vence hoje',null,item.id));
    unsent.slice(0,2).forEach((item)=>queueRow('✉',`${identity(item)} · registro a enviar à coordenação`,plantName(item)||'—',null,item.id));
    if(review)queueRow('○',`${review} ${review===1?'ocorrência aguardando':'ocorrências aguardando'} triagem`,'a revisar',()=>openTab('occurrences','board'));
    if(queue.children.length===2)queue.append(node('p','Nada na sua fila agora.','muted'));

    // Atividade recente: o que entrou e o que encerrou por último, pelas datas da planilha.
    const events=[];
    for(const item of items){
      const v=item.values||{};
      if(v.start_date)events.push({at:`${v.start_date}T${asText(v.start_time)||'00:00'}`,text:`${identity(item)} registrada`,plant:plantName(item),id:item.id,kind:'+'});
      if(v.end_date)events.push({at:`${v.end_date}T${asText(v.end_time)||'00:00'}`,text:`${identity(item)} encerrada`,plant:plantName(item),id:item.id,kind:'✓'});
    }
    const recent=events.filter((event)=>event.at<=`${today}T23:59`).sort((a,b)=>b.at.localeCompare(a.at)).slice(0,5),activity=node('section',undefined,'start-panel start-activity');
    activity.append(node('h3','Atividade recente'),node('p','Últimas ocorrências registradas e encerradas.','start-sub'));
    recent.forEach((event)=>{const row=node('button',undefined,'start-event');row.type='button';row.dataset.openItem=event.id;row.append(node('span',event.kind,'start-event-kind'),node('strong',event.text),node('span',event.plant||'Usina não informada','start-tag'),node('span',dateTime(event.at),'start-when'));activity.append(row);});
    if(gen.coletado_em){const row=go(node('button',undefined,'start-event'),()=>showView('generation'));row.type='button';row.append(node('span','↻','start-event-kind'),node('strong','Geração atualizada pelos relatórios diários'),node('span',`${plants.length} usinas`,'start-tag'),node('span',dateTime(gen.coletado_em),'start-when'));activity.append(row);}

    const columns=node('div',undefined,'start-columns');columns.append(modules,list);
    const bottom=node('div',undefined,'start-bottom');bottom.append(queue,activity);
    $("start-body").replaceChildren(head,strip,columns,bottom);
  }  let startActions=[];
  const kpiUI={month:null,tab:'overview'};
  const KPI_STATUS={ok:['✓','Meta cumprida'],warn:['▲','Perto da meta'],bad:['✕','Fora da meta'],none:['–','Poucos dados']};
  const KPI_LEVEL={3:['!','Prioridade'],2:['▲','Atenção'],1:['i','Observação']};
  const kpiNumber=(value)=>value.toLocaleString('pt-BR',{maximumFractionDigits:1});
  const kpiValue=(value,unit)=>value==null?'—':unit==='%'?`${kpiNumber(value)}%`:unit==='d'?`${kpiNumber(value)} ${value===1?'dia':'dias'}`:kpiNumber(value);
  const kpiTarget=(def)=>`meta ${def.better==='up'?'≥':'≤'} ${kpiValue(def.target,def.unit)}`;
  // Lista de ocorrências por trás de um número: o indicador sempre leva às ocorrências que o explicam.
  function openItemList(title,ids){
    const wanted=new Set(ids),list=model.state.items.filter((item)=>wanted.has(item.id)).sort((a,b)=>asText(b.values?.start_date).localeCompare(asText(a.values?.start_date)));
    $("day-title").textContent=`${title} · ${list.length}`;
    $("day-list").replaceChildren(...list.map((item)=>{const row=calChip(item);row.classList.add('day-row');row.append(node('small',`Início ${shortDate(item.values?.start_date)} · ${conclusion(item)}`));return row;}));
    cal.returnDay=null;$("day-dialog").showModal();
  }
  function kpiSpark(def,months,selected,data){
    const W=280,H=78,left=8,right=8,top=10,bottom=18;
    const points=months.map((month)=>({month,...data.by_month[month].kpis[def.key]}));
    const values=points.map((point)=>point.value).filter((value)=>value!=null).concat(def.target);
    let low=Math.min(...values),high=Math.max(...values);
    if(def.unit==='%'){low=Math.max(0,low-8);high=Math.min(100,high+8);}else{low=0;high=high*1.2||1;}
    if(high===low)high=low+1;
    const x=(index)=>left+(W-left-right)*(months.length===1?0.5:index/(months.length-1)),y=(value)=>top+(H-top-bottom)*(1-(value-low)/(high-low));
    const svg=svgEl('svg',{viewBox:`0 0 ${W} ${H}`,class:'kpi-spark-svg',role:'img','aria-label':`${def.name} por mês, com a linha da meta`});
    svgEl('line',{class:'kpi-meta',x1:left,x2:W-right,y1:y(def.target),y2:y(def.target)},svg);
    // O rótulo "meta" fica do lado oposto ao último ponto, para não cobrir a linha.
    const lastValue=[...points].reverse().find((point)=>point.value!=null)?.value;
    const metaLabel=svgEl('text',{class:'kpi-axis',x:W-right,y:y(def.target)+(lastValue!=null&&lastValue>def.target?10:-4),'text-anchor':'end'},svg);metaLabel.textContent='meta';
    let path='';
    points.forEach((point,index)=>{if(point.value==null)return;path+=`${path&&points[index-1]?.value!=null?'L':'M'}${x(index).toFixed(1)} ${y(point.value).toFixed(1)}`;});
    if(path)svgEl('path',{class:'kpi-line',d:path},svg);
    // Rótulos só no primeiro mês, no último e no escolhido (sem encostar um no outro).
    const chosen=points.findIndex((point)=>point.month===selected),labeled=new Set([chosen,chosen>1||chosen<0?0:-1,chosen<points.length-2?points.length-1:-1]);
    points.forEach((point,index)=>{
      if(labeled.has(index)){const label=svgEl('text',{class:`kpi-axis${point.month===selected?' sel':''}`,x:x(index),y:H-4,'text-anchor':index===0?'start':index===points.length-1?'end':'middle'},svg);label.textContent=monthLabel(point.month);}
      if(point.value!=null)svgEl('circle',{class:`kpi-dot${point.month===selected?' sel':''}`,cx:x(index),cy:y(point.value),r:point.month===selected?5.5:3.5},svg);
      // Alvo de clique maior que a marca: abre o mês.
      const hit=svgEl('circle',{class:'kpi-hit',cx:x(index),cy:point.value!=null?y(point.value):H/2,r:13,'data-kpi-month':point.month,tabindex:0,role:'button','aria-label':`${monthLabel(point.month,true)}: ${kpiValue(point.value,def.unit)}`},svg);
      svgEl('title',{},hit).textContent=`${monthLabel(point.month,true)}: ${kpiValue(point.value,def.unit)}${point.value!=null?` · base ${point.n}`:''}${point.month===data.current?' (em andamento)':''}`;
    });
    return svg;
  }
  function kpiCard(def,key,data,months){
    const entry=data.by_month[key].kpis[def.key],[icon,label]=KPI_STATUS[entry.status],card=node('article',undefined,`kpi-card is-${entry.status}`);
    const head=node('header'),chip=node('span',undefined,`kpi-chip ${entry.status}`);chip.append(node('span',icon,'kpi-chip-icon'),document.createTextNode(label));
    head.append(node('h3',def.name),chip);
    const main=node('div',undefined,'kpi-main');main.append(node('strong',kpiValue(entry.value,def.unit),'kpi-value'),node('span',kpiTarget(def),'kpi-target'));
    card.append(head,node('p',def.question,'kpi-question'),main);
    // Variação contra a média dos 3 meses anteriores, já dizendo se é melhora ou piora.
    const previous=[1,2,3].map((step)=>data.by_month[shiftMonthKey(key,-step)]?.kpis[def.key].value).filter((value)=>value!=null);
    if(entry.value!=null&&previous.length){
      const diff=entry.value-previous.reduce((a,b)=>a+b,0)/previous.length,better=def.better==='up'?diff>0:diff<0,flat=Math.abs(diff)<0.05,delta=node('p',undefined,'kpi-delta');
      delta.append(node('span',flat?'= estável':`${diff>0?'▲ +':'▼ −'}${kpiNumber(Math.abs(diff))}${def.unit==='%'?' p.p.':def.unit==='d'?' d':''} · ${better?'melhorou':'piorou'}`,`kpi-trend${flat?'':better?' good':' bad'}`),document.createTextNode(' contra a média dos 3 meses anteriores'));
      card.append(delta);
    }
    const spark=node('div',undefined,'kpi-spark');spark.append(kpiSpark(def,months,key,data));card.append(spark);
    card.append(node('p',entry.reading,'kpi-reading'));
    const foot=node('div',undefined,'kpi-foot');
    if(entry.failing.length){const open=node('button',`Ver ${entry.failing.length===1?'a ocorrência':`as ${entry.failing.length} ocorrências`}`,'kpi-open');open.type='button';open.dataset.kpiList=def.key;foot.append(open);}
    const how=node('details',undefined,'kpi-how');how.append(node('summary','Como é medido'),node('p',`${def.how}${entry.n?` Base deste mês: ${entry.n}.`:''}`));foot.append(how);
    card.append(foot);
    return card;
  }
  // Mapa de calor: um só tom, do claro ao escuro; o número fica sempre visível na célula.
  function kpiHeat(caption,columns,rows,rowHead){
    const table=node('table',undefined,'kpi-heat'),head=node('tr'),body=node('tbody'),peak=Math.max(1,...rows.flatMap((row)=>row.counts));
    const corner=node('th',rowHead);corner.scope='col';head.append(corner);
    columns.forEach((label)=>{const th=node('th',label);th.scope='col';head.append(th);});
    for(const row of rows){
      const tr=node('tr'),th=node('th',row.name);th.scope='row';tr.append(th);
      row.counts.forEach((count,index)=>{const td=node('td',count?String(count):'·'),level=count/peak;td.style.setProperty('--heat',`${count?Math.round(10+level*80):0}%`);if(level>0.55)td.classList.add('deep');td.title=`${row.name} · ${columns[index]}: ${count}`;tr.append(td);});
      body.append(tr);
    }
    const thead=node('thead');thead.append(head);table.append(node('caption',caption,'sr-only'),thead,body);
    const wrap=node('div',undefined,'mtable-wrap');wrap.append(table);return wrap;
  }
  function renderKpis(){
    if(!model.state||model.view!=='kpis')return;
    const data=model.state.kpis||{months:[]},body=$("kpi-body");
    if(!data.months.length){body.replaceChildren(node('p','Nenhuma ocorrência importada.','cal-empty'));return;}
    const current=data.current,months=data.months,last=months[months.length-1];
    // Sem escolha, abre o último mês fechado: é o que dá para julgar por inteiro.
    if(!kpiUI.month||!months.includes(kpiUI.month))kpiUI.month=months.includes(shiftMonthKey(current,-1))?shiftMonthKey(current,-1):last;
    const key=kpiUI.month,month=data.by_month[key],select=$("kpi-month"),partial=key===current;
    if(select.options.length!==months.length||select.options[0].value!==last)select.replaceChildren(...[...months].reverse().map((entry)=>new Option(monthLabel(entry,true),entry)));
    select.value=key;$("kpi-prev").disabled=key===months[0];$("kpi-next").disabled=key===last;
    $("kpi-title").textContent=monthLabel(key,true);$("kpi-partial").hidden=!partial;
    const defs=data.definitions,names=(status)=>defs.filter((def)=>month.kpis[def.key].status===status).map((def)=>def.name);
    const score=month.score,judged=score.ok+score.warn+score.bad;

    // Veredito: quantas metas foram cumpridas e por onde começar.
    const verdict=node('section',undefined,`kpi-verdict${score.bad?' is-bad':score.warn?' is-warn':judged?' is-ok':''}`),say=node('div',undefined,'kpi-verdict-main');
    say.append(node('strong',judged?`${score.ok} de ${judged} ${judged===1?'meta cumprida':'metas cumpridas'}`:'Ainda sem base para julgar este mês','kpi-verdict-title'),
      node('p',score.bad?`Comece por: ${names('bad').join(' · ')}.`:score.warn?`Perto do limite: ${names('warn').join(' · ')}.`:judged?'Todos os indicadores julgados estão dentro da meta.':'Os percentuais só são julgados com 5 casos ou mais.'));
    const chips=node('div',undefined,'kpi-verdict-chips');
    for(const status of ['ok','warn','bad','none']){if(!score[status])continue;const chip=node('span',undefined,`kpi-chip ${status}`);chip.append(node('span',KPI_STATUS[status][0],'kpi-chip-icon'),document.createTextNode(`${score[status]} ${{ok:'dentro da meta',warn:'perto da meta',bad:'fora da meta',none:'com poucos dados'}[status]}`));chips.append(chip);}
    say.append(chips);
    const volume=month.volume,context=node('div',undefined,'kpi-context'),ratio=volume.baseline?volume.total/volume.baseline:null;
    context.append(node('span','Contexto do mês','kpi-context-label'),node('strong',`${volume.total} ocorrências`),
      node('p',volume.baseline?`Normal: cerca de ${Math.round(volume.baseline)} (média dos 3 meses anteriores)${partial?'':ratio>=1.3?' · acima do normal':ratio<=0.7?' · abaixo do normal':' · dentro do normal'}.`:'Sem meses anteriores para comparar.'),
      node('p',volume.classes.slice(0,5).map((entry)=>`${entry.name} ${entry.count}`).join(' · '),'kpi-context-classes'));
    verdict.append(say,context);

    const span=months.indexOf(key)>=months.length-8?months.slice(-8):months.slice(Math.max(0,months.indexOf(key)-7),months.indexOf(key)+1);
    const grid=node('div',undefined,'kpi-grid');
    defs.forEach((def)=>grid.append(kpiCard(def,key,data,span)));

    // Guia: achados do mês, do mais grave ao mais leve, sempre com o que fazer.
    const management=model.state.management||{actions:[],stages:{},causes:{families:[],unmatched:[]}},openRefs=new Set(management.actions.filter((action)=>action.status==='aberta').map((action)=>action.ref));
    const guide=node('section',undefined,'kpi-guide');
    guide.append(node('h3','Onde agir'),node('p','Padrões encontrados nas ocorrências deste mês, do mais grave ao mais leve.','mcard-sub'));
    if(!month.findings.length)guide.append(node('p','Nenhum padrão fora do normal neste mês.','muted'));
    month.findings.forEach((found,index)=>{
      const row=node('article',undefined,`kpi-finding level-${found.level}`),chip=node('span',undefined,`kpi-level level-${found.level}`),text=node('div',undefined,'kpi-finding-text');
      chip.append(node('span',KPI_LEVEL[found.level][0],'kpi-chip-icon'),document.createTextNode(KPI_LEVEL[found.level][1]));
      const action=node('p',undefined,'kpi-action');action.append(node('span','O que fazer: ','kpi-action-label'),document.createTextNode(found.action));
      text.append(node('h4',found.title),node('p',found.detail),action);
      const buttons=node('div',undefined,'kpi-finding-actions');
      if(found.items.length){const open=node('button',`Ver ${found.items.length===1?'a ocorrência':`as ${found.items.length}`}`,'kpi-open');open.type='button';open.dataset.kpiFinding=index;buttons.append(open);}
      // Um achado tem no máximo uma ação aberta.
      if(openRefs.has(found.ref))buttons.append(node('span','Ação aberta','kpi-has-action'));
      else {const make=node('button','Criar ação','kpi-open');make.type='button';make.dataset.kpiAction=index;buttons.append(make);}
      row.append(chip,text,buttons);
      guide.append(row);
    });

    // Ações de melhoria: dono, prazo e a medida antes e agora.
    const actionBox=node('section',undefined,'kpi-guide kpi-actions');
    actionBox.append(node('h3','Ações de melhoria'),node('p','Cada ação guarda a medida do dia em que foi criada e mostra como ela está agora. Concluída há 30 dias sem melhora, a ação pede revisão.','mcard-sub'));
    if(!management.actions.length)actionBox.append(node('p','Nenhuma ação aberta. Crie uma a partir de um item de "Onde agir".','muted'));
    for(const action of management.actions){
      const row=node('article',undefined,`kpi-action-row${action.status==='concluida'?' done':''}`),text=node('div',undefined,'kpi-finding-text'),metric=action.metric,side=node('div',undefined,'kpi-finding-actions');
      const tone=action.status==='concluida'?(action.review==='sem_melhora'?'bad':'ok'):action.late?'bad':'warn';
      const chip=node('span',undefined,`kpi-chip ${tone}`);chip.append(node('span',action.status==='concluida'?(action.review==='sem_melhora'?'✕':'✓'):action.late?'!':'…','kpi-chip-icon'),
        document.createTextNode(action.status==='concluida'?(action.review==='sem_melhora'?'Sem melhora':action.review==='aguardando'?'Concluída · em observação':'Concluída'):action.late?'Atrasada':'Em andamento'));
      text.append(node('h4',action.title),node('p',`${action.owner} · prazo ${shortDate(action.due)}${action.done_at?` · concluída em ${shortDate(action.done_at)}`:''}`),node('p',action.todo,'kpi-action'));
      if(action.note)text.append(node('p',`Resultado: ${action.note}`));
      if(metric&&metric.value!=null){
        const measure=node('p',undefined,'kpi-measure'),unit=metric.type==='coord24'?'%':'';
        measure.append(node('strong',`${metric.value}${unit} → ${action.current??'—'}${action.current!=null?unit:''}`),document.createTextNode(` ${metric.label}`));
        if(action.effect)measure.append(node('span',action.effect,`kpi-trend ${action.effect==='melhorou'?'good':action.effect==='piorou'?'bad':''}`));
        text.append(measure);
      }
      const button=(label,data)=>{const b=node('button',label,'kpi-open');b.type='button';Object.assign(b.dataset,data);side.append(b);};
      if(action.status==='aberta'){button('Concluir',{actionDone:action.id});button('Cancelar',{actionCancel:action.id});}
      else button('Reabrir',{actionReopen:action.id});
      row.append(chip,text,side);actionBox.append(row);
    }

    // Padrões: onde, o quê e quando.
    const patterns=month.patterns,labels=patterns.months.map((entry)=>monthLabel(entry)),section=node('div',undefined,'kpi-patterns');
    const plants=mcard('Usinas: ocorrências por mês','kpi-wide');plants.append(node('p','Últimos 6 meses. Quanto mais escuro, mais ocorrências.','mcard-sub'),kpiHeat('Ocorrências por usina e mês',labels,patterns.plants,'Usina'));
    const classes=mcard('Classificação: ocorrências por mês');classes.append(node('p','Mostra de onde vem o volume.','mcard-sub'),kpiHeat('Ocorrências por classificação e mês',labels,patterns.classes,'Classificação'));
    const causes=mcard('Causas do mês, por família');
    if(patterns.causes.length){
      const bars=barList(patterns.causes.map((entry)=>[entry.name,entry.count]),patterns.causes_total);
      // Passe o mouse para ver como cada família foi escrita na planilha.
      [...bars.children].forEach((row,index)=>{row.title=patterns.causes[index].texts.map((text)=>`${text.name} (${text.count})`).join(' · ');});
      causes.append(bars);
    } else causes.append(node('p','Nenhuma causa informada neste mês.','muted'));
    if(patterns.causes_blank)causes.append(node('p',`${patterns.causes_blank} ${patterns.causes_blank===1?'ocorrência':'ocorrências'} sem causa informada.`,'mcard-foot'));
    if(management.causes.unmatched.length)causes.append(node('p',`${management.causes.unmatched.length} textos de causa ainda sem família. Use o botão Causas para agrupar.`,'mcard-foot'));

    // Onde o tempo fica: mediana de dias em cada etapa, com quem responde por ela.
    const stages=management.stages[key]||[],stageCard=mcard('Onde o tempo fica'),stageTable=node('table',undefined,'mtable'),stageHead=node('tr'),stageBody=node('tbody');
    stageCard.append(node('p','Ocorrências iniciadas no mês. Etapa ainda em andamento conta até hoje.','mcard-sub'));
    ['Etapa','Quem responde','Mediana','Pior caso','Casos',''].forEach((label)=>{const th=node('th',label);th.scope='col';stageHead.append(th);});
    stages.forEach((stage,index)=>{
      const tr=node('tr'),open=node('button','Ver','kpi-open'),cell=node('td');open.type='button';open.dataset.kpiStage=index;cell.append(open);
      tr.append(node('td',stage.name),node('td',stage.owner),node('td',`${kpiNumber(stage.median)} d`),node('td',`${kpiNumber(stage.worst)} d`),node('td',`${stage.n}${stage.running?` (${stage.running} em andamento)`:''}`),cell);stageBody.append(tr);
    });
    if(stages.length){const stageHeadBox=node('thead');stageHeadBox.append(stageHead);stageTable.append(stageHeadBox,stageBody);const box=node('div',undefined,'mtable-wrap');box.append(stageTable);stageCard.append(box);}
    else stageCard.append(node('p','Sem etapas medidas neste mês.','muted'));

    // Por que estão paradas: motivo da espera das ocorrências em acompanhamento (informado no card).
    const waiting=model.state.items.filter((item)=>['forms','documents'].includes(item.workflow?.category)),reasons=new Map();
    waiting.forEach((item)=>{const reason=item.wait?.reason||'Sem motivo informado';reasons.set(reason,(reasons.get(reason)||0)+1);});
    const waitCard=mcard('Por que estão paradas');
    waitCard.append(node('p','Ocorrências em acompanhamento hoje. O motivo é informado no card da ocorrência.','mcard-sub'));
    waitCard.append(waiting.length?barList([...reasons.entries()].sort((a,b)=>(a[0]==='Sem motivo informado')-(b[0]==='Sem motivo informado')||b[1]-a[1]),waiting.length):node('p','Nenhuma ocorrência em acompanhamento.','muted'));
    const week=mcard('Quando acontecem');
    week.append(node('p',`Início das ocorrências por dia da semana e horário, de ${shortDate(patterns.week.from).slice(0,5)} a ${shortDate(patterns.week.to).slice(0,5)}.`,'mcard-sub'),
      kpiHeat('Ocorrências por dia da semana e horário',patterns.week.blocks,patterns.week.days.map((name,index)=>({name,counts:patterns.week.counts[index]})),'Dia'));
    const coords=mcard('Coordenações no mês','kpi-wide'),table=node('table',undefined,'mtable'),head=node('tr'),rows=node('tbody');
    ['Coordenação','Ocorrências','Manutenção em até 24 h','Em aberto','Abertas há +30 dias'].forEach((label)=>{const th=node('th',label);th.scope='col';head.append(th);});
    for(const entry of patterns.coordinations){const tr=node('tr');tr.append(node('td',entry.name),node('td',String(entry.total)),node('td',entry.pct24==null?'—':`${entry.pct24}% de ${entry.maintenance}`),node('td',String(entry.open)),node('td',String(entry.open30)));rows.append(tr);}
    const thead=node('thead');thead.append(head);table.append(thead,rows);const wrap=node('div',undefined,'mtable-wrap');wrap.append(table);coords.append(wrap);
    stageCard.classList.add('kpi-wide');
    section.append(stageCard,causes,waitCard,plants,classes,week,coords);

    // Quatro áreas: como estamos e onde agir · indicadores · por que acontece · o que vamos fazer.
    body.replaceChildren(...({overview:[verdict,guide],indicators:[grid],diagnosis:[section],plan:[actionBox]}[kpiUI.tab]||[verdict,guide]));
    $("kpi-note").textContent=`"Manutenção" = classificações cujo fluxo exige registro (hoje: ${(data.scope||[]).join(', ')||'nenhuma'}). Percentuais com menos de 5 casos não são julgados. Mesmo recorte do relatório mensal: linhas da aba CONTROLE pela Data Início.`;
  }
  function openKpiTargets(){
    const data=model.state?.kpis;if(!data)return;
    $("kpi-form-rows").replaceChildren(...data.definitions.map((def)=>{
      const row=node('label',undefined,'kpi-form-row'),input=node('input'),field=node('span',undefined,'kpi-form-field');
      input.type='number';input.name=def.key;input.min=def.min;input.max=def.max;input.step=1;input.required=true;input.value=def.target;input.dataset.default=def.default;
      field.append(node('span',def.better==='up'?'no mínimo':'no máximo'),input,node('span',def.unit==='%'?'%':def.unit==='d'?'dias':'ocorrências'));
      row.append(node('strong',def.name),node('small',def.how),field);return row;
    }));
    $("kpi-form-error").hidden=true;$("kpi-dialog").showModal();
  }

  // Geração: grupos geradores pelos relatórios diários, cruzados com as ocorrências (cores calculadas em checklist.py).
  const GEN_RANK={amarelo:0,vermelho:1,cinza:2,azul:3,verde:4};
  const GEN_LABEL={verde:'Gerando',azul:'Parada e disponível',vermelho:'Parada com ocorrência',amarelo:'Parada sem ocorrência',cinza:'Não reconhecido'};
  const genTime=(value)=>{const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});};
  function genUnit(unit,plant){
    // Clique na UG abre o texto do relatório e o relato operativo (sem poluir a tela).
    const box=node('div',undefined,`gen-unit ${unit.cor}`);
    box.tabIndex=0;box.setAttribute('role','button');box.setAttribute('aria-expanded','false');
    box.title=`${unit.nome}: ${GEN_LABEL[unit.cor]||''} · clique para ver o relatório`;
    const head=node('div',undefined,'gen-unit-head');
    head.append(node('strong',unit.nome),node('span',unit.mw?`${unit.mw.toLocaleString('pt-BR',{maximumFractionDigits:2})} MW`:unit.texto||'—'));
    const detail=node('div',undefined,'gen-detail');
    detail.append(node('span',GEN_LABEL[unit.cor]||'','gen-detail-label'),node('p',`Relatório: "${unit.texto||'—'}"`));
    if(plant.relato)detail.append(node('p',`Relato operativo: ${plant.relato}`));
    box.append(head,detail);
    if(unit.cor==='vermelho'){
      for(const occurrence of unit.ocorrencias){
        const link=node('button',`Ocorrência ${occurrence.legacy_id||`Forms ${occurrence.forms_id}`} · ${shortDate(occurrence.inicio).slice(0,5)}${occurrence.da_usina?' · usina inteira':''}`,'gen-occurrence');
        link.type='button';link.dataset.openItem=occurrence.id;link.title=`${occurrence.equipamento}: ${occurrence.descricao}`;box.append(link);
      }
    } else if(unit.cor==='amarelo')box.append(node('span','Nenhuma ocorrência aberta explica esta parada','gen-missing'));
    // Histórico dos últimos 30 dias: um quadrado por dia com relatório, na cor do dia.
    const history=unit.historico;
    if(history&&history.days.length){
      const strip=node('div',undefined,'gen-history');strip.setAttribute('role','img');
      const summary=history.availability==null?'sem dias reconhecidos':`disponível em ${history.availability}% de ${history.known} ${history.known===1?'dia':'dias'}${history.unexplained?` · ${history.unexplained} sem ocorrência`:''}`;
      strip.setAttribute('aria-label',`Histórico de ${unit.nome}: ${summary}`);
      history.days.forEach((day)=>{const cell=node('span',undefined,`gen-day ${day.color}`);cell.title=`${shortDate(day.day).slice(0,5)}: ${GEN_LABEL[day.color]||''}`;strip.append(cell);});
      box.append(strip,node('span',summary,'gen-history-text'));
    }
    return box;
  }
  const genUI={filter:'all',query:''};
  function renderGeneration(){
    if(!model.state||model.view!=='generation')return;
    const gen=model.state.generation||{},plants=[...(gen.usinas||[])];
    const worst=(plant)=>Math.min(9,...plant.unidades.map((unit)=>GEN_RANK[unit.cor]??9));
    plants.sort((a,b)=>Number(a.solar)-Number(b.solar)||worst(a)-worst(b)||asText(a.usina).localeCompare(asText(b.usina),'pt-BR'));
    const units=plants.flatMap((plant)=>plant.unidades),count=(color)=>units.filter((unit)=>unit.cor===color).length;
    $("gen-info").textContent=plants.length?`${plants.length} usinas · ${count('verde')} gerando · ${count('amarelo')} sem ocorrência · ${count('vermelho')} com ocorrência${gen.coletado_em?` · lido às ${genTime(gen.coletado_em).slice(-5)}`:''}`:'';
    const alert={erro:`Não foi possível ler os relatórios: ${gen.erro||'erro desconhecido'}.`,sem_dados:'Ainda não houve leitura dos relatórios. Use "Ler relatórios agora".'}[gen.situacao];
    $("gen-alert").hidden=!alert;$("gen-alert").textContent=alert||'';
    // Resumo no topo: cada número também é um filtro.
    const has=(plant,color)=>plant.unidades.some((unit)=>unit.cor===color),alerting=plants.filter((plant)=>has(plant,'amarelo')||has(plant,'vermelho')||plant.desatualizado);
    const strip=$("gen-strip"),tile=(filter,value,label,detail,tone='')=>{const box=node('button',undefined,`start-stat ${tone}${genUI.filter===filter?' on':''}`),words=node('span',undefined,'start-stat-text'),line=node('strong');box.type='button';box.dataset.genFilter=filter;
      line.append(node('b',String(value)),document.createTextNode(` ${label}`));words.append(line,node('small',detail));box.append(words);return box;};
    strip.replaceChildren(tile('all',plants.length,plants.length===1?'usina':'usinas','com relatório diário'),
      tile('running',count('verde'),'gerando',units.length?`${Math.round(count('verde')/units.length*100)}% dos ${units.length} grupos`:'sem grupos lidos'),
      tile('unexplained',count('amarelo'),'sem ocorrência','parados sem explicação',count('amarelo')?'bad':''),
      tile('stopped',count('vermelho'),'com ocorrência','parados ou em manutenção'),
      tile('none','↻',gen.coletado_em?`às ${genTime(gen.coletado_em).slice(-5)}`:'sem leitura','atualização automática a cada 10 min'));
    const FILTERS=[['all',`Todas (${plants.length})`],['alerts',`Com alertas (${alerting.length})`],['running',`Gerando (${plants.filter((plant)=>has(plant,'verde')).length})`],['unexplained',`Sem ocorrência (${plants.filter((plant)=>has(plant,'amarelo')).length})`],['stopped',`Com ocorrência (${plants.filter((plant)=>has(plant,'vermelho')).length})`]];
    $("gen-filters").replaceChildren(...FILTERS.map(([key,label])=>{const button=node('button',label);button.type='button';button.dataset.genFilter=key;button.setAttribute('aria-pressed',String(genUI.filter===key));return button;}));
    const query=normalized(genUI.query).trim(),match={all:()=>true,alerts:(plant)=>alerting.includes(plant),running:(plant)=>has(plant,'verde'),unexplained:(plant)=>has(plant,'amarelo'),stopped:(plant)=>has(plant,'vermelho')}[genUI.filter];
    const shown=plants.filter((plant)=>match(plant)&&(!query||normalized(plant.usina).includes(query)||plant.unidades.some((unit)=>normalized(unit.nome).includes(query))));
    // Alertas da geração: o que pede ação, do mais grave ao mais leve.
    const alerts=$("gen-alerts"),rows=[];
    plants.filter((plant)=>has(plant,'amarelo')).forEach((plant)=>{const stopped=plant.unidades.filter((unit)=>unit.cor==='amarelo');rows.push(['bad','Crítico',`${plant.usina}: ${stopped.length} ${stopped.length===1?'gerador parado':'geradores parados'} sem ocorrência que explique`,stopped.map((unit)=>unit.nome).join(' e '),plant.usina]);});
    plants.filter((plant)=>has(plant,'vermelho')).forEach((plant)=>{const stopped=plant.unidades.filter((unit)=>unit.cor==='vermelho');rows.push(['warn','Atenção',`${plant.usina}: ${stopped.length} ${stopped.length===1?'grupo parado':'grupos parados'} com ocorrência aberta`,stopped.map((unit)=>unit.nome).join(' e '),plant.usina]);});
    plants.filter((plant)=>plant.desatualizado).forEach((plant)=>rows.push(['info','Relatório',`${plant.usina}: relatório desatualizado`,`último em ${genTime(plant.recebido)}`,plant.usina]));
    alerts.replaceChildren(node('h3','Alertas da geração'),node('p','Principais pontos de atenção das usinas.','start-sub'));
    if(!rows.length)alerts.append(node('p','Nenhum alerta agora.','muted'));
    rows.forEach(([tone,label,title,detail,name])=>{const row=node('button',undefined,`start-row ${tone}`),words=node('span',undefined,'start-row-text');row.type='button';row.dataset.genPlant=name;words.append(node('span',label,`start-badge ${tone}`),node('strong',title),node('small',detail));row.append(words,node('span','›','start-go'));alerts.append(row);});
    const fragment=document.createDocumentFragment();
    for(const plant of shown){
      const row=node('article',undefined,`gen-row${plant.unidades.some((unit)=>unit.cor==='amarelo')?' needs':''}`),info=node('div',undefined,'gen-plant'),meta=node('p',undefined,'gen-meta');row.dataset.plant=plant.usina;
      meta.append(node('span',plant.total_mw!=null?`${plant.total_mw.toLocaleString('pt-BR',{maximumFractionDigits:2})} MW no total`:plant.solar?'Usina solar':'Geração não informada'),node('span',`relatório de ${genTime(plant.recebido)}`));
      if(plant.desatualizado)meta.append(node('span','desatualizado','gen-stale'));
      info.append(node('h3',plant.usina),meta);
      const list=node('div',undefined,'gen-units');
      if(plant.solar)list.append(node('p','Relatório por inversores; ainda não interpretado aqui.','gen-solar'));
      else if(!plant.unidades.length)list.append(node('p','Não encontrei os grupos geradores neste relatório.','gen-solar'));
      plant.unidades.forEach((unit)=>list.append(genUnit(unit,plant)));
      row.append(info,list);fragment.append(row);
    }
    if(plants.length&&!shown.length)fragment.append(node('p','Nenhuma usina neste filtro.','cal-empty'));
    if(!plants.length&&!alert)fragment.append(node('p','Nenhum relatório de geração: preencha a aba "Geracao" da planilha de dados.','cal-empty'));
    $("gen-list").replaceChildren(fragment);
  }

  // ---------- Regras do fluxo (mapa estilo n8n): rascunho → testar → publicar ----------
  // O mapa é desenhado a partir do rascunho; cada bloco mostra quantas ocorrências estão nele agora e
  // quantas estariam com o rascunho (simulação do servidor). Nada muda no quadro antes de publicar.
  const RULE_KEYS=['flow_types','flows','document_days','reminder_days','sla_hours','followup_hour','phase_targets','ug_occurrence_days'];
  const PHASE_ORDER=['review','monitoring','sending','forms','documents','closed'];
  const rulesUI={draft:null,base:null,sim:null,selected:'review',trace:null,timer:0,error:'',saving:false,mode:'map',zoom:null};
  try{if(localStorage.getItem('sisco.regras.modo')==='bpmn')rulesUI.mode='bpmn';}catch(_){/* começa no mapa */}
  const clone=(value)=>JSON.parse(JSON.stringify(value??null));
  const phaseName=(key)=>PHASES.find((phase)=>phase.key===key)?.name||key;
  function publishedRules(){
    const c=model.state.configuration||{},rules={};
    for(const key of RULE_KEYS)rules[key]=clone(c[key]);
    rules.flows=(c.flows||[]).map((f)=>({classification:f.classification,flow:f.flow}));
    rules.flow_types=(c.flow_types||[]).map((t)=>({name:t.name,followup:t.followup,coordinator:t.coordinator,recipient:t.recipient}));
    return rules;
  }
  // Compara pelo conteúdo, sem depender da ordem dos campos.
  const canon=(value)=>Array.isArray(value)?value.map(canon):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map((key)=>[key,canon(value[key])])):value;
  const sameRules=(a,b)=>JSON.stringify(canon(a))===JSON.stringify(canon(b));
  function syncRulesDraft(){
    const server=model.state.rules?.draft,key=`${server?.updated_at||''}|${model.state.configuration?.version}`;
    if(rulesUI.base===key&&rulesUI.draft)return;
    rulesUI.base=key;
    rulesUI.draft=server?{...publishedRules(),...clone(server.rules)}:publishedRules();
    if(!server)rulesUI.sim=null;
    else if(!rulesUI.sim)simulateRules();
  }
  async function simulateRules(){
    try{rulesUI.sim=await request('/api/rules/simulate','POST',{rules:rulesUI.draft});renderRules();}catch(error){rulesUI.error=error.message;renderRules();}
  }
  function scheduleRulesSave(){
    clearTimeout(rulesUI.timer);rulesUI.error='';
    rulesUI.timer=setTimeout(saveRulesDraft,650);
    renderRulesCanvas();renderRulesStatus();
  }
  async function saveRulesDraft(){
    rulesUI.saving=true;renderRulesStatus();
    try{
      if(sameRules(rulesUI.draft,publishedRules())){
        if(model.state.rules?.draft)model.state=await request('/api/rules/discard','POST',{});
        rulesUI.sim=null;rulesUI.base=`|${model.state.configuration?.version}`;
      } else {
        const result=await request('/api/rules/draft','PATCH',{rules:rulesUI.draft});
        model.state.rules={...(model.state.rules||{}),draft:result.draft};rulesUI.sim=result.simulation;
        rulesUI.base=`${result.draft.updated_at}|${model.state.configuration?.version}`;
      }
    } catch(error){rulesUI.error=error.message;}
    finally{rulesUI.saving=false;renderRulesCanvas();renderRulesStatus();renderRulesDiff();}
  }
  function countFor(lane,phase,side){
    const lanes=rulesUI.sim?.lanes?.[side]||rulesUI.sim?.lanes?.before;
    let counts=lanes;
    if(!counts){
      counts={};const flows=model.state.configuration?.flows||[];
      for(const item of model.state.items){const flow=flows.find((f)=>normalized(f.classification)===normalized(item.values?.classification))?.flow||'';const key=`${flow}|${phaseKey(item)}`;counts[key]=(counts[key]||0)+1;}
    }
    return Object.entries(counts).filter(([key])=>key.endsWith(`|${phase}`)&&(lane==='*'||key.split('|')[0]===lane)).reduce((sum,[,n])=>sum+n,0);
  }
  function nodeEdited(id){
    const p=publishedRules(),d=rulesUI.draft,diff=(...keys)=>keys.some((key)=>!sameRules(key.split('.').reduce((o,k)=>o?.[k],p),key.split('.').reduce((o,k)=>o?.[k],d)));
    if(id.startsWith('type:')){const name=id.slice(5),a=p.flow_types.find((t)=>t.name===name),b=d.flow_types.find((t)=>t.name===name);return !sameRules(a,b);}
    return {review:diff('phase_targets.review'),classify:diff('flows'),monitoring:diff('sla_hours','phase_targets.monitoring'),sending:diff('phase_targets.sending'),
      forms:diff('reminder_days','followup_hour','phase_targets.forms'),documents:diff('document_days','phase_targets.documents'),other:diff('ug_occurrence_days')}[id]||false;
  }
  function rulesNode(id,title,lines,{lane='*',phase='',color='#8a98a6',kind=''}={}){
    const button=node('button',undefined,`rnode${kind?` ${kind}`:''}${rulesUI.selected===id?' selected':''}${nodeEdited(id)?' edited':''}`);
    button.type='button';button.dataset.node=id;button.style.setProperty('--node',color);
    button.append(node('strong',title));
    lines.filter(Boolean).forEach((line)=>button.append(node('span',line,'rnode-line')));
    if(phase){
      const before=countFor(lane,phase,'before'),after=countFor(lane,phase,'after');
      button.append(node('span',before===after?`${before} aqui agora`:`${before} → ${after} com o rascunho`,`rnode-count${before!==after?' changed':''}`));
    }
    const trace=rulesUI.trace;
    if(trace&&phase&&(lane==='*'||lane===trace.lane)){
      const at=PHASE_ORDER.indexOf(trace.phase),here=PHASE_ORDER.indexOf(phase);
      if(here<=at)button.classList.add('traced');
      if(here===at)button.classList.add('trace-here');
    }
    if(trace&&!phase&&['source','classify'].includes(id))button.classList.add('traced');
    return button;
  }
  const arrow=()=>node('span',undefined,'rarrow');
  function renderRulesMap(){
    const d=rulesUI.draft,map=$("rules-map");if(!d)return;
    const target=(key)=>`meta ${d.phase_targets?.[key]} d`;
    const trunk=node('div',undefined,'rtrunk');
    trunk.append(rulesNode('source','Nova ocorrência',['Planilha de dados'],{kind:'trigger',color:'#1f8bf4'}),arrow(),
      rulesNode('review','Triagem',['Classificar e revisar',target('review')],{phase:'review',color:PHASES[0].color}),arrow(),
      rulesNode('classify','Classificação',[`${d.flows.length} classificações → ${d.flow_types.length} tipos`],{kind:'decision',color:'#1d2b3a'}),
      rulesNode('other','Outras regras',[`UG: ocorrências de até ${d.ug_occurrence_days} d`],{kind:'aside',color:'#6b7a89'}));
    const lanes=node('div',undefined,'rlanes');
    for(const type of d.flow_types){
      const lane=node('div',undefined,'rlane'),classes=d.flows.filter((f)=>normalized(f.flow)===normalized(type.name)).map((f)=>f.classification);
      const head=node('button',undefined,`rlane-head${rulesUI.selected===`type:${type.name}`?' selected':''}${nodeEdited(`type:${type.name}`)?' edited':''}`);
      head.type='button';head.dataset.node=`type:${type.name}`;
      head.append(node('strong',type.name),node('span',classes.length?classes.join(' · '):'nenhuma classificação','rlane-classes'));
      if(rulesUI.trace&&rulesUI.trace.lane===type.name)head.classList.add('traced');
      lane.append(head,arrow());
      if(!type.followup)lane.append(rulesNode('closed','Concluída',['sem acompanhamento'],{lane:type.name,phase:'closed',color:PHASES.at(-1).color}));
      else {
        lane.append(rulesNode('monitoring','Monitoramento',[`até ${d.sla_hours} h`,target('monitoring')],{lane:type.name,phase:'monitoring',color:PHASES[1].color}),arrow(),
          rulesNode('sending','Aguardando envio',[type.recipient==='classificacao'?'para o e-mail da classificação':'para a coordenação da usina',target('sending')],{lane:type.name,phase:'sending',color:'#12a8a0'}),arrow(),
          rulesNode('forms','Retorno da manutenção',[`cobra a cada ${d.reminder_days} d às ${String(d.followup_hour).padStart(2,'0')}h`,target('forms')],{lane:type.name,phase:'forms',color:'#1f8bf4'}),arrow());
        if(type.coordinator)lane.append(rulesNode('documents','Registro (Word)',[`prazo ${d.document_days} d`,target('documents')],{lane:type.name,phase:'documents',color:'#d9547a'}),arrow());
        lane.append(rulesNode('closed','Concluída',[],{lane:type.name,phase:'closed',color:PHASES.at(-1).color}));
      }
      lanes.append(lane);
    }
    map.replaceChildren(trunk,lanes);
  }
  function renderRulesStatus(){
    const draft=model.state.rules?.draft,dirty=Boolean(draft)||!sameRules(rulesUI.draft,publishedRules()),status=$("rules-status");
    const version=model.state.configuration?.version??0;
    status.className=`rules-status${rulesUI.error?' error':dirty?' draft':''}`;
    status.textContent=rulesUI.error?`Rascunho não salvo: ${rulesUI.error}`:rulesUI.saving?'Salvando rascunho e testando com as ocorrências…':
      dirty?`Rascunho não publicado · ${rulesUI.sim?`${rulesUI.sim.changed_total} ${rulesUI.sim.changed_total===1?'ocorrência muda':'ocorrências mudam'}`:'testando…'} · publicado: versão ${version}`:`Publicado · versão ${version} · sem rascunho`;
    $("rules-publish").hidden=!draft||Boolean(rulesUI.error);$("rules-discard").hidden=!dirty;
    if(rulesUI.trace)status.append(node('span',` · Caminho: ${identity(rulesUI.trace.item)} (${rulesUI.trace.lane||'sem tipo de fluxo'}) está em ${phaseName(rulesUI.trace.phase)}`,'rules-trace-info'));
    const versions=model.state.rules?.versions||[],select=$("rules-versions");
    // Cada versão guardada é como as regras estavam antes de uma publicação (a nota é dessa publicação).
    select.replaceChildren(new Option('Histórico de versões',''),...versions.map((v)=>new Option(`Voltar à versão ${v.version} (antes de ${dateTime(v.at)}${v.note?`: ${v.note}`:''})`,v.version)));
  }
  function renderRulesDiff(){
    const sim=rulesUI.sim,box=$("rules-diff");
    box.hidden=!sim||!sim.changed_total;if(box.hidden)return;
    const table=node('table',undefined,'mtable'),head=node('tr'),body=node('tbody');
    ['Ocorrência','Usina','Hoje','Com o rascunho'].forEach((label)=>head.append(node('th',label)));
    for(const change of sim.changed){
      const tr=node('tr');tr.dataset.openItem=change.id;
      const label=change.legacy_id?`Ocorrência ${change.legacy_id}`:`Forms ${change.forms_id}`;
      const describeSide=(side)=>`${phaseName(side.phase)} · ${side.conclusion}${side.due_at?` · prazo ${dateTime(side.due_at)}`:''}${side.overdue?' · atrasada':''}`;
      tr.append(node('td',label),node('td',change.plant||'—'),node('td',describeSide(change.before)),node('td',describeSide(change.after),'rules-after'));
      body.append(tr);
    }
    const thead=node('thead');thead.append(head);table.append(thead,body);
    box.replaceChildren(node('h3',`Com o rascunho, ${sim.changed_total} ${sim.changed_total===1?'ocorrência muda':'ocorrências mudam'}${sim.changed_total>sim.changed.length?` (mostrando ${sim.changed.length})`:''}`),table);
  }
  // Campo numérico do painel: muda o rascunho na hora (o mapa e a simulação acompanham).
  function ruleNumber(label,value,min,max,unit,apply){
    const wrap=node('label',undefined,'rfield'),input=node('input');
    input.type='number';input.min=min;input.max=max;input.value=value;
    input.addEventListener('input',()=>{const n=Number(input.value);if(Number.isInteger(n)&&n>=min&&n<=max){apply(n);scheduleRulesSave();}});
    wrap.append(node('span',label),input,node('small',unit));
    return wrap;
  }
  function renderRulesPanel(){
    const d=rulesUI.draft,panel=$("rules-panel"),id=rulesUI.selected,fragment=document.createDocumentFragment();if(!d)return;
    const title=(text,sub)=>{fragment.append(node('h3',text));if(sub)fragment.append(node('p',sub,'rpanel-sub'));};
    const target=(key)=>ruleNumber('Meta nesta fase',d.phase_targets[key],1,365,'dias · acima disso o card fica "Parado"',(n)=>{d.phase_targets[key]=n;});
    if(id==='source'){title('Nova ocorrência','Gatilho: cada linha nova da planilha de dados entra na Triagem.');}
    else if(id==='review'){title('Triagem','A ocorrência fica aqui até ser classificada e revisada.');fragment.append(target('review'));}
    else if(id==='other'){title('Outras regras','Regras usadas fora do quadro.');fragment.append(ruleNumber('Geração: ocorrência que explica UG parada',d.ug_occurrence_days,1,730,'dias · ocorrências sem encerramento mais antigas que isso não contam',(n)=>{d.ug_occurrence_days=n;}));}
    else if(id==='monitoring'){title('Monitoramento',`Enquanto a ocorrência dura menos que o limite, fica em monitoramento. Acima dele é "Superior 24h"/"Analisar". Vale para todos os tipos com acompanhamento.`);
      fragment.append(ruleNumber('Limite do monitoramento',d.sla_hours,1,168,'horas',(n)=>{d.sla_hours=n;}),target('monitoring'));}
    else if(id==='sending'){title('Aguardando envio','Forms ou registro prontos e ainda não enviados. O destinatário vem do tipo de fluxo (clique no nome do tipo para mudar).');fragment.append(target('sending'));
      const list=node('ul',undefined,'rpanel-list');d.flow_types.filter((t)=>t.followup).forEach((t)=>list.append(node('li',`${t.name}: ${t.recipient==='classificacao'?'e-mail da classificação':'coordenação da usina'}`)));fragment.append(list);}
    else if(id==='forms'){title('Retorno da manutenção','Cobranças de status até a manutenção informar a previsão ou o encerramento.');
      fragment.append(ruleNumber('Cobrar a cada',d.reminder_days,1,365,'dias',(n)=>{d.reminder_days=n;}),ruleNumber('Hora da cobrança',d.followup_hour,0,23,'h (também no dia seguinte à previsão)',(n)=>{d.followup_hour=n;}),target('forms'));}
    else if(id==='documents'){title('Registro (Word)','Só nos tipos que exigem registro. Prazo para o coordenador devolver o Word preenchido.');
      fragment.append(ruleNumber('Prazo do registro',d.document_days,1,365,'dias',(n)=>{d.document_days=n;}),target('documents'));}
    else if(id==='closed'){title('Concluída','Fim do fluxo. Tipos sem acompanhamento chegam aqui logo depois da triagem.');}
    else if(id==='classify'){
      title('Classificação → tipo de fluxo','Cada classificação segue um tipo de fluxo. O tipo define as etapas.');
      const list=node('div',undefined,'rpanel-rows');
      d.flows.forEach((flow,index)=>{
        const row=node('div',undefined,'rpanel-row'),name=node('input'),select=node('select'),remove=node('button','×','icon-button');
        name.value=flow.classification;name.maxLength=100;name.setAttribute('aria-label','Classificação');
        name.addEventListener('input',()=>{flow.classification=name.value;scheduleRulesSave();});
        d.flow_types.forEach((t)=>select.append(new Option(t.name,t.name)));select.value=flow.flow;select.setAttribute('aria-label','Tipo de fluxo');
        select.addEventListener('change',()=>{flow.flow=select.value;scheduleRulesSave();});
        remove.type='button';remove.setAttribute('aria-label',`Remover ${flow.classification}`);remove.addEventListener('click',()=>{d.flows.splice(index,1);scheduleRulesSave();renderRulesPanel();});
        row.append(name,select,remove);list.append(row);
      });
      const add=node('button','+ Classificação','button secondary');add.type='button';add.addEventListener('click',()=>{d.flows.push({classification:'',flow:d.flow_types[0]?.name||''});renderRulesPanel();$("rules-panel").querySelector('.rpanel-row:last-child input')?.focus();});
      const addType=node('button','+ Tipo de fluxo','button secondary');addType.type='button';addType.addEventListener('click',()=>{let n=1;while(d.flow_types.some((t)=>t.name===`NOVO_${n}`))n++;d.flow_types.push({name:`NOVO_${n}`,followup:true,coordinator:false,recipient:'coordenacao'});rulesUI.selected=`type:NOVO_${n}`;scheduleRulesSave();renderRulesPanel();});
      const actions=node('div',undefined,'rpanel-actions');actions.append(add,addType);
      fragment.append(list,actions);
    }
    else if(id.startsWith('type:')){
      const type=d.flow_types.find((t)=>t.name===id.slice(5));
      if(!type){rulesUI.selected='classify';return renderRulesPanel();}
      title(`Tipo de fluxo: ${type.name}`,'As etapas do ramo mudam na hora no mapa.');
      const nameLabel=node('label',undefined,'rfield'),name=node('input');name.value=type.name;name.maxLength=40;
      name.addEventListener('change',()=>{const old=type.name,next=name.value.trim();if(!next||d.flow_types.some((t)=>t!==type&&normalized(t.name)===normalized(next))){name.value=old;return;}
        type.name=next;d.flows.forEach((f)=>{if(f.flow===old)f.flow=next;});rulesUI.selected=`type:${next}`;scheduleRulesSave();renderRulesPanel();});
      nameLabel.append(node('span','Nome'),name);fragment.append(nameLabel);
      for(const [key,label] of [['followup','Acompanha o retorno da manutenção (cobranças de status)'],['coordinator','Exige registro Word do coordenador']]){
        const wrap=node('label',undefined,'rcheck'),box=node('input');box.type='checkbox';box.checked=Boolean(type[key]);
        box.addEventListener('change',()=>{type[key]=box.checked;scheduleRulesSave();});wrap.append(box,node('span',label));fragment.append(wrap);
      }
      const recipient=node('label',undefined,'rfield'),select=node('select');
      select.append(new Option('Coordenação da usina','coordenacao'),new Option('E-mail da própria classificação (ex.: TI)','classificacao'));select.value=type.recipient;
      select.addEventListener('change',()=>{type.recipient=select.value;scheduleRulesSave();});recipient.append(node('span','Para quem vão os e-mails'),select);fragment.append(recipient);
      const used=d.flows.filter((f)=>f.flow===type.name).map((f)=>f.classification);
      fragment.append(node('p',used.length?`Usado por: ${used.join(', ')}`:'Nenhuma classificação usa este tipo.','rpanel-sub'));
      const remove=node('button','Excluir tipo de fluxo','button secondary');remove.type='button';remove.disabled=used.length>0;remove.title=used.length?'Mude as classificações para outro tipo antes de excluir':'';
      remove.addEventListener('click',()=>{d.flow_types=d.flow_types.filter((t)=>t!==type);rulesUI.selected='classify';scheduleRulesSave();renderRulesPanel();});
      fragment.append(remove);
    }
    if(nodeEdited(id))fragment.append(node('p','Alterado no rascunho (ainda não publicado).','rpanel-edited'));
    const logic=node('details',undefined,'studio-json');logic.open=true;logic.append(node('summary','Prévia da lógica (JSON)'),node('pre',JSON.stringify(ruleSlice(id),null,2)));fragment.append(logic);
    panel.replaceChildren(fragment);
  }
  // ---------- Visão BPMN das mesmas regras (estilo Bizagi) ----------
  // O diagrama é montado a partir do rascunho: os nomes dos tipos de fluxo nas decisões, as horas, os dias
  // e os destinatários vêm das regras. O mesmo modelo gera o SVG da tela e o arquivo .bpmn (BPMN 2.0 com DI).
  const SVGNS='http://www.w3.org/2000/svg';
  const svgEl=(tag,attrs={},parent)=>{const el=document.createElementNS(SVGNS,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);parent?.append(el);return el;};
  const BP={task:{fill:'#eaf0fb',stroke:'#1f5fa8'},gateway:{fill:'#fcf8c4',stroke:'#a3a31f'},start:{fill:'#d7f0b8',stroke:'#6aaa2a'},
    timer:{fill:'#fdf6dc',stroke:'#a8902d'},end:{fill:'#f8d0d0',stroke:'#c0262c'},ink:'#1d1d1d',line:'#2b2b2b',font:'Segoe UI, Arial, sans-serif'};
  const BP_LANES=[{id:'lane_op',name:'Operação (COG)'},{id:'lane_sisco',name:'SISCO'},{id:'lane_mnt',name:'Manutenção / Coordenação'}];
  const BP_PHASE_EL={review:'t_review',monitoring:'timer_monitor',sending:'t_send',forms:'t_respond',documents:'t_fill'};
  function wrapText(text,max){
    const words=String(text||'').split(/\s+/),lines=[];let line='';
    for(const word of words){if((line+' '+word).trim().length>max&&line){lines.push(line);line=word;}else line=(line+' '+word).trim();}
    if(line)lines.push(line);return lines;
  }
  function bpmnModel(){
    const d=rulesUI.draft,types=d.flow_types||[],names=(list)=>list.map((t)=>t.name).join(', ')||'nenhum tipo';
    const follow=types.filter((t)=>t.followup),noFollow=types.filter((t)=>!t.followup),withWord=follow.filter((t)=>t.coordinator),noWord=follow.filter((t)=>!t.coordinator);
    const toClass=follow.some((t)=>t.recipient==='classificacao'),toCoord=follow.some((t)=>t.recipient!=='classificacao');
    const recipients=toClass&&toCoord?'coordenação ou e-mail da classificação':toClass?'e-mail da classificação':'coordenação da usina';
    const hour=`${String(d.followup_hour).padStart(2,'0')}h`,H=180,W=1560;
    const nodes=[],flows=[];
    const add=(id,type,lane,cx,cy,label,extra={})=>{const size={task:[140,70],gateway:[50,50],boundary:[28,28]}[type.startsWith('task')?'task':type]||[36,36];
      nodes.push({id,type,lane,x:cx-size[0]/2,y:cy-size[1]/2,w:size[0],h:size[1],cx,cy,label,...extra});};
    // Operação (COG)
    add('start','start','lane_op',125,90,'Ocorrência comunicada',{node:'source',labelAt:'below',labelMax:12});
    add('t_review','task-user','lane_op',240,90,'Classificar e revisar a ocorrência',{node:'review',phase:'review'});
    add('g_follow','gateway','lane_op',380,90,'Acompanha o retorno?',{node:'classify',labelAt:'above'});
    add('end_simple','end','lane_op',620,90,'Encerrada sem acompanhamento',{node:'closed',labelAt:'below'});
    // SISCO
    add('timer_monitor','timer','lane_sisco',380,270,`Monitorar até ${d.sla_hours} h`,{node:'monitoring',phase:'monitoring',labelAt:'below'});
    add('g_sla','gateway','lane_sisco',480,270,`Encerrou em até ${d.sla_hours} h?`,{node:'monitoring',labelAt:'above'});
    add('end_ok','end','lane_sisco',480,335,`Encerrada em até ${d.sla_hours} h`,{node:'closed',labelAt:'right'});
    add('t_send','task-send','lane_sisco',630,270,`Enviar solicitação de status (${recipients})`,{node:'sending',phase:'sending'});
    add('t_remind','task-send','lane_sisco',785,270,'Cobrar atualização',{node:'forms'});
    add('end_rem1','end','lane_sisco',900,270,'Cobrança enviada',{node:'forms',labelAt:'below'});
    add('t_word','task-send','lane_sisco',1070,270,'Gerar e enviar registro (Word)',{node:'documents'});
    add('t_doc_remind','task-send','lane_sisco',1365,270,'Cobrar registro',{node:'documents'});
    add('end_rem2','end','lane_sisco',1480,270,'Cobrança enviada',{node:'documents',labelAt:'below'});
    // Manutenção / Coordenação
    add('t_respond','task-user','lane_mnt',630,450,'Informar previsão ou encerramento (Forms)',{node:'forms',phase:'forms'});
    add('b_cob','boundary','lane_mnt',685,415,`Cobrança: a cada ${d.reminder_days} d às ${hour}`,{node:'forms',attachedTo:'t_respond',labelPos:[735,320,'middle'],labelMax:16});
    add('g_done','gateway','lane_mnt',780,450,'Manutenção finalizou?',{node:'forms',labelAt:'above'});
    add('g_word','gateway','lane_mnt',930,450,'Exige registro Word?',{node:'classify',labelAt:'above'});
    add('end_closed','end','lane_mnt',930,518,'Encerrada',{node:'closed',labelAt:'right'});
    add('t_fill','task-user','lane_mnt',1240,450,'Preencher e devolver o registro',{node:'documents',phase:'documents'});
    add('b_doc','boundary','lane_mnt',1295,415,`Prazo de ${d.document_days} d`,{node:'documents',attachedTo:'t_fill',labelAt:'right'});
    add('end_formal','terminate','lane_mnt',1420,450,'Ocorrência formalizada',{node:'closed',labelAt:'below'});
    const link=(id,from,to,points,label='',muted=false,side='',labelPos=null)=>flows.push({id,from,to,points,label,muted,side,labelPos});
    link('f1','start','t_review',[[143,90],[170,90]]);
    link('f2','t_review','g_follow',[[310,90],[355,90]]);
    link('f3','g_follow','end_simple',[[405,90],[602,90]],`Não · ${names(noFollow)}`,!noFollow.length);
    link('f4','g_follow','timer_monitor',[[380,115],[380,252]],`Sim · ${names(follow)}`,!follow.length);
    link('f5','timer_monitor','g_sla',[[398,270],[455,270]]);
    link('f6','g_sla','end_ok',[[480,295],[480,317]],'Sim');
    link('f7','g_sla','t_send',[[505,270],[560,270]],'Não');
    link('f8','t_send','t_respond',[[630,305],[630,415]]);
    link('f9','t_respond','g_done',[[700,450],[755,450]]);
    link('f10','g_done','t_respond',[[780,475],[780,505],[630,505],[630,485]],'Não · em aberto',false,'',[705,519,'middle']);
    link('f11','b_cob','t_remind',[[685,401],[685,345],[785,345],[785,305]]);
    link('f12','t_remind','end_rem1',[[850,270],[882,270]]);
    link('f13','g_done','g_word',[[805,450],[905,450]],'Sim');
    link('f14','g_word','end_closed',[[930,475],[930,500]],`Não · ${names(noWord)}`,!noWord.length,'left');
    link('f15','g_word','t_word',[[955,450],[1070,450],[1070,305]],`Sim · ${names(withWord)}`,!withWord.length);
    link('f16','t_word','t_fill',[[1140,270],[1240,270],[1240,415]]);
    link('f17','t_fill','end_formal',[[1310,450],[1402,450]]);
    link('f18','b_doc','t_doc_remind',[[1295,401],[1295,345],[1365,345],[1365,305]]);
    link('f19','t_doc_remind','end_rem2',[[1430,270],[1462,270]]);
    return {W,H,lanes:BP_LANES.map((lane,index)=>({...lane,y:index*H})),nodes,flows};
  }
  // Caminho de uma ocorrência no BPMN: elementos até a fase em que ela está.
  function bpmnTrace(){
    const trace=rulesUI.trace;if(!trace)return {path:new Set(),here:''};
    const type=(rulesUI.draft.flow_types||[]).find((t)=>t.name===trace.lane),base=['start','t_review','g_follow'];
    const steps={review:['start','t_review'],monitoring:[...base,'timer_monitor'],sending:[...base,'timer_monitor','g_sla','t_send'],
      forms:[...base,'timer_monitor','g_sla','t_send','t_respond'],documents:[...base,'timer_monitor','g_sla','t_send','t_respond','g_done','g_word','t_word','t_fill']};
    let path=steps[trace.phase],here=BP_PHASE_EL[trace.phase];
    if(trace.phase==='closed'){
      // Concluída: o fim depende do que de fato aconteceu (encerrou dentro do prazo, Forms enviado, registro enviado).
      const values=trace.item.values||{},sent=(value)=>!['','-','nao se aplica','não se aplica'].includes(normalized(value));
      const hours=trace.item.workflow?.hours;
      if(!type||!type.followup){path=[...base,'end_simple'];here='end_simple';}
      else if(!sent(values.forms_sent)&&hours!=null&&hours<=rulesUI.draft.sla_hours){path=[...base,'timer_monitor','g_sla','end_ok'];here='end_ok';}
      else if(type.coordinator&&sent(values.document_sent)){path=[...steps.documents,'end_formal'];here='end_formal';}
      else {path=[...steps.forms,'g_done','g_word','end_closed'];here='end_closed';}
    }
    return {path:new Set(path||[]),here};
  }
  function drawIcon(g,type,x,y){
    if(type==='task-user'){svgEl('circle',{cx:x+8,cy:y+6,r:3.2,fill:'none',stroke:BP.ink,'stroke-width':1.2},g);svgEl('path',{d:`M${x+2} ${y+16}c0-4 2.6-6.2 6-6.2s6 2.2 6 6.2z`,fill:'none',stroke:BP.ink,'stroke-width':1.2},g);}
    if(type==='task-send'){svgEl('rect',{x,y:y+2,width:16,height:11,rx:1,fill:BP.ink},g);svgEl('path',{d:`M${x} ${y+2}l8 6 8-6`,fill:'none',stroke:'#fff','stroke-width':1.1},g);}
  }
  function drawClock(g,cx,cy,r){
    svgEl('circle',{cx,cy,r,fill:'#fff',stroke:BP.timer.stroke,'stroke-width':1},g);
    for(let i=0;i<12;i+=3){const a=i*Math.PI/6;svgEl('line',{x1:cx+Math.sin(a)*(r-2.5),y1:cy-Math.cos(a)*(r-2.5),x2:cx+Math.sin(a)*r,y2:cy-Math.cos(a)*r,stroke:BP.timer.stroke,'stroke-width':1},g);}
    svgEl('path',{d:`M${cx} ${cy-r*0.65}V${cy}l${r*0.5} ${r*0.25}`,fill:'none',stroke:BP.ink,'stroke-width':1.1},g);
  }
  function drawLabel(parent,text,x,y,{anchor='middle',size=10.5,max=22,weight=400,fill=BP.ink}={}){
    const lines=wrapText(text,max),label=svgEl('text',{x,y,'text-anchor':anchor,'font-size':size,'font-family':BP.font,'font-weight':weight,fill},parent);
    lines.forEach((line,index)=>{const span=svgEl('tspan',{x,dy:index?size*1.25:0},label);span.textContent=line;});
    return lines.length;
  }
  function buildBpmnSvg(model,{interactive=true}={}){
    const {W,H,lanes,nodes,flows}=model,height=H*lanes.length,svg=svgEl('svg',{xmlns:SVGNS,viewBox:`-10 -10 ${W+20} ${height+20}`,width:W+20,height:height+20,class:'bpmn-svg'});
    svgEl('rect',{x:-10,y:-10,width:W+20,height:height+20,fill:'#ffffff'},svg);
    const defs=svgEl('defs',{},svg),marker=svgEl('marker',{id:'bp-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'},defs);
    svgEl('path',{d:'M0 0L10 5L0 10z',fill:BP.line},marker);
    const markerOn=svgEl('marker',{id:'bp-arrow-on',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'},defs);
    svgEl('path',{d:'M0 0L10 5L0 10z',fill:'#12a8a0'},markerOn);
    // Piscina e raias, com os títulos na vertical (como no Bizagi).
    svgEl('rect',{x:0,y:0,width:W,height,fill:'#fff',stroke:'#3a3a3a','stroke-width':1.2},svg);
    svgEl('line',{x1:30,y1:0,x2:30,y2:height,stroke:'#3a3a3a','stroke-width':1.2},svg);
    const pool=svgEl('text',{x:19,y:height/2,'text-anchor':'middle','font-size':14,'font-weight':700,'font-family':BP.font,fill:BP.ink,transform:`rotate(-90 19 ${height/2})`},svg);pool.textContent='Gestão de Ocorrências';
    lanes.forEach((lane,index)=>{
      if(index)svgEl('line',{x1:30,y1:lane.y,x2:W,y2:lane.y,stroke:'#3a3a3a','stroke-width':1},svg);
      svgEl('line',{x1:60,y1:lane.y,x2:60,y2:lane.y+H,stroke:'#9a9a9a','stroke-width':0.8},svg);
      const t=svgEl('text',{x:46,y:lane.y+H/2,'text-anchor':'middle','font-size':11.5,'font-weight':600,'font-family':BP.font,fill:BP.ink,transform:`rotate(-90 46 ${lane.y+H/2})`},svg);t.textContent=lane.name;
    });
    const {path,here}=interactive?bpmnTrace():{path:new Set(),here:''};
    for(const flow of flows){
      const on=flow.id!=='f10'&&path.has(flow.from)&&path.has(flow.to),g=svgEl('g',{class:`bp-flow${on?' traced':''}${flow.muted?' muted':''}`},svg);
      svgEl('polyline',{points:flow.points.map((p)=>p.join(',')).join(' '),fill:'none',stroke:on?'#12a8a0':flow.muted?'#b9b9b9':BP.line,'stroke-width':on?2.4:1.2,'stroke-dasharray':flow.muted?'4 3':'none','marker-end':`url(#${on?'bp-arrow-on':'bp-arrow'})`},g);
      if(flow.label){
        // Rótulo junto da saída do gateway: acima da linha (horizontal) ou ao lado dela (vertical).
        const [a,b]=flow.points,vertical=a[0]===b[0],fill=flow.muted?'#9a9a9a':BP.ink;
        if(flow.labelPos)drawLabel(g,flow.label,flow.labelPos[0],flow.labelPos[1],{anchor:flow.labelPos[2],size:10,max:22,fill});
        else if(vertical){const left=flow.side==='left';drawLabel(g,flow.label,a[0]+(left?-6:6),Math.min(a[1],b[1])+(Math.abs(b[1]-a[1])<40?10:20),{anchor:left?'end':'start',size:10,max:22,fill});}
        else {const lines=wrapText(flow.label,22).length;drawLabel(g,flow.label,Math.min(a[0],b[0])+6,a[1]-7-(lines-1)*12.5,{anchor:'start',size:10,max:22,fill});}
      }
    }
    const counts=(phase)=>({before:countFor('*',phase,'before'),after:countFor('*',phase,'after')});
    for(const n of nodes){
      const g=svgEl('g',{class:`bp-el${interactive&&rulesUI.selected===n.node?' selected':''}${path.has(n.id)?' traced':''}${here===n.id?' here':''}`,'data-node':n.node||'','data-el':n.id},svg);
      const title=svgEl('title',{},g);title.textContent=n.label;
      if(interactive&&n.node){g.setAttribute('tabindex','0');g.setAttribute('role','button');g.setAttribute('aria-label',n.label);}
      if(n.type.startsWith('task')){
        svgEl('rect',{class:'bp-shape',x:n.x,y:n.y,width:n.w,height:n.h,rx:9,fill:BP.task.fill,stroke:BP.task.stroke,'stroke-width':1.8},g);
        drawIcon(g,n.type,n.x+7,n.y+6);
        drawLabel(g,n.label,n.cx,n.cy-(wrapText(n.label,20).length-1)*6.5+4,{size:10.5,max:20});
      } else if(n.type==='gateway'){
        svgEl('path',{class:'bp-shape',d:`M${n.cx} ${n.y}L${n.x+n.w} ${n.cy}L${n.cx} ${n.y+n.h}L${n.x} ${n.cy}Z`,fill:BP.gateway.fill,stroke:BP.gateway.stroke,'stroke-width':1.6},g);
      } else if(n.type==='start'){
        svgEl('circle',{class:'bp-shape',cx:n.cx,cy:n.cy,r:18,fill:BP.start.fill,stroke:BP.start.stroke,'stroke-width':2},g);
        svgEl('rect',{x:n.cx-8,y:n.cy-5.5,width:16,height:11,rx:1,fill:'none',stroke:BP.start.stroke,'stroke-width':1.3},g);
        svgEl('path',{d:`M${n.cx-8} ${n.cy-5.5}l8 6 8-6`,fill:'none',stroke:BP.start.stroke,'stroke-width':1.3},g);
      } else if(n.type==='timer'||n.type==='boundary'){
        const r=n.type==='boundary'?14:18;
        svgEl('circle',{class:'bp-shape',cx:n.cx,cy:n.cy,r,fill:BP.timer.fill,stroke:BP.timer.stroke,'stroke-width':1.4,'stroke-dasharray':n.type==='boundary'?'3 2':'none'},g);
        svgEl('circle',{cx:n.cx,cy:n.cy,r:r-3,fill:'none',stroke:BP.timer.stroke,'stroke-width':1,'stroke-dasharray':n.type==='boundary'?'3 2':'none'},g);
        drawClock(g,n.cx,n.cy,r-6);
      } else {
        svgEl('circle',{class:'bp-shape',cx:n.cx,cy:n.cy,r:18,fill:BP.end.fill,stroke:BP.end.stroke,'stroke-width':3.2},g);
        if(n.type==='terminate')svgEl('circle',{cx:n.cx,cy:n.cy,r:9,fill:'#a11319'},g);
      }
      if(n.labelAt||n.labelPos){
        const pos=n.labelPos||{below:[n.cx,n.y+n.h+14,'middle'],above:[n.cx,n.y-(wrapText(n.label,24).length*13)+4,'middle'],right:[n.x+n.w+6,n.cy-3,'start']}[n.labelAt];
        drawLabel(g,n.label,pos[0],pos[1],{anchor:pos[2],size:10.5,max:n.labelMax||24});
      }
      if(interactive&&n.phase){
        const {before,after}=counts(n.phase),text=before===after?String(before):`${before}→${after}`,width=Math.max(22,text.length*7+10);
        // Na tarefa, o selo fica no canto esquerdo de cima (o direito é dos eventos de borda).
        const bx=n.type.startsWith('task')?n.x+width/2+4:n.cx+12,by=n.type.startsWith('task')?n.y-9:n.y-6;
        svgEl('rect',{x:bx-width/2,y:by-9,width,height:18,rx:9,fill:before===after?'#1f8bf4':'#e0a21a'},g);
        const badge=svgEl('text',{x:bx,y:by+4,'text-anchor':'middle','font-size':10.5,'font-weight':700,'font-family':BP.font,fill:'#fff'},g);badge.textContent=text;
      }
      if(interactive&&n.node&&nodeEdited(n.node)&&(n.type.startsWith('task')||n.type==='gateway'||n.type==='timer'))svgEl('circle',{cx:n.x+4,cy:n.y+4,r:5,fill:'#e0a21a',stroke:'#fff','stroke-width':1.5},g);
    }
    return svg;
  }
  function renderRulesBpmn(){
    const box=$("rules-bpmn");if(!rulesUI.draft)return;
    const model=bpmnModel(),svg=buildBpmnSvg(model),width=model.W+20,height=model.H*model.lanes.length+20;
    // Ao abrir: largura da tela, mas sem ficar ilegível (mínimo 75%, com rolagem). "Ajustar à tela" mostra o fluxo inteiro.
    const fit=((box.clientWidth||$("rules-canvas").clientWidth)-34)/width;
    if(rulesUI.zoom==null)rulesUI.zoom=Math.max(0.75,Math.min(1.2,fit));
    else if(rulesUI.zoom==='fit')rulesUI.zoom=Math.max(0.3,Math.min(1.2,fit));
    // Tamanho também no style: a regra global "svg{width:20px}" dos ícones venceria os atributos.
    for(const [key,value] of [['width',width],['height',height]]){const px=Math.round(value*rulesUI.zoom);svg.setAttribute(key,px);svg.style[key]=`${px}px`;}
    // Paleta de elementos: cada tipo com a quantidade no fluxo; escolher um tipo destaca onde ele aparece.
    const KINDS=[['start','Início',['start']],['task','Tarefa',['task-user']],['message','Mensagem',['task-send']],['gateway','Decisão',['gateway']],['timer','Timer',['timer','boundary']],['end','Fim',['end','terminate']]];
    const chosen=KINDS.find(([key])=>key===rulesUI.kind),typeOf=Object.fromEntries(model.nodes.map((n)=>[n.id,n.type]));
    if(chosen)svg.querySelectorAll('.bp-el').forEach((el)=>el.classList.toggle('dim',!chosen[2].includes(typeOf[el.dataset.el])));
    $("bpmn-palette").replaceChildren(node('span','Elementos BPMN','bpmn-palette-title'),...KINDS.map(([key,label,types])=>{
      const chip=node('button',undefined,`bpmn-kind kind-${key}`);chip.type='button';chip.dataset.bpmnKind=key;chip.setAttribute('aria-pressed',String(rulesUI.kind===key));
      chip.append(node('span',undefined,'bpmn-kind-shape'),document.createTextNode(label),node('b',String(model.nodes.filter((n)=>types.includes(n.type)).length)));return chip;}),
      node('span',chosen?`${chosen[1]} em destaque. Clique num elemento para editar a regra dele.`:'Escolha um tipo para destacá-lo. Arraste o fundo para mover e use Ctrl + roda do mouse para o zoom.','bpmn-palette-hint'));
    box.replaceChildren(svg);$("bpmn-zoom").textContent=`${Math.round(rulesUI.zoom*100)}%`;
  }
  function renderRulesCanvas(){
    const bpmn=rulesUI.mode==='bpmn';
    $("rules-map").hidden=bpmn;$("rules-bpmn").hidden=!bpmn;$("bpmn-tools").hidden=!bpmn;$("bpmn-palette").hidden=!bpmn;$("rules-canvas").classList.toggle('paper',bpmn);
    document.querySelectorAll('[data-rules-mode]').forEach((button)=>button.setAttribute('aria-pressed',String(button.dataset.rulesMode===rulesUI.mode)));
    if(bpmn)renderRulesBpmn();else renderRulesMap();
    renderStudio();
  }
  // Flow Studio: resumo do fluxo (sempre) e o Simulador (comparativo entre a versão publicada e o rascunho).
  function renderStudio(){
    const d=rulesUI.draft;if(!d)return;
    const sim=rulesUI.mode==='sim',editor=rulesUI.mode==='editor',flow=bpmnModel(),count=(test)=>flow.nodes.filter(test).length,items=model.state.items;
    // O editor do fluxo tem a própria barra (rascunho e publicação do fluxo, separados dos parâmetros das regras).
    // As tabelas DMN editam as mesmas regras do Mapa: usam a barra das regras (rascunho, validar, publicar).
    const dmn=rulesUI.mode==='dmn';
    document.querySelector('#rules-view > .rules-layout').hidden=sim||editor||dmn;$("rules-sim").hidden=!sim;$("flow-editor").hidden=!editor;$("rules-dmn").hidden=!dmn;
    document.querySelector('#rules-view > .rules-bar').hidden=editor;$("rules-summary").hidden=editor;if(editor)$("rules-diff").hidden=true;
    if(editor)renderFlowEditor();
    if(dmn)renderRulesDmn();
    const draft=Boolean(model.state.rules?.draft)||!sameRules(d,publishedRules()),summary=$("rules-summary");
    const tile=(tone,title,detail)=>{const box=node('div',undefined,`studio-tile ${tone}`);box.append(node('strong',title),node('span',detail));return box;};
    summary.replaceChildren(
      tile(rulesUI.error?'bad':'ok',rulesUI.error?'Rascunho com erro':'Fluxo válido',rulesUI.error||(draft?'Rascunho sem erros de regra':'Regras publicadas sem erros')),
      tile('',`${count((n)=>n.type.startsWith('task'))} atividades`,`${count((n)=>n.type==='gateway')} decisões · ${count((n)=>['timer','boundary'].includes(n.type))} timers · ${count((n)=>n.type==='task-send'||n.type==='start')} mensagens`),
      tile('',`${d.flow_types.length} tipos de fluxo`,`${d.flows.length} classificações · prazos, cobranças e registros`),
      tile('',`${items.length} ocorrências`,`${items.filter((item)=>['monitoring','sending','forms','documents'].includes(phaseKey(item))).length} em acompanhamento hoje`));
    if(!sim)return;
    const box=$("rules-sim"),result=rulesUI.sim,table=node('table',undefined,'mtable'),head=node('tr'),body=node('tbody');
    ['Resultado da simulação','Versão publicada','Com o rascunho'].forEach((label)=>{const th=node('th',label);th.scope='col';head.append(th);});
    const line=(label,before,after)=>{const tr=node('tr'),last=node('td',String(after));if(String(before)!==String(after))last.className='rules-after';tr.append(node('td',label),node('td',String(before)),last);body.append(tr);};
    PHASE_ORDER.forEach((phase)=>line(`Ocorrências em ${phaseName(phase)}`,countFor('*',phase,'before'),countFor('*',phase,'after')));
    const lanes=(side)=>{const totals={};Object.entries(result?.lanes?.[side]||{}).forEach(([key,n])=>{const name=key.split('|')[0]||'sem tipo de fluxo';totals[name]=(totals[name]||0)+n;});return totals;};
    const before=lanes('before'),after=lanes('after');
    [...new Set([...Object.keys(before),...Object.keys(after)])].sort().forEach((name)=>line(`Encaminhadas ao fluxo ${name}`,before[name]||0,after[name]||0));
    line('Ocorrências que mudam de fase, conclusão ou prazo','—',result?result.changed_total:0);
    line('Regras inválidas',0,rulesUI.error?1:0);
    line('Ações externas executadas','Nenhuma','Nenhuma');
    const thead=node('thead');thead.append(head);table.append(thead,body);const wrap=node('div',undefined,'mtable-wrap');wrap.append(table);
    box.replaceChildren(node('h3','Simular antes de publicar'),
      node('p',draft?'O rascunho foi aplicado a todas as ocorrências existentes, sem gravar nem enviar nada. A lista abaixo mostra as que mudam.':'Não há rascunho: os dois lados são iguais. Edite uma regra no Mapa ou no BPMN e volte aqui para ver o efeito.','mcard-sub'),wrap,
      node('p','Para seguir uma ocorrência específica, digite o ID no campo "Ver o caminho da ocorrência" e abra o Mapa ou o BPMN.','mcard-sub'));
  }
  // ---------- Editor do fluxo: bpmn-js (o editor BPMN de código aberto do Camunda) + o motor do SISCO ----------
  // O desenho é BPMN 2.0 de verdade (piscinas, subprocessos, gateways, tarefas, artefatos). Os campos que o motor usa
  // ficam em atributos "sisco:" de cada elemento e são editados no painel da direita. A cada mudança o desenho é salvo
  // como rascunho; o servidor lê o BPMN, monta o fluxo, aponta os problemas e simula. Só publica sem problemas.
  const flowUI={saving:false,error:'',sim:null,problems:[],trace:null};
  const bpmnUI={modeler:null,loading:null,key:null,timer:0,dirty:false,importing:false,selected:null,organized:null};
  const BPMN_VENDOR='/vendor/bpmn-js-18.31.1/';
  const SISCO_MODDLE={name:'SISCO',uri:'http://sisco.local/bpmn',prefix:'sisco',xml:{tagAlias:'lowerCase'},types:[{name:'Campos',isAbstract:true,extends:['bpmn:BaseElement'],properties:
    ['pergunta','ramo','conclusao','fase','acao','tipoAcao','prazo','destino','descricao'].map((name)=>({name,isAttr:true,type:'String'})).concat([{name:'automatico',isAttr:true,type:'Boolean'}])}]};
  const TASK_TYPES=['bpmn:Task','bpmn:UserTask','bpmn:SendTask','bpmn:ServiceTask','bpmn:ManualTask','bpmn:ScriptTask','bpmn:BusinessRuleTask','bpmn:ReceiveTask'];
  const PIECE_TASK={usuario:'bpmn:UserTask',envio:'bpmn:SendTask',servico:'bpmn:ServiceTask'};
  function loadBpmnJs(){
    if(window.BpmnJS)return Promise.resolve();
    if(bpmnUI.loading)return bpmnUI.loading;
    const css=(file)=>{const link=document.createElement('link');link.rel='stylesheet';link.href=BPMN_VENDOR+file;document.head.append(link);};
    ['diagram-js.css','bpmn-js.css','bpmn-embedded.css'].forEach(css);
    bpmnUI.loading=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=BPMN_VENDOR+'bpmn-modeler.production.min.js';
      script.onload=()=>resolve();script.onerror=()=>{bpmnUI.loading=null;reject(new Error('O editor BPMN não carregou.'));};document.head.append(script);});
    return bpmnUI.loading;
  }
  const flowKey=()=>{const st=model.state.flow;return `${st.draft?.updated_at||'publicado'}|${st.version}`;};
  async function renderFlowEditor(){
    if(!model.state?.flow||rulesUI.mode!=='editor')return;
    renderFlowStatus();
    if(bpmnUI.importing||bpmnUI.dirty||flowUI.saving)return;          // não troca o desenho no meio de uma edição
    if(bpmnUI.modeler&&bpmnUI.key===flowKey()){paintFlowMarkers();return;}
    try{
      await loadBpmnJs();
      const files=await request('/api/flow/bpmn');
      if(!bpmnUI.modeler){
        $("flow-bpmn").replaceChildren();
        bpmnUI.modeler=new window.BpmnJS({container:'#flow-bpmn',moddleExtensions:{sisco:SISCO_MODDLE}});
        hookFlowModeler(bpmnUI.modeler);
      }
      await importFlowXml(files.draft||files.published,files.organized);
    }catch(error){flowUI.error=error.message;renderFlowStatus();}
  }
  async function importFlowXml(xml,organized){
    const modeler=bpmnUI.modeler,keepRoot=bpmnUI.root;bpmnUI.importing=true;
    try{
      const result=await modeler.importXML(xml);
      if(result.warnings?.length)console.warn('BPMN',result.warnings.length,'avisos na leitura');
      bpmnUI.key=flowKey();bpmnUI.organized=organized??bpmnUI.organized;
      const canvas=modeler.get('canvas'),root=keepRoot&&canvas.findRoot(keepRoot);
      if(root)canvas.setRootElement(root);
      canvas.zoom('fit-viewport','auto');canvas.scroll({dx:70,dy:0});   // a paleta fica por cima da borda esquerda
      const selected=bpmnUI.selected&&modeler.get('elementRegistry').get(bpmnUI.selected);
      if(selected)modeler.get('selection').select(selected);
    }finally{bpmnUI.importing=false;}
    paintFlowMarkers();renderFlowPanel();updateFlowZoom();
    // Rascunho já salvo (ao reabrir a página): simula de novo para mostrar quantas ocorrências mudam.
    const draft=model.state.flow.draft;
    if(draft&&!draft.problems?.length&&!flowUI.sim)request('/api/flow/simulate','POST',{flow:draft.flow}).then((sim)=>{flowUI.sim=sim;renderFlowStatus();}).catch(()=>{});
  }
  function hookFlowModeler(modeler){
    modeler.on('commandStack.changed',()=>{if(bpmnUI.importing)return;bpmnUI.dirty=true;flowUI.error='';clearTimeout(bpmnUI.timer);bpmnUI.timer=setTimeout(saveFlowDraft,700);renderFlowStatus();});
    modeler.on('selection.changed',({newSelection})=>{bpmnUI.selected=newSelection[0]?.id||null;renderFlowPanel();});
    modeler.on('element.changed',({element})=>{if(element.id===bpmnUI.selected&&!$("flow-panel").contains(document.activeElement))renderFlowPanel();});
    modeler.on('root.set',({element})=>{bpmnUI.root=element.id;$("flow-up").hidden=!element.id.endsWith('_plane');});
    modeler.on('canvas.viewbox.changed',updateFlowZoom);
  }
  function updateFlowZoom(){if(bpmnUI.modeler)$("flow-zoom").textContent=`${Math.round(bpmnUI.modeler.get('canvas').zoom()*100)}%`;}
  async function saveFlowDraft(){
    if(!bpmnUI.modeler)return;
    flowUI.saving=true;renderFlowStatus();
    try{
      const {xml}=await bpmnUI.modeler.saveXML({format:true});
      const result=await request('/api/flow/draft','PATCH',{bpmn:xml});
      model.state.flow={...model.state.flow,draft:result.draft};flowUI.sim=result.simulation;flowUI.problems=result.draft.problems||[];
      bpmnUI.key=flowKey();bpmnUI.dirty=false;
    }catch(error){flowUI.error=error.message;}
    finally{flowUI.saving=false;renderFlowStatus();paintFlowMarkers();}
  }
  // Marcas no desenho: o caminho de uma ocorrência (verde-azulado), os elementos com problema (vermelho)
  // e, em cada situação, quantas ocorrências estão paradas nela hoje.
  function paintFlowMarkers(){
    const modeler=bpmnUI.modeler;if(!modeler||bpmnUI.importing)return;
    const registry=modeler.get('elementRegistry'),canvas=modeler.get('canvas'),overlays=modeler.get('overlays');
    registry.getAll().forEach((el)=>{canvas.removeMarker(el,'flow-on');canvas.removeMarker(el,'flow-bad');});
    overlays.remove({type:'sisco-count'});
    const path=flowUI.trace?.path||[],onPath=new Set(path);
    const subsOf=(id)=>{const out=[];let bo=id&&registry.get(id)?.businessObject?.$parent;while(bo&&bo.$type==='bpmn:SubProcess'){out.push(bo.id);bo=bo.$parent;}return out;};
    path.forEach((id)=>subsOf(id).forEach((sub)=>onPath.add(sub)));
    // Uma ligação está no caminho quando leva de um passo ao seguinte: direto, entrando num subprocesso,
    // saindo por um evento de saída (sisco:destino) ou saindo do subprocesso fechado no nível de cima.
    const contains=(bo,id)=>Boolean(bo)&&(bo.id===id||(bo.$type==='bpmn:SubProcess'&&subsOf(id).includes(bo.id)));
    const leadsTo=(bo,id)=>contains(bo,id)||(bo?.$type==='bpmn:EndEvent'&&Boolean(bo.get('sisco:destino'))&&(bo.get('sisco:destino')===id||contains(registry.get(bo.get('sisco:destino'))?.businessObject,id)));
    const steps=path.map((b,i)=>[i?path[i-1]:null,b]);
    const stepOn=(source,target)=>steps.some(([a,b])=>{
      if(!leadsTo(target,b))return false;
      if(a&&source.id===a)return true;
      if(a&&source.$type==='bpmn:SubProcess')return subsOf(a).includes(source.id)&&!subsOf(b).includes(source.id);
      if(source.$type==='bpmn:StartEvent'){const host=source.$parent;return host?.$type==='bpmn:SubProcess'?subsOf(b).includes(host.id)&&!(a&&subsOf(a).includes(host.id)):!a;}
      return false;});
    registry.getAll().forEach((el)=>{
      if(el.labelTarget)return;
      if(onPath.has(el.id))canvas.addMarker(el,'flow-on');
      const bo=el.businessObject;
      if(el.waypoints&&path.length&&bo?.$type==='bpmn:SequenceFlow'&&bo.sourceRef&&stepOn(bo.sourceRef,bo.targetRef)){
        canvas.addMarker(el,'flow-on');[el.source,el.target].forEach((end)=>end&&canvas.addMarker(end,'flow-on'));}
    });
    // Problemas vêm com o nome do elemento entre aspas: marca os que têm esse nome.
    const named=new Map();registry.getAll().forEach((el)=>{const bo=el.businessObject,name=bo?.get?.('sisco:conclusao')||bo?.name||(bo?.$type==='bpmn:ExclusiveGateway'?'Decisão':TASK_TYPES.includes(bo?.$type)?'Situação':'');if(name&&!el.waypoints&&!el.labelTarget)(named.get(name)||named.set(name,[]).get(name)).push(el);});
    (flowUI.problems||[]).forEach((text)=>{const name=/"([^"]+)"/.exec(text)?.[1];(named.get(name)||[]).forEach((el)=>canvas.addMarker(el,'flow-bad'));});
    const counts={};model.state.items.forEach((item)=>{const end=item.workflow?.path?.at(-1);if(end)counts[end]=(counts[end]||0)+1;});
    Object.entries(counts).forEach(([id,n])=>{const el=registry.get(id);if(!el)return;
      const badge=node('button',String(n),'flow-count');badge.type='button';badge.title=`${n} ${n===1?'ocorrência está':'ocorrências estão'} aqui hoje`;
      badge.addEventListener('click',(event)=>{event.stopPropagation();const ids=model.state.items.filter((item)=>item.workflow?.path?.at(-1)===id).map((item)=>item.id);openItemList(el.businessObject.get('sisco:conclusao')||el.businessObject.name||id,ids);});
      overlays.add(el,'sisco-count',{position:{top:-10,right:8},html:badge});});
  }
  function renderFlowStatus(){
    const st=model.state.flow,draft=st.draft,status=$("flow-status");
    if(!draft)flowUI.problems=[];else if(!flowUI.saving&&!bpmnUI.dirty)flowUI.problems=draft.problems||[];
    const list=flowUI.problems;
    status.className=`rules-status${flowUI.error||list.length?' error':draft||bpmnUI.dirty?' draft':''}`;
    status.textContent=flowUI.error?`Rascunho não salvo: ${flowUI.error}`:flowUI.saving||bpmnUI.dirty?'Salvando rascunho…':
      !draft?`Fluxo publicado · versão ${st.version}${st.fallback?' (o salvo era inválido; vale o padrão)':''} · sem rascunho`:
      list.length?`Rascunho com ${list.length} ${list.length===1?'problema':'problemas'} · não pode ser publicado`:
      `Rascunho válido · ${flowUI.sim?`${flowUI.sim.changed_total} ${flowUI.sim.changed_total===1?'ocorrência muda':'ocorrências mudam'}`:'simule para ver o efeito'} · publicado: versão ${st.version}`;
    if(flowUI.trace)status.append(node('span',` · Caminho de ${identity(flowUI.trace.item)} no fluxo publicado`,'rules-trace-info'));
    const auto=st.automation,autoOn=Object.values(st.published.elementos).some((el)=>el.automatico);
    if(autoOn&&auto)status.append(node('span',` · Peças automáticas: ${auto.done.length} ${auto.done.length===1?'executada':'executadas'} na última leitura${auto.blocked.length?`, ${auto.blocked.length} ${auto.blocked.length===1?'bloqueada':'bloqueadas'} (${auto.blocked[0].label}: ${auto.blocked[0].reason})`:''}`,'rules-trace-info'));
    $("flow-publish").hidden=!draft||Boolean(list.length)||Boolean(flowUI.error)||bpmnUI.dirty||flowUI.saving;$("flow-discard").hidden=!draft;
    const select=$("flow-versions");
    select.replaceChildren(new Option('Histórico do fluxo',''),...st.versions.map((v)=>new Option(`Voltar à versão ${v.version} (antes de ${dateTime(v.at)}${v.note?`: ${v.note}`:''})`,v.version)));
    const box=$("flow-problems");box.hidden=!list.length;
    if(list.length){const ul=node('ul',undefined,'rpanel-list');list.forEach((p)=>ul.append(node('li',p)));box.replaceChildren(node('h3','O que falta para publicar'),ul);}
  }
  function flowField(label,control,hint){const wrap=node('label',undefined,'rfield');wrap.append(node('span',label),control);if(hint)wrap.append(node('small',hint));return wrap;}
  // Painel da direita: os campos do SISCO do elemento escolhido no desenho.
  function renderFlowPanel(){
    const panel=$("flow-panel"),modeler=bpmnUI.modeler,st=model.state.flow,fragment=document.createDocumentFragment();
    const el=modeler&&bpmnUI.selected?modeler.get('elementRegistry').get(bpmnUI.selected):null;
    if(!el){panel.replaceChildren(node('h3','Editor BPMN'),node('p','Clique num elemento do desenho para configurar o que o SISCO faz nele.','rpanel-sub'),
      node('p','Paleta à esquerda do desenho: gateway (decisão), tarefa (situação), evento de fim, subprocesso e artefatos como anotações e objetos de dados.','rpanel-sub'),
      node('p','Ligue os elementos puxando a seta do menu que aparece ao lado de cada um. As saídas de uma decisão precisam dizer se são o caminho Sim ou Não.','rpanel-sub'));return;}
    const modeling=modeler.get('modeling'),bo=el.businessObject,type=bo.$type,get=(name)=>bo.get(`sisco:${name}`);
    const set=(props)=>modeling.updateProperties(el,props);
    const text=(value,apply,max=120)=>{const input=node('input');input.type='text';input.maxLength=max;input.value=value||'';input.addEventListener('change',(event)=>apply(event.target.value.trim()));return input;};
    const choose=(entries,value,apply,empty)=>{const select=node('select');if(empty)select.append(new Option(empty,''));entries.forEach(([k,label])=>select.append(new Option(label,k)));select.value=value??'';select.addEventListener('change',(event)=>apply(event.target.value));return select;};
    const title=(heading,sub)=>fragment.append(node('h3',heading),node('p',sub,'rpanel-sub'));
    const situation=TASK_TYPES.includes(type)||(type==='bpmn:EndEvent'&&!get('destino')&&!(bo.$parent?.$type==='bpmn:SubProcess'&&!get('fase')&&!get('conclusao')));
    if(type==='bpmn:ExclusiveGateway'){
      title('Decisão','Uma pergunta sobre a ocorrência; cada saída é o caminho Sim ou o caminho Não.');
      fragment.append(flowField('Nome no desenho',text(bo.name,(v)=>set({name:v}),60)));
      fragment.append(flowField('Pergunta',choose(Object.entries(st.conditions),get('pergunta'),(v)=>set({'sisco:pergunta':v}),'— escolha a pergunta —')));
      const outs=el.outgoing||[];
      if(!outs.length)fragment.append(node('p','Ainda não há saídas: puxe duas setas deste losango, uma para o Sim e outra para o Não.','rpanel-sub'));
      outs.forEach((flow)=>{const target=flow.target?.businessObject,branch=flow.businessObject.get('sisco:ramo')||'';
        fragment.append(flowField(`Saída para "${target?.get?.('sisco:conclusao')||target?.name||flow.target?.id}"`,choose([['sim','Caminho Sim'],['nao','Caminho Não']],branch,(v)=>modeling.updateProperties(flow,{'sisco:ramo':v||undefined,name:v==='sim'?'Sim':v==='nao'?'Não':undefined}),'— Sim ou Não? —')));});
      if(outs.length===2){const swap=node('button','Trocar Sim e Não','button secondary');swap.type='button';
        swap.addEventListener('click',()=>{const [a,b]=outs,ra=a.businessObject.get('sisco:ramo'),rb=b.businessObject.get('sisco:ramo');
          modeling.updateProperties(a,{'sisco:ramo':rb,name:rb==='sim'?'Sim':'Não'});modeling.updateProperties(b,{'sisco:ramo':ra,name:ra==='sim'?'Sim':'Não'});renderFlowPanel();});
        const actions=node('div',undefined,'rpanel-actions');actions.append(swap);fragment.append(actions);}
    } else if(situation){
      title(type==='bpmn:EndEvent'?'Situação final':'Situação','Onde a ocorrência para: conclusão, fase no quadro, próxima ação e prazo.');
      fragment.append(flowField('Conclusão',text(get('conclusao')||bo.name,(v)=>set({'sisco:conclusao':v,name:v}))));
      fragment.append(flowField('Fase no quadro',choose(Object.entries(st.phases),get('fase')||(type==='bpmn:EndEvent'?'closed':''),(v)=>set({'sisco:fase':v}),'— escolha a fase —')));
      fragment.append(flowField('Próxima ação',text(get('acao'),(v)=>set({'sisco:acao':v}))));
      const TYPE_TEXT={usuario:'Tarefa humana: aparece como botão no card.',envio:'Envio de e-mail: sempre manual, com prévia (no teste, só para o e-mail de teste).',servico:'O SISCO faz sozinho: pode rodar automaticamente.'};
      const pieceKey=get('tipoAcao'),piece=st.actions?.[pieceKey];
      fragment.append(flowField('Peça (o que a situação pede)',choose(Object.entries(st.actions||{}).map(([k,a])=>[k,a.name]),pieceKey,(v)=>{
        const next=st.actions[v],props={'sisco:tipoAcao':v};if(!next?.automatic)props['sisco:automatico']=undefined;
        if(next&&!get('acao'))props['sisco:acao']=next.name;set(props);
        // O formato da tarefa acompanha a peça: humana, envio ou serviço (o fim continua fim).
        const want=next&&PIECE_TASK[next.type];if(want&&TASK_TYPES.includes(type)&&want!==type){const fresh=modeler.get('bpmnReplace').replaceElement(el,{type:want});modeler.get('selection').select(fresh);}
      },'— escolha a peça —'),piece?TYPE_TEXT[piece.type]:''));
      if(piece?.automatic){
        const wrap=node('label',undefined,'rcheck'),box=node('input');box.type='checkbox';box.checked=Boolean(get('automatico'));
        box.addEventListener('change',()=>set({'sisco:automatico':box.checked||undefined}));
        wrap.append(box,node('span','Executar automaticamente: quando uma ocorrência revisada chegar aqui, o SISCO gera o Word sozinho (nada é enviado).'));fragment.append(wrap);
      }
      fragment.append(flowField('Prazo',choose(Object.entries(st.deadlines),get('prazo')||'',(v)=>set({'sisco:prazo':v||undefined}))));
      const here=model.state.items.filter((item)=>item.workflow?.path?.at(-1)===el.id);
      if(here.length){const open=node('button',`Ver as ${here.length} ocorrências nesta situação`,'kpi-open');open.type='button';open.addEventListener('click',()=>openItemList(get('conclusao')||bo.name,here.map((item)=>item.id)));fragment.append(open);}
    } else if(type==='bpmn:EndEvent'||type==='bpmn:StartEvent'&&bo.$parent?.$type==='bpmn:SubProcess'){
      if(type==='bpmn:StartEvent'){title('Entrada do subprocesso','A ocorrência entra no subprocesso por aqui. Ligue-a ao primeiro elemento de dentro; cada subprocesso tem uma só entrada.');}
      else{
        title('Saída do subprocesso','Para onde a ocorrência segue depois deste subprocesso.');
        const targets=[];modeler.get('elementRegistry').forEach((other)=>{const b=other.businessObject;if(other.waypoints||other.labelTarget||other.id===el.id)return;
          if(b.$type==='bpmn:SubProcess')targets.push([other.id,`Subprocesso: ${b.name||other.id}`]);
          else if(b.$type==='bpmn:ExclusiveGateway'||TASK_TYPES.includes(b.$type)||(b.$type==='bpmn:EndEvent'&&!b.get('sisco:destino')&&b.get('sisco:fase')))targets.push([other.id,b.get('sisco:conclusao')||b.name||other.id]);});
        targets.sort((a,b)=>a[1].localeCompare(b[1],'pt-BR'));
        fragment.append(flowField('Segue para',choose(targets,get('destino'),(v)=>{const name=targets.find(([k])=>k===v)?.[1].replace(/^Subprocesso: /,'')||'';set({'sisco:destino':v||undefined,name});},'— escolha o destino —')));
        if(!get('destino'))fragment.append(node('p','Sem destino, este fim vira uma situação final: escolha a fase dele.','rpanel-sub'),flowField('Fase no quadro (se for um fim de verdade)',choose(Object.entries(st.phases),get('fase'),(v)=>set({'sisco:fase':v||undefined}),'— é uma saída —')));
      }
    } else if(type==='bpmn:SubProcess'){
      title('Subprocesso','Uma fase do fluxo, fechada no nível de cima. Use o ícone no canto dele (ou o botão abaixo) para abrir e editar o que tem dentro.');
      fragment.append(flowField('Nome',text(bo.name,(v)=>set({name:v}),60)),flowField('Descrição',text(get('descricao'),(v)=>set({'sisco:descricao':v||undefined}),80)));
      const inside=(bo.flowElements||[]).filter((child)=>child.$type==='bpmn:ExclusiveGateway'||TASK_TYPES.includes(child.$type));
      fragment.append(node('p',`${inside.length} ${inside.length===1?'elemento':'elementos'} dentro: ${inside.filter((c)=>c.$type==='bpmn:ExclusiveGateway').length} decisões e ${inside.length-inside.filter((c)=>c.$type==='bpmn:ExclusiveGateway').length} situação(ões).`,'rpanel-sub'));
      const root=modeler.get('canvas').findRoot(`${el.id}_plane`);
      if(root){const openButton=node('button','Abrir subprocesso','button primary');openButton.type='button';openButton.addEventListener('click',()=>modeler.get('canvas').setRootElement(root));const actions=node('div',undefined,'rpanel-actions');actions.append(openButton);fragment.append(actions);}
    } else if(type==='bpmn:SequenceFlow'){
      const source=el.source?.businessObject;
      title('Ligação',source?.$type==='bpmn:ExclusiveGateway'?'Saída de uma decisão: diga se é o caminho Sim ou Não.':'Leva a ocorrência ao próximo elemento.');
      if(source?.$type==='bpmn:ExclusiveGateway')fragment.append(flowField('Caminho',choose([['sim','Sim'],['nao','Não']],bo.get('sisco:ramo')||'',(v)=>set({'sisco:ramo':v||undefined,name:v==='sim'?'Sim':v==='nao'?'Não':undefined}),'— Sim ou Não? —')));
    } else if(type==='bpmn:StartEvent'){
      title('Início do fluxo','Uma ocorrência nova (Forms ou planilha) entra aqui. Ligue o início ao primeiro elemento do fluxo.');
    } else {
      title(bo.name||type.replace('bpmn:',''),'Elemento de documentação do BPMN (piscina, mensagem, anotação, dado…). Aparece no desenho, mas o motor do SISCO não o percorre.');
      if('name' in bo||type==='bpmn:Participant')fragment.append(flowField('Nome',text(bo.name,(v)=>set({name:v}),80)));
      if(type==='bpmn:TextAnnotation')fragment.append(flowField('Texto',text(bo.text,(v)=>set({text:v}),200)));
    }
    if(situation||type==='bpmn:ExclusiveGateway'){
      const incoming=(el.incoming||[]).map((flow)=>flow.source?.businessObject).filter(Boolean).map((b)=>b.get?.('sisco:conclusao')||b.name||b.id);
      fragment.append(node('p',incoming.length?`Chega aqui por: ${incoming.join(' · ')}`:'Nenhuma ligação chega aqui ainda.','rpanel-sub'));
    }
    const logic=node('details',undefined,'studio-json');
    const attrs=Object.fromEntries(Object.entries(bo.$attrs||{}).filter(([k])=>k.startsWith('sisco:')));
    ['pergunta','ramo','conclusao','fase','acao','tipoAcao','prazo','automatico','destino','descricao'].forEach((k)=>{const v=get(k);if(v!==undefined&&v!=='')attrs[`sisco:${k}`]=v;});
    logic.append(node('summary','Prévia da lógica (atributos BPMN)'),node('pre',JSON.stringify({id:el.id,tipo:type,nome:bo.name||'',...attrs},null,2)));fragment.append(logic);
    panel.replaceChildren(fragment);
  }

  // ---------- Decisões (DMN): classificação → tipo de fluxo → tratamento, no dmn-js (editor DMN do Camunda) ----------
  // As tabelas são montadas pelo servidor a partir do rascunho das regras e lidas de volta a cada edição.
  // Sem problemas, o resultado entra no mesmo rascunho do Mapa (validar, simular e publicar pela barra das regras).
  const dmnUI={modeler:null,loading:null,key:null,timer:0,dirty:false,importing:false,problems:[],error:'',view:'tipo_de_fluxo',hooked:new WeakSet()};
  const DMN_VENDOR='/vendor/dmn-js-18.0.3/';
  const DMN_PT={'Add Rule Above':'Linha acima','Add Rule Below':'Linha abaixo','Remove Rule':'Excluir linha','Copy Rule Above':'Copiar linha acima','Copy Rule Below':'Copiar linha abaixo',
    'Add Input Left':'Entrada à esquerda','Add Input Right':'Entrada à direita','Remove Input':'Excluir entrada','Add Output Left':'Saída à esquerda','Add Output Right':'Saída à direita','Remove Output':'Excluir saída',
    'Rule':'Linha','Input':'Entrada','Output':'Saída','Annotations':'Anotações','Hit Policy:':'Política:','Unique':'Única','Add Rule':'Nova linha','View DRD':'Ver o DRD'};
  const dmnTranslate={translate:['value',(template,replacements={})=>(DMN_PT[template]||template).replace(/{([^}]+)}/g,(_,key)=>replacements[key]??`{${key}}`)]};
  const dmnRulesKey=()=>JSON.stringify(canon({flows:rulesUI.draft?.flows,flow_types:rulesUI.draft?.flow_types}));
  function loadDmnJs(){
    if(window.DmnJS)return Promise.resolve();
    if(dmnUI.loading)return dmnUI.loading;
    ['diagram-js.css','dmn-js-shared.css','dmn-js-drd.css','dmn-js-decision-table.css','dmn-js-decision-table-controls.css','dmn-js-literal-expression.css','dmn-js-boxed-expression.css','dmn-js-boxed-expression-controls.css','dmn-embedded.css']
      .forEach((file)=>{const link=document.createElement('link');link.rel='stylesheet';link.href=DMN_VENDOR+file;document.head.append(link);});
    dmnUI.loading=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=DMN_VENDOR+'dmn-modeler.production.min.js';
      script.onload=()=>resolve();script.onerror=()=>{dmnUI.loading=null;reject(new Error('O editor DMN não carregou.'));};document.head.append(script);});
    return dmnUI.loading;
  }
  async function renderRulesDmn(){
    if(rulesUI.mode!=='dmn'||!rulesUI.draft)return;
    renderDmnPanel();
    if(dmnUI.importing||dmnUI.dirty)return;                       // não refaz a tabela no meio de uma edição
    const key=dmnRulesKey();
    if(dmnUI.modeler&&dmnUI.key===key)return;
    try{
      await loadDmnJs();
      const {dmn}=await request('/api/rules/dmn','POST',{flows:rulesUI.draft.flows,flow_types:rulesUI.draft.flow_types});
      if(!dmnUI.modeler){
        $("dmn-editor").replaceChildren();
        dmnUI.modeler=new window.DmnJS({container:'#dmn-editor',common:{additionalModules:[dmnTranslate]}});
        dmnUI.modeler.on('views.changed',({activeView})=>{hookDmnViewer();if(activeView&&!dmnUI.importing)dmnUI.view=activeView.type==='drd'?'drd':activeView.element.id;renderDmnViews();});
      }
      dmnUI.importing=true;
      try{await dmnUI.modeler.importXML(dmn);}finally{dmnUI.importing=false;}
      dmnUI.key=key;dmnUI.problems=[];dmnUI.error='';
      const views=dmnUI.modeler.getViews(),view=views.find((v)=>(v.type==='drd'?'drd':v.element.id)===dmnUI.view)||views.find((v)=>v.type==='decisionTable');
      if(view)await dmnUI.modeler.open(view);
      hookDmnViewer();renderDmnViews();
    }catch(error){dmnUI.error=error.message;}
    renderDmnPanel();
  }
  function hookDmnViewer(){
    const viewer=dmnUI.modeler?.getActiveViewer();if(!viewer||dmnUI.hooked.has(viewer))return;dmnUI.hooked.add(viewer);
    viewer.on('commandStack.changed',(event)=>{
      if(dmnUI.importing||event?.trigger==='clear')return;          // abrir uma tabela limpa o histórico: não é edição
      dmnUI.dirty=true;clearTimeout(dmnUI.timer);dmnUI.timer=setTimeout(readDmn,800);renderDmnPanel();});
  }
  async function readDmn(){
    try{
      const {xml}=await dmnUI.modeler.saveXML({format:true});
      const result=await request('/api/rules/dmn','POST',{dmn:xml});
      dmnUI.problems=result.problems||[];dmnUI.error='';
      if(!dmnUI.problems.length){
        rulesUI.draft.flows=result.flows;rulesUI.draft.flow_types=result.flow_types;
        dmnUI.key=dmnRulesKey();dmnUI.dirty=false;scheduleRulesSave();
      }
    }catch(error){dmnUI.error=error.message;}
    finally{dmnUI.dirty=false;renderDmnPanel();}
  }
  function renderDmnViews(){
    const modeler=dmnUI.modeler;if(!modeler)return;
    const active=modeler.getActiveView(),same=(a,b)=>a&&b&&a.type===b.type&&a.element?.id===b.element?.id;
    $("dmn-views").replaceChildren(...modeler.getViews().map((view)=>{
      const button=node('button',view.type==='drd'?'Mapa das decisões (DRD)':`Tabela: ${view.element.name||view.element.id}`,'cal-btn');button.type='button';
      button.setAttribute('aria-pressed',String(same(view,active)));button.addEventListener('click',()=>modeler.open(view));return button;}));
  }
  function renderDmnPanel(){
    const panel=$("dmn-panel"),d=rulesUI.draft;if(!panel||!d)return;
    const fragment=document.createDocumentFragment();
    fragment.append(node('h3','Tabelas de decisão'),node('p','A mesma regra do Mapa, em DMN: a classificação escolhe o tipo de fluxo, e o tipo diz como a ocorrência é tratada.','rpanel-sub'));
    if(dmnUI.error)fragment.append(node('p',`A tabela não pôde ser lida: ${dmnUI.error}`,'rpanel-sub danger-text'));
    if(dmnUI.dirty)fragment.append(node('p','Lendo a tabela…','rpanel-sub'));
    else if(dmnUI.problems.length){
      fragment.append(node('h4','Corrija para a mudança valer no rascunho'));
      const list=node('ul',undefined,'rpanel-list flow-problems');dmnUI.problems.forEach((text)=>list.append(node('li',text)));fragment.append(list);
    } else fragment.append(node('p',`${d.flows.length} classificações → ${d.flow_types.length} tipos de fluxo. Cada mudança válida entra no rascunho das regras: confira em Validar ou no Simulador e use Publicar regras na barra acima.`,'rpanel-sub'));
    // Quantas ocorrências cada tipo recebe com as regras do rascunho (a mesma conta do Mapa).
    const typeOf=(item)=>d.flows.find((f)=>normalized(f.classification)===normalized(item.values?.classification))?.flow||'';
    const totals={};model.state.items.forEach((item)=>{const name=typeOf(item)||'sem tipo de fluxo';totals[name]=(totals[name]||0)+1;});
    const table=node('ul',undefined,'rpanel-list');
    Object.entries(totals).sort((a,b)=>b[1]-a[1]).forEach(([name,n])=>table.append(node('li',`${name}: ${n} ${n===1?'ocorrência':'ocorrências'}`)));
    fragment.append(node('h4','Ocorrências por tipo, com estas regras'),table);
    const help=node('details',undefined,'studio-json'),tips=node('ul',undefined,'rpanel-list');
    help.append(node('summary','Como escrever nas células'));
    ['Textos entre aspas: "Manutenção". Várias classificações na mesma linha: "Manutenção", "Telecom".',
     '"Acompanha" e "Registro com a coordenação": true ou false.',
     '"E-mail vai para": "coordenacao" (coordenação da usina) ou "classificacao" (o e-mail da classificação, em Configurações).',
     'Linha nova: botão direito numa linha → Linha abaixo. Classificação sem linha fica sem tipo de fluxo.',
     'Política U (única): cada classificação só pode aparecer em uma linha.',
     'A coluna de anotações e o desenho do DRD não são guardados: as tabelas são refeitas a partir das regras.'].forEach((text)=>tips.append(node('li',text)));
    help.append(tips);fragment.append(help);
    panel.replaceChildren(fragment);
  }
  // Prévia da lógica do elemento escolhido: o trecho das regras que ele controla.
  function ruleSlice(id){
    const d=rulesUI.draft,meta=(key)=>({meta_dias:d.phase_targets[key]});
    if(id.startsWith('type:'))return d.flow_types.find((type)=>type.name===id.slice(5))||{};
    return {source:{gatilho:'forms_ou_planilha'},review:{etapa:'triagem',...meta('review')},classify:{classificacoes:d.flows},monitoring:{tipo:'monitoramento',limite_horas:d.sla_hours,...meta('monitoring')},
      sending:{etapa:'aguardando_envio',...meta('sending')},forms:{etapa:'retorno_da_manutencao',cobranca:{a_cada_dias:d.reminder_days,hora:d.followup_hour},...meta('forms')},
      documents:{etapa:'registro_word',prazo_dias:d.document_days,...meta('documents')},closed:{etapa:'concluida'},other:{ug_ocorrencias_ate_dias:d.ug_occurrence_days}}[id]||{};
  }
  // Arquivo .bpmn (BPMN 2.0 com diagrama), para abrir no Bizagi ou em outro modelador.
  function bpmnXml(model){
    const esc=(text)=>String(text??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    const {W,H,lanes,nodes,flows}=model,height=H*lanes.length,lines=[];
    const tag={'task-user':'userTask','task-send':'sendTask',gateway:'exclusiveGateway',start:'startEvent',timer:'intermediateCatchEvent',boundary:'boundaryEvent',end:'endEvent',terminate:'endEvent'};
    lines.push('<?xml version="1.0" encoding="UTF-8"?>',
      '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="sisco_regras" targetNamespace="http://sisco.local/regras" exporter="SISCO" exporterVersion="1.0">',
      '  <collaboration id="colaboracao"><participant id="piscina" name="Gestão de Ocorrências" processRef="processo"/></collaboration>',
      '  <process id="processo" name="Gestão de Ocorrências (regras do SISCO)" isExecutable="false">','    <laneSet id="raias">');
    for(const lane of lanes)lines.push(`      <lane id="${lane.id}" name="${esc(lane.name)}">${nodes.filter((n)=>n.lane===lane.id).map((n)=>`<flowNodeRef>${n.id}</flowNodeRef>`).join('')}</lane>`);
    lines.push('    </laneSet>');
    for(const n of nodes){
      const inc=flows.filter((f)=>f.to===n.id).map((f)=>`<incoming>${f.id}</incoming>`).join(''),out=flows.filter((f)=>f.from===n.id).map((f)=>`<outgoing>${f.id}</outgoing>`).join('');
      const attrs=n.type==='boundary'?` attachedToRef="${n.attachedTo}" cancelActivity="false"`:'';
      const def={start:`<messageEventDefinition id="${n.id}_def"/>`,timer:`<timerEventDefinition id="${n.id}_def"/>`,boundary:`<timerEventDefinition id="${n.id}_def"/>`,terminate:`<terminateEventDefinition id="${n.id}_def"/>`}[n.type]||'';
      lines.push(`    <${tag[n.type]} id="${n.id}" name="${esc(n.label)}"${attrs}>${inc}${out}${def}</${tag[n.type]}>`);
    }
    for(const f of flows)lines.push(`    <sequenceFlow id="${f.id}" name="${esc(f.label)}" sourceRef="${f.from}" targetRef="${f.to}"/>`);
    lines.push('  </process>','  <bpmndi:BPMNDiagram id="diagrama"><bpmndi:BPMNPlane id="plano" bpmnElement="colaboracao">',
      `    <bpmndi:BPMNShape id="piscina_di" bpmnElement="piscina" isHorizontal="true"><dc:Bounds x="0" y="0" width="${W}" height="${height}"/></bpmndi:BPMNShape>`);
    for(const lane of lanes)lines.push(`    <bpmndi:BPMNShape id="${lane.id}_di" bpmnElement="${lane.id}" isHorizontal="true"><dc:Bounds x="30" y="${lane.y}" width="${W-30}" height="${H}"/></bpmndi:BPMNShape>`);
    for(const n of nodes)lines.push(`    <bpmndi:BPMNShape id="${n.id}_di" bpmnElement="${n.id}"${n.type==='gateway'?' isMarkerVisible="false"':''}><dc:Bounds x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}"/></bpmndi:BPMNShape>`);
    for(const f of flows)lines.push(`    <bpmndi:BPMNEdge id="${f.id}_di" bpmnElement="${f.id}">${f.points.map(([x,y])=>`<di:waypoint x="${x}" y="${y}"/>`).join('')}</bpmndi:BPMNEdge>`);
    lines.push('  </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>','</definitions>');
    return lines.join('\n');
  }
  function downloadText(text,filename,type){
    const url=URL.createObjectURL(new Blob([text],{type})),a=node('a');
    a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
  }
  function renderRules(){
    if(!model.state||model.view!=='rules')return;
    // A leitura automática não pode apagar o que está sendo digitado no painel.
    syncRulesDraft();renderRulesCanvas();if(!$("rules-panel").contains(document.activeElement))renderRulesPanel();renderRulesStatus();renderRulesDiff();
  }

  // Visões salvas (estilo Linear): filtros do quadro guardados só neste navegador.
  const VIEWS_KEY='sisco.visoes';
  const readViews=()=>{try{const views=JSON.parse(localStorage.getItem(VIEWS_KEY)||'[]');return Array.isArray(views)?views:[];}catch(_){return [];}};
  const writeViews=(views)=>{try{localStorage.setItem(VIEWS_KEY,JSON.stringify(views));return true;}catch(_){toast('Este navegador não permitiu guardar a visão.');return false;}};
  const currentFilters=()=>({query:$("board-search").value,classification:$("board-classification").value,plant:$("board-plant").value,overdue:pressed("board-overdue"),pending:pressed("board-pending")});
  function renderSavedViews(selected=''){
    const views=readViews(),select=$("saved-views");
    select.replaceChildren(new Option('Todas as ocorrências',''),...views.map((view)=>new Option(view.name,view.name)));
    select.value=views.some((view)=>view.name===selected)?selected:'';
    $("delete-view").hidden=!select.value;
  }
  function applySavedView(name){
    const view=readViews().find((entry)=>entry.name===name)||{};
    $("board-search").value=view.query||'';
    for(const [id,value] of [["board-classification",view.classification],["board-plant",view.plant]]){
      const select=$(id);if(value&&![...select.options].some((option)=>option.value===value))select.append(new Option(value,value));select.value=value||'';
    }
    $("board-overdue").setAttribute('aria-pressed',String(Boolean(view.overdue)));$("board-pending").setAttribute('aria-pressed',String(Boolean(view.pending)));
    $("delete-view").hidden=!name;renderBoard();
  }

  // Busca rápida (Ctrl+K ou /), estilo Linear: ocorrências e comandos na mesma caixa.
  const COMMANDS=[
    {label:'Ir para as Fases',run:()=>{showView('board');if(model.view==='board')setBoardMode('board');}},
    {label:'Alternar tema claro/escuro',run:()=>toggleTheme()},
    {label:'Ir para a Lista',run:()=>showView('occurrences')},
    {label:'Ir para o Calendário',run:()=>{showView('board');if(model.view==='board')setBoardMode('calendar');}},
    {label:'Abrir Relatórios',run:()=>showView('monthly')},
    {label:'Abrir KPIs',run:()=>showView('kpis')},
    {label:'Abrir Usinas',run:()=>showView('plants')},
    {label:'Atualizar dados agora',run:()=>sync()},
    {label:'Abrir Configurações',run:()=>$("open-settings").click()}];
  let paletteResults=[],paletteIndex=0;
  function renderPalette(){
    const query=normalized($("palette-input").value).trim(),items=model.state?.items||[];
    let found=[];
    if(query){
      const exact=items.filter((item)=>String(item.legacy_id)===query||String(item.forms_id)===query);
      const loose=items.filter((item)=>!exact.includes(item)&&normalized([item.legacy_id,item.forms_id,plantName(item),item.values?.equipment,item.values?.classification,conclusion(item)].join(' ')).includes(query));
      found=[...exact,...loose.sort((a,b)=>chronological(b,a))].slice(0,8);
    }
    const commands=COMMANDS.filter((command)=>!query||normalized(command.label).includes(query));
    paletteResults=[...found.map((item)=>({item})),...commands.map((command)=>({command}))];
    paletteIndex=Math.min(paletteIndex,Math.max(0,paletteResults.length-1));
    const list=$("palette-list"),fragment=document.createDocumentFragment();
    paletteResults.forEach((result,index)=>{
      const li=node('li',undefined,`palette-option${index===paletteIndex?' active':''}`);
      li.setAttribute('role','option');li.setAttribute('aria-selected',String(index===paletteIndex));li.dataset.index=index;li.id=`palette-option-${index}`;
      if(result.item){
        const phase=PHASES[phaseIndex(result.item)],dot=node('span',undefined,'palette-dot');dot.style.background=phase.color;
        li.append(dot,node('strong',identity(result.item)),node('span',`${plantName(result.item)||'Usina não informada'} · ${result.item.values?.equipment||'—'}`),node('small',`${phase.name} · ${conclusion(result.item)}`));
      } else li.append(node('span','↳','palette-dot command'),node('strong',result.command.label));
      fragment.append(li);
    });
    if(!paletteResults.length)fragment.append(node('li','Nada encontrado. Tente o ID, a usina ou o equipamento.','palette-empty'));
    list.replaceChildren(fragment);
    $("palette-input").setAttribute('aria-activedescendant',paletteResults.length?`palette-option-${paletteIndex}`:'');
    list.querySelector('.active')?.scrollIntoView({block:'nearest'});
  }
  function openPalette(){
    if(model.saving||document.querySelector('dialog[open]'))return;
    $("palette-input").value='';paletteIndex=0;renderPalette();$("palette").showModal();$("palette-input").focus();
  }
  function choosePalette(index){
    const choice=paletteResults[index];if(!choice)return;
    $("palette").close();
    if(choice.item)openCard(choice.item.id);else choice.command.run();
  }
  function setBoardMode(mode){
    board.mode=mode==='calendar'?'calendar':'board';
    try{localStorage.setItem('sisco.gestao.modo',board.mode);}catch(_){/* Preferência só deste navegador. */}
    renderBoard();animateBoard();
  }
  function animateBoard(){
    if(!motionOk())return;
    if(board.mode==='calendar'){$("calendar").animate([{opacity:0,transform:'translateY(14px)'},{opacity:1,transform:'none'}],{duration:420,easing:'cubic-bezier(.2,.8,.2,1)'});return;}
    [...$("board").children].forEach((col,index)=>col.animate([{opacity:0,transform:'translateY(18px)'},{opacity:1,transform:'none'}],{duration:440,delay:index*60,easing:'cubic-bezier(.2,.8,.2,1)',fill:'backwards'}));
  }
  function renderCardPhases(){
    const item=current();if(!item)return;
    const at=phaseIndex(item),wf=item.workflow||{},list=node('ol',undefined,'kphases'),head=node('div',undefined,'kphases-head');
    PHASES.forEach((phase,index)=>{const step=node('li',phase.name,`kphase${index===at?' current':''}`);step.style.setProperty('--phase',phase.color);if(index===at)step.setAttribute('aria-current','step');list.append(step);});
    const next=PHASES[at].key==='closed'?'Nenhuma ação pendente':item.reviewed?wf.action||'Consultar ocorrência':'Revisar ocorrência';
    const since=wf.phase_since?`${phaseAge(item)}${isStuck(item)?` · parada (meta ${PHASES[at].sla} d)`:''}`:'';
    head.append(node('span','FASE ATUAL','eyebrow'),node('strong',PHASES[at].name),node('span',since,'kphases-since'),node('span',`Próxima ação: ${next}`,'kphases-action'));
    $("card-phases").style.setProperty('--phase',PHASES[at].color);
    $("card-phases").replaceChildren(head,list);
    if(board.openPhase!=null&&board.openPhase!==at){toast(`Card movido para “${PHASES[at].name}”.`);$("card-move-hint").hidden=true;}
    board.openPhase=at;
  }
  // O card abre o mesmo painel de detalhes da aba Ocorrências, levado para dentro da janela.
  function openCard(id,target=null){
    const dialog=$("card-dialog");
    if(model.saving||!itemById(id))return;
    if(String(model.selected)!==String(id)){selectItem(id);if(String(model.selected)!==String(id))return;}
    if(!dialog.open){$("card-slot").append($("detail-panel"));dialog.showModal();}
    board.openPhase=null;renderCardPhases();
    const item=current(),hint=$("card-move-hint");
    hint.hidden=target==null;
    if(target==null){$("detail-panel").querySelector('.detail-scroll').scrollTop=0;return;}
    hint.textContent=`Para levar este card a “${PHASES[target].name}”, conclua a etapa atual: ${item.reviewed?item.workflow?.action||'Consultar ocorrência':'Revisar ocorrência'}. A fase muda sozinha quando a regra for cumprida.`;
    requestAnimationFrame(()=>dialog.querySelector(item.reviewed?'.workflow-section':'.review-section')?.scrollIntoView({block:'center',behavior:motionOk()?'smooth':'auto'}));
  }
  function closeCard(){
    if(model.saving||!discardAllowed())return false;
    const id=model.selected;
    model.selected=null;model.renderedVersion=null;setDirty(false);$("detail-content").hidden=true;$("detail-empty").hidden=false;
    document.querySelector(".workbench").append($("detail-panel"));
    board.openPhase=null;$("card-move-hint").hidden=true;
    if($("card-dialog").open)$("card-dialog").close();
    renderList();renderBoard();renderMonthly();
    ($("board-view").querySelector(`[data-item-id="${id}"]`)||$("cal-grid").querySelector(`[data-day-more="${cal.returnDay}"]`)||$("monthly-body").querySelector(`button[data-open-item="${id}"]`))?.focus({preventScroll:true});
    cal.returnDay=null;
    return true;
  }

  function clearDetail() {
    model.selected = null; model.renderedVersion = null; setDirty(false);
    $("detail-content").hidden = true; $("detail-empty").hidden = false;
  }

  function resetFilters() {
    $("search").value = ""; $("status-filter").value = ""; $("classification-filter").value = ""; $("conclusion-filter").value = ""; $("plant-filter").value = ""; model.filter = "all";
    document.querySelectorAll(".filter-tab").forEach((entry) => { const active = entry.dataset.filter === "all"; entry.classList.toggle("active", active); entry.setAttribute("aria-pressed", String(active)); });
  }

  function openPlant(group) {
    if (model.saving) return;
    if (current() && plantKey(current()) !== group.key) {
      if (!discardAllowed()) return;
      clearDetail();
    }
    model.plantKey = group.key; model.plantLabel = group.name; resetFilters(); showView("plant");
  }

  function showView(view) {
    if (finishTransition) finishTransition();
    if (!["start","home","occurrences","plant","board","generation","rules","monthly","kpis","plants","import"].includes(view)) view="start";
    if (model.view!==view) {
      if ($("card-dialog").open && !closeCard()) return;
      if (!discardAllowed()) return;
      clearDetail();
    }
    model.view=view;
    if(view==='occurrences'){model.plantKey=null;resetFilters();}
    const occurrences=['occurrences','plant'].includes(view);
    document.body.classList.toggle('timeline-mode',occurrences||['home','board','monthly','kpis'].includes(view));
    document.body.classList.toggle('bi-mode',occurrences||['board','monthly','kpis'].includes(view));
    $("home-view").hidden=view!=='home';
    $("occurrences-view").hidden=!occurrences; $("board-view").hidden=view!=='board'; $("monthly-view").hidden=view!=='monthly'; $("kpis-view").hidden=view!=='kpis'; $("start-view").hidden=view!=='start'; $("generation-view").hidden=view!=='generation'; $("rules-view").hidden=view!=='rules'; $("plants-view").hidden=view!=='plants'; $("import-view").hidden=view!=='import';
    $("back-plants").hidden=view!=='plant';
    // Faixa superior comum a todas as abas, alinhada ao logo da barra lateral.
    const heroWords={home:['Acompanhamento','Geral'],occurrences:['Ocorrências','Totais'],plant:['Ocorrências',model.plantLabel],board:['Gestão de','Ocorrências'],generation:['Geração das','Usinas'],rules:['Regras do','Fluxo'],monthly:['Acompanhamento','Mensal'],kpis:['KPIs da','Gestão'],start:['Bem-vindo ao','SISCO'],plants:['Ocorrências por','Usina'],import:['Conferência da','Importação']}[view];
    $("hero-word-a").textContent=heroWords[0]; $("hero-word-b").textContent=heroWords[1];
    $("occurrences-hero").classList.toggle('hero-plant',view==='plant');
    $("occurrences-hero").classList.toggle('hero-with-count',occurrences);
    $("occurrences-eyebrow").textContent=view==='plant'?'OCORRÊNCIAS DA USINA':'CONTROLE OPERACIONAL';
    $("occurrences-subtitle").textContent='Revise, acompanhe a conclusão e execute as tratativas nesta mesma lista.';
    $("list-title").textContent='Histórico de ocorrências';
    const navView=view==='plant'?'plants':['occurrences','home'].includes(view)?'board':view;
    // Título no estilo do relatório do Power BI: texto branco e a palavra-chave em azul.
    const [title,accent,sub]={board:['Gestão de','Ocorrências','Fases do controle operacional'],occurrences:['Gestão de','Ocorrências','Lista do controle operacional'],plant:['Ocorrências de',model.plantLabel,'Usina'],generation:['Geração das','Usinas','Grupos geradores pelos relatórios diários'],rules:['Regras do','Fluxo','Rascunho, teste com as ocorrências reais e publicação'],monthly:['Relatório','Mensal','Acompanhamento do mês'],kpis:['KPIs da','Gestão','Metas, tendência e onde agir'],start:['Bem-vindo ao','SISCO','Escolha o ambiente em que deseja trabalhar'],plants:['Carteira de','Usinas','Situação de cada usina'],import:['Sincronização da','Planilha','Leitura do controle operacional'],home:['Painel','Power BI','Análise geral']}[view];
    $("page-title").replaceChildren(document.createTextNode(`${title} `),node('em',accent));$("pipe-sub").textContent=sub;
    syncPipeViews();
    document.querySelectorAll('.nav-item').forEach(entry=>{const active=entry.dataset.view===navView;entry.classList.toggle('active',active);if(active)entry.setAttribute('aria-current','page');else entry.removeAttribute('aria-current');});
    timelineReset=true;
    renderAnalytics();
    if(model.state)renderState();
    window.scrollTo({top:0,behavior:'instant'});
    $("page-title").focus({preventScroll:true});
    applyBar();
    if(view==='board'&&model.state)animateBoard();
    else if(motionOk()){
      const section={occurrences:'occurrences-view',plant:'occurrences-view',generation:'generation-view',rules:'rules-view',monthly:'monthly-view',kpis:'kpis-view',start:'start-view',plants:'plants-view',import:'import-view',home:'home-view'}[view];
      $(section)?.animate([{opacity:0,transform:'translateY(10px)'},{opacity:1,transform:'none'}],{duration:260,easing:'cubic-bezier(.2,.8,.2,1)'});
    }
  }

  // Painel Power BI: o navegador carrega o relatório direto da Microsoft; o SISCO só guarda o link.
  let analyticsEditing = false, analyticsSaving = false;
  function renderAnalytics() {
    const frame=$("analytics-frame"),url=asText(model.state?.analytics?.powerbi_url);
    $("analytics-panel").hidden=!model.state;
    const editing=!url||analyticsEditing;
    $("analytics-form").hidden=!editing; frame.hidden=editing;
    // Trocar o src recarrega o relatório; só fazer quando o link realmente mudar.
    if(url&&frame.getAttribute('src')!==url)frame.setAttribute('src',url);
    if(!url&&frame.hasAttribute('src'))frame.removeAttribute('src');
    $("analytics-open").hidden=!url; if(url)$("analytics-open").href=url;
    $("sidebar-powerbi").hidden=!url; if(url)$("sidebar-powerbi").href=url;
    $("analytics-edit").hidden=!url||editing; $("analytics-cancel").hidden=!url;
  }
  function editAnalytics(editing) {
    analyticsEditing=editing; message("analytics-message","");
    if(editing)$("analytics-url").value=asText(model.state?.analytics?.powerbi_url);
    renderAnalytics(); if(editing)$("analytics-url").focus();
  }
  // O Power BI oferece o código <iframe …> para copiar; aproveitar só o src.
  function powerBiLink(text) {
    const link=asText(text).trim(), snippet=/<iframe\b[^>]*\bsrc\s*=\s*["']([^"']+)["']/i.exec(link);
    return snippet?snippet[1].replace(/&amp;/g,"&"):link;
  }
  async function saveAnalytics(event) {
    event.preventDefault(); if(analyticsSaving||!model.state)return;
    analyticsSaving=true; message("analytics-message","");
    try {
      model.state.analytics=await request("/api/analytics","PATCH",{version:model.state.analytics?.version||0,powerbi_url:powerBiLink($("analytics-url").value)});
      analyticsEditing=false; renderAnalytics(); toast(model.state.analytics.powerbi_url?"Painel do Power BI conectado.":"Link do Power BI removido.");
    } catch (error) { message("analytics-message", error.message, error.status===409?"warning":"error"); }
    finally { analyticsSaving=false; }
  }

  // Barra azul arrastável: na posição padrão fica logo após o título; pode ir para a esquerda até encostar
  // na coluna do logo. A caixa do número se estica junto (cobrindo o título) e o painel esquerdo encolhe.
  const BAR_WIDTH = 24, BAR_KEY = "sisco.barra";
  let barX = (() => { try { const v = localStorage.getItem(BAR_KEY); return v === null ? null : Number(v); } catch (_) { return null; } })();
  const barEnabled = () => window.innerWidth >= 1100;
  const barDefault = () => $("page-title").getBoundingClientRect().right - $("occurrences-hero").getBoundingClientRect().left;
  function applyBar() {
    const hero = $("occurrences-hero"), slider = $("hero-slider"), split = document.querySelector(".bi-split");
    if (!hero.classList.contains("hero-with-count") || $("page-title").offsetParent === null) return;
    const def = barDefault(), boxWidth = barEnabled() ? 194 : 140;
    const x = barEnabled() && Number.isFinite(barX) && barX !== null ? Math.max(0, Math.min(def, barX)) : def;
    slider.style.left = `${x}px`; slider.style.width = `${def - x + BAR_WIDTH + boxWidth}px`;
    split.style.setProperty("--bi-left", `${x}px`);
    const handle = $("bar-handle");
    handle.setAttribute("aria-valuemin", "0"); handle.setAttribute("aria-valuemax", String(Math.round(def))); handle.setAttribute("aria-valuenow", String(Math.round(x)));
  }
  function setBar(x) {
    barX = x; applyBar();
    try { if (x === null) localStorage.removeItem(BAR_KEY); else localStorage.setItem(BAR_KEY, String(Math.round(x))); } catch (_) {}
  }
  function startBarDrag(event) {
    if (!barEnabled() || event.button > 0) return;
    event.preventDefault();
    const heroLeft = $("occurrences-hero").getBoundingClientRect().left, grab = event.clientX - heroLeft - parseFloat($("hero-slider").style.left || "0");
    document.body.classList.add("dragging-bar");
    const move = (ev) => { barX = ev.clientX - heroLeft - grab; applyBar(); };
    const stop = () => { window.removeEventListener("pointermove", move); document.body.classList.remove("dragging-bar"); setBar(Math.max(0, Math.min(barDefault(), barX))); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }
  function toggleBar() { if (barEnabled()) setBar(parseFloat($("hero-slider").style.left || "0") > 4 ? 0 : null); }
  ["bar-handle", "bar-lower"].forEach((id) => { $(id).addEventListener("pointerdown", startBarDrag); $(id).addEventListener("dblclick", toggleBar); });
  $("bar-handle").addEventListener("keydown", (event) => {
    if (!barEnabled()) return;
    const current = parseFloat($("hero-slider").style.left || "0"), steps = { ArrowLeft: -24, ArrowRight: 24 };
    if (event.key in steps) setBar(current + steps[event.key]);
    else if (event.key === "Home") setBar(0);
    else if (event.key === "End") setBar(null);
    else return;
    event.preventDefault();
  });
  window.addEventListener("resize", applyBar);
  if (document.fonts) document.fonts.ready.then(applyBar);

  // Links de entrada, ex. botão do Power BI: /?view=ocorrencias&filtro=todas (opcional &item=<id local>).
  const routeViews={inicio:'start',capa:'start',painel:'home',ocorrencias:'occurrences',gestao:'board',geracao:'generation',regras:'rules',mensal:'monthly',kpis:'kpis',indicadores:'kpis',usinas:'plants',importacoes:'import'};
  const routeFilters={todas:'all','a-revisar':'pending','com-tratativas':'action',revisadas:'reviewed','fonte-alterada':'changed'};
  function readRoute(search) {
    const params=new URLSearchParams(search),item=Number(params.get('item'))||null;
    const view=routeViews[normalized(params.get('view'))]||(item||params.has('filtro')?'occurrences':null);
    const mode={calendario:'calendar',quadro:'board'}[normalized(params.get('modo'))]||null;
    const month=/^\d{4}-\d{2}$/.test(params.get('mes')||'')?params.get('mes'):null;
    return view?{view,filter:routeFilters[normalized(params.get('filtro'))]||null,item,mode,month}:null;
  }
  function applyRoute(route) {
    showView(route.view);
    if(model.view!==route.view)return;
    if(route.view==='monthly'){if(route.month){monthly.month=route.month;renderMonthly();}return;}
    if(route.view==='board'){if(route.mode)setBoardMode(route.mode);if(route.item&&itemById(route.item))openCard(route.item);return;}
    if(route.filter)document.querySelector(`.filter-tab[data-filter="${route.filter}"]`)?.click();
    if(route.item&&model.state?.items.some(i=>i.id===route.item)){selectItem(route.item);requestAnimationFrame(()=>$("items-body").querySelector(`tr[data-item-id="${route.item}"]`)?.scrollIntoView({block:'nearest'}));}
  }
  // Transição Início → Ocorrências. O relatório é de outra origem e não pode ser animado; cópias dos
  // elementos são posicionadas sobre o painel e levadas até o cabeçalho. O visualizador "Publicar na Web"
  // encaixa a página no quadro, centralizada, acima da barra de navegação (footer). A página começa na
  // faixa cinza (o papel de parede azul ao lado não é publicado). Posições em pixels da página, medidas
  // no Power BI Desktop e conferidas no relatório publicado; ajustar aqui se o layout do relatório mudar.
  const BI_PAGE = { width: 1423, height: 800, footer: 60,
    logo: { x: 13, y: 160, w: 142, h: 83 }, card: { x: 185, y: 155, w: 208, h: 101 },
    title: { cx: 290, cy: 176, size: 17 }, number: { cx: 289, cy: 219, size: 34 } };
  let pendingTransition = null, stateWaiters = [], finishTransition = null;
  const motionOk = () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches && typeof Element.prototype.animate === "function";
  const whenState = () => model.state ? Promise.resolve() : new Promise((resolve) => stateWaiters.push(resolve));
  function stateArrived() { const waiting = stateWaiters; stateWaiters = []; waiting.forEach((resolve) => resolve()); }
  // Na aba aberta pelo link (ex.: Power BI online), o Power BI não está nesta tela: não há de onde
  // "levar" os elementos, e uma imitação dele nunca coincide com a página vista antes. A entrada é o
  // próprio SISCO se montando: a faixa fica parada (é a moldura alinhada ao logo) e o conteúdo entra
  // em sequência curta. ~0,9 s no total.
  function playEntrance() {
    const ease = "cubic-bezier(.2,.8,.2,1)";
    const enter = (el, from, delay, duration = 520) => el && el.animate(
      [{ opacity: 0, transform: from }, { opacity: 1, transform: "none" }],
      { duration, delay, easing: ease, fill: "backwards" });
    enter($("brand-logo"), "scale(.9)", 0, 480);
    enter($("hero-word-a"), "translateX(-28px)", 90);
    enter($("hero-word-b"), "translateX(28px)", 170);
    enter($("hero-slider"), "translateY(10px)", 240, 460);
    enter(document.querySelector(".hero-art"), "translateX(24px)", 240, 600);
    enter(document.querySelector(".bi-left"), "none", 300, 450);
    enter(document.querySelector(".bi-right"), "translateY(14px)", 360, 560);
  }
  const center = (r) => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
  function flyText(layer, text, cls, size, color, at) {
    const el = node("span", text, `flight ${cls}`);
    Object.assign(el.style, { fontSize: `${size}px`, fontWeight: "700", color, lineHeight: "1", left: "0px", top: "0px" });
    layer.append(el);
    const r = el.getBoundingClientRect();
    el.style.left = `${at.x - r.width / 2}px`; el.style.top = `${at.y - r.height / 2}px`;
    return el;
  }
  function transitionTo(route, entrance = false) {
    const frame = $("analytics-frame"), home = $("home-view"), occ = $("occurrences-view");
    if (finishTransition) return;
    // Clique antes da primeira carga dos dados: esperar a carga em vez de pular a animação.
    if (!model.state) { whenState().then(() => transitionTo(route, entrance)); return; }
    if (!motionOk() || route.view !== "occurrences" || route.item) { if (!entrance) applyRoute(route); return; }
    const fromPanel = !entrance && model.view === 'home' && !frame.hidden;
    const fr = fromPanel ? frame.getBoundingClientRect() : {left:0,top:0,width:window.innerWidth,height:window.innerHeight};
    const availH = Math.max(1, fr.height - (fromPanel ? BI_PAGE.footer : 0));
    const k = Math.min(fr.width / BI_PAGE.width, availH / BI_PAGE.height);
    const ox = fr.left + (fr.width - BI_PAGE.width * k) / 2, oy = fr.top + (availH - BI_PAGE.height * k) / 2;
    const at = (x, y) => ({ x: ox + x * k, y: oy + y * k });
    const layer = node("div", undefined, "flight-layer"); layer.setAttribute("aria-hidden", "true");
    document.body.append(layer);
    let backdrop = null;
    if (!fromPanel) {
      backdrop = node('div', undefined, 'flight-backdrop');
      backdrop.style.setProperty('--source-top', `${oy + 135 * k}px`);
      backdrop.style.setProperty('--source-side', `${ox + 175 * k}px`);
      layer.append(backdrop);
    }
    // Ponto de partida: as cópias cobrem o cartão, o título, o número e o logo do relatório.
    const card = node("div", undefined, "flight-card"), c = BI_PAGE.card;
    Object.assign(card.style, { left: `${ox + c.x * k}px`, top: `${oy + c.y * k}px`, width: `${c.w * k}px`, height: `${c.h * k}px`, borderRadius: `${22 * k}px` });
    const logo = node("div", undefined, "flight-logo"), l = BI_PAGE.logo, logoImg = node("img");
    logoImg.src = "/marca/logo-branco.svg"; logoImg.alt = ""; logo.append(logoImg);
    const logoFrom = { left: ox + l.x * k, top: oy + l.y * k, width: l.w * k, height: l.h * k };
    Object.assign(logo.style, { left: `${logoFrom.left}px`, top: `${logoFrom.top}px`, width: `${logoFrom.width}px`, height: `${logoFrom.height}px`,
      background: "#d3d3d3", border: "1.5px solid rgba(255,255,255,.9)", borderRadius: `${20 * k}px`, boxShadow: "0 4px 10px rgba(0,0,0,.35)" });
    logoImg.style.width = `${logoFrom.width * .8}px`;
    layer.append(card, logo);
    const titleSize = BI_PAGE.title.size * k, t0 = at(BI_PAGE.title.cx, BI_PAGE.title.cy);
    const whole = flyText(layer, "Ocorrências Totais", "", titleSize, "#1b1b1b", t0), wr = whole.getBoundingClientRect();
    const wordA = flyText(layer, "Ocorrências", "", titleSize, "#1b1b1b", { x: 0, y: 0 });
    const wordB = flyText(layer, "Totais", "", titleSize, "#1b1b1b", { x: 0, y: 0 }), br = wordB.getBoundingClientRect();
    wordA.style.left = `${wr.left}px`; wordA.style.top = `${wr.top}px`;
    wordB.style.left = `${wr.right - br.width}px`; wordB.style.top = `${wr.top}px`;
    whole.remove();
    const number = flyText(layer, $("hero-count").textContent, "", BI_PAGE.number.size * k, "#111", at(BI_PAGE.number.cx, BI_PAGE.number.cy));
    // O Início fica parado onde está enquanto desaparece; a lista entra por baixo.
    const hr = home.getBoundingClientRect();
    if (!entrance) applyRoute(route);
    if (model.view !== "occurrences") { layer.remove(); return; }
    if (fromPanel) {
      Object.assign(home.style, { left: `${hr.left}px`, top: `${hr.top}px`, width: `${hr.width}px`, height: `${hr.height}px` });
      home.classList.add("leaving"); home.hidden = false;
    }
    const targets = [$("brand-logo"), $("hero-word-a"), $("hero-word-b"), $("hero-count")];
    targets.forEach((el) => el.classList.add("landing"));
    occ.style.opacity = "0";
    const ease = "cubic-bezier(.4,0,.2,1)", duration = 1450, animations = [];
    const animate = (el, frames, options = {}) => {
      const animation = el.animate(frames, {duration, easing:ease, fill:'both', ...options});
      animations.push(animation); return animation;
    };
    // Curva que sai lateralmente e sobe até o destino, como as setas da referência.
    const curve = (start, end, p, bow) => {
      const q = 1-p, dx = end.x-start.x;
      return {x:q*q*q*start.x+3*q*q*p*(start.x+dx*.55)+3*q*p*p*end.x+p*p*p*end.x,
        y:q*q*q*start.y+3*q*q*p*(start.y+bow)+3*q*p*p*(end.y+Math.max(25,Math.abs(end.y-start.y)*.45))+p*p*p*end.y};
    };
    const fly = (el, target, toColor, fade = false, bow = 35) => {
      const s = el.getBoundingClientRect(), range = document.createRange();
      range.selectNodeContents(target);
      const t = range.getBoundingClientRect(), style = getComputedStyle(target);
      const scale = parseFloat(style.fontSize) / parseFloat(el.style.fontSize);
      const frames = Array.from({length:25}, (_, i) => {
        const p=i/24, at=curve(center(s),center(t),p,bow), zoom=1+(scale-1)*p;
        return {offset:p, transform:`translate(${at.x-s.width*zoom/2-s.left}px,${at.y-s.height*zoom/2-s.top}px) scale(${zoom})`};
      });
      Object.assign(frames[0], {color:el.style.color,opacity:1,fontWeight:'700',letterSpacing:'0px'});
      Object.assign(frames[24], {color:toColor,opacity:fade?0:1,fontWeight:style.fontWeight,letterSpacing:`${(parseFloat(style.letterSpacing)||0)/scale}px`});
      animate(el, frames, {delay:120});
    };
    // Com a barra recolhida para a esquerda, a caixa do número cobre o título: as palavras somem no caminho.
    const titleCovered = parseFloat($("hero-slider").style.left || "0") < barDefault() - 1;
    fly(wordA, $("hero-word-a"), "#ffffff", titleCovered);
    fly(wordB, $("hero-word-b"), getComputedStyle($("hero-word-b")).color, titleCovered);
    fly(number, $("hero-count"), getComputedStyle($("hero-count")).color, false, 85);
    const lt = $("brand-logo").getBoundingClientRect(), ls = getComputedStyle($("brand-logo"));
    animate(logo, [
      { left: `${logoFrom.left}px`, top: `${logoFrom.top}px`, width: `${logoFrom.width}px`, height: `${logoFrom.height}px`, backgroundColor: "#d3d3d3", borderRadius: `${20 * k}px` },
      { left: `${lt.left}px`, top: `${lt.top}px`, width: `${lt.width}px`, height: `${lt.height}px`, backgroundColor: ls.backgroundColor, borderRadius: ls.borderTopLeftRadius }],
      {delay:80});
    animate(logoImg, [{ width: `${logoFrom.width * .8}px` }, { width: `${$("brand-logo").querySelector("img").getBoundingClientRect().width}px` }], {delay:80});
    animate(card, [{opacity:1}, {opacity:0,transform:'scale(.94)'}], {duration:480,delay:160});
    if (fromPanel) animate(home, [{opacity:1}, {opacity:0}], {duration:750,delay:100});
    if (backdrop) animate(backdrop, [{opacity:1}, {opacity:0}], {duration:1050,delay:150});
    animate(occ, [{opacity:0,transform:'translateY(18px)'}, {opacity:1,transform:'none'}], {duration:800,delay:650});
    let cleaned = false, watchdog;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true; clearTimeout(watchdog); finishTransition = null;
      window.removeEventListener('resize', cleanup);
      animations.forEach(a=>a.cancel());
      targets.forEach((el) => el.classList.remove("landing"));
      layer.remove();
      home.classList.remove("leaving"); home.removeAttribute("style");
      home.hidden = model.view !== "home";
      occ.style.opacity = "";
    };
    finishTransition = cleanup;
    window.addEventListener('resize', cleanup, {once:true});
    watchdog = setTimeout(cleanup, duration + 1000);
    Promise.allSettled(animations.map(a=>a.finished)).then(cleanup);
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !pendingTransition) return;
    const route = pendingTransition; pendingTransition = null;
    requestAnimationFrame(() => transitionTo(route));
  });

  // O Power BI sempre abre links em aba nova. Se o clique veio do painel dentro de uma aba do SISCO
  // (que acabou de ficar oculta), essa aba assume a navegação e a aba nova se fecha.
  const channel='BroadcastChannel' in window?new BroadcastChannel('sisco-local'):null;
  let hiddenAt=document.hidden?Date.now():0;
  document.addEventListener('visibilitychange',()=>{if(document.hidden)hiddenAt=Date.now();});
  if(channel)channel.addEventListener('message',({data})=>{
    if(data?.type!=='open'||!data.route||model.dirty||settingsDirty||model.saving)return;
    // Só assume se o clique veio do painel DESTA aba: o quadro do Power BI está com o foco (o clique o
    // focou) E a aba está visível (aba nova aberta em segundo plano) ou acabou de ficar oculta porque a
    // aba nova abriu na frente. O foco continua no quadro depois de trocar de aba; sem a checagem de
    // visibilidade, uma aba do SISCO esquecida "roubava" o link vindo do Power BI online e a aba nova
    // se fechava.
    const panelFocused=model.view==='home'&&document.activeElement===$("analytics-frame");
    const fromPanel=panelFocused&&!document.hidden;
    const justHidden=panelFocused&&document.hidden&&Date.now()-hiddenAt<=4000;
    if(!fromPanel&&!justHidden)return;
    channel.postMessage({type:'claimed',id:data.id});
    // Oculta: a transição roda quando a aba voltar a aparecer. Visível (aba nova em segundo plano): roda já.
    if(document.hidden)pendingTransition=data.route;else transitionTo(data.route);
  });
  function handOff(route) {
    if(!channel)return;
    const id=Math.random().toString(36).slice(2);
    const onReply=({data})=>{if(data?.type==='claimed'&&data.id===id){channel.removeEventListener('message',onReply);window.close();}};
    channel.addEventListener('message',onReply);
    channel.postMessage({type:'open',id,route});
    setTimeout(()=>channel.removeEventListener('message',onReply),3000);
  }

  function chronological(a,b) {
    const av=a.values||{},bv=b.values||{};
    return asText(av.start_date).localeCompare(asText(bv.start_date)) || asText(av.start_time).localeCompare(asText(bv.start_time)) ||
      asText(a.legacy_id||a.forms_id).localeCompare(asText(b.legacy_id||b.forms_id),'pt-BR',{numeric:true}) || a.id-b.id;
  }

  function renderRecordNavigation() {
    const index=visibleIds.indexOf(Number(model.selected));
    $("previous-record").disabled=model.saving||index<=0;
    $("next-record").disabled=model.saving||index<0||index>=visibleIds.length-1;
    $("record-position").textContent=index>=0?`${index+1} de ${visibleIds.length}`:'';
  }

  function stepRecord(direction) {
    const index=visibleIds.indexOf(Number(model.selected)),target=visibleIds[index+direction];
    if(index<0||!target||model.saving)return;
    selectItem(target);
    if(Number(model.selected)===target)requestAnimationFrame(()=>$("items-body").querySelector(`tr[data-item-id="${target}"]`)?.scrollIntoView({block:'nearest'}));
  }

  function maintenanceTypeVisibility() {
    const form=$("edit-form"),applies=normalized(form.elements.classification.value)==='manutencao';
    $("maintenance-type-field").hidden=!applies;
    form.elements.maintenance_type.disabled=!applies||model.saving;
    if(!applies)form.elements.maintenance_type.value='';
  }

  function closeSettings(event) {
    if(settingsDirty&&!window.confirm('Descartar as alterações de configuração não salvas?')){event?.preventDefault();return;}
    if(settingsDirty)renderSettings();
    $("settings-dialog").close();
  }

  function message(target, text, tone = "error") {
    const el = $(target); el.textContent = text || ""; el.className = `message ${tone}`; el.hidden = !text;
  }
  function toast(text) { clearTimeout(toastTimer); $("toast").textContent = text; $("toast").hidden = false; toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500); }

  async function request(path, method = "GET", body) {
    const options = { method, credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } };
    if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.headers["X-SISCO-Token"] = model.state?.csrf_token || ""; options.body = JSON.stringify(body); }
    let response;
    try { response = await fetch(path, options); } catch (_) { throw new Error("Não foi possível conectar ao SISCO local. Confira se o programa continua em execução."); }
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("O programa retornou uma resposta inesperada. Tente atualizar novamente."); }
    if (!response.ok) { const error = new Error(asText(data.error || data.message || "Não foi possível concluir a operação.")); error.status = response.status; throw error; }
    return data;
  }

  function setDirty(value) {
    model.dirty = value;
    renderRecordNavigation();
    $("dirty-state").textContent = value ? "Alterações não salvas" : "Dados salvos";
    $("dirty-state").classList.toggle("dirty", value);
    $("save-button").disabled = !value || model.saving;
    $("discard-button").disabled = !value || model.saving;
    $("save-next").disabled = !value || model.saving;
    const blocked = !current()?.reviewed || value || model.saving;
    ["create-word","upload-word","prepare-email","open-return"].forEach(id=>$(id).disabled=blocked);
  }

  function updateOptions(id, values, placeholder) {
    const select = $(id), before = select.value;
    const sorted = [...new Set(values.filter(Boolean).map(asText))].sort((a, b) => a.localeCompare(b, "pt-BR"));
    select.replaceChildren(new Option(placeholder, ""), ...sorted.map((value) => new Option(value, value)));
    select.value = sorted.includes(before) ? before : "";
  }

  function renderState() {
    const state = model.state, items = scopedItems(), sync = state.sync || {};
    renderAnalytics();
    $("count-total").textContent = items.length.toLocaleString("pt-BR");
    // Mesmo critério do Power BI (linhas do CONTROLE), sem contar duas vezes um ID repetido.
    $("hero-count").textContent = new Set(items.filter((item) => item.legacy_id).map((item) => String(item.legacy_id))).size.toLocaleString("pt-BR");
    $("count-pending").textContent = items.filter((item) => !item.reviewed).length.toLocaleString("pt-BR");
    $("count-reviewed").textContent = items.filter((item) => item.reviewed).length.toLocaleString("pt-BR");
    $("count-changed").textContent = items.filter((item) => item.source_changed).length.toLocaleString("pt-BR");
    updateOptions("status-filter", items.map(status), "Todas as situações");
    updateOptions("classification-filter", items.map((item) => item.values?.classification), "Todas as classificações");
    updateOptions("conclusion-filter", items.map(conclusion), "Todas as conclusões");
    updateOptions("plant-filter", items.map(plantName), "Todas as usinas");
    $("origin-options").replaceChildren(...[...new Set(items.map((item) => item.values?.origin).filter(Boolean))].sort().map((value) => new Option(value)));
    $("sync-text").textContent = sync.error ? `Última leitura com aviso · ${dateTime(sync.last_attempt)}` : sync.last_success ? `Última leitura concluída em ${dateTime(sync.last_success)}` : "Aguardando a primeira importação";
    $("sync-indicator").className = `sync-indicator ${sync.error ? "error" : sync.last_success ? "success" : ""}`;
    $("sync-auto").textContent = `Leitura automática a cada ${state.settings?.sync_seconds || 60} segundos`;
    // Metas de cada fase vêm das regras publicadas.
    PHASES.forEach((phase) => { if (phase.key !== "closed") phase.sla = state.configuration?.phase_targets?.[phase.key] ?? phase.sla; });
    renderList(); renderImport(); renderPlants(); renderBoard(); renderMonthly(); renderKpis(); renderStart(); renderShell(); renderGeneration(); renderRules();
    if (!settingsLoaded) renderSettings();
    const selected = current();
    if (selected && !model.dirty && !model.saving && model.renderedVersion !== selected.version) renderDetail(selected);
    if (selected && model.dirty && model.renderedVersion !== selected.version) message("editor-message", "Há uma versão mais recente deste registro. Suas alterações continuam aqui. Ao salvar, o sistema verificará se houve conflito.", "warning");
    const seconds = Math.max(10, Number(state.settings?.sync_seconds) || 60);
    if (!model.poll) model.poll = setInterval(() => { if (!document.hidden) loadState(); }, seconds * 1000);
  }

  function renderList() {
    if (!model.state) return;
    const query = normalized($("search").value).trim(), classification = $("classification-filter").value, situation = $("status-filter").value;
    const scope = scopedItems();
    const items = scope.filter((item) => {
      if (model.filter === "action" && !categoryMatches(item,"pending")) return false;
      if ($("conclusion-filter").value && conclusion(item) !== $("conclusion-filter").value) return false;
      if ($("plant-filter").value && plantName(item) !== $("plant-filter").value) return false;
      if (model.filter === "pending" && item.reviewed) return false;
      if (model.filter === "reviewed" && !item.reviewed) return false;
      if (model.filter === "changed" && !item.source_changed) return false;
      if (classification && asText(item.values?.classification) !== classification) return false;
      if (situation && status(item) !== situation) return false;
      return !query || normalized([item.legacy_id, item.forms_id, item.source_key, item.raw_report, ...Object.values(item.values || {})].join(" ")).includes(query);
    });
    items.sort(chronological);
    visibleIds=items.map(item=>item.id);
    const scroller=$("occurrence-scroll"),oldTop=scroller.scrollTop;
    const atBottom=scroller.scrollHeight-scroller.clientHeight-oldTop<30;
    const top=scroller.getBoundingClientRect().top;
    const anchor=[...$("items-body").children].find(row=>row.getBoundingClientRect().bottom>top+35);
    const anchorId=anchor?.dataset.itemId,anchorOffset=anchor?anchor.getBoundingClientRect().top-top:0;
    $("filtered-count").textContent=`${items.length} de ${scope.length}`;
    const fragment=document.createDocumentFragment();
    for(const item of items){
      const v=item.values||{},wf=item.workflow||{},row=node('tr');
      row.dataset.itemId=item.id;row.classList.toggle('selected',String(item.id)===String(model.selected));
      const start=node('td',undefined,'start-cell');start.append(node('strong',v.start_date?shortDate(v.start_date):'Sem data'),node('small',v.start_time||'Hora não informada'));
      const entry=node('td',undefined,'occurrence-cell'),button=node('button',undefined,'row-button');button.type='button';button.setAttribute('aria-label',`Abrir ${identity(item)}, ${v.plant||'usina não informada'}`);
      const heading=node('span',undefined,'row-heading');heading.append(node('span',identity(item),'row-title'),makeBadge(item.reviewed?'Revisado':'A revisar',item.reviewed?'teal':'neutral'));
      button.append(heading,node('span',`${v.plant||'Usina não informada'} · ${v.equipment||v.description||'Equipamento não informado'}`,'row-subtitle'));
      entry.append(button);
      if(item.forms_id&&!item.control_row)entry.append(node('span','Sem vínculo confirmado','changed-marker'));
      if(item.source_changed)entry.append(node('span','Fonte alterada','changed-marker'));
      const classification=node('td',undefined,'classification-cell');classification.append(node('span',v.classification||'Não classificada'));
      if(normalized(v.classification)==='manutencao')classification.append(node('small',v.maintenance_type||'Tipo não informado'));
      const generation=node('td',undefined,'generation-cell');generation.append(makeBadge(v.generation_impact==='Sim'?'Afeta':v.generation_impact==='Não'?'Não afeta':'Não informado',v.generation_impact==='Sim'?'amber':v.generation_impact==='Não'?'teal':'neutral'));
      const result=node('td',undefined,'conclusion-cell');result.append(node('strong',conclusion(item),'conclusion-text'));
      const due=wf.category==='documents'?(wf.document_deadline||v.document_deadline_date):wf.due_at;
      if(due)result.append(node('small',`${wf.overdue?'Prazo vencido':'Prazo'}: ${shortDate(due)}`,wf.overdue?'due-label overdue':'due-label'));
      const actionCell=node('td',undefined,'action-cell'),action=node('button',item.reviewed?wf.action||'Consultar':'Revisar','row-action');action.type='button';
      action.addEventListener('click',event=>{event.stopPropagation();selectItem(item.id);});actionCell.append(action);
      row.append(start,entry,classification,generation,result,actionCell);row.addEventListener('click',()=>selectItem(item.id));fragment.append(row);
    }
    $("items-body").replaceChildren(fragment);
    const empty=$("list-empty");empty.hidden=items.length>0;
    if(!items.length)empty.replaceChildren(node('h3',model.state.items.length?'Nenhuma ocorrência encontrada':'Nenhuma ocorrência importada'),node('p','Ajuste os filtros ou use Atualizar dados.'));
    if(!$("occurrences-view").hidden){
      const goToLatest=timelineReset||atBottom;timelineReset=false;
      if(scrollFrame)cancelAnimationFrame(scrollFrame);
      scrollFrame=requestAnimationFrame(()=>{
        if($("occurrences-view").hidden)return;
        if(goToLatest){scroller.scrollTop=scroller.scrollHeight;return;}
        const saved=anchorId?$("items-body").querySelector(`tr[data-item-id="${anchorId}"]`):null;
        scroller.scrollTop=saved?scroller.scrollTop+saved.getBoundingClientRect().top-scroller.getBoundingClientRect().top-anchorOffset:oldTop;
      });
    }
    renderRecordNavigation();
  }

  function discardAllowed() { return !model.dirty || window.confirm("Você tem alterações não salvas. Deseja descartá-las e continuar?"); }
  function selectItem(id) {
    if (model.saving || String(id) === String(model.selected)) return;
    if (!discardAllowed()) return;
    model.selected = id;
    renderDetail(current()); renderList();
    $("detail-panel").scrollTop=0;
    loadHistory(id);
    document.querySelector(".workbench").classList.add("has-selection");
    if (window.innerWidth <= 1020) $("detail-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function explain(value) {
    if (typeof value !== "object" || value == null) return asText(value);
    return asText(value.message || value.description || value.detail || value.reason || value.warning || value.error) || Object.entries(value).map(([key, val]) => `${key}: ${asText(val)}`).join(" · ");
  }

  function appendRawFields(fragment, value, prefix = "") {
    for (const [key, entry] of Object.entries(value)) {
      const label = prefix ? `${prefix} · ${key}` : key;
      if (entry !== null && typeof entry === "object" && !Array.isArray(entry)) appendRawFields(fragment, entry, label);
      else fragment.append(node("dt", label), node("dd", Array.isArray(entry) ? entry.map(explain).join("\n") : asText(entry) || "—"));
    }
  }

  // Linha do tempo do card: data de cada etapa e quantos dias entre uma e outra.
  function renderTimeline(item) {
    const list = $("timeline"), timeline = item?.timeline, select = $("wait-reason");
    if (!timeline) { list.replaceChildren(); return; }
    let previous = null;
    list.replaceChildren(...timeline.steps.map((step) => {
      const row = node("li", undefined, `timeline-step${step.at ? " done" : ""}`), when = new Date(step.at);
      row.append(node("span", step.label, "timeline-label"), node("span", step.at ? `${dateTime(step.at)}${step.estimated ? " (pode ser de um reenvio)" : ""}` : "ainda não", "timeline-when"));
      if (step.at && previous) { const days = (when - previous) / 86400000; if (days >= 0) row.append(node("span", days < 1 ? `+${Math.round(days * 24)} h` : `+${kpiNumber(Math.round(days * 10) / 10)} d`, "timeline-gap")); }
      if (step.at) previous = when;
      return row;
    }));
    const reasons = model.state?.management?.wait_reasons || [];
    select.replaceChildren(new Option("Não informado", ""), ...reasons.map((reason) => new Option(reason, reason)));
    select.value = item.wait?.reason || ""; $("wait-note").value = item.wait?.note || ""; $("wait-save").dataset.itemId = item.id; $("wait-status").textContent = "";
  }
  function renderDetail(item) {
    if (!item) return;
    renderTimeline(model.state?.items.find((entry) => entry.id === item.id) || item);
    const values = item.values || {}, form = $("edit-form");
    model.renderedVersion = item.version;
    $("detail-empty").hidden = true; $("detail-content").hidden = false;
    $("detail-identity").textContent = `${identity(item)}${item.forms_id ? ` · ID Forms ${item.forms_id}` : ""}`;
    $("detail-title").textContent = values.plant || "Usina não informada";
    $("detail-badges").replaceChildren(makeBadge(status(item), statusTone(status(item))), makeBadge(item.reviewed ? "Revisado neste ambiente" : "Aguardando revisão", item.reviewed ? "teal" : "neutral"));
    $("raw-report").textContent = item.raw_report || "O relato original não está disponível nesta fonte. Consulte os dados de origem abaixo.";
    const raw = item.raw_fields || {}, rawFragment = document.createDocumentFragment();
    appendRawFields(rawFragment, raw);
    if (!Object.keys(raw).length) rawFragment.append(node("dd", "Não há campos adicionais disponíveis."));
    $("raw-fields").replaceChildren(rawFragment);
    $("source-reference").textContent = `${item.source_key || "Registro importado"}${item.source_row ? ` · Linha ${item.source_row} na origem` : ""}`;
    $("source-change-notice").hidden = !item.source_changed;
    const warnings = Array.isArray(item.warnings) ? item.warnings : item.warnings ? [item.warnings] : [];
    $("record-warnings").hidden = !warnings.length;
    const warningList = node("ul"); warnings.forEach((warning) => warningList.append(node("li", explain(warning)))); $("record-warnings").replaceChildren(warningList);
    const classSelect = $("edit-classification");
    for (const flow of model.state.configuration?.flows || []) if (![...classSelect.options].some(o=>o.value===flow.classification)) classSelect.append(new Option(flow.classification,flow.classification));
    classSelect.querySelectorAll("option[data-extra]").forEach((option) => option.remove());
    if (values.classification && !Array.from(classSelect.options).some((option) => option.value === asText(values.classification))) { const option = new Option(values.classification, values.classification); option.dataset.extra = "true"; classSelect.append(option); }
    const maintenance=form.elements.maintenance_status;
    if (values.maintenance_status && ![...maintenance.options].some(o=>o.value===values.maintenance_status)) maintenance.append(new Option(values.maintenance_status,values.maintenance_status));
    for (const field of [...editable, "plant", "coordination"]) form.elements[field].value = asText(values[field]);
    $("reviewed").checked = Boolean(item.reviewed);
    $("reconfirm-button").hidden = !(item.reviewed && item.source_changed);
    $("review-date").textContent = item.reviewed && item.reviewed_at ? `Revisão registrada em ${dateTime(item.reviewed_at)}.` : "";
    $("source-review").textContent = `Revisão informada na planilha: ${asText(item.source_review) || "não informada"}. Depois da importação, a revisão é feita no SISCO.`;
    message("editor-message", ""); $("reload-record").hidden = true;
    setDirty(false);
    renderWorkflow(item);
    $("preparation-notes").textContent=(item.preparation?.notes||[]).join(' ');
    $("preparation-notes").hidden=!item.preparation?.notes?.length;
    renderReturns(item);
    maintenanceTypeVisibility();
    renderRecordNavigation();
  }

  function returnCard(ret) {
    const card=node('div',undefined,'return-card');
    card.append(makeBadge(returnLabels[ret.status]||ret.status,['error','conflict'].includes(ret.status)?'amber':ret.status==='applied'?'teal':'neutral'));
    card.append(node('strong',ret.response_id ? `Resposta ${ret.response_id} · Ocorrência ${ret.occurrence_id||'sem ID válido'}` : 'Registro manual'),node('p',ret.message),node('small',dateTime(ret.submitted_at||ret.received_at)));
    const v=ret.values||{};
    const parts=[v.maintenance_status,v.forecast_date?`Previsão: ${shortDate(v.forecast_date)}`:'',v.end_date?`Fim: ${shortDate(v.end_date)}`:'',v.end_time||''].filter(Boolean);
    card.append(node('p',parts.join(' · '),'muted'));
    if(Object.keys(ret.raw_fields||{}).length){const details=node('details',undefined,'source-details'),fields=node('dl');details.append(node('summary','Conferir resposta original'));appendRawFields(fields,ret.raw_fields);details.append(fields);card.append(details);}
    return card;
  }

  function renderReturns(item) {
    const entries=item.maintenance_returns||(model.state.maintenance_returns||[]).filter(r=>r.item_id===item.id);
    $("maintenance-history").replaceChildren(...(entries.length?entries.map(returnCard):[node('p','Nenhum retorno registrado para esta ocorrência.','muted')]));
    $("open-return").disabled=!item.reviewed||model.dirty||model.saving;
  }

  function returnFields() {
    const closed=$("return-form").elements.maintenance_status.value==='Finalizada';
    $("return-forecast-label").hidden=closed;
    document.querySelectorAll('.return-end').forEach(el=>el.hidden=!closed);
  }

  function openReturn() {
    const item=current();if(!item||!item.reviewed||model.dirty||model.saving)return;
    returnRequest={itemId:item.id,version:item.version,request_id:crypto.randomUUID()};
    const form=$("return-form");form.reset();
    form.elements.maintenance_status.value=(item.values.maintenance_status==='Finalizada'||item.values.end_date||item.values.end_time)?'Finalizada':'Em Aberto';
    for(const field of ['forecast_date','end_date','end_time'])form.elements[field].value=item.values[field]||'';
    $("return-identity").textContent=`${identity(item)} · ${item.values.plant||'Usina não informada'}`;
    message('return-message','');returnFields();$("return-dialog").showModal();
  }

  async function saveReturn(event) {
    event.preventDefault();if(!returnRequest||model.saving)return;
    const requestInfo={...returnRequest},form=$("return-form"),status=form.elements.maintenance_status.value;
    const values={maintenance_status:status,forecast_date:status==='Em Aberto'?form.elements.forecast_date.value:'',end_date:status==='Finalizada'?form.elements.end_date.value:'',end_time:status==='Finalizada'?form.elements.end_time.value:''};
    model.saving=true;model.requestEpoch++;$("save-return").disabled=true;$("close-return").disabled=true;
    try {
      const item=await request(`/api/items/${requestInfo.itemId}/maintenance-return`,'POST',{version:requestInfo.version,request_id:requestInfo.request_id,values});
      $("return-dialog").close();replaceItem(item);toast('Retorno registrado. Consulte a conclusão e o histórico da ocorrência.');
    } catch(e) {message('return-message',e.message);}
    finally {model.saving=false;$("save-return").disabled=false;$("close-return").disabled=false;setDirty(model.dirty);if(current())renderReturns(current());await loadState();}
  }

  function renderWorkflow(item) {
    const wf=item.workflow||{},v=item.values||{};
    $("detail-conclusion").textContent=conclusion(item);
    $("conclusion-reference").textContent=wf.basis==='excel' ? 'Conclusão informada na planilha.' : 'Conclusão calculada pelo SISCO.';
    const dates=document.createDocumentFragment();
    for (const [label,value] of [["Previsão",v.forecast_date],["Próxima cobrança de status",v.forms_due_at],["Prazo do registro",wf.document_deadline||v.document_deadline_date],["Próxima cobrança documental",v.document_next_date],["Retorno do registro",v.document_return_date]]) {
      if (value) {const el=node('div');el.append(node('span',label),node('strong',shortDate(value)));dates.append(el);}
    }
    $("workflow-dates").replaceChildren(dates);
    $("action-permission").textContent=item.reviewed ? `Próxima ação: ${wf.action||'Consultar ocorrência'}.` : 'Revise e salve a ocorrência abaixo para liberar as ações.';
    const docs=document.createDocumentFragment();
    for(const doc of item.documents||[]) {const link=node('a',`↓ ${doc.filename}`);link.href=`/api/documents/${encodeURIComponent(doc.id)}`;link.setAttribute('download',doc.filename);const entry=node('div');entry.append(link,node('small',`${doc.kind} · ${dateTime(doc.created_at)}`));docs.append(entry);}
    $("document-list").replaceChildren(docs);
    $("action-history").replaceChildren(...(item.deliveries||[]).map(d=>node('p',`${dateTime(d.created_at)} · ${d.kind} · ${d.status}${d.error ? ' — '+d.error : ''}`)));
  }

  async function loadHistory(id) {try {const item=await request(`/api/items/${id}`);if(String(model.selected)!==String(id))return;const frag=document.createDocumentFragment();for(const h of item.history||[])frag.append(node('p',`${dateTime(h.at)} · ${h.action}`));for(const d of item.deliveries||[])frag.append(node('p',`${dateTime(d.created_at)} · ${d.status}${d.error?' · '+d.error:''}`));$("action-history").replaceChildren(frag);if(item.version===current()?.version)renderReturns(item);}catch(_) {/* Preserve existing detail when history is temporarily unavailable. */}}

  function replaceItem(item) { const i=model.state.items.findIndex(entry=>entry.id===item.id);if(i>=0)model.state.items[i]=item;renderDetail(item);renderState();loadHistory(item.id); }

  async function createWord(file) {
    const item=current();if(!item||model.saving)return;
    if(model.dirty||!item.reviewed){message('action-message','Salve a revisão antes de continuar.','warning');return;}
    model.saving=true;setDirty(model.dirty);message('action-message',file?'Anexando documento…':'Gerando Word…','success');
    try{const body={version:item.version};if(file){if(file.size>8000000)throw new Error('Use um DOCX de até 8 MB.');body.content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});}const result=await request(`/api/items/${item.id}/word`,'POST',body);replaceItem(result.item);message('action-message',file?'Versão revisada anexada. Confira a prévia antes de enviar.':'Word criado. Baixe o documento abaixo para completar o preenchimento.','success');}catch(e){message('action-message',e.message);}finally{model.saving=false;setDirty(model.dirty);$("upload-word").value='';}
  }

  function openEmail() {
    if(model.dirty||!current()?.reviewed)return;
    const kind=current().workflow?.action_kind;
    $("email-kind").value=['request','reminder','document','document_reminder'].includes(kind)?kind:current().workflow?.category==='documents'?(current().values.document_sent?.toLowerCase().includes('enviado')?'document_reminder':'document'):'request';
    emailPreview=null;$("email-preview").hidden=true;$("send-test").disabled=true;message('email-message','');$("email-dialog").showModal();refreshPreview();
  }

  async function refreshPreview(){
    const item=current();if(!item||model.saving)return;const epoch=++previewEpoch;emailPreview=null;$("send-test").disabled=true;$("email-preview").hidden=true;
    try{const p=await request(`/api/items/${item.id}/preview`,'POST',{version:item.version,kind:$("email-kind").value});if(epoch!==previewEpoch)return;emailPreview=p;$("email-to").textContent=p.to;$("email-subject").textContent=p.subject;$("email-attachment").textContent=p.attachment||'Nenhum';$("email-body").textContent=p.body;$("email-routing").textContent=`Para: ${p.original_routing.to||'Não configurado'}\nCC: ${p.original_routing.cc||'Nenhum'}\nCCO: ${p.original_routing.bcc||'Nenhum'}`;$("email-preview").hidden=false;$("send-test").disabled=false;message('email-message','');}catch(e){if(epoch===previewEpoch)message('email-message',e.message);}
  }

  async function sendTest(){
    if(!emailPreview||model.saving)return;const p=emailPreview;model.saving=true;$("send-test").disabled=true;$("refresh-preview").disabled=true;$("email-kind").disabled=true;message('email-message','Submetendo ao Outlook… Aguarde o resultado.','success');
    try{const item=await request(`/api/items/${p.item_id}/send`,'POST',{version:p.version,kind:p.kind,preview_token:p.preview_token});replaceItem(item);emailPreview=null;message('email-message','Mensagem de teste submetida ao Outlook. Isso não confirma a entrega na caixa do destinatário.','success');}catch(e){emailPreview=null;message('email-message',e.message);try{replaceItem(await request(`/api/items/${p.item_id}`));}catch(_) {/* Polling will refresh the history when the service returns. */}}finally{model.saving=false;$("refresh-preview").disabled=false;$("email-kind").disabled=false;setDirty(model.dirty);}
  }

  // Configurações: tipos de fluxo definem o comportamento; cada classificação escolhe um tipo.
  const recipientLabels={coordenacao:'Coordenação da usina',classificacao:'Só o e-mail da classificação'};
  const cell=(el)=>{const td=node('td');td.append(el);return td;};
  function removeButton(row,label){const b=node('button','Remover','button secondary settings-remove');b.type='button';b.setAttribute('aria-label',label);b.addEventListener('click',()=>{row.remove();settingsDirty=true;syncFlowOptions();});return b;}
  function flowTypeRow(type){
    const tr=node('tr');
    const name=node('input');name.value=type.name;name.maxLength=40;name.required=true;name.dataset.key='name';name.dataset.prev=type.name;name.setAttribute('aria-label','Nome do tipo de fluxo');
    // Renomear o tipo leva junto as classificações que o usam.
    name.addEventListener('input',()=>{for(const s of $("settings-flows").querySelectorAll('[data-key="flow"]'))if(s.dataset.value===name.dataset.prev)s.dataset.value=name.value.trim();name.dataset.prev=name.value.trim();syncFlowOptions();});
    const follow=node('input');follow.type='checkbox';follow.checked=!!type.followup;follow.dataset.key='followup';follow.setAttribute('aria-label',`Acompanhamento de status ${type.name}`);
    const coord=node('input');coord.type='checkbox';coord.checked=!!type.coordinator;coord.dataset.key='coordinator';coord.setAttribute('aria-label',`Registro Word ${type.name}`);
    const recipient=node('select');for(const [value,label] of Object.entries(recipientLabels))recipient.append(new Option(label,value));recipient.value=type.recipient||'coordenacao';recipient.dataset.key='recipient';recipient.setAttribute('aria-label',`Para quem vai o e-mail ${type.name}`);
    tr.append(cell(name),cell(follow),cell(coord),cell(recipient),cell(removeButton(tr,`Remover tipo ${type.name}`)));
    return tr;
  }
  const flowTypesInForm=()=>[...$("settings-flow-types").children].map(row=>({name:row.querySelector('[data-key="name"]').value.trim(),followup:row.querySelector('[data-key="followup"]').checked,coordinator:row.querySelector('[data-key="coordinator"]').checked,recipient:row.querySelector('[data-key="recipient"]').value}));
  const flowSummary=(type)=>!type?'Tipo de fluxo não encontrado: escolha outro antes de salvar.':[type.followup?'Com acompanhamento':'Sem acompanhamento',type.coordinator?'com registro Word':'sem registro Word',type.recipient==='classificacao'?'e-mail só para a classificação':'e-mail para a coordenação'].join(' · ');
  function classificationRow(flow){
    const tr=node('tr');
    const name=node('input');name.value=flow.classification;name.maxLength=100;name.required=true;name.dataset.key='classification';name.dataset.prev=flow.classification;name.setAttribute('aria-label','Classificação');
    // Renomear a classificação leva junto o bloco de e-mails dela.
    name.addEventListener('change',()=>{const box=[...$("settings-class-routes").children].find(b=>normalized(b.dataset.name)===normalized(name.dataset.prev));const exists=[...$("settings-class-routes").children].some(b=>normalized(b.dataset.name)===normalized(name.value));if(box&&!exists&&name.value.trim()){box.dataset.name=name.value.trim();box.querySelector('legend').textContent=name.value.trim();}name.dataset.prev=name.value.trim();syncFlowOptions();});
    const select=node('select');select.dataset.key='flow';select.dataset.value=flow.flow;select.setAttribute('aria-label',`Tipo de fluxo ${flow.classification}`);
    select.addEventListener('change',()=>{select.dataset.value=select.value;syncFlowOptions();});
    tr.append(cell(name),cell(select),node('td','','muted flow-summary'),cell(removeButton(tr,`Remover classificação ${flow.classification}`)));
    return tr;
  }
  function classRouteBox(route){
    const box=node('fieldset',undefined,'route-row');box.dataset.name=route.classification;box.append(node('legend',route.classification));
    for(const key of ['to','cc','bcc']){const label=node('label',key==='to'?'Para':key==='cc'?'CC':'CCO');const input=node('input');input.value=route[key]||'';input.dataset.key=key;input.maxLength=2000;input.setAttribute('aria-label',`${key==='to'?'Para':key==='cc'?'CC':'CCO'} ${route.classification}`);label.append(input);box.append(label);}
    box.append(node('small','Este tipo de fluxo envia para esta classificação, sem a coordenação: informe o Para.','route-hint'));
    return box;
  }
  function syncFlowOptions(){
    const types=flowTypesInForm().filter(t=>t.name),holder=$("settings-class-routes");
    const boxes=new Map([...holder.children].map(b=>[normalized(b.dataset.name),b]));
    for(const row of $("settings-flows").children){
      const select=row.querySelector('[data-key="flow"]'),current=select.dataset.value;
      const match=types.find(t=>normalized(t.name)===normalized(current));
      select.replaceChildren(...types.map(t=>new Option(t.name,t.name)));
      if(match)select.value=match.name;else if(current){select.append(new Option(`${current} (removido)`,current));select.value=current;}
      row.querySelector('.flow-summary').textContent=flowSummary(match);
      const name=row.querySelector('[data-key="classification"]').value.trim();
      if(name&&!boxes.has(normalized(name))){const box=classRouteBox({classification:name});holder.append(box);boxes.set(normalized(name),box);}
      boxes.get(normalized(name))?.classList.toggle('needs-to',match?.recipient==='classificacao');
    }
  }
  function routeRows(id,rows,nameKey){const frag=document.createDocumentFragment();for(const route of rows){const box=node('fieldset',undefined,'route-row');box.append(node('legend',route[nameKey]));box.dataset.name=route[nameKey];for(const key of ['to','cc','bcc'].filter(k=>k in route)){const label=node('label',key==='to'?'Para':key==='cc'?'CC':'CCO');const input=node('input');input.value=route[key];input.dataset.key=key;input.maxLength=2000;label.append(input);box.append(label);}frag.append(box);}$(id).replaceChildren(frag);}
  function renderSettings(){const c=model.state.configuration;if(!c)return;settingsVersion=c.version;settingsLoaded=true;settingsDirty=false;$("settings-flow-types").replaceChildren(...(c.flow_types||[]).map(flowTypeRow));$("settings-flows").replaceChildren(...c.flows.map(classificationRow));routeRows('settings-routes',c.routes,'coordination');$("settings-class-routes").replaceChildren(...c.class_routes.map(classRouteBox));syncFlowOptions();$("settings-document-days").value=c.document_days;$("settings-reminder-days").value=c.reminder_days;$("settings-signature").value=c.signature;}
  async function saveSettings(event){event.preventDefault();const flows=[...$("settings-flows").children].map(row=>({classification:row.querySelector('[data-key="classification"]').value.trim(),flow:row.querySelector('[data-key="flow"]').value}));const routes=(id,key)=>[...$(id).children].map(row=>({[key]:row.dataset.name,...Object.fromEntries([...row.querySelectorAll('[data-key]')].map(el=>[el.dataset.key,el.value.trim()]))}));try{const c=await request('/api/configuration','PATCH',{version:settingsVersion,flow_types:flowTypesInForm(),flows,routes:routes('settings-routes','coordination'),class_routes:routes('settings-class-routes','classification'),signature:$("settings-signature").value,document_days:Number($("settings-document-days").value),reminder_days:Number($("settings-reminder-days").value)});model.state.configuration=c;renderSettings();message('settings-message','Configuração salva somente no novo controle.','success');await loadState();}catch(e){message('settings-message',e.message);}}

  function renderImport() {
    const returns=model.state.maintenance_returns||[];
    $("return-count").textContent=returns.length;
    $("return-summary").replaceChildren(...Object.entries(returnLabels).map(([key,label])=>[label,returns.filter(r=>r.status===key).length]).filter(([,count])=>count).map(([label,count])=>makeBadge(`${label}: ${count}`)));
    const attention=returns.filter(r=>['error','conflict','pending'].includes(r.status));
    $("return-issues").replaceChildren(...(attention.length?attention.map(ret=>{const card=returnCard(ret);if(ret.item_id){const link=node('button','Abrir ocorrência','button secondary');link.type='button';link.addEventListener('click',()=>{if(!discardAllowed())return;showView('occurrences');selectItem(ret.item_id);});card.append(link);}return card;}):[node('p','Nenhum retorno aguardando conferência.','muted')]));
    const sync = model.state.sync || {};
    $("import-path").textContent = sync.source_path || "Fonte ainda não configurada";
    $("import-saved-at").textContent = dateTime(sync.source_saved_at);
    $("import-success").textContent = dateTime(sync.last_success);
    $("import-attempt").textContent = dateTime(sync.last_attempt);
    $("test-email").textContent = model.state.settings?.test_email || "—";
    message("import-error", sync.error ? explain(sync.error) : "");
    const summary = $("import-summary"); summary.replaceChildren();
    const labels = { added: "Registros novos", absent: "Ausentes nesta leitura", read: "Registros lidos", imported: "Registros novos", inserted: "Registros novos", created: "Registros novos", new: "Registros novos", updated: "Registros atualizados", unchanged: "Sem mudanças", skipped: "Não importados", total: "Registros lidos", total_rows: "Linhas lidas", total_items: "Registros na base", source_rows: "Linhas na origem", errors: "Erros de leitura", warnings: "Avisos", changed: "Registros com mudança", forms_rows: "Registros do Forms", control_rows: "Registros do controle", issues: "Pontos de atenção" };
    Object.assign(labels, {maintenance_responses: "Respostas de manutenção na fonte", records: "Cadastros importáveis", linked_records: "Vínculos Forms e controle confirmados", forms_only: "Respostas sem vínculo confirmado", legacy_records: "Legados sem ID Forms", issue_count: "Pontos de atenção", records_with_warnings: "Registros com avisos"});
    if (sync.summary && typeof sync.summary === "object") for (const [key, value] of Object.entries(sync.summary)) { const row = node("div", undefined, "summary-row"); row.append(node("span", labels[key] || key.replaceAll("_", " ")), node("strong", asText(value))); summary.append(row); }
    else summary.append(node("p", asText(sync.summary) || "Aguardando a primeira leitura.", "muted"));
    const issues = Array.isArray(sync.issues) ? sync.issues : sync.issues ? [sync.issues] : [];
    $("issue-count").textContent = issues.length;
    const fragment = document.createDocumentFragment();
    for (const issue of issues) {
      const row = node("div", undefined, "issue-row");
      if (typeof issue === "object" && issue !== null && (issue.row || issue.source_row)) row.append(node("span", `Linha ${issue.row || issue.source_row}`, "issue-label"));
      const body = node("div"); body.append(node("p", explain(issue)));
      if (issue && typeof issue === "object") {
        const evidence = [...(issue.rows || []), ...(issue.control_rows || []), ...(issue.forms_rows || [])];
        if (issue.raw_fields) evidence.push(issue);
        if (evidence.length) {
          const details = node("details", undefined, "source-details"); details.append(node("summary", "Conferir registros envolvidos"));
          for (const entry of evidence) {
            details.append(node("h4", `${entry.source_sheet || "Origem"} · linha ${entry.source_row || "—"}`));
            const fields = node("dl"); appendRawFields(fields, entry.raw_fields || {}); details.append(fields);
          }
          body.append(details);
        }
      }
      row.append(body); fragment.append(row);
    }
    if (!issues.length) fragment.append(node("p", sync.last_success ? "Nenhum ponto de atenção registrado na última leitura." : "Os resultados aparecerão após a primeira leitura.", "issues-empty"));
    $("import-issues").replaceChildren(fragment);
  }

  async function loadState() {
    if (model.loading || model.syncing || model.saving) return;
    model.loading = true;
    const epoch = model.requestEpoch;
    try { const data = await request("/api/state"); if (epoch !== model.requestEpoch) return; model.state = data; model.state.items ||= []; renderState(); message("global-message", ""); stateArrived(); }
    catch (error) { message("global-message", error.message); if (!model.state) { $("list-empty").replaceChildren(node("h3", "O controle local não respondeu"), node("p", "Confira se o programa está em execução e tente Atualizar dados.")); $("sync-text").textContent = "Conexão indisponível"; } }
    finally { model.loading = false; }
  }

  async function sync() {
    if (model.syncing || model.saving) return;
    model.syncing = true;
    model.requestEpoch += 1;
    const buttons = document.querySelectorAll(".sync-button");
    buttons.forEach((button) => { button.disabled = true; button.classList.add("loading"); button.querySelector("span").textContent = "Consultando…"; });
    try {
      if (!model.state?.csrf_token) { model.state = await request("/api/state"); model.state.items ||= []; }
      const data = await request("/api/sync", "POST", {}); model.state = data; model.state.items ||= []; renderState(); stateArrived();
      if (data.sync?.error) message("global-message", explain(data.sync.error)); else { message("global-message", ""); toast("Leitura concluída. Os dados do novo controle foram atualizados."); }
    } catch (error) { message("global-message", error.message, error.status === 409 ? "warning" : "error"); }
    finally { model.syncing = false; buttons.forEach((button) => { button.disabled = false; button.classList.remove("loading"); button.querySelector("span").textContent = "Atualizar dados"; }); }
  }

  async function save(event, advance = false) {
    event.preventDefault(); if (!model.dirty || model.saving || !current()) return;
    const form = $("edit-form"), item = current(), values = {};
    const visibleIds=[...$("items-body").querySelectorAll('tr[data-item-id]')].map(el=>Number(el.dataset.itemId));
    const nextId=visibleIds[visibleIds.indexOf(item.id)+1];
    editable.forEach((field) => { values[field] = form.elements[field].value; });
    const payload = { version: model.renderedVersion, values, reviewed: $("reviewed").checked };
    model.saving = true; model.requestEpoch += 1; setDirty(true); $("save-button").textContent = "Salvando…";
    for (const field of form.elements) field.disabled = true;
    try {
      const saved = await request(`/api/items/${encodeURIComponent(item.id)}`, "PATCH", payload);
      const index = model.state.items.findIndex((entry) => String(entry.id) === String(saved.id));
      if (index < 0) throw new Error("A resposta não identificou o registro salvo. Atualize a lista para conferir.");
      model.state.items[index] = saved;
      renderDetail(saved); renderState(); toast(saved.reviewed ? "Alterações salvas. Ocorrência revisada neste ambiente." : "Alterações salvas na base de testes.");
      if (advance) {model.selected=null;model.renderedVersion=null;if(nextId){model.selected=nextId;renderDetail(current());renderList();loadHistory(nextId);}else clearDetail();}
    } catch (error) { message("editor-message", error.status === 409 ? `${error.message} Suas edições foram mantidas. Recarregue este registro para conferir a versão mais recente antes de editar novamente.` : error.message); $("reload-record").hidden = error.status !== 409; }
    finally { model.saving = false; for (const field of form.elements) field.disabled = false; $("save-button").textContent = "Salvar alterações"; setDirty(model.dirty); maintenanceTypeVisibility(); }
  }

  document.querySelectorAll(".nav-item[data-view]").forEach((button) => button.addEventListener("click", () => { if (!model.saving) showView(button.dataset.view); }));
  document.querySelectorAll("#pipe-views [data-pipe-mode]").forEach((button) => button.addEventListener("click", () => {
    if (model.saving) return;
    const mode = button.dataset.pipeMode;
    if (mode === "list") { showView("occurrences"); return; }
    if (model.view !== "board") showView("board");
    if (model.view === "board") setBoardMode(mode);
  }));
  $("back-plants").addEventListener("click", () => { if (!model.saving) showView("plants"); });
  $("board").addEventListener("click", (event) => {
    if (event.target.closest("[data-more]")) { board.closedLimit += CLOSED_PAGE; renderBoard(); return; }
    const card = event.target.closest(".kcard"); if (card) openCard(Number(card.dataset.itemId));
  });
  $("board-search").addEventListener("input", renderBoard);
  $("board-sort").value = boardSort;
  $("board-sort").addEventListener("change", (event) => { boardSort = event.target.value; try { localStorage.setItem("sisco.fases.ordem", boardSort); } catch (_) { /* só nesta sessão */ } renderBoard(); });
  ["board-classification", "board-plant"].forEach((id) => $(id).addEventListener("change", renderBoard));
  const filterMenu = document.querySelector('.board-filter-menu');
  document.addEventListener('click', (event) => {
    if (filterMenu.open && !filterMenu.contains(event.target)) filterMenu.open = false;
  });
  filterMenu.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && filterMenu.open) {
      filterMenu.open = false;
      filterMenu.querySelector('summary').focus();
    }
  });
  ["board-overdue", "board-pending"].forEach((id) => $(id).addEventListener("click", () => { $(id).setAttribute("aria-pressed", String(!pressed(id))); renderBoard(); }));
  $("board").addEventListener("dragstart", (event) => {
    const card = event.target.closest(".kcard"), item = card && itemById(card.dataset.itemId);
    if (!item || model.saving) { event.preventDefault(); return; }
    board.dragId = item.id; event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", String(item.id));
    card.classList.add("dragging"); document.body.classList.add("board-dragging");
    const from = phaseIndex(item); [...$("board").children].forEach((col, index) => col.classList.toggle("drop-allowed", index > from));
  });
  $("board").addEventListener("dragend", () => { board.dragId = null; document.body.classList.remove("board-dragging"); $("board").querySelectorAll(".dragging,.drop-ok,.drop-allowed").forEach((el) => el.classList.remove("dragging", "drop-ok", "drop-allowed")); });
  $("board").addEventListener("dragover", (event) => {
    const col = event.target.closest(".kcol"); if (board.dragId == null || !col?.classList.contains("drop-allowed")) return;
    event.preventDefault(); event.dataTransfer.dropEffect = "move";
    $("board").querySelectorAll(".drop-ok").forEach((el) => { if (el !== col) el.classList.remove("drop-ok"); }); col.classList.add("drop-ok");
  });
  $("board").addEventListener("dragleave", (event) => { const col = event.target.closest(".kcol"); if (col && !col.contains(event.relatedTarget)) col.classList.remove("drop-ok"); });
  $("board").addEventListener("drop", (event) => {
    const col = event.target.closest(".kcol"); if (board.dragId == null || !col?.classList.contains("drop-allowed")) return;
    event.preventDefault(); openCard(board.dragId, Number(col.dataset.phase));
  });
  document.querySelectorAll(".board-mode").forEach((button) => button.addEventListener("click", () => setBoardMode(button.dataset.mode)));
  const shiftMonth = (step) => { cal.month = new Date(cal.month.getFullYear(), cal.month.getMonth() + step, 1); renderBoard(); };
  $("cal-prev").addEventListener("click", () => shiftMonth(-1));
  $("cal-next").addEventListener("click", () => shiftMonth(1));
  $("cal-today").addEventListener("click", () => { cal.month = null; renderBoard(); });
  document.querySelectorAll(".cal-basis button").forEach((button) => button.addEventListener("click", () => { cal.basis = button.dataset.basis; renderBoard(); }));
  $("cal-grid").addEventListener("click", (event) => {
    const more = event.target.closest("[data-day-more]"); if (more) { openDay(more.dataset.dayMore); return; }
    const chip = event.target.closest(".cal-chip"); if (chip) openCard(Number(chip.dataset.itemId));
  });
  $("day-list").addEventListener("click", (event) => { const chip = event.target.closest(".cal-chip"); if (!chip) return; $("day-dialog").close(); openCard(Number(chip.dataset.itemId)); });
  $("close-day").addEventListener("click", () => $("day-dialog").close());
  $("day-dialog").addEventListener("click", (event) => { if (event.target === $("day-dialog")) $("day-dialog").close(); });
  window.addEventListener("resize", () => { if (model.view !== "board" || board.mode !== "calendar") return; clearTimeout(cal.frame); cal.frame = setTimeout(renderBoard, 120); });
  const toggleUnit = (unit) => { const open = unit.classList.toggle("open"); unit.setAttribute("aria-expanded", String(open)); };
  $("gen-list").addEventListener("click", (event) => {
    const link = event.target.closest("[data-open-item]"); if (link) { openCard(Number(link.dataset.openItem)); return; }
    const unit = event.target.closest(".gen-unit"); if (unit) toggleUnit(unit);
  });
  $("gen-list").addEventListener("keydown", (event) => { const unit = event.target.closest(".gen-unit"); if (unit && event.target === unit && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); toggleUnit(unit); } });
  $("theme-toggle").setAttribute("aria-pressed", String(document.documentElement.dataset.theme === "dark"));
  $("theme-toggle").addEventListener("click", toggleTheme);
  // Geração: filtros (pelos números do topo ou pelas pílulas), busca e alertas que levam à usina.
  $("generation-view").addEventListener("click", (event) => {
    const filter = event.target.closest("[data-gen-filter]"), alert = event.target.closest("[data-gen-plant]");
    if (filter) { if (filter.dataset.genFilter !== "none") { genUI.filter = filter.dataset.genFilter; renderGeneration(); } return; }
    if (alert) { genUI.filter = "all"; genUI.query = ""; $("gen-search").value = ""; renderGeneration(); const row = [...document.querySelectorAll("#gen-list .gen-row")].find((entry) => entry.dataset.plant === alert.dataset.genPlant); row?.scrollIntoView({ block: "center", behavior: motionOk() ? "smooth" : "auto" }); row?.classList.add("flash"); setTimeout(() => row?.classList.remove("flash"), 1600); }
  });
  $("gen-search").addEventListener("input", (event) => { genUI.query = event.target.value; renderGeneration(); });
  $("gen-refresh").addEventListener("click", async () => {
    const button = $("gen-refresh"); button.disabled = true; button.textContent = "Lendo relatórios…";
    try { model.state = await request("/api/generation/refresh", "POST", {}); model.state.items ||= []; renderState(); toast("Planilha de dados lida de novo."); }
    catch (error) { message("gen-alert", error.message, error.status === 409 ? "warning" : "error"); }
    finally { button.disabled = false; button.textContent = "Ler relatórios agora"; }
  });
  $("rules-map").addEventListener("click", (event) => { const target = event.target.closest("[data-node]"); if (!target) return; rulesUI.selected = target.dataset.node; renderRulesCanvas(); renderRulesPanel(); });
  const pickBpmn = (event) => { const target = event.target.closest("[data-node]"); if (!target?.dataset.node) return; rulesUI.selected = target.dataset.node; renderRulesCanvas(); renderRulesPanel(); $("rules-bpmn").querySelector(`[data-el="${target.dataset.el}"]`)?.focus(); };
  $("rules-bpmn").addEventListener("click", pickBpmn);
  $("rules-bpmn").addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pickBpmn(event); } });
  document.querySelectorAll("[data-rules-mode]").forEach((button) => button.addEventListener("click", () => {
    rulesUI.mode = button.dataset.rulesMode; try { localStorage.setItem("sisco.regras.modo", rulesUI.mode); } catch (_) { /* só nesta sessão */ }
    renderRulesCanvas(); renderShell();
  }));
  const zoomBpmn = (factor) => { rulesUI.zoom = Math.max(0.3, Math.min(2.5, (rulesUI.zoom || 1) * factor)); renderRulesBpmn(); };
  $("bpmn-zoom-in").addEventListener("click", () => zoomBpmn(1.2));
  $("bpmn-zoom-out").addEventListener("click", () => zoomBpmn(1 / 1.2));
  $("bpmn-fit").addEventListener("click", () => { rulesUI.zoom = "fit"; renderRulesBpmn(); });
  $("bpmn-svg").addEventListener("click", () => {
    const svg = buildBpmnSvg(bpmnModel(), { interactive: false });
    downloadText(`<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(svg)}`, "SISCO_fluxo_ocorrencias.svg", "image/svg+xml");
  });
  $("bpmn-xml").addEventListener("click", () => downloadText(bpmnXml(bpmnModel()), "SISCO_fluxo_ocorrencias.bpmn", "application/xml"));
  $("rules-diff").addEventListener("click", (event) => { const row = event.target.closest("[data-open-item]"); if (row) openCard(Number(row.dataset.openItem)); });
  // BPMN: paleta de elementos, arrastar o fundo para mover e Ctrl + roda para o zoom.
  $("bpmn-palette").addEventListener("click", (event) => { const chip = event.target.closest("[data-bpmn-kind]"); if (!chip) return; rulesUI.kind = rulesUI.kind === chip.dataset.bpmnKind ? null : chip.dataset.bpmnKind; renderRulesBpmn(); });
  let bpmnPan = null;
  $("rules-bpmn").addEventListener("pointerdown", (event) => { if (event.button !== 0 || event.target.closest(".bp-el")) return; const box = $("rules-bpmn"); bpmnPan = { x: event.clientX, y: event.clientY, left: box.scrollLeft, top: box.scrollTop }; box.setPointerCapture(event.pointerId); box.classList.add("panning"); });
  $("rules-bpmn").addEventListener("pointermove", (event) => { if (!bpmnPan) return; const box = $("rules-bpmn"); box.scrollLeft = bpmnPan.left - (event.clientX - bpmnPan.x); box.scrollTop = bpmnPan.top - (event.clientY - bpmnPan.y); });
  for (const name of ["pointerup", "pointercancel"]) $("rules-bpmn").addEventListener(name, () => { bpmnPan = null; $("rules-bpmn").classList.remove("panning"); });
  $("rules-bpmn").addEventListener("wheel", (event) => { if (!event.ctrlKey) return; event.preventDefault(); rulesUI.zoom = Math.max(0.3, Math.min(2.5, (rulesUI.zoom || 1) * (event.deltaY < 0 ? 1.1 : 1 / 1.1))); renderRulesBpmn(); }, { passive: false });
  // Editor do fluxo (bpmn-js): zoom, ajustar, voltar ao processo, organizar, baixar, caminho de uma ocorrência, publicar e voltar versão.
  const flowCanvas = () => bpmnUI.modeler?.get("canvas");
  const zoomFlow = (factor) => { const canvas = flowCanvas(); if (canvas) canvas.zoom(Math.max(0.2, Math.min(3, canvas.zoom() * factor))); };
  $("flow-zoom-in").addEventListener("click", () => zoomFlow(1.2));
  $("flow-zoom-out").addEventListener("click", () => zoomFlow(1 / 1.2));
  $("flow-fit").addEventListener("click", () => { const canvas = flowCanvas(); if (canvas) { canvas.zoom("fit-viewport", "auto"); canvas.scroll({ dx: 70, dy: 0 }); } });
  $("flow-up").addEventListener("click", () => { const canvas = flowCanvas(), root = canvas?.getRootElements().find((r) => !r.id.endsWith("_plane")); if (root) canvas.setRootElement(root); });
  $("flow-autolayout").addEventListener("click", async () => {
    if (!bpmnUI.modeler) return;
    if (bpmnUI.dirty || flowUI.saving) { toast("Espere o rascunho terminar de salvar."); return; }
    try {
      const files = await request("/api/flow/bpmn");
      if (!files.organized) { toast("O rascunho está incompleto demais para redesenhar. Corrija os problemas primeiro."); return; }
      if (!confirm("Redesenhar o fluxo com a organização automática do SISCO? As regras continuam as mesmas, mas posições e artefatos feitos à mão (anotações, objetos de dados) são perdidos.")) return;
      bpmnUI.root = null; await importFlowXml(files.organized, files.organized); bpmnUI.dirty = true; await saveFlowDraft();
    } catch (error) { toast(error.message); }
  });
  $("flow-download").addEventListener("click", async () => {
    if (!bpmnUI.modeler) return;
    const { xml } = await bpmnUI.modeler.saveXML({ format: true }), link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([xml], { type: "application/xml" }));
    link.download = `fluxo-sisco-${model.state.flow.draft ? "rascunho" : `v${model.state.flow.version}`}.bpmn`;
    link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 2000);
  });
  $("flow-trace").addEventListener("input", (event) => {
    const query = normalized(event.target.value).trim(), items = model.state.items;
    const item = query ? items.find((entry) => normalized(entry.legacy_id) === query) || items.find((entry) => normalized(entry.forms_id) === query) : null;
    flowUI.trace = item?.workflow?.path ? { item, path: item.workflow.path } : null;
    const modeler = bpmnUI.modeler, el = flowUI.trace && modeler?.get("elementRegistry").get(flowUI.trace.path.at(-1));
    if (el) { const canvas = modeler.get("canvas"); canvas.setRootElement(canvas.findRoot(el)); modeler.get("selection").select(el); canvas.scrollToElement(el); }   // abre onde ela está
    renderFlowStatus(); paintFlowMarkers();
  });
  $("flow-publish").addEventListener("click", async () => {
    const changed = flowUI.sim?.changed_total ?? 0;
    if (!confirm(`Publicar o fluxo? ${changed} ${changed === 1 ? "ocorrência muda" : "ocorrências mudam"} de fase, conclusão ou prazo no quadro.`)) return;
    const note = (prompt("Descreva a mudança (fica no histórico):", "") || "").trim();
    try { model.state = await request("/api/flow/publish", "POST", { version: model.state.flow.version, note }); flowUI.sim = null; renderState(); toast("Fluxo publicado. O quadro já segue o fluxo novo."); }
    catch (error) { flowUI.error = error.message; renderFlowStatus(); }
  });
  $("flow-discard").addEventListener("click", async () => {
    if (!confirm("Descartar o rascunho e voltar ao fluxo publicado?")) return;
    clearTimeout(bpmnUI.timer); bpmnUI.dirty = false;
    try { model.state = await request("/api/flow/discard", "POST", {}); flowUI.sim = null; flowUI.error = ""; renderState(); toast("Rascunho do fluxo descartado."); }
    catch (error) { toast(error.message); }
  });
  $("flow-versions").addEventListener("change", async (event) => {
    const version = Number(event.target.value); if (!event.target.value) return;
    if (!confirm(`Colocar a versão ${version} do fluxo no rascunho? Ela só vale depois de publicada.`)) { event.target.value = ""; return; }
    clearTimeout(bpmnUI.timer); bpmnUI.dirty = false;
    try { const result = await request("/api/flow/restore", "POST", { version }); model.state.flow = { ...model.state.flow, draft: result.draft }; flowUI.sim = result.simulation; renderFlowEditor(); toast(`Versão ${version} no rascunho.`); }
    catch (error) { toast(error.message); }
  });
  $("dmn-download").addEventListener("click", async () => {
    if (!dmnUI.modeler) return;
    const { xml } = await dmnUI.modeler.saveXML({ format: true }), link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([xml], { type: "application/xml" })); link.download = "decisoes-sisco.dmn";
    link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 2000);
  });
  // Validar: o servidor confere o rascunho com as mesmas regras da publicação e simula, sem gravar nada.
  $("rules-validate").addEventListener("click", async () => {
    try { rulesUI.sim = await request("/api/rules/simulate", "POST", { rules: rulesUI.draft }); rulesUI.error = ""; renderRules(); toast(`Fluxo válido. ${rulesUI.sim.changed_total} ${rulesUI.sim.changed_total === 1 ? "ocorrência mudaria" : "ocorrências mudariam"} com estas regras.`); }
    catch (error) { rulesUI.error = error.message; renderRules(); toast(`Fluxo com erro: ${error.message}`); }
  });
  $("rules-publish").addEventListener("click", async () => {
    const changed = rulesUI.sim?.changed_total ?? 0;
    if (!window.confirm(`Publicar as regras? ${changed} ${changed === 1 ? "ocorrência muda" : "ocorrências mudam"} de fase, conclusão ou prazo no quadro.`)) return;
    const note = (window.prompt("Descreva a mudança (fica no histórico):", "") || "").trim();
    try { model.state = await request("/api/rules/publish", "POST", { version: model.state.configuration.version, note }); model.state.items ||= []; rulesUI.base = null; rulesUI.sim = null; renderState(); toast("Regras publicadas. O quadro já segue as novas regras."); }
    catch (error) { rulesUI.error = error.message; renderRulesStatus(); }
  });
  $("rules-discard").addEventListener("click", async () => {
    if (!window.confirm("Descartar o rascunho e voltar às regras publicadas?")) return;
    clearTimeout(rulesUI.timer);
    try { model.state = await request("/api/rules/discard", "POST", {}); model.state.items ||= []; rulesUI.base = null; rulesUI.sim = null; rulesUI.error = ""; renderState(); toast("Rascunho descartado."); }
    catch (error) { rulesUI.error = error.message; renderRulesStatus(); }
  });
  $("rules-versions").addEventListener("change", async (event) => {
    const version = Number(event.target.value); if (!event.target.value) return;
    if (!window.confirm(`Colocar as regras da versão ${version} no rascunho? Nada muda até você publicar.`)) { event.target.value = ""; return; }
    try { const result = await request("/api/rules/restore", "POST", { version }); model.state.rules = { ...(model.state.rules || {}), draft: result.draft }; rulesUI.base = null; rulesUI.sim = result.simulation; renderRules(); toast(`Regras da versão ${version} no rascunho. Confira e publique.`); }
    catch (error) { rulesUI.error = error.message; renderRulesStatus(); }
  });
  $("rules-trace").addEventListener("input", () => {
    const query = normalized($("rules-trace").value).trim().replace(/^(ocorrencia|forms)\s*/, "");
    // O ID da ocorrência tem prioridade; o ID Forms só vale se nenhuma ocorrência tiver esse número.
    const items = model.state?.items || [], item = query && (items.find((entry) => String(entry.legacy_id) === query) || items.find((entry) => String(entry.forms_id) === query));
    rulesUI.trace = item ? { item, phase: phaseKey(item), lane: rulesUI.draft.flows.find((f) => normalized(f.classification) === normalized(item.values?.classification))?.flow || "" } : null;
    renderRulesCanvas(); renderRulesStatus();
  });
  $("saved-views").addEventListener("change", (event) => applySavedView(event.target.value));
  $("save-view").addEventListener("click", () => {
    const name = (window.prompt("Nome da visão (ex.: Atrasadas da Manutenção):", $("saved-views").value) || "").trim().slice(0, 60);
    if (!name) return;
    if (writeViews([...readViews().filter((view) => view.name !== name), { name, ...currentFilters() }])) { renderSavedViews(name); toast(`Visão "${name}" salva neste navegador.`); }
  });
  $("delete-view").addEventListener("click", () => {
    const name = $("saved-views").value;
    if (!name || !window.confirm(`Excluir a visão "${name}"?`)) return;
    if (writeViews(readViews().filter((view) => view.name !== name))) { renderSavedViews(); applySavedView(""); }
  });
  renderSavedViews();
  $("open-palette").addEventListener("click", openPalette);
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); if ($("palette").open) $("palette").close(); else openPalette(); return; }
    if (event.key === "/" && !event.target.closest("input,textarea,select,[contenteditable]") && !document.querySelector("dialog[open]")) { event.preventDefault(); openPalette(); }
  });
  $("palette-input").addEventListener("input", () => { paletteIndex = 0; renderPalette(); });
  $("palette-input").addEventListener("keydown", (event) => {
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (step && paletteResults.length) { event.preventDefault(); paletteIndex = (paletteIndex + step + paletteResults.length) % paletteResults.length; renderPalette(); }
    else if (event.key === "Enter") { event.preventDefault(); choosePalette(paletteIndex); }
  });
  $("palette-list").addEventListener("click", (event) => { const option = event.target.closest("[data-index]"); if (option) choosePalette(Number(option.dataset.index)); });
  $("palette").addEventListener("click", (event) => { if (event.target === $("palette")) $("palette").close(); });
  // KPIs: mês, lista por trás de cada número e metas.
  const setKpiMonth = (month) => { kpiUI.month = month; renderKpis(); };
  $("kpi-month").addEventListener("change", (event) => setKpiMonth(event.target.value));
  $("kpi-prev").addEventListener("click", () => setKpiMonth(shiftMonthKey(kpiUI.month, -1)));
  $("kpi-next").addEventListener("click", () => setKpiMonth(shiftMonthKey(kpiUI.month, 1)));
  const kpiBodyAction = (event) => {
    const month = model.state.kpis.by_month[kpiUI.month], hit = event.target.closest("[data-kpi-month]"), list = event.target.closest("[data-kpi-list]"), found = event.target.closest("[data-kpi-finding]");
    if (hit) return setKpiMonth(hit.dataset.kpiMonth);
    if (list) { const def = model.state.kpis.definitions.find((entry) => entry.key === list.dataset.kpiList); return openItemList(def.name, month.kpis[def.key].failing); }
    if (found) { const entry = month.findings[Number(found.dataset.kpiFinding)]; return openItemList(entry.title, entry.items); }
    const make = event.target.closest("[data-kpi-action]"), stage = event.target.closest("[data-kpi-stage]"), act = event.target.closest("[data-action-done],[data-action-cancel],[data-action-reopen]");
    if (make) return openActionDialog(month.findings[Number(make.dataset.kpiAction)]);
    if (stage) { const entry = model.state.management.stages[kpiUI.month][Number(stage.dataset.kpiStage)]; return openItemList(`${entry.name} · os mais demorados`, entry.items); }
    if (act) return changeAction(act.dataset);
  };
  // Ações de melhoria: criar a partir de um achado, concluir (com o resultado), cancelar e reabrir.
  let actionFinding = null;
  function openActionDialog(found) {
    actionFinding = found;
    const form = $("action-form"), due = new Date(); due.setDate(due.getDate() + 30);
    form.elements.title.value = found.title; form.elements.todo.value = found.action; form.elements.owner.value = ""; form.elements.due.value = dayKey(due);
    $("action-measure").textContent = found.metric ? `Medida acompanhada: ${found.metric.label}. O valor de hoje fica guardado como "antes".` : "Este achado não tem medida automática.";
    $("action-error").hidden = true; $("action-dialog").showModal(); form.elements.owner.focus();
  }
  async function changeAction(data) {
    const id = data.actionDone || data.actionCancel || data.actionReopen;
    let body = { status: "aberta" };
    if (data.actionDone) { const note = prompt("O que foi feito? (fica registrado como resultado da ação)", ""); if (note === null) return; body = { status: "concluida", note }; }
    if (data.actionCancel) { if (!confirm("Cancelar esta ação? Ela sai da lista.")) return; body = { status: "cancelada" }; }
    try { model.state = await request(`/api/actions/${id}`, "PATCH", body); renderState(); toast(data.actionDone ? "Ação concluída. A medida segue em observação." : data.actionCancel ? "Ação cancelada." : "Ação reaberta."); }
    catch (error) { toast(error.message); }
  }
  $("action-dialog-close").addEventListener("click", () => $("action-dialog").close());
  $("action-dialog").addEventListener("click", (event) => { if (event.target === $("action-dialog")) $("action-dialog").close(); });
  $("action-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = $("action-form");
    try {
      model.state = await request("/api/actions", "POST", { month: kpiUI.month, ref: actionFinding.ref, title: form.elements.title.value, todo: form.elements.todo.value, owner: form.elements.owner.value, due: form.elements.due.value });
      $("action-dialog").close(); renderState(); toast("Ação criada.");
    } catch (error) { $("action-error").textContent = error.message; $("action-error").hidden = false; }
  });
  // Famílias de causa: nome + palavras-chave; a ordem da lista decide quando mais de uma casa.
  function causeRow(family) {
    const row = node("div", undefined, "cause-row"), name = node("input"), words = node("input"), remove = node("button", "×", "icon-button");
    name.type = "text"; name.maxLength = 60; name.required = true; name.value = family.name; name.placeholder = "Nome da família"; name.setAttribute("aria-label", "Nome da família");
    words.type = "text"; words.required = true; words.value = family.keywords.join(", "); words.placeholder = "palavras-chave separadas por vírgula"; words.setAttribute("aria-label", "Palavras-chave");
    remove.type = "button"; remove.setAttribute("aria-label", "Remover família"); remove.addEventListener("click", () => row.remove());
    row.append(name, words, remove); return row;
  }
  $("kpi-causes-open").addEventListener("click", () => {
    const causes = model.state.management.causes;
    $("cause-rows").replaceChildren(...causes.families.map(causeRow));
    const box = $("cause-unmatched"); box.replaceChildren();
    if (causes.unmatched.length) box.append(node("h3", `Textos ainda sem família · ${causes.unmatched.length}`), node("p", causes.unmatched.map((entry) => `${entry.name} (${entry.count})`).join(" · ")));
    $("cause-error").hidden = true; $("cause-dialog").showModal();
  });
  $("cause-add").addEventListener("click", () => { const row = causeRow({ name: "", keywords: [] }); $("cause-rows").append(row); row.querySelector("input").focus(); });
  $("cause-dialog-close").addEventListener("click", () => $("cause-dialog").close());
  $("cause-dialog").addEventListener("click", (event) => { if (event.target === $("cause-dialog")) $("cause-dialog").close(); });
  $("cause-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const families = [...$("cause-rows").children].map((row) => { const [name, words] = row.querySelectorAll("input"); return { name: name.value, keywords: words.value.split(",").map((word) => word.trim()).filter(Boolean) }; });
    try { model.state = await request("/api/causes", "PATCH", { version: model.state.management.causes.version, families }); $("cause-dialog").close(); renderState(); toast("Famílias de causa salvas."); }
    catch (error) { $("cause-error").textContent = error.message; $("cause-error").hidden = false; }
  });
  // Motivo da espera (fica só no SISCO).
  $("wait-save").addEventListener("click", async () => {
    const id = Number($("wait-save").dataset.itemId); if (!id) return;
    try {
      model.state = await request(`/api/items/${id}/wait`, "PATCH", { reason: $("wait-reason").value, note: $("wait-note").value });
      renderTimeline(model.state.items.find((item) => item.id === id)); renderKpis(); $("wait-status").textContent = $("wait-reason").value ? "Motivo salvo." : "Motivo removido.";
    } catch (error) { $("wait-status").textContent = error.message; }
  });
  $("kpi-body").addEventListener("click", kpiBodyAction);
  $("kpi-body").addEventListener("keydown", (event) => { if ((event.key === "Enter" || event.key === " ") && event.target.closest("[data-kpi-month]")) { event.preventDefault(); kpiBodyAction(event); } });
  $("kpi-targets-open").addEventListener("click", openKpiTargets);
  $("kpi-dialog-close").addEventListener("click", () => $("kpi-dialog").close());
  $("kpi-dialog").addEventListener("click", (event) => { if (event.target === $("kpi-dialog")) $("kpi-dialog").close(); });
  $("kpi-form-defaults").addEventListener("click", () => $("kpi-form").querySelectorAll("input[type=number]").forEach((input) => { input.value = input.dataset.default; }));
  $("kpi-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const targets = Object.fromEntries([...$("kpi-form").querySelectorAll("input[type=number]")].map((input) => [input.name, Number(input.value)]));
    try {
      model.state = await request("/api/kpis/targets", "PATCH", { version: model.state.kpis.version, targets });
      $("kpi-dialog").close(); renderState(); toast("Metas salvas.");
    } catch (error) { $("kpi-form-error").textContent = error.message; $("kpi-form-error").hidden = false; }
  });
  const setMonth = (month) => { monthly.month = month; renderMonthly(); };
  $("month-select").addEventListener("change", (event) => setMonth(event.target.value));
  $("month-prev").addEventListener("click", () => setMonth(shiftMonthKey(monthly.month, -1)));
  $("month-next").addEventListener("click", () => setMonth(shiftMonthKey(monthly.month, 1)));
  $("monthly-body").addEventListener("click", (event) => {
    const month = event.target.closest("[data-month]"); if (month) { setMonth(month.dataset.month); return; }
    const item = event.target.closest("[data-open-item]"); if (item) { openCard(Number(item.dataset.openItem)); return; }
    if (event.target.closest("[data-go-board]") && !model.saving) showView("board");
  });
  $("card-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeCard(); });
  $("card-dialog").addEventListener("click", (event) => { if (event.target === $("card-dialog")) closeCard(); });
  $("plant-search").addEventListener("input", renderPlants);
  document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); if (!model.saving) showView("start"); });
  // Casca limpa: marca, menu do ambiente, cartões do início e menu de administração.
  $("shell-home").addEventListener("click", () => { if (!model.saving) showView("start"); });
  $("env-tabs").addEventListener("click", (event) => { const tab = event.target.closest("[data-env-tab]"); if (tab && !model.saving) openTab(envOf(model.view), tab.dataset.envTab); });
  $("start-body").addEventListener("click", (event) => {
    const row = event.target.closest("[data-start-row]");
    const item = event.target.closest("[data-open-item]"); if (item) return openCard(Number(item.dataset.openItem));
    if (row) startActions[Number(row.dataset.startRow)]?.();
  });
  const adminMenu = (open) => { $("shell-admin-menu").hidden = !open; $("shell-admin").setAttribute("aria-expanded", String(open)); };
  $("shell-admin").addEventListener("click", (event) => { event.stopPropagation(); adminMenu($("shell-admin-menu").hidden); });
  document.addEventListener("click", () => adminMenu(false));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") adminMenu(false); });
  $("shell-admin-menu").addEventListener("click", (event) => {
    const choice = event.target.closest("[data-admin]")?.dataset.admin; if (!choice) return;
    if (choice === "settings") return $("open-settings").click();
    if (choice === "powerbi") { const url = $("sidebar-powerbi").href; return url && !$("sidebar-powerbi").hidden ? window.open(url, "_blank", "noopener") : toast("O link do Power BI ainda não foi informado."); }
    showView(choice);
  });
  document.querySelectorAll(".filter-tab").forEach((button) => button.addEventListener("click", () => { model.filter = button.dataset.filter; timelineReset=true; document.querySelectorAll(".filter-tab").forEach((entry) => { const active = entry === button; entry.classList.toggle("active", active); entry.setAttribute("aria-pressed", String(active)); }); renderList(); }));
  ["search", "status-filter", "classification-filter", "conclusion-filter", "plant-filter"].forEach((id) => $(id).addEventListener(id === "search" ? "input" : "change", ()=>{timelineReset=true;renderList();}));
  document.querySelectorAll(".sync-button").forEach((button) => button.addEventListener("click", sync));
  // Trocar a planilha de dados: o servidor confere o arquivo antes de substituir o atual.
  $("fonte-arquivo").addEventListener("change", async (event) => {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    const modo = document.querySelector('input[name="fonte-modo"]:checked')?.value || "atualizar";
    if (modo === "nova_base" && !confirm(`Começar uma base nova com "${file.name}"? As revisões e o histórico atuais ficam guardados em dados/arquivo e deixam de aparecer.`)) return;
    message("fonte-mensagem", `Conferindo "${file.name}"…`, "success");
    try {
      const conteudo = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1] || ""); reader.onerror = () => reject(new Error("Não foi possível ler o arquivo escolhido.")); reader.readAsDataURL(file); });
      model.state = await request("/api/source/upload", "POST", { nome: file.name, conteudo, modo }); model.state.items ||= [];
      renderState(); stateArrived();
      const lidas = model.state.sync?.summary?.records ?? model.state.items.length;
      message("fonte-mensagem", `Planilha "${model.state.sync?.source_file || file.name}" em uso: ${lidas} ocorrências lidas.`, "success");
      toast("Planilha de dados trocada.");
    } catch (error) { message("fonte-mensagem", error.message); }
  });
  $("edit-form").addEventListener("input", () => { setDirty(true); });
  $("edit-form").addEventListener("change", () => { setDirty(true); });
  $("edit-classification").addEventListener('change',maintenanceTypeVisibility);
  $("latest-records").addEventListener('click',()=>{$("occurrence-scroll").scrollTo({top:$("occurrence-scroll").scrollHeight,behavior:'smooth'});});
  $("previous-record").addEventListener('click',()=>stepRecord(-1));
  $("next-record").addEventListener('click',()=>stepRecord(1));
  $("items-body").addEventListener('keydown',event=>{if(!['ArrowUp','ArrowDown'].includes(event.key))return;const row=event.target.closest('tr[data-item-id]');if(!row)return;event.preventDefault();const index=visibleIds.indexOf(Number(row.dataset.itemId)),id=visibleIds[index+(event.key==='ArrowUp'?-1:1)];if(id)$("items-body").querySelector(`tr[data-item-id="${id}"] .row-button`)?.focus();});
  $("home-occurrences").addEventListener('click',()=>{if(!model.saving)transitionTo({view:'occurrences',filter:'all'});});
  $("analytics-edit").addEventListener('click',()=>editAnalytics(true));
  $("analytics-cancel").addEventListener('click',()=>editAnalytics(false));
  $("analytics-form").addEventListener('submit',saveAnalytics);
  $("open-settings").addEventListener('click',()=>{if(!model.state)return;if(!settingsDirty)renderSettings();$("settings-dialog").showModal();});
  $("close-settings").addEventListener('click',closeSettings);
  $("settings-dialog").addEventListener('cancel',closeSettings);
  $("edit-form").addEventListener("submit", save);
  $("save-next").addEventListener('click',event=>save(event,true));
  $("create-word").addEventListener('click',()=>createWord());
  $("upload-word").addEventListener('change',event=>{if(event.target.files[0])createWord(event.target.files[0]);});
  $("prepare-email").addEventListener('click',openEmail);
  $("open-return").addEventListener('click',openReturn);
  $("close-return").addEventListener('click',()=>$("return-dialog").close());
  $("return-form").addEventListener('submit',saveReturn);
  $("return-form").elements.maintenance_status.addEventListener('change',returnFields);
  $("return-dialog").addEventListener('cancel',event=>{if(model.saving)event.preventDefault();});
  $("refresh-preview").addEventListener('click',refreshPreview);
  $("email-kind").addEventListener('change',refreshPreview);
  $("send-test").addEventListener('click',sendTest);
  $("settings-form").addEventListener('submit',saveSettings);
  $("settings-form").addEventListener('input',()=>{settingsDirty=true;});
  $("settings-reload").addEventListener('click',()=>{if(!settingsDirty||window.confirm('Descartar as alterações de configuração não salvas?'))renderSettings();});
  $("add-flow").addEventListener('click',()=>{const first=flowTypesInForm().find(t=>t.name);const row=classificationRow({classification:'',flow:first?first.name:''});$("settings-flows").append(row);settingsDirty=true;syncFlowOptions();row.querySelector('input').focus();});
  $("add-flow-type").addEventListener('click',()=>{const row=flowTypeRow({name:'',followup:true,coordinator:false,recipient:'coordenacao'});$("settings-flow-types").append(row);settingsDirty=true;syncFlowOptions();row.querySelector('input').focus();});
  $("reconfirm-button").addEventListener("click", () => { if (model.saving) return; $("reviewed").checked = true; setDirty(true); $("edit-form").requestSubmit(); });
  $("discard-button").addEventListener("click", () => { if (discardAllowed()) renderDetail(current()); });
  $("close-detail").addEventListener("click", () => { if ($("card-dialog").open) { closeCard(); return; } if (model.saving || !discardAllowed()) return; model.selected = null; model.renderedVersion = null; setDirty(false); $("detail-content").hidden = true; $("detail-empty").hidden = false; renderList(); });
  $("reload-record").addEventListener("click", async () => { if (!discardAllowed()) return; try { const state = await request("/api/state"); model.state = state; model.state.items ||= []; renderDetail(current()); renderState(); } catch (error) { message("editor-message", error.message); } });
  window.addEventListener("beforeunload", (event) => { if (model.dirty || settingsDirty) { event.preventDefault(); event.returnValue = ""; } });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadState(); });
  const entryRoute=readRoute(window.location.search);
  const animateEntry=entryRoute?.view==='occurrences'&&!entryRoute.item&&motionOk();
  if(animateEntry)document.body.classList.add('route-arriving');
  if(entryRoute){history.replaceState(null,'',window.location.pathname);handOff(entryRoute);}
  showView(entryRoute?.view||'start');
  loadState().then(()=>{
    if(!entryRoute)return;
    applyRoute(entryRoute);
    document.body.classList.remove('route-arriving');
    if(animateEntry&&model.state&&model.view==='occurrences'&&motionOk())playEntrance();
  }).finally(()=>document.body.classList.remove('route-arriving'));
})();
