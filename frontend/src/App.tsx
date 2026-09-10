import { useEffect, useRef, useState } from 'react';
import { Activity, ArrowDownToLine, ArrowRight, ArrowUp, BookOpen, Bot, Box, Braces, Check, CheckCheck, ChevronDown, ChevronRight, CircleHelp, CirclePlay, Code2, Copy, Cpu, Download, Ellipsis, ExternalLink, FileCode2, FileText, Folder, GitBranch, Globe, Layers3, Loader2, Maximize2, MemoryStick, MessageSquare, Monitor, PanelLeftClose, PanelLeftOpen, Play, Plus, Search, Settings2, Shield, ShieldCheck, Sparkles, Square, Terminal, Trash2, WandSparkles, Wrench, X, Zap } from 'lucide-react';
import { Client } from '@gradio/client';
import { zipSync, strToU8 } from 'fflate';
import { demoAnswer, models, projectFiles } from './project';
import type { LucideIcon } from 'lucide-react';

type Message = { role: 'user' | 'assistant'; content: string };
type Session = { id: string; title: string; messages: Message[] };
type Tab = 'workspace' | 'skills' | 'files';

const modeMap: Record<string, string> = {
    'Полный цикл': '🧠 Полный цикл (Dev + Sec + Patch + QA)',
    'Авто': '⚡ Авто (умный выбор)',
    'Только код': '💻 Только код',
    'Аудит безопасности': '🛡️ Только аудит безопасности',
    'Генерация тестов': '🧪 Генерация тестов (pytest)',
};

const roles: {name: string; label: string; icon: LucideIcon; color: string}[] = [
    {name:'Архитектор',label:'Планирует',icon:Layers3,color:'purple'},
{name:'Разработчик',label:'Пишет код',icon:Code2,color:'blue'},
{name:'Безопасность',label:'Находит риски',icon:ShieldCheck,color:'orange'},
{name:'Патчер',label:'Исправляет',icon:Wrench,color:'pink'},
{name:'QA-инженер',label:'Тестирует',icon:CheckCheck,color:'green'},
];
const skillDefs: {id:string; name:string; text:string; icon:LucideIcon; tag:string}[] = [
    {id:'doctor',name:'Environment Doctor',text:'Проверка зависимостей, окружения и готовности сервиса перед запуском.',icon:Activity,tag:'DevOps'},
{id:'security',name:'Аудит безопасности',text:'Анализ кода, рекомендации OWASP и поиск потенциальных уязвимостей.',icon:ShieldCheck,tag:'Security'},
{id:'tests',name:'Генерация pytest',text:'Позитивные и негативные сценарии, регрессионные тесты для вашего API.',icon:Braces,tag:'QA'},
{id:'rag',name:'RAG-память',text:'Рекомендации по семантическому поиску заметок с nomic-embed-text.',icon:BookOpen,tag:'Memory'},
{id:'deps',name:'Dependency Audit',text:'Проверка совместимости пакетов и план аудита зависимостей с pip-audit.',icon:Box,tag:'DevOps'},
{id:'review',name:'Code Review',text:'Проверка читаемости, безопасных путей и изоляции пользовательских сессий.',icon:GitBranch,tag:'Quality'},
];
const initialSessions: Session[] = [
    {id:'welcome',title:'Новый сеанс',messages:[]},
{id:'preview',title:'Исправление Live Preview',messages:[{role:'user',content:'Почему превью падает с No module named uvicorn?'},{role:'assistant',content:demoAnswer}]},
{id:'audit',title:'Аудит FastAPI-сервиса',messages:[{role:'user',content:'Что проверить в безопасности моего агента?'},{role:'assistant',content:'В предоставленном коде стоит исправить проверку путей через startswith, включить очистку HTML в чате и отказаться от глобальной остановки моделей Ollama. Изолируйте процессы по ID сессии и не запускайте недоверенный код напрямую. Подробности — в README.md комплекта исправлений.'}]},
{id:'api',title:'REST API с авторизацией',messages:[{role:'user',content:'Спланируй REST API с авторизацией.'},{role:'assistant',content:'Предлагаемый стек: FastAPI, SQLAlchemy и SQLite для локальной разработки. Начните с /health, /auth/register и /auth/login. Храните только хеши паролей, секреты — вне репозитория, добавьте проверку доступа и тесты. Подключите Ollama в настройках, чтобы продолжить разработку с локальной моделью.'}]},
];
function loadSessions(): Session[] { try { const saved = JSON.parse(localStorage.getItem('popcat-sessions') || 'null'); return Array.isArray(saved) && saved.length && saved.every(s=>typeof s.id==='string' && typeof s.title==='string' && Array.isArray(s.messages)) ? saved : initialSessions; } catch { return initialSessions; } }
function CatLogo({small=false}:{small?:boolean}) { return <div className={`cat-logo ${small?'small':''}`}><svg viewBox="0 0 32 32" fill="none"><path d="M6 13 5 5l9 5h4l9-5-1 8c3 4 2 11-3 14H9C4 24 3 17 6 13Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="m10 17 3 2m9-2-3 2m-6 4h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg></div>; }

