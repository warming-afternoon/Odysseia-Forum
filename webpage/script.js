(function(){
	"use strict";

	/** 数据与状态 **/
	const state = {
		all: [],
		filtered: [],
		page: 1,
		perPage: 24,
		sort: "relevance",
		query: "",
		channels: new Set(),
		includeTags: new Set(),
		excludeTags: new Set(),
		tagLogic: "AND",
		timeFrom: null,
		timeTo: null
	};

	/** DOM **/
	const el = {
		keyword: document.getElementById("keywordInput"),
		searchBtn: document.getElementById("searchBtn"),
		// custom dropdowns
		chWrap: document.getElementById("channelDropdown"),
		inWrap: document.getElementById("includeDropdown"),
		exWrap: document.getElementById("excludeDropdown"),
		tagLogic: document.getElementById("tagLogic"),
		timeFrom: document.getElementById("timeFrom"),
		timeTo: document.getElementById("timeTo"),
		sort: document.getElementById("sortSelect"),
		perPage: document.getElementById("perPage"),
		stats: document.getElementById("resultCount"),
		buildTime: document.getElementById("buildTime"),
		results: document.getElementById("results"),
		pagination: document.getElementById("pagination"),
		applyBtn: document.getElementById("applyBtn"),
		resetBtn: document.getElementById("resetBtn")
	};

	/** 工具函数 **/
	const fmtDate = (d)=> {
		if(!d) return "";
		// 确保正确解析 UTC 时间字符串
		// 如果字符串不包含时区信息，需要明确指定为 UTC
		let dt;
		if(typeof d === 'string'){
			// 如果字符串不以 Z 结尾且不包含时区偏移，添加 Z 表示 UTC
			if(!d.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(d)){
				dt = new Date(d + 'Z');
			} else {
				dt = new Date(d);
			}
		} else {
			dt = new Date(d);
		}
		
		const now = Date.now();
		const diff = now - dt.getTime();
		
		// 处理未来时间（可能由于时区问题）
		if(diff < 0) return "刚刚";
		
		const sec = Math.floor(diff / 1000);
		const min = Math.floor(sec / 60);
		const hour = Math.floor(min / 60);
		const day = Math.floor(hour / 24);
		
		if(sec < 60) return "刚刚";
		if(min < 60) return `${min}分钟前`;
		if(hour < 24) return `${hour}小时前`;
		if(day < 7) return `${day}天前`;
		
		const year = dt.getFullYear();
		const month = dt.getMonth() + 1;
		const date = dt.getDate();
		const thisYear = new Date().getFullYear();
		
		if(year === thisYear) return `${month}月${date}日`;
		return `${year}年${month}月${date}日`;
	};
	const debounce = (fn,ms)=>{ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; };
	function escapeHtml(s){ return (s==null?"":String(s)).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }
	function escapeAttr(s){ return String(s||"").replace(/"/g,'&quot;'); }
	
	/** 简单的 Markdown 渲染器 **/
	function renderMarkdown(text){
		if(!text) return "";
		let html = escapeHtml(text);
		
		// Discord 自定义表情 <:emoji_name:emoji_id> 或 <a:emoji_name:emoji_id> (动画表情)
		html = html.replace(/&lt;a?:([^:]+):(\d+)&gt;/g, '<img class="discord-emoji" src="https://cdn.discordapp.com/emojis/$2.webp" alt=":$1:" title=":$1:" loading="lazy">');
		
		// 代码块 ```code```
		html = html.replace(/```([^`]+)```/g, '<pre><code>$1</code></pre>');
		
		// 行内代码 `code`
		html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
		
		// 粗体 **bold** 或 __bold__
		html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
		html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
		
		// 斜体 *italic* 或 _italic_
		html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
		html = html.replace(/_([^_]+)_/g, '<em>$1</em>');
		
		// 删除线 ~~strikethrough~~
		html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');
		
		// 链接 [text](url)
		html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
		
		// 标题 # Header
		html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
		html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
		html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
		
		// 引用 > quote
		html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
		
		// 无序列表 - item 或 * item
		html = html.replace(/^[*-] (.+)$/gm, '<li>$1</li>');
		html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
		
		// 换行
		html = html.replace(/\n/g, '<br>');
		
		return html;
	}

	/** URL 状态同步 **/
	function readFromURL(){
		const p = new URLSearchParams(location.search);
		state.query = p.get("q") || "";
		state.sort = p.get("sort") || "relevance";
		state.page = +(p.get("page") || 1) || 1;
		state.perPage = +(p.get("pp") || 24) || 24;
		state.channels = new Set((p.get("ch")||"").split("|").filter(Boolean).map(x=>x.trim()));
		state.includeTags = new Set((p.get("ti")||"").split("|").filter(Boolean).map(x=>x.trim()));
		state.excludeTags = new Set((p.get("te")||"").split("|").filter(Boolean).map(x=>x.trim()));
		state.tagLogic = p.get("tl") || "AND";
		state.timeFrom = p.get("tf") ? new Date(+p.get("tf")) : null;
		state.timeTo = p.get("tt") ? new Date(+p.get("tt")) : null;
	}
	function writeToURL(replace=false){
		const p = new URLSearchParams();
		if(state.query) p.set("q", state.query);
		if(state.sort && state.sort!=="relevance") p.set("sort", state.sort);
		if(state.page>1) p.set("page", String(state.page));
		if(state.perPage!==24) p.set("pp", String(state.perPage));
		if(state.channels.size) p.set("ch", [...state.channels].join("|"));
		if(state.includeTags.size) p.set("ti", [...state.includeTags].join("|"));
		if(state.excludeTags.size) p.set("te", [...state.excludeTags].join("|"));
		if(state.tagLogic && state.tagLogic!=="AND") p.set("tl", state.tagLogic);
		if(state.timeFrom) p.set("tf", String(+state.timeFrom));
		if(state.timeTo) p.set("tt", String(+state.timeTo));
		const url = `${location.pathname}?${p.toString()}`;
		history[replace?"replaceState":"pushState"]({}, "", url);
	}

	/** 渲染选中值到按钮文本 **/
	function setMultiLabel(wrap, items){
		const label = wrap.querySelector('.multi-label');
		if(!items.size){ label.textContent = wrap===el.chWrap? '全部频道' : '不限'; return; }
		label.textContent = [...items].slice(0,3).join(', ') + (items.size>3? ` 等${items.size}项` : '');
	}

	/** 初始化频道与标签选项（菜单内容） **/
	function initChannels(all){
		const byId = window.CHANNELS || {};
		const discovered = new Set(all.map(x=>String(x.channel_id)));
		const options = [];
		discovered.forEach(id=>{ options.push({id, name: byId[id] || `频道 ${id}`}); });
		options.sort((a,b)=> a.name.localeCompare(b.name, 'zh-Hans'));
		const menu = el.chWrap.querySelector('.multi-menu');
		menu.innerHTML = options.map(o=>`<label class="multi-option"><input type="checkbox" value="${o.id}"><span>${escapeHtml(o.name)}</span></label>`).join('');
		// 恢复 URL 选择
		menu.querySelectorAll('input[type=checkbox]').forEach(cb=>{ cb.checked = state.channels.has(cb.value); });
		setMultiLabel(el.chWrap, state.channels.size? new Set([...state.channels].map(id=> (byId[id]||`频道 ${id}`))) : new Set());
	}

	function computeAvailableTags(){
		// 基于所选频道集合（若未选择则全部频道）来收集所有帖子 tags
		const allowAll = state.channels.size===0;
		const tagSet = new Set();
		state.all.forEach(item=>{
			if(allowAll || state.channels.has(String(item.channel_id))){
				(item.tags||[]).forEach(t=> tagSet.add(t));
			}
		});
		return [...tagSet].sort((a,b)=> a.localeCompare(b,'zh-Hans'));
	}
	function refreshTagMenus(){
		const tags = computeAvailableTags();
		const html = tags.map(t=>`<label class="multi-option"><input type="checkbox" value="${escapeAttr(t)}"><span>${escapeHtml(t)}</span></label>`).join('');
		const menuIn = el.inWrap.querySelector('.multi-menu');
		const menuEx = el.exWrap.querySelector('.multi-menu');
		menuIn.innerHTML = html; menuEx.innerHTML = html;
		// 恢复
		menuIn.querySelectorAll('input').forEach(i=> i.checked = state.includeTags.has(i.value));
		menuEx.querySelectorAll('input').forEach(i=> i.checked = state.excludeTags.has(i.value));
		setMultiLabel(el.inWrap, state.includeTags);
		setMultiLabel(el.exWrap, state.excludeTags);
	}

	/** 搜索与排序 **/
	function normalize(text){ return (text||"").toLowerCase().replace(/[\u3000\s]+/g," ").trim(); }
	
	// 解析高级搜索语法：author:xxx "精确匹配" -排除
	function parseQuery(q){
		const parsed = { authors: [], exact: [], include: [], exclude: [] };
		if(!q) return parsed;
		
		// 匹配模式：author:xxx "quoted" -exclude word
		const regex = /author:(\S+)|"([^"]+)"|-(\S+)|(\S+)/gi;
		let match;
		while((match = regex.exec(q)) !== null){
			if(match[1]){ // author:xxx
				parsed.authors.push(normalize(match[1]));
			} else if(match[2]){ // "精确匹配"
				parsed.exact.push(normalize(match[2]));
			} else if(match[3]){ // -排除
				parsed.exclude.push(normalize(match[3]));
			} else if(match[4]){ // 普通词
				parsed.include.push(normalize(match[4]));
			}
		}
		return parsed;
	}
	
	function matchesQuery(item, parsed){
		const title = normalize(item.title);
		const author = normalize(item.author);
		const excerpt = normalize(item.first_message_excerpt);
		const combined = title + " " + excerpt;
		
		// 检查 author: 完全匹配
		if(parsed.authors.length > 0){
			const found = parsed.authors.some(a=> author === a);
			if(!found) return false;
		}
		
		// 检查精确匹配
		for(const ex of parsed.exact){
			if(!combined.includes(ex) && !title.includes(ex)) return false;
		}
		
		// 检查排除词
		for(const ex of parsed.exclude){
			if(combined.includes(ex) || title.includes(ex) || author.includes(ex)) return false;
		}
		
		// 检查包含词（全部必须匹配）
		for(const inc of parsed.include){
			if(!combined.includes(inc) && !title.includes(inc) && !author.includes(inc)) return false;
		}
		
		return true;
	}
	
	function keywordScore(item, parsed){
		if(parsed.authors.length===0 && parsed.exact.length===0 && parsed.include.length===0) return 0;
		const title = normalize(item.title);
		const author = normalize(item.author);
		const excerpt = normalize(item.first_message_excerpt);
		let score = 0;
		
		// author: 匹配加分
		for(const a of parsed.authors){ if(author.includes(a)) score += 10; }
		
		// 精确匹配高分
		for(const ex of parsed.exact){
			if(title.includes(ex)) score += 8;
			else if(excerpt.includes(ex)) score += 4;
		}
		
		// 普通词匹配
		for(const inc of parsed.include){
			if(title.includes(inc)) score += 5;
			else if(author.includes(inc)) score += 3;
			else if(excerpt.includes(inc)) score += 2;
		}
		
		// 时间加权
		if(item.last_active_at){
			const d=(Date.now()-new Date(item.last_active_at).getTime())/86400000;
			score += Math.max(0,5-Math.min(5,d/7));
		}
		return score;
	}
	
	function includesAllTags(itemTags, required){ for(const t of required){ if(!itemTags.includes(t)) return false; } return true; }
	function includesAnyTag(itemTags, required){ for(const t of required){ if(itemTags.includes(t)) return true; } return false; }
	function excludesAnyTags(itemTags, banned){ for(const t of banned){ if(itemTags.includes(t)) return true; } return false; }

	function applyFilters(){
		const parsed = parseQuery(state.query);
		const chSet = state.channels;
		const inc = [...state.includeTags].map(normalize);
		const exc = [...state.excludeTags].map(normalize);
		const from = state.timeFrom; const to = state.timeTo;

		let res = state.all.filter(item=>{
			if(chSet.size && !chSet.has(String(item.channel_id))) return false;
			const itemTags = (item.tags||[]).map(normalize);
			// 标签逻辑：AND 全部包含 / OR 任一即可
			if(inc.length){
				if(state.tagLogic === "AND"){
					if(!includesAllTags(itemTags, inc)) return false;
				} else {
					if(!includesAnyTag(itemTags, inc)) return false;
				}
			}
			if(exc.length && excludesAnyTags(itemTags, exc)) return false;
			// 时间筛选：只基于发帖时间（created_at）
			if(from || to){
				if(!item.created_at) return false;
				// 正确解析 UTC 时间
				let createdDate;
				if(typeof item.created_at === 'string'){
					if(!item.created_at.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(item.created_at)){
						createdDate = new Date(item.created_at + 'Z');
					} else {
						createdDate = new Date(item.created_at);
					}
				} else {
					createdDate = new Date(item.created_at);
				}
				// 不早于：发帖时间 >= from 的开始（本地时区）
				if(from && createdDate < startOfDay(from)) return false;
				// 不晚于：发帖时间 <= to 的结束（本地时区）
				if(to && createdDate > endOfDay(to)) return false;
			}
			// 使用高级搜索逻辑
			if(state.query && !matchesQuery(item, parsed)) return false;
			return true;
		});

		switch(state.sort){
			case "relevance":
				res = res.map(x=>({item:x, s:keywordScore(x, parsed)})).sort((a,b)=> b.s - a.s || new Date(b.item.last_active_at||0)-new Date(a.item.last_active_at||0)).map(x=>x.item);
				break;
			case "last_active_desc": res.sort((a,b)=> new Date(b.last_active_at||0)-new Date(a.last_active_at||0)); break;
			case "created_desc": res.sort((a,b)=> new Date(b.created_at||0)-new Date(a.created_at||0)); break;
			case "reply_desc": res.sort((a,b)=> (b.reply_count||0)-(a.reply_count||0)); break;
			case "reaction_desc": res.sort((a,b)=> (b.reaction_count||0)-(a.reaction_count||0)); break;
		}

		state.filtered = res;
	}

	function startOfDay(d){ const x = new Date(d); x.setHours(0,0,0,0); return x; }
	function endOfDay(d){ const x = new Date(d); x.setHours(23,59,59,999); return x; }

	/** 渲染 **/
	function render(){
		const total = state.filtered.length;
		const pages = Math.max(1, Math.ceil(total / state.perPage));
		if(state.page>pages) state.page = pages;
		const start = (state.page-1)*state.perPage;
		const slice = state.filtered.slice(start, start+state.perPage);
		el.stats.textContent = `共 ${total} 条结果 · 第 ${state.page}/${pages} 页`;

		el.results.innerHTML = slice.map(renderCard).join("");
		renderPagination(state.page, pages);
		// 基于所选频道刷新可选标签
		refreshTagMenus();
		
		// 滚动到页面顶部
		window.scrollTo({top: 0, behavior: 'smooth'});
	}

	function renderCard(item){
		const imgHtml = item.thumbnail_url ? `<div class="media-img"><img src="${escapeAttr(item.thumbnail_url)}" alt="${escapeAttr(item.title)} 缩略图" loading="lazy" class="card-img" data-src="${escapeAttr(item.thumbnail_url)}"></div>` : `<div class="media-img"></div>`;
		const excerptText = limitText(item.first_message_excerpt||"", item.thumbnail_url ? 500 : 800);
		const excerptHtml = `<div class="excerpt markdown-content">${renderMarkdown(excerptText)}</div>`;
		const channelName = (window.CHANNELS||{})[String(item.channel_id)] || `频道 ${item.channel_id}`;
		const created = fmtDate(item.created_at);
		const active = fmtDate(item.last_active_at);
		const authorName = item.author || "未知作者";
		const guildId = window.GUILD_ID || "1134557553011998840";
		return `
		<article class="card" tabindex="0">
			<div class="card-media">${imgHtml}${excerptHtml}</div>
			<div class="card-body">
				<h2 class="card-title" title="${escapeAttr(item.title)}">${highlight(item.title)}</h2>
				<div class="card-meta">
					<span class="badge" title="频道"><span class="dot"></span>${escapeHtml(channelName)}</span>
					<span class="badge badge-author" title="点击搜索该作者" data-author="${escapeAttr(authorName)}">👤 ${escapeHtml(authorName)}</span>
					<span class="badge" title="发布时间 ${new Date(item.created_at).toLocaleString()}">🕒 ${escapeHtml(created)}</span>
					<span class="badge" title="最近活跃 ${new Date(item.last_active_at).toLocaleString()}">🔥 ${escapeHtml(active)}</span>
					<span class="badge" title="回复">💬 ${escapeHtml(item.reply_count||0)}</span>
					<span class="badge" title="反应">⭐ ${escapeHtml(item.reaction_count||0)}</span>
				</div>
				<div class="tags">${(item.tags||[]).map(t=>`<span class="tag"># ${escapeHtml(t)}</span>`).join("")}</div>
			</div>
			<div class="card-actions">
				<div class="left"> </div>
				<div class="right"><a class="link discord-link" href="#" data-guild="${guildId}" data-channel="${item.channel_id}" data-thread="${item.thread_id}">打开原帖 →</a></div>
			</div>
		</article>`;
	}
	function limitText(s,n){ if(!s) return ""; return s.length>n? s.slice(0,n-1)+"…" : s; }
	function highlight(text){
		const parsed = parseQuery(state.query);
		const allTokens = [...parsed.include, ...parsed.exact, ...parsed.authors].filter(t=>t.length>=2);
		if(allTokens.length===0) return escapeHtml(text||"");
		let html = escapeHtml(text||"");
		for(const t of allTokens){
			const rx=new RegExp(`(${escapeRegExp(t)})`,'ig');
			html=html.replace(rx,'<mark>$1</mark>');
		}
		return html;
	}
	function escapeRegExp(s){ return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

	function renderPagination(page, total){
		const btn = (p, label, disabled=false, current=false)=>`<button class="page-btn" ${disabled?"disabled":""} ${current?"aria-current=\"page\"":""} data-page="${p}">${label}</button>`;
		const items = [];
		items.push(btn(Math.max(1,page-1), "上一页", page<=1));
		const windowSize = 5; const start = Math.max(1, page - Math.floor(windowSize/2)); const end = Math.min(total, start + windowSize - 1);
		for(let i=start;i<=end;i++) items.push(btn(i, i, false, i===page));
		items.push(btn(Math.min(total,page+1), "下一页", page>=total));
		el.pagination.innerHTML = items.join("");
	}

	/** 自定义下拉：打开/关闭与选择同步 **/
	function setupMulti(wrap, onChange){
		const btn = wrap.querySelector('.multi-toggle');
		const menu = wrap.querySelector('.multi-menu');
		btn.addEventListener('click', (e)=>{
			const expanded = btn.getAttribute('aria-expanded') === 'true';
			btn.setAttribute('aria-expanded', String(!expanded));
		});
		document.addEventListener('click', (e)=>{
			if(!wrap.contains(e.target)) btn.setAttribute('aria-expanded','false');
		});
		menu.addEventListener('change', ()=> onChange());
	}

	function collectChecked(wrap){
		return new Set([...wrap.querySelectorAll('.multi-menu input:checked')].map(i=> i.value));
	}

	/** 事件绑定 **/
	function bindEvents(){
		setupMulti(el.chWrap, ()=>{
			state.channels = collectChecked(el.chWrap);
			setMultiLabel(el.chWrap, new Set([...state.channels].map(id=> (window.CHANNELS?.[id]||`频道 ${id}`))));
			// 频道变化 -> 清空标签筛选并刷新标签可选项
			state.includeTags.clear();
			state.excludeTags.clear();
			refreshTagMenus();
		});
		setupMulti(el.inWrap, ()=>{ state.includeTags = collectChecked(el.inWrap); setMultiLabel(el.inWrap, state.includeTags); });
		setupMulti(el.exWrap, ()=>{ state.excludeTags = collectChecked(el.exWrap); setMultiLabel(el.exWrap, state.excludeTags); });

		el.searchBtn.addEventListener('click', ()=>{ state.page=1; state.query=el.keyword.value; syncAndRender(); });
		el.keyword.addEventListener('input', debounce(()=>{ state.page=1; state.query=el.keyword.value; syncAndRender(true); }, 250));
		el.applyBtn.addEventListener('click', ()=>{ 
			state.page=1; 
			state.timeFrom = el.timeFrom.value ? new Date(el.timeFrom.value) : null;
			state.timeTo = el.timeTo.value ? new Date(el.timeTo.value) : null;
			syncAndRender(); 
		});
		el.resetBtn.addEventListener('click', ()=>{
			state.page=1; state.channels.clear(); state.includeTags.clear(); state.excludeTags.clear(); state.tagLogic="AND"; state.timeFrom=null; state.timeTo=null; state.query=""; state.sort="relevance"; state.perPage=24;
			// 清 UI
			el.keyword.value=""; el.sort.value="relevance"; el.perPage.value="24"; el.tagLogic.value="AND"; el.timeFrom.value=""; el.timeTo.value="";
			el.chWrap.querySelectorAll('input').forEach(i=> i.checked=false);
			el.inWrap.querySelectorAll('input').forEach(i=> i.checked=false);
			el.exWrap.querySelectorAll('input').forEach(i=> i.checked=false);
			setMultiLabel(el.chWrap, new Set()); setMultiLabel(el.inWrap, new Set()); setMultiLabel(el.exWrap, new Set());
			syncAndRender();
		});
		el.sort.addEventListener('change', ()=>{ state.sort=el.sort.value; state.page=1; syncAndRender(); });
		el.perPage.addEventListener('change', ()=>{ state.perPage=+el.perPage.value||24; state.page=1; syncAndRender(); });
		el.tagLogic.addEventListener('change', ()=>{ state.tagLogic=el.tagLogic.value; state.page=1; syncAndRender(); });
		el.pagination.addEventListener('click', (e)=>{ const b = e.target.closest('button[data-page]'); if(!b) return; const p = +b.getAttribute('data-page'); if(!isNaN(p)) { state.page = p; syncAndRender(); } });
		
		// 点击作者跳转搜索
		el.results.addEventListener('click', (e)=>{
			const authorBadge = e.target.closest('.badge-author');
			if(authorBadge){
				const author = authorBadge.getAttribute('data-author');
				if(author){
					state.query = `author:${author}`;
					el.keyword.value = state.query;
					state.page = 1;
					syncAndRender();
					window.scrollTo({top:0, behavior:'smooth'});
				}
				return;
			}
			
			// 点击标签添加到包含标签筛选
			const tag = e.target.closest('.tag');
			if(tag){
				const tagText = tag.textContent.trim().replace(/^#\s*/, '');
				if(tagText && !state.includeTags.has(tagText)){
					state.includeTags.add(tagText);
					// 更新 UI
					el.inWrap.querySelectorAll('input').forEach(i=> {
						if(i.value === tagText) i.checked = true;
					});
					setMultiLabel(el.inWrap, state.includeTags);
					state.page = 1;
					syncAndRender();
					window.scrollTo({top:0, behavior:'smooth'});
				}
				return;
			}
			
			// 点击图片弹出大图
			const img = e.target.closest('.card-img');
			if(img){
				const src = img.getAttribute('data-src');
				if(src) openImagePopup(src);
				return;
			}
			
			// 点击 Discord 链接
			const discordLink = e.target.closest('.discord-link');
			if(discordLink){
				e.preventDefault();
				const guild = discordLink.getAttribute('data-guild');
				const channel = discordLink.getAttribute('data-channel');
				const thread = discordLink.getAttribute('data-thread');
				openDiscordLink(guild, channel, thread);
				return;
			}
		});
		
		window.addEventListener('popstate', ()=>{ readFromURL(); hydrateControls(); applyFilters(); render(); });
	}
	
	/** Discord 链接跳转：优先唤起客户端 **/
	function openDiscordLink(guild, channel, thread){
		const appUrl = `discord://-/channels/${guild}/${thread}`;
		const webUrl = `https://discord.com/channels/${guild}/${thread}`;
		
		// 创建隐藏 iframe 尝试唤起客户端
		const iframe = document.createElement('iframe');
		iframe.style.display = 'none';
		iframe.src = appUrl;
		document.body.appendChild(iframe);
		
		// 设置超时：如果 1.5 秒内未成功唤起，则打开网页版
		let opened = false;
		const timeout = setTimeout(()=>{
			if(!opened){
				window.open(webUrl, '_blank', 'noopener,noreferrer');
			}
			document.body.removeChild(iframe);
		}, 1500);
		
		// 监听页面失焦（表示客户端成功唤起）
		const onBlur = ()=>{
			opened = true;
			clearTimeout(timeout);
			setTimeout(()=> document.body.removeChild(iframe), 100);
			window.removeEventListener('blur', onBlur);
		};
		window.addEventListener('blur', onBlur);
		
		// 备用：直接尝试打开 app URL
		window.location.href = appUrl;
	}
	
	/** 图片弹出层 **/
	function openImagePopup(src){
		const popup = document.createElement('div');
		popup.className = 'image-popup';
		popup.innerHTML = `
			<div class="popup-backdrop"></div>
			<div class="popup-content">
				<img src="${escapeAttr(src)}" alt="大图预览">
				<button class="popup-close" aria-label="关闭">✕</button>
			</div>
		`;
		document.body.appendChild(popup);
		
		const close = ()=>{ popup.remove(); };
		popup.querySelector('.popup-backdrop').addEventListener('click', close);
		popup.querySelector('.popup-close').addEventListener('click', close);
		document.addEventListener('keydown', function onEsc(e){ if(e.key==='Escape'){ close(); document.removeEventListener('keydown', onEsc); } });
	}

	function hydrateControls(){
		el.keyword.value = state.query;
		el.sort.value = state.sort;
		el.perPage.value = String(state.perPage);
		el.tagLogic.value = state.tagLogic;
		// 恢复频道和标签菜单
		el.chWrap.querySelectorAll('input').forEach(i=> i.checked = state.channels.has(i.value));
		el.inWrap.querySelectorAll('input').forEach(i=> i.checked = state.includeTags.has(i.value));
		el.exWrap.querySelectorAll('input').forEach(i=> i.checked = state.excludeTags.has(i.value));
		setMultiLabel(el.chWrap, new Set([...state.channels].map(id=> (window.CHANNELS?.[id]||`频道 ${id}`))));
		setMultiLabel(el.inWrap, state.includeTags);
		setMultiLabel(el.exWrap, state.excludeTags);
		el.timeFrom.value = state.timeFrom ? toISODate(state.timeFrom) : "";
		el.timeTo.value = state.timeTo ? toISODate(state.timeTo) : "";
	}
	function toISODate(d){ const x = new Date(d); x.setHours(0,0,0,0); return x.toISOString().slice(0,10); }

	function syncAndRender(replace=false){
		writeToURL(replace);
		applyFilters();
		render();
	}

	/** 数据加载 **/
	async function loadIndex(){
		const res = await fetch('index.json', {cache:'no-store'});
		const text = await res.text();
		// 使用自定义 JSON 解析，将大数字 ID 保持为字符串
		const data = JSON.parse(text, (key, value) => {
			// 将 ID 字段保持为字符串，避免精度丢失
			if((key === 'channel_id' || key === 'thread_id' || key === 'author_id') && typeof value === 'number'){
				return String(value);
			}
			return value;
		});
		state.all = data.map(x=>({
			channel_id: String(x.channel_id),
			thread_id: String(x.thread_id),
			title: x.title || "",
			author_id: String(x.author_id),
			author: x.author || "",
			created_at: x.created_at || "",
			last_active_at: x.last_active_at || "",
			reaction_count: x.reaction_count||0,
			reply_count: x.reply_count||0,
			first_message_excerpt: x.first_message_excerpt || "",
			thumbnail_url: x.thumbnail_url || "",
			tags: Array.isArray(x.tags)? x.tags : []
		}));
		// 初始化频道菜单与标签菜单
		initChannels(state.all);
		refreshTagMenus();
	}

	/** 构建时间 **/
	async function loadBuildTime(){ try{ document.getElementById('buildTime').textContent = new Date().toLocaleString(); }catch{} }

	/** 启动 **/
	(async function init(){
		readFromURL();
		await Promise.all([loadIndex(), loadBuildTime()]);
		hydrateControls();
		applyFilters();
		render();
		bindEvents();
	})();
})();
