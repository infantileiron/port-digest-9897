import {state} from '../stores/session.js';
import {api,post} from '../api/client.js';
import {esc} from '../utils/html.js';
import {local} from '../utils/dates.js';
import {DIFF_STATUS,ACT_LABEL} from './sync.js';

const STEPS=[['station_out','改站移出北站'],['revise_twice','同一班次连改两次'],['new_and_late','补录实际并新增班次'],['station_back','改站移回北站']];

let rerender=()=>{};let rebind=()=>{};
export function render(){
 let dr=state.drill;
 let list=(state.drills||[]).map(r=>`<button class="study" data-drillpick="${r.id}">#${r.id} ${esc(r.status)} · ${esc(local(r.created_at))}</button>`).join('');
 return `<div class="split"><section class="panel"><h2>${dr?'演练 #'+dr.drill.id+' 各端进度与差异':'选择或创建一次接收端演练'}</h2>
 <p class="notice">演练使用独立的港站数据副本，不污染真实台账。观察改站往返、断连重连，以及“落库后、确认前”中断后的断点续跑；两个接收端各自推进，慢端不影响快端。</p>
 ${dr?drillBody(dr):'<p>暂无演练。可在右侧新建，或到“增量同步”页创建。</p>'}
 </section><aside class="side"><h2>历史演练</h2>${list||'<p>尚无演练。</p>'}<p><button id="drill-create2">新建演练</button></p><p class="eyebrow">每个接收端一个 SQLite 台账文件，路径见各端卡片。</p></aside></div>`;
}

function drillBody(dr){
 let recs=dr.receivers||[];let steps=(dr.timeline||[]);
 return `<p>${STEPS.map(([k,t])=>`<button data-drill-step="${k}">${t}</button>`).join(' ')}</p>
 ${steps.length?`<div class="scroll"><table><thead><tr><th>变更步</th><th>序号</th><th>事件</th><th>说明</th></tr></thead><tbody>${steps.map(s=>`<tr><td>${esc(s.label)}</td><td>${esc(String(s.seq_from)+'–'+String(s.seq_to))}</td><td>${esc(s.server_events)}</td><td>${esc(s.note)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="notice">尚未制造变更。先让两端各追平一次，再逐步制造改站、连改与新增。</p>'}
 ${recs.map(r=>`<div class="rec"><strong>${esc(r.receiver)}</strong> ${r.offline?'<span class="tag">断连</span>':'<span class="tag">在线</span>'}
 <div>服务端已确认 ${String(r.cursor_seq)} / 最新 ${String(r.head_seq)} · 落后 ${String(r.lag)} · 台账 ${String(r.records)} 条 · 对账 ${DIFF_STATUS[r.diff.status]||r.diff.status}</div>
 <div class="eyebrow">本端落库 applied ${String(r.ledger.applied_seq??'—')} · 已确认 confirmed ${String(r.ledger.confirmed_seq??'—')} · 本地 ${String(r.ledger.records??'—')} 条</div>
 <div class="eyebrow">台账文件 ${esc(r.db||'—')}</div>
 <p>${Object.entries(ACT_LABEL).map(([k,t])=>`<button data-drill-act="${esc(r.receiver)}|${k}">${t}</button>`).join(' ')}<button data-drill-act="${esc(r.receiver)}|${r.offline?'reconnect':'offline'}">${r.offline?'重连':'断开'}</button></p>
 ${r.diff.details&&r.diff.details.length?`<div class="notice">差异 ${String(r.diff.details.length)} 条：<br>${r.diff.details.slice(0,8).map(d=>esc(d.code+'：'+d.problem+(d.fields&&d.fields.length?'（'+d.fields.join(',')+'）':''))).join('<br>')}</div>`:''}
 </div>`).join('')}`;
}

export function bind(render,bind){rerender=render;rebind=bind;
 let create=document.querySelector('#drill-create2');
 if(create)create.onclick=async()=>{try{let run=await post('/sync-drills',{});state.drills=await api('/sync-drills');state.drillId=run.id;state.drill=await api('/sync-drills/'+run.id);rerender();rebind()}catch(e){mapError(e)}};
 document.querySelectorAll('[data-drillpick]').forEach(b=>b.onclick=async()=>{state.drillId=Number(b.dataset.drillpick);state.drill=await api('/sync-drills/'+state.drillId);rerender();rebind()});
 document.querySelectorAll('[data-drill-step]').forEach(b=>b.onclick=async()=>{try{await post('/sync-drills/'+state.drill.drill.id+'/steps/'+b.dataset.drillStep,{});state.drill=await api('/sync-drills/'+state.drill.drill.id);rerender();rebind()}catch(e){mapError(e)}});
 document.querySelectorAll('[data-drill-act]').forEach(b=>b.onclick=async()=>{try{let [recv,act]=b.dataset.drillAct.split('|');await post('/sync-drills/'+state.drill.drill.id+'/receivers/'+encodeURIComponent(recv)+'/actions/'+act,{});state.drill=await api('/sync-drills/'+state.drill.drill.id);rerender();rebind()}catch(e){mapError(e)}});
}
function mapError(e){let n=document.querySelector('#error');if(n)n.textContent=e.message}