function App() {
    const [sessions,setSessions] = useState<Session[]>(loadSessions);
    const [activeId,setActiveId] = useState(sessions[0].id);
    const [tab,setTab] = useState<Tab>('workspace');
    const [input,setInput] = useState('');
    const [mode,setMode] = useState('Полный цикл');
    const [busy,setBusy] = useState(false);
    const [sidebar,setSidebar] = useState(true);
    const [modal,setModal] = useState<'settings'|'help'|'system'|null>(null);
    const [settingTab,setSettingTab] = useState('models');
    const [endpoint,setEndpoint] = useState(()=>localStorage.getItem('popcat-endpoint')||'http://127.0.0.1:7860');
    const [connected,setConnected] = useState(false);
    const [checking,setChecking] = useState(false);
    const [modelList,setModelList] = useState(models.map(m=>m[0]));
    const [selectedModels,setSelectedModels] = useState(()=>{try { const saved=JSON.parse(localStorage.getItem('popcat-models')||'null'); if(Array.isArray(saved)&&saved.length===5)return saved as string[]; }catch{} return roles.map((_,i)=>i===2?models[5][0]:models[0][0]);});
    const [enabled,setEnabled] = useState<string[]>(['doctor','security','tests']);
    const [previewMode,setPreviewMode] = useState('direct');
    const [previewState,setPreviewState] = useState<'idle'|'starting'|'running'>('idle');
    const [repaired,setRepaired] = useState(false);
    const [previewTab,setPreviewTab] = useState('preview');
    const [consoleOpen,setConsoleOpen] = useState(true);
    const [logs,setLogs] = useState<string[]>(['[system] Рабочее пространство инициализировано','[models] Загружен каталог из 17 моделей','[ready] Ожидание задачи']);
    const [file,setFile] = useState('preview_runtime.py');
    const [toast,setToast] = useState('');
    const [query,setQuery] = useState('');
    const [searchOpen,setSearchOpen] = useState(false);
    const [editing,setEditing] = useState<string|null>(null);
    const [editTitle,setEditTitle] = useState('');
    const [fullPreview,setFullPreview] = useState(false);
    const abortRef = useRef<AbortController|null>(null);
    const previewTimer = useRef<ReturnType<typeof setTimeout>|null>(null);
    const endRef = useRef<HTMLDivElement>(null);
    const clientRef = useRef<any>(null);
    const activeSession = sessions.find(s=>s.id===activeId) || sessions[0];
    const messages = activeSession.messages;
    const notify=(text:string)=>setToast(text);
    const log=(text:string)=>setLogs(prev=>[...prev.slice(-99),text]);
    useEffect(()=>{try{localStorage.setItem('popcat-sessions',JSON.stringify(sessions));}catch{}},[sessions]);
    useEffect(()=>{if(toast){const id=setTimeout(()=>setToast(''),4000);return()=>clearTimeout(id);}},[toast]);
    useEffect(()=>{endRef.current?.scrollIntoView({behavior:'smooth',block:'nearest'});},[messages,busy]);
    useEffect(()=>()=>{abortRef.current?.abort();if(previewTimer.current)clearTimeout(previewTimer.current);},[]);
    useEffect(()=>{const handler=(e:KeyboardEvent)=>{if(e.key==='Escape'){setModal(null);setFullPreview(false);} if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();setSearchOpen(v=>!v);}};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);},[]);
    const stop=()=>{abortRef.current?.abort();setBusy(false);log('[stop] Запрос остановлен пользователем');};
    const newSession=()=>{if(busy)stop();const id=crypto.randomUUID();setSessions(s=>[{id,title:'Новый сеанс',messages:[]},...s]);setActiveId(id);setTab('workspace');setInput('');};
    useEffect(()=>{const handler=(e:KeyboardEvent)=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='n'){e.preventDefault();newSession();}if((e.metaKey||e.ctrlKey)&&e.key===','){e.preventDefault();setModal('settings');}};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);},[busy]);
    const selectSession=(id:string)=>{if(busy)stop();setActiveId(id);setTab('workspace');};
    const deleteSession=(id:string)=>{if(busy)stop();const next=sessions.filter(s=>s.id!==id);if(!next.length){const replacement={id:crypto.randomUUID(),title:'Новый сеанс',messages:[]};next.push(replacement);}setSessions(next);if(id===activeId)setActiveId(next[0].id);};
    const toggleSkill=(id:string)=>setEnabled(v=>v.includes(id)?v.filter(x=>x!==id):[...v,id]);
    const copy=async(text:string)=>{try{await navigator.clipboard.writeText(text);notify('Скопировано в буфер обмена');}catch{notify('Буфер обмена недоступен. Скачайте файл.');}};
    const download=(single?:string)=>{const files=single?{[single]:projectFiles[single]}:projectFiles;const data=zipSync(Object.fromEntries(Object.entries(files).map(([k,v])=>[k,strToU8(v)])));const url=URL.createObjectURL(new Blob([new Uint8Array(data)],{type:'application/zip'}));const a=document.createElement('a');a.href=url;a.download=single?`${single}.zip`:'popcat-preview-fix.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Комплект исправлений скачан');log('[export] ZIP с инструкцией и исправлениями собран');};
    const checkConnection=async()=>{
        setChecking(true);
        setConnected(false);
        try {
            const testClient = await Client.connect(endpoint);
            clientRef.current = testClient;
            setConnected(true);
            localStorage.setItem('popcat-endpoint', endpoint);
            notify('Подключение к бэкенду установлено');
            log('[backend] Подключение к Gradio API установлено');
        } catch(e) {
            notify(`Нет подключения: ${e instanceof Error ? e.message : 'ошибка сети'}`);
            setConnected(false);
        } finally {
            setChecking(false);
        }
    };
    const send=async()=>{
        const text=input.trim();
        if(!text||busy)return;
        const id=activeSession.id;
        const next:Message[]=[...messages,{role:'user',content:text}];
        setSessions(s=>s.map(v=>v.id===id?{...v,title:v.messages.length?v.title:text.slice(0,34),messages:next}:v));
        setInput('');
        setBusy(true);
        const controller=new AbortController();
        abortRef.current=controller;
        const modeValue = modeMap[mode] || mode;
        log(`[task] ${mode} · ${connected ? 'бэкенд' : 'демонстрация'}`);
        try{
            let answer:string;
            if(connected && clientRef.current){
                // Используем Gradio Client
                const result = await clientRef.current.predict("/process_chat", {
                    user_request: text,
                    history: [],
                    user_mode_choice: modeValue,
                    general_m: selectedModels[0],
                    coder_m: selectedModels[1],
                    cyber_m: selectedModels[2],
                    patch_m: selectedModels[3],
                    qa_m: selectedModels[4],
                });
                // result.data — массив результатов
                if (result && result.data && result.data.length > 0) {
                    answer = String(result.data[0] || 'Получен пустой ответ');
                } else {
                    answer = 'Получен пустой ответ от сервера';
                }
            } else {
                await new Promise<void>((resolve,reject)=>{
                    const timer=setTimeout(resolve,1250);
                    controller.signal.addEventListener('abort',()=>{
                        clearTimeout(timer);
                        reject(new DOMException('Aborted','AbortError'));
                    },{once:true});
                });
                answer=/uvicorn|превью|preview|ошиб|исправ/i.test(text)?demoAnswer:`Демонстрационный режим\n\nЗадача «${text}» сохранена в этом сеансе. Выбран режим «${mode}» и ${enabled.length} навыка.\n\nДля генерации настоящего ответа подключите локальный бэкенд: Настройки → Подключение → Проверить подключение.`;
            }
            if(controller.signal.aborted)return;
            setSessions(s=>s.map(v=>v.id===id?{...v,messages:[...v.messages,{role:'assistant',content:answer}]}:v));
            log('[done] Ответ подготовлен · сеанс сохранён');
        } catch(e){
            if(!controller.signal.aborted){
                const err=e instanceof Error?e.message:'Ошибка сети';
                setSessions(s=>s.map(v=>v.id===id?{...v,messages:[...v.messages,{role:'assistant',content:`Не удалось получить ответ: ${err}`}]}:v));
                log(`[error] ${err}`);
            }
        } finally{
            if(abortRef.current===controller) setBusy(false);
        }
    };
    const repair=()=>{setRepaired(true);log('[doctor] Комплект исправления готов');notify('Исправление подготовлено. Скачайте ZIP.');};
    const runPreview=()=>{if(previewState==='running'){setPreviewState('idle');log('[preview] Демонстрация остановлена');return;}if(!repaired){notify('Сначала подготовьте исправление окружения');return;}setPreviewState('starting');previewTimer.current=setTimeout(()=>{setPreviewState('running');log('[preview] Демонстрационный API-экран готов');},1100);};
    const previewDocument=`<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>body{background:#111817;color:#dce8e3;font:13px system-ui;padding:22px}h2{font-size:20px;font-weight:600;margin:8px 0}small{color:#7c978b}.badge{display:inline-block;background:#173c2c;color:#7ee2aa;padding:4px 8px;border-radius:5px;margin:12px 0}button{width:100%;background:#173b2c;border:1px solid #326f4e;color:#aff1c9;padding:12px;text-align:left;border-radius:6px;cursor:pointer}pre{white-space:pre-wrap;color:#91b6a4;font-size:12px}footer{font-size:10px;color:#73897e;margin-top:30px}</style></head><body><small>FASTAPI · ДЕМОНСТРАЦИЯ</small><h2>PopCat Preview API</h2><div class="badge">v1.0.0</div><p>Проверьте пример ответа сервиса.</p><button onclick="document.getElementById('result').textContent='200 OK\\n\\n'+JSON.stringify({status:'ok',service:'popcat-preview'},null,2)"><b>GET</b> &nbsp; /health &nbsp; →</button><pre id="result">Нажмите на endpoint для проверки.</pre><footer>Браузерный пример. Не подключён к Python-сервису.</footer></body></html>`;
    const previewContent=<><div className="preview-browser"><div className="browser-dots"><i/><i/><i/></div><span><Shield size={11}/> {previewState==='running'?'demo.local / docs':'Превью приложения'}</span><button className="icon-button" title="Развернуть превью" onClick={()=>setFullPreview(true)}><Maximize2 size={13}/></button></div>{previewState==='running'?<iframe title="Демонстрационный API" sandbox="allow-scripts" srcDoc={previewDocument}/>:<div className="empty-preview"><div className="preview-art"><div className="art-window"><span/><span/><span/><Code2 size={31}/></div><div className="art-spark"><Sparkles size={17}/></div></div><strong>{previewState==='starting'?'Подготавливаем превью…':'Ваш проект — в действии'}</strong><p>Запустите превью, чтобы проверить<br/>сервис прямо здесь.</p><span className="subtle-badge"><span className="dot"/> Браузерная демонстрация</span></div>}</>;
    return <div className={`app-shell ${!sidebar?'sidebar-hidden':''}`}>
    <aside className="sidebar">
    <div className="brand"><CatLogo/><div>PopCat <span>PRO</span><small>LOCAL AGENT WORKSPACE</small></div><button className="icon-button collapse" title="Свернуть панель" onClick={()=>setSidebar(v=>!v)}><PanelLeftClose size={17}/></button></div>
    <button className="new-session" onClick={newSession}><Plus size={17}/> Новый сеанс <span>⌘ N</span></button>
    <div className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
    <nav className="main-nav"><button className={tab==='workspace'?'selected':''} onClick={()=>setTab('workspace')}><MessageSquare size={17}/> Агенты <span className="nav-count">5</span></button><button className={tab==='skills'?'selected':''} onClick={()=>setTab('skills')}><Zap size={17}/> Библиотека скиллов <span className="new-badge">NEW</span></button><button className={tab==='files'?'selected':''} onClick={()=>setTab('files')}><Folder size={17}/> Файлы проекта <span className="muted-count">6</span></button></nav>
    <div className="nav-label history-label"><span>ИСТОРИЯ СЕАНСОВ</span><button className="icon-button" title="Поиск сеансов" onClick={()=>setSearchOpen(!searchOpen)}><Search size={14}/></button></div>
    {searchOpen&&<div className="session-search"><Search size={13}/><input autoFocus placeholder="Найти сеанс…" value={query} onChange={e=>setQuery(e.target.value)}/></div>}
    <div className="session-list"><div className="day-label">Сегодня</div>{sessions.filter(s=>s.title.toLowerCase().includes(query.toLowerCase())).map((s,i)=><div key={s.id}>{i===3&&<div className="day-label">Ранее</div>}<div className={`session ${activeId===s.id?'active':''}`}><button className="session-title" onClick={()=>selectSession(s.id)}><MessageSquare size={14}/><span>{s.title}</span></button><button className="icon-button session-menu" title="Переименовать сеанс" onClick={()=>{setEditing(s.id);setEditTitle(s.title);}}><Ellipsis size={15}/></button></div></div>)}{!sessions.filter(s=>s.title.toLowerCase().includes(query.toLowerCase())).length&&<p className="empty-search">Сеансы не найдены</p>}</div>
    <div className="sidebar-bottom"><div className="local-card"><div className="local-card-icon"><ShieldCheck size={19}/></div><div><strong>Ваш код. Ваши модели.</strong><p>Всё под вашим контролем.</p></div><span className="dot"/></div><button className="bottom-link" onClick={()=>{setSettingTab('models');setModal('settings');}}><Settings2 size={16}/> Настройки <span>⌘ ,</span></button><button className="bottom-link" onClick={()=>setModal('help')}><CircleHelp size={16}/> Документация <ExternalLink size={13}/></button><div className="profile"><div className="avatar">B</div><div><strong>beluga</strong><small>Локальное пространство</small></div><button className="icon-button" title="Настройки профиля" onClick={()=>setModal('system')}><ChevronDown size={15}/></button></div></div>
    </aside>
    <div className="app-body">
    <header className="topbar"><div className="breadcrumbs"><button className={`icon-button mobile-menu ${!sidebar?'always-visible':''}`} title="Переключить панель" onClick={()=>setSidebar(v=>!v)}><PanelLeftOpen size={18}/></button><span>Рабочее пространство</span><ChevronRight size={13}/><strong>{tab==='workspace'?'Агенты':tab==='skills'?'Библиотека скиллов':'Файлы проекта'}</strong></div><div className="topbar-right"><button className="connection-status" onClick={()=>{setSettingTab('connection');setModal('settings');}}><span className={`dot ${connected?'':'dim'}`}/>{connected?'Бэкенд подключен':'Локальный режим'}<ChevronDown size={12}/></button><span className="topbar-divider"/><span className="version">v2.5.0</span><button className="icon-button" title="Настройки" onClick={()=>setModal('settings')}><Settings2 size={17}/></button></div></header>
    <div className="workspace-layout"><main className="main-panel">
    <div className="workspace-heading"><div><div className="eyebrow"><span className="tiny-square"/> MULTI-AGENT STUDIO</div><h1>{tab==='workspace'?'Давайте создадим что-то крутое.':tab==='skills'?'Больше возможностей. Меньше рутины.':'Всё для уверенного запуска.'}</h1><p>{tab==='workspace'?'Пять экспертов. Один рабочий процесс. Полностью локально.':tab==='skills'?'Подключайте полезные навыки для работы над вашим проектом.':'Изучите исправления и заберите готовый комплект с собой.'}</p></div><button className="icon-button heading-more" title="Информация о пространстве" onClick={()=>setModal('help')}><Ellipsis size={20}/></button></div>
    <div className="content-tabs"><button className={tab==='workspace'?'active':''} onClick={()=>setTab('workspace')}><Bot size={16}/> Рабочая область</button><button className={tab==='skills'?'active':''} onClick={()=>setTab('skills')}><Zap size={15}/> Скиллы <span>{enabled.length}</span></button><button className={tab==='files'?'active':''} onClick={()=>setTab('files')}><FileCode2 size={15}/> Файлы проекта <span>6</span></button><div className="session-autosave"><span className="dot"/> Автосохранение</div></div>
    {tab==='workspace'?<>
        <div className="pipeline-head"><span>ВАША КОМАНДА АГЕНТОВ</span><button onClick={()=>{setSettingTab('models');setModal('settings');}}><Settings2 size={13}/> Настроить модели</button></div>
        <div className="agent-pipeline">{roles.map((r,i)=><div className="agent-wrap" key={r.name}><button className={`agent-card ${busy?'working':''}`} onClick={()=>{setSettingTab('models');setModal('settings');}}><div className={`agent-icon ${r.color}`}><r.icon size={19}/><span/></div><strong>{r.name}</strong><small>{r.label}</small><span className="model-label">{selectedModels[i].startsWith('Llama-3')?'Llama 3 · 8B':selectedModels[i].startsWith('Qwen2.5-Coder:7')?'Qwen 2.5 · 7B':selectedModels[i].split(':')[0].slice(0,14)}<ChevronDown size={10}/></span></button>{i<4&&<ChevronRight className="pipeline-arrow" size={13}/>}</div>)}</div>
        <div className={`conversation ${messages.length?'has-messages':''}`}>
        {!messages.length?<div className="welcome"><div className="welcome-symbol"><div className="welcome-orbit"/><Sparkles size={29}/></div><h2>От идеи — к рабочему коду.</h2><p>Опишите задачу. Агенты спроектируют, напишут код,<br className="desktop-break"/> проверят безопасность и подготовят тесты.</p><div className="suggestions"><button onClick={()=>setInput('Создай REST API на FastAPI с SQLite и авторизацией')}><Code2 size={17}/><span>Создать сервис<small>От архитектуры до API</small></span><ArrowUp size={13}/></button><button onClick={()=>{setMode('Аудит безопасности');setInput('Проведи аудит безопасности моего web_agent.py: проверь HTML, пути и изоляцию сессий')}}><ShieldCheck size={17}/><span>Проверить безопасность<small>Найти и устранить риски</small></span><ArrowUp size={13}/></button><button onClick={()=>{setMode('Генерация тестов');setInput('Напиши pytest-тесты для проверки FastAPI-сервиса')}}><Braces size={17}/><span>Написать тесты<small>Убедиться, что всё работает</small></span><ArrowUp size={13}/></button><button onClick={()=>setInput('Исправь ошибку превью: No module named uvicorn. Объясни изменения для venv и Docker.')}><Wrench size={17}/><span>Исправить ошибку<small>Разобраться и починить</small></span><ArrowUp size={13}/></button></div></div>:<div className="message-list">{messages.map((m,i)=><div className={`message ${m.role}`} key={i}>{m.role==='assistant'?<CatLogo small/>:<div className="message-avatar">B</div>}<div className="message-body"><div className="message-author">{m.role==='assistant'?'PopCat Agent':'Вы'}<span>{m.role==='assistant'?(connected?'Бэкенд':'Демо-ответ'):'Задача'}</span></div><div className="message-text">{m.content}</div>{m.role==='assistant'&&<button className="copy-message" onClick={()=>copy(m.content)}><Copy size={12}/> Скопировать</button>}</div></div>)}{busy&&<div className="thinking"><Loader2 size={16} className="spin"/>{connected?'Бэкенд обрабатывает запрос…':'Готовим демонстрационный ответ…'}</div>}<div ref={endRef}/></div>}
        </div>
        <div className="composer"><textarea aria-label="Задача для агентов" value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}} placeholder="Опишите задачу или вставьте код для анализа…" rows={2}/><div className="composer-toolbar"><div className="composer-options"><label className="mode-select"><Zap size={13}/><select aria-label="Режим работы" value={mode} onChange={e=>setMode(e.target.value)}>{['Полный цикл','Авто','Только код','Аудит безопасности','Генерация тестов'].map(v=><option key={v}>{v}</option>)}</select><ChevronDown size={11}/></label><span className="composer-divider"/><button className="skills-shortcut" onClick={()=>setTab('skills')}><Layers3 size={13}/><span>{enabled.length} скилла</span></button></div><button className={`send-button ${busy?'stop':''}`} onClick={busy?stop:send} disabled={!busy&&!input.trim()}>{busy?'Остановить':'Отправить'}{busy?<Square size={13}/>:<ArrowUp size={15}/>}</button></div></div>
        <div className="composer-note"><span><Shield size={11}/> {connected?'Запросы отправляются в бэкенд':'Демо-режим · подключите бэкенд для генерации'}</span><span><kbd>Enter</kbd> отправить <span className="note-dot">·</span> <kbd>Shift + Enter</kbd> новая строка</span></div>
        </>:tab==='skills'?<div className="skills-page"><div className="info-banner"><Sparkles size={17}/><p>Выбранные навыки дополняют промпт модели. Исполнение инструментов требует локального Python-агента.</p></div><div className="skill-grid">{skillDefs.map(s=><div className={`skill-card ${enabled.includes(s.id)?'enabled':''}`} key={s.id}><div className="skill-top"><div className="skill-icon"><s.icon size={22}/></div><span>{s.tag}</span></div><h3>{s.name}</h3><p>{s.text}</p><button className={enabled.includes(s.id)?'enabled-button':''} onClick={()=>toggleSkill(s.id)}>{enabled.includes(s.id)?<Check size={14}/>:<Plus size={14}/>} {enabled.includes(s.id)?'Подключён':'Подключить'}<span className={`toggle ${enabled.includes(s.id)?'on':''}`}><i/></span></button></div>)}</div></div>:<div className="files-page"><div className="file-page-heading"><div><h3>Комплект исправления превью</h3><p>6 файлов · инструкция и пример сервиса</p></div><button className="secondary-button" onClick={()=>download()}><Download size={14}/> Скачать ZIP</button></div><div className="file-workbench"><div className="file-list">{Object.keys(projectFiles).map(f=><button className={file===f?'active':''} key={f} onClick={()=>setFile(f)}>{f.endsWith('.py')?<FileCode2 size={14}/>:<FileText size={14}/>}<span>{f}</span></button>)}</div><div className="file-view"><div className="file-view-header"><span>{file}</span><button className="icon-button" title="Скопировать файл" onClick={()=>copy(projectFiles[file])}><Copy size={14}/></button></div><pre>{projectFiles[file].split('\n').map((line,i)=><div className="code-line" key={i}><span>{i+1}</span><code>{line||' '}</code></div>)}</pre></div></div><div className="info-banner"><Shield size={15}/><p>Файлы не применяются автоматически. Изучите README.md и сделайте резервную копию проекта.</p></div></div>}
        <section className={`console ${consoleOpen?'open':''}`}><button className="console-heading" onClick={()=>setConsoleOpen(!consoleOpen)}><div><Terminal size={15}/><strong>Консоль агентов</strong><span className="live-badge"><span className="dot"/> LIVE</span></div><div><span className="console-count">{logs.length} событий</span><ChevronDown size={14} className={!consoleOpen?'closed-chevron':''}/></div></button>{consoleOpen&&<><div className="console-output">{logs.slice(-4).map((l,i)=><div key={`${l}-${i}`}><span className="log-time">{String(i+1).padStart(2,'0')}</span><span className={l.includes('[ready]')||l.includes('[done]')?'log-green':''}>{l}</span></div>)}</div><button className="console-clear" title="Очистить консоль" onClick={()=>setLogs([])}><Trash2 size={12}/></button></>}</section>
        <footer className="main-footer"><span><span className="dot"/> {busy?'Обработка задачи':'Готов к работе'}</span><span>Создавайте смело. Проверяйте внимательно. <Sparkles size={11}/></span></footer>
        </main>
        <aside className="right-panel"><div className="preview-heading"><span><Monitor size={17}/> Live Preview <span className="beta-badge">BETA</span></span><button className="icon-button" title="О превью" onClick={()=>setModal('help')}><CircleHelp size={15}/></button></div><p className="preview-description">Запустите, проверьте и скачайте.<br/>Всё — не покидая рабочего пространства.</p><div className="preview-tabs"><button className={previewTab==='preview'?'active':''} onClick={()=>setPreviewTab('preview')}><CirclePlay size={14}/> Превью</button><button className={previewTab==='logs'?'active':''} onClick={()=>setPreviewTab('logs')}><Terminal size={14}/> Логи запуска <span>{repaired?'0':'1'}</span></button></div>
        <label className="field-label">Режим запуска <span>ⓘ</span></label><div className="launch-mode"><button className={previewMode==='direct'?'active':''} onClick={()=>{if(previewState==='idle')setPreviewMode('direct');}}><Zap size={14}/> Прямой запуск <span>Быстро</span></button><button className={previewMode==='docker'?'active':''} onClick={()=>{if(previewState==='idle')setPreviewMode('docker');}}><Box size={15}/> Docker</button></div>
        <button className={`launch-button ${previewState==='running'?'running':''}`} onClick={runPreview} disabled={previewState==='starting'}>{previewState==='starting'?<Loader2 size={15} className="spin"/>:previewState==='running'?<Square size={13}/>:<Play size={14} fill="currentColor"/>}{previewState==='running'?'Остановить превью':previewState==='starting'?'Подготовка…':'Запустить превью'}</button>
        <div className={`diagnostic ${repaired?'resolved':''}`}><div className="diagnostic-title">{repaired?<Check size={15}/>:<Wrench size={15}/>}<strong>{repaired?'Исправление подготовлено':'Окружению нужна помощь'}</strong><span className="dot"/></div><p>{repaired?'Скачайте комплект и примените на ПК. Здесь доступно демонстрационное превью.':<>В логе запуска отсутствует <code>uvicorn</code>.<br/>Doctor поможет подготовить исправление.</>}</p><button onClick={repaired?()=>{setTab('files');setFile('README.md');}:repair}>{repaired?<FileText size={13}/>:<WandSparkles size={13}/>} {repaired?'Посмотреть инструкцию':'Исправить окружение'}<ArrowRight size={13}/></button></div>
        <div className="preview-window">{previewTab==='preview'?previewContent:<div className="preview-logs"><div><Terminal size={14}/> Логи исходного запуска</div><pre>{'python: No module named uvicorn\n\ndocker: exec: "uvicorn": executable\nfile not found in $PATH\n\n'+(repaired?'[doctor] Комплект исправления готов.\nПримените изменения локально.':'[doctor] Runtime-зависимость отсутствует.\nНужна установка и пересборка.')}</pre><small>Из вашего сообщения · не живой лог ПК</small></div>}</div>
        <div className="export-card"><div><div className="export-icon"><Box size={20}/></div><span><strong>Заберите проект с собой</strong><small>Код, Dockerfile и инструкция</small></span></div><button className="export-button" onClick={()=>download()}><ArrowDownToLine size={15}/> Собрать и скачать .zip <Download size={13}/></button></div>
        <div className="hardware-card"><div className="hardware-heading"><span><Cpu size={15}/> Ваша система</span><span className="hardware-profile">Профиль</span></div><div className="hardware-item"><Cpu size={13}/><span>AMD Ryzen 7 5700G</span><small>8 ядер</small></div><div className="hardware-item"><Monitor size={13}/><span>Radeon RX 6600</span><small>8 GB</small></div><div className="hardware-item"><MemoryStick size={13}/><span>HyperX DDR4 · 3200 MHz</span><small>16 GB</small></div><div className="resource-label"><span>Рекомендуемый размер модели</span><strong>4.7 / 8 GB</strong></div><div className="resource-bar"><span/></div><div className="hardware-foot"><span><span className="dot"/> Manjaro Linux</span><button onClick={()=>setModal('system')}>Подробнее <ArrowRight size={11}/></button></div></div>
        </aside></div>
        </div>
        {toast&&<div className="toast" role="status"><Check size={17}/><span>{toast}</span><button className="icon-button" onClick={()=>setToast('')}><X size={14}/></button></div>}
        {editing&&<div className="modal-overlay" onClick={()=>setEditing(null)}><section className="modal rename-modal" onClick={e=>e.stopPropagation()}><div className="modal-header"><h2>Настройки сеанса</h2><button className="icon-button" onClick={()=>setEditing(null)}><X size={18}/></button></div><label className="field-label">Название сеанса</label><input className="text-input" autoFocus value={editTitle} onChange={e=>setEditTitle(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&editTitle.trim()){setSessions(s=>s.map(v=>v.id===editing?{...v,title:editTitle.trim()}:v));setEditing(null);}}}/><div className="modal-actions"><button className="danger-button" onClick={()=>{deleteSession(editing);setEditing(null);}}><Trash2 size={14}/> Удалить</button><button className="primary-button" disabled={!editTitle.trim()} onClick={()=>{setSessions(s=>s.map(v=>v.id===editing?{...v,title:editTitle.trim()}:v));setEditing(null);}}>Сохранить</button></div></section></div>}
        {modal&&<div className="modal-overlay" onClick={()=>setModal(null)}><section className="modal" role="dialog" aria-modal="true" aria-label={modal==='settings'?'Настройки':'Информация'} onClick={e=>e.stopPropagation()}><div className="modal-header"><div><span className="eyebrow">POPCAT PRO</span><h2>{modal==='settings'?'Ваше локальное пространство':modal==='system'?'Профиль вашей системы':'Как работает PopCat Pro'}</h2></div><button className="icon-button" title="Закрыть" onClick={()=>setModal(null)}><X size={20}/></button></div>{modal==='settings'?<><div className="settings-tabs"><button className={settingTab==='models'?'active':''} onClick={()=>setSettingTab('models')}><Cpu size={15}/> Модели агентов</button><button className={settingTab==='connection'?'active':''} onClick={()=>setSettingTab('connection')}><Globe size={15}/> Подключение</button></div>{settingTab==='models'?<><p className="settings-description">Каталог из вашего списка. Для 8 GB VRAM начните с одной Qwen 2.5 Coder 7B Q4 и контекста 4096.</p>{roles.map((r,i)=><label className="model-setting" key={r.name}><span><r.icon size={16}/>{r.name}</span><select value={selectedModels[i]} onChange={e=>setSelectedModels(s=>s.map((m,j)=>i===j?e.target.value:m))}>{[...new Set([...modelList,selectedModels[i]])].filter(n=>!n.startsWith('nomic-embed')).map(m=><option key={m}>{m}</option>)}</select></label>)}<div className="info-banner"><MemoryStick size={17}/><p>Один активный запрос. Размер модели на диске не равен потреблению VRAM. Клиент не запускает CrewAI-конвейер.</p></div></>:<div className="connection-form"><label className="field-label">Адрес API бэкенда</label><input className="text-input" value={endpoint} onChange={e=>{setEndpoint(e.target.value);setConnected(false);}} placeholder="http://127.0.0.1:7860"/><p>Введите URL вашего Gradio бэкенда: http://127.0.0.1:7860</p><button className="secondary-button" disabled={checking} onClick={checkConnection}>{checking?<Loader2 size={15} className="spin"/>:<Activity size={15}/>} {checking?'Проверяем…':'Проверить подключение'}</button><div className={`connection-result ${connected?'success':''}`}><span className="dot"/>{connected?'Подключено. Доступна генерация ответов.':'Не подключено. Используется деморежим.'}</div><button className="text-button" onClick={()=>{setConnected(false);notify('Демонстрационный режим включён');}}>Использовать без подключения</button></div>}<div className="modal-actions"><button className="secondary-button" onClick={()=>setModal(null)}>Закрыть</button><button className="primary-button" onClick={()=>{localStorage.setItem('popcat-models',JSON.stringify(selectedModels));localStorage.setItem('popcat-endpoint',endpoint);setModal(null);notify('Настройки сохранены');}}><Check size={14}/> Сохранить настройки</button></div></>:modal==='system'?<div className="system-details"><p>Характеристики из вашего сообщения, а не телеметрия устройства.</p>{[['Операционная система','Manjaro Linux'],['Процессор','AMD Ryzen 7 5700G · 8 ядер / 16 потоков'],['Видеокарта','Sapphire Pulse Radeon RX 6600 · 8 GB'],['Оперативная память','HyperX Predator 2 × 8 GB DDR4 · 3200 MHz'],['Накопитель','Apacer AS2280Q4U · PCIe Gen4 · 1 TB']].map(([a,b])=><div className="spec-row" key={a}><span>{a}</span><strong>{b}</strong></div>)}<div className="info-banner"><Zap size={19}/><p>Рекомендуем 7B Q4, контекст 4096 и последовательную обработку. Ускорение RX 6600 зависит от драйверов и backend Ollama — проверьте ollama ps.</p></div><button className="primary-button" onClick={()=>{setSettingTab('models');setModal('settings');}}>Настроить модели <ArrowRight size={14}/></button></div>:<div className="help-content"><p>Рабочее пространство для ваших локальных моделей. История и настройки сохраняются в этом браузере.</p><h3>1. Подключите бэкенд</h3><p>В настройках укажите URL Gradio API и проверьте соединение. Без подключения доступны демонстрационные ответы.</p><h3>2. Опишите задачу</h3><p>Выберите режим и навыки. Подключённый клиент отправляет запрос в Gradio бэкенд.</p><h3>3. Исправьте локальное превью</h3><p>Нажмите «Исправить окружение», изучите файлы и скачайте ZIP. README объясняет, как установить Uvicorn и интегрировать launcher. Превью в браузере — интерактивный пример, не ваш локальный сервис.</p><button className="primary-button" onClick={()=>{setModal(null);setTab('files');setFile('README.md');}}><BookOpen size={15}/> Открыть инструкцию</button></div>}</section></div>}
        {fullPreview&&<div className="modal-overlay" onClick={()=>setFullPreview(false)}><section className="modal full-preview" onClick={e=>e.stopPropagation()}><div className="modal-header"><h2>Live Preview <span className="beta-badge">ДЕМО</span></h2><button className="icon-button" onClick={()=>setFullPreview(false)}><X size={20}/></button></div><div className="preview-window">{previewContent}</div></section></div>}
        </div>;
}
export default App;
