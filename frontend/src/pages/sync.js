import {state} from '../stores/session.js';
import {api,post} from '../api/client.js';
import {table} from '../components/common/table.js';
import {field,select} from '../components/common/fields.js';
import {STATIONS} from '../constants/enums.js';
import {esc} from '../utils/html.js';
import {local} from '../utils/dates.js';

export const DIFF_STATUS={ok:'一致',mismatch:'有差异',unreported:'未对账'};
export const ACT_LABEL={sync_one:'同步 1 批',sync_all:'追平',crash_after_apply:'落库后中断',resume:'断点续跑',report:'上报对账',reset_ledger:'重建本端台账'};

let rerender=()=>{};let rebind=()=>{};
export function render(){
 const subs=state.syncSubs||[];
 const rows=subs.map(p=>[p.subscription.name,p.subscription.station,p.subscription.enabled?'启用':'暂停',String(p.cursor_seq)+' / '+String(p.head_seq),String(p.lag),p.bootstrapped?'已全量':'引导中',DIFF_STATUS[p.diff.status]||p.diff.status,String(p.station_count),String(p.pending_batches.length)]);
 return `<div class="split"><section class="panel">
 <h2>每个接收端独立推进，慢端不阻塞快端</h2>
 <p class="notice">每端持自己的接收端令牌，只订阅本港站台账。首次全量冻结当时的站台账，与后续增量在同一变更序号链上无缝衔接；班次改站先从原港站撤回，再在新港站补全；同一班次多次修订按提交先后逐次生效。批次从待接收到已确认，确认前可反复重取原内容；断点续跑按本端落库位置继续。</p>
 ${table(['订阅','港站','状态','已确认 / 最新','落后','全量','对账','服务端条数','待确认批次'],rows)}
 <p><button id="sync-refresh">刷新各端进度</button></p>
 <p class="notice">${subs.length?'':'尚无增量订阅，先在右侧生成接收端令牌。'}</p>
 </section><aside class="side"><h2>新建港站增量订阅</h2>
 <form id="sync-sub">${field('订阅名称','name','text','required minlength=2')}${select('港站','station',STATIONS.map(s=>[s,s]))}${field('接收端名称','receiver','text','required value="北站值班接收端"')}${select('启用','enabled',[['true','启用'],['false','暂停']])}<button class="primary">生成接收端令牌</button></form>
 <p class="notice">令牌仅创建/轮换时显示一次，作为接收端 Bearer 凭据，不是值班员登录。</p>
 <p class="eyebrow">命令行接收端<br>python -m receiver.cli --url http://backend:8000 --token psync_… --db 本端.sqlite --mode run</p>
 <p><button id="drill-create">创建接收端演练</button></p>
 </aside></div>`;
}

export function bind(render,bind){rerender=render;rebind=bind;
 let refresh=document.querySelector('#sync-refresh');
 if(refresh)refresh.onclick=async()=>{state.syncSubs=await api('/sync-subscriptions');rerender();rebind()};
 let form=document.querySelector('#sync-sub');
 if(form)form.onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(form));d.enabled=d.enabled==='true';try{let out=await post('/sync-subscriptions',d);state.syncSubs=await api('/sync-subscriptions');rerender();rebind();feedback('接收端令牌（只显示一次）：'+out.token)}catch(err){feedback(err.message)}};
 let create=document.querySelector('#drill-create');
 if(create)create.onclick=async()=>{try{let run=await post('/sync-drills',{});state.drillId=run.id;location.hash='5'}catch(e){feedback(e.message)}};
}
function feedback(t){let n=document.querySelector('#error');if(n)n.textContent=t}
