
(function(){
	"use strict";

    const url = new URL(window.location.href);
    const error = url.searchParams.get("error");
    if(error){
        alert("登录失败: " + error);
    }

	/** 数据与状态 **/
	const state = {
		filtered: [],
		total: 0,
		page: 1,
		perPage: 24,
		sort: "relevance",
		query: "",
		channels: new Set(),
		includeTags: new Set(),
		excludeTags: new Set(),
		tagLogic: "AND",
		timeFrom: null,
		timeTo: null,
		authed: true,
		loading: false,
		availableChannels: new Map(),
		availableTags: new Set()
	};

	// iOS 兼容：从回调 URL 片段中获取 token 并持久化
	(function hydrateAuthToken(){
		try{
			const m = location.hash && location.hash.match(/[#&]token=([^&]+)/);
			const fromHash = m ? decodeURIComponent(m[1]) : null;
			const fromStorage = window.localStorage.getItem('auth_token') || null;
			if(fromHash){
				window.AUTH_TOKEN = fromHash;
				window.localStorage.setItem('auth_token', fromHash);
				// 清理 hash，避免泄露
				history.replaceState({}, '', location.pathname + location.search);
			}else if(fromStorage){
				window.AUTH_TOKEN = fromStorage;
			}
		}catch{}
	})();

	function authHeaders(){
		const h = {};
		if(window.AUTH_TOKEN){ h['Authorization'] = 'Bearer ' + window.AUTH_TOKEN; }
		return h;
	}

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
		let dt;
		if(typeof d === 'string'){
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
		
		// Discord 自定义表情
		html = html.replace(/&lt;a?:([^:]+):(\d+)&gt;/g, '<img class="discord-emoji" src="https://cdn.discordapp.com/emojis/$2.webp" alt=":$1:" title=":$1:" loading="lazy">');
		
		// 代码块
		html = html.replace(/```([^`]+)```/g, '<pre><code>$1</code></pre>');
		html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
		
		// 粗体
		html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
		html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
		
		// 斜体
		html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
		html = html.replace(/_([^_]+)_/g, '<em>$1</em>');
		
		// 删除线
		html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');
		
		// 链接
		html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
		
		// 标题
		html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
		html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
		html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
		
		// 引用
		html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
		
		// 无序列表
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

	/** 初始化频道选项 **/
	async function initChannels(){
		const byId = window.CHANNELS || {};
		state.availableChannels = new Map(Object.entries(byId).map(([id, name]) => [id, name]));
		
		const options = Array.from(state.availableChannels.entries())
			.map(([id, name]) => ({id, name}))
			.sort((a,b)=> a.name.localeCompare(b.name, 'zh-Hans'));
		
		const menu = el.chWrap.querySelector('.multi-menu');
		menu.innerHTML = options.map(o=>`<label class="multi-option"><input type="checkbox" value="${o.id}"><span>${escapeHtml(o.name)}</span></label>`).join('');
		
		menu.querySelectorAll('input[type=checkbox]').forEach(cb=>{ cb.checked = state.channels.has(cb.value); });
		setMultiLabel(el.chWrap, state.channels.size? new Set([...state.channels].map(id=> (byId[id]||`频道 ${id}`))) : new Set());
	}

	/** 初始化标签选项（从后端元数据获取） **/
	async function initTags(){
		// 这里可以从后端 API 获取可用标签列表
		// 暂时使用空集合，实际使用时可以调用 /v1/meta/tags 等接口
		refreshTagMenus();
	}

	function refreshTagMenus(){
		const tags = [...state.availableTags].sort((a,b)=> a.localeCompare(b,'zh-Hans'));
		const html = tags.map(t=>`<label class="multi-option"><input type="checkbox" value="${escapeAttr(t)}"><span>${escapeHtml(t)}</span></label>`).join('');
		const menuIn = el.inWrap.querySelector('.multi-menu');
		const menuEx = el.exWrap.querySelector('.multi-menu');
		menuIn.innerHTML = html; menuEx.innerHTML = html;
		
		menuIn.querySelectorAll('input').forEach(i=> i.checked = state.includeTags.has(i.value));
		menuEx.querySelectorAll('input').forEach(i=> i.checked = state.excludeTags.has(i.value));
		setMultiLabel(el.inWrap, state.includeTags);
		setMultiLabel(el.exWrap, state.excludeTags);
	}

	/** 构建搜索请求参数 **/
	function buildSearchRequest(){
		const sortMap = {
			"relevance": { method: "comprehensive", order: "desc" },
			"last_active_desc": { method: "last_active", order: "desc" },
			"created_desc": { method: "created_at", order: "desc" },
			"reply_desc": { method: "reply_count", order: "desc" },
			"reaction_desc": { method: "reaction_count", order: "desc" }
		};
		const sortConfig = sortMap[state.sort] || sortMap["relevance"];
		
		return {
			channel_ids: state.channels.size > 0 ? Array.from(state.channels).map(id => parseInt(id)) : null,
			include_tags: Array.from(state.includeTags),
			exclude_tags: Array.from(state.excludeTags),
			tag_logic: state.tagLogic.toLowerCase(),
			keywords: state.query || null,
			created_after: state.timeFrom ? formatDateForAPI(state.timeFrom) : null,
			created_before: state.timeTo ? formatDateForAPI(state.timeTo) : null,
			sort_method: sortConfig.method,
			sort_order: sortConfig.order,
			limit: state.perPage,
			offset: (state.page - 1) * state.perPage
		};
	}

	function formatDateForAPI(date){
		const d = new Date(date);
		const year = d.getFullYear();
		const month = String(d.getMonth() + 1).padStart(2, '0');
		const day = String(d.getDate()).padStart(2, '0');
		return `${year}-${month}-${day}`;
	}

	/** 从服务端获取搜索结果 **/
	async function fetchSearchResults(){
		if(state.loading) return;
		
		state.loading = true;
		showLoadingPlaceholders();
		
		try{
			const searchRequest = buildSearchRequest();
			const res = await fetch(window.AUTH_URL + '/api/search', {
				method: 'POST',
				credentials: 'include',
				headers: {
					'Content-Type': 'application/json',
					...authHeaders()
				},
				body: JSON.stringify(searchRequest)
			


			});
			
			if(!res || res.status === 401){
				state.authed = false;
				state.loading = false;
				render();
				return;
			}
			
			if(!res.ok){
				console.error('搜索请求失败:', res.status);
				state.loading = false;
				el.stats.textContent = "搜索失败，请稍后重试";
				el.results.innerHTML = '<div class="error-message">搜索失败，请稍后重试</div>';
				return;
			}
			
			const data = await res.json();
			state.filtered = data.results || [];
			state.total = data.total || 0;
			
			// 从结果中提取可用标签
			const tagsSet = new Set();
			state.filtered.forEach(item => {
				if(Array.isArray(item.tags)){
					item.tags.forEach(tag => tagsSet.add(tag));
				}
			});
			state.availableTags = tagsSet;
			
		}catch(e){
			console.error('获取搜索结果时出错:', e);
			state.loading = false;
			el.stats.textContent = "搜索出错";
			el.results.innerHTML = '<div class="error-message">搜索出错，请稍后重试</div>';
			return;
		}
		
		state.loading = false;
		render();
	}

	/** 显示加载占位符 **/
	function showLoadingPlaceholders(){
		const placeholders = Array(state.perPage).fill(0).map(() => `
			<article class="card loading-card">
				<div class="card-media">
					<div class="media-img skeleton"></div>
					<div class="excerpt skeleton-text">
						<div class="skeleton-line"></div>
						<div class="skeleton-line"></div>
						<div class="skeleton-line short"></div>
					</div>
				</div>
				<div class="card-body">
					<div class="skeleton-title"></div>
					<div class="card-meta">
						<span class="skeleton-badge"></span>
						<span class="skeleton-badge"></span>
						<span class="skeleton-badge"></span>
					</div>
				</div>
			</article>
		`).join('');
		
		el.results.innerHTML = placeholders;
		el.stats.textContent = "加载中...";
	}

	/** 渲染 **/
	function render(){
		if(!state.authed){
			el.stats.textContent = "您需要先登录才能浏览搜索结果";
			el.results.innerHTML = `<div class="auth-required">
				<div class="auth-card">
					<h3>需要登录</h3>
					<p>请先使用 Discord 登录以加载搜索索引并浏览搜索结果。</p>
					<button id="loginBtn" class="btn primary">登录 Discord</button>
				</div>
			</div>`;
			el.pagination.innerHTML = "";
			const btn = document.getElementById('loginBtn');
			if(btn){ btn.addEventListener('click', ()=> login()); }
			return;
		}
		
		const total = state.total;
		const pages = Math.max(1, Math.ceil(total / state.perPage));
		if(state.page > pages && pages > 0) state.page = pages;
		
		el.stats.textContent = `共 ${total} 条结果 · 第 ${state.page}/${pages} 页`;
		el.results.innerHTML = state.filtered.map(renderCard).join("");
		renderPagination(state.page, pages);
		refreshTagMenus();
		
		window.scrollTo({top: 0, behavior: 'smooth'});
	}

	function renderCard(item){
		const author = item.author || {};
		const imgHtml = item.thumbnail_url ? `<div class="media-img"><img src="${escapeAttr(item.thumbnail_url)}" alt="${escapeAttr(item.title)} 缩略图" loading="lazy" class="card-img" data-src="${escapeAttr(item.thumbnail_url)}"></div>` : `<div class="media-img"></div>`;
		const excerptText = limitText(item.first_message_excerpt||"", item.thumbnail_url ? 500 : 800);
		const excerptHtml = `<div class="excerpt markdown-content">${renderMarkdown(excerptText)}</div>`;
		const channelName = state.availableChannels.get(String(item.channel_id)) || `频道 ${item.channel_id}`;
		const created = fmtDate(item.created_at);
		const active = fmtDate(item.last_active_at);
		const authorName = author.display_name || author.global_name || author.username || "未知作者";
		const guildId = window.GUILD_ID || "1134557553011998840";
		
		return `
		<article class="card" tabindex="0">
			<div class="card-media">${imgHtml}${excerptHtml}</div>
			<div class="card-body">
				<h2 class="card-title" title="${escapeAttr(item.title)}">${escapeHtml(item.title)}</h2>
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

	function renderPagination(page, total){
		const btn = (p, label, disabled=false, current=false)=>`<button class="page-btn" ${disabled?"disabled":""} ${current?"aria-current=\"page\"":""} data-page="${p}">${label}</button>`;
		const items = [];
		items.push(btn(Math.max(1,page-1), "上一页", page<=1));
		const windowSize = 5; 
		const start = Math.max(1, page - Math.floor(windowSize/2)); 
		const end = Math.min(total, start + windowSize - 1);
		for(let i=start;i<=end;i++) items.push(btn(i, i, false, i===page));
		items.push(btn(Math.min(total,page+1), "下一页", page>=total));
		el.pagination.innerHTML = items.join("");
	}

	/** 自定义下拉 **/
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
			setMultiLabel(el.chWrap, new Set([...state.channels].map(id=> (state.availableChannels.get(id)||`频道 ${id}`))));
		});
		setupMulti(el.inWrap, ()=>{ 
			state.includeTags = collectChecked(el.inWrap); 
			setMultiLabel(el.inWrap, state.includeTags); 
		});
		setupMulti(el.exWrap, ()=>{ 
			state.excludeTags = collectChecked(el.exWrap); 
			setMultiLabel(el.exWrap, state.excludeTags); 
		});

		el.searchBtn.addEventListener('click', ()=>{ 
			state.page=1; 
			state.query=el.keyword.value; 
			syncAndSearch(); 
		});
		el.keyword.addEventListener('input', debounce(()=>{ 
			state.page=1; 
			state.query=el.keyword.value; 
			syncAndSearch(true); 
		}, 250));
		el.applyBtn.addEventListener('click', ()=>{ 
			state.page=1; 
			state.timeFrom = el.timeFrom.value ? new Date(el.timeFrom.value) : null;
			state.timeTo = el.timeTo.value ? new Date(el.timeTo.value) : null;
			syncAndSearch(); 
		});
		el.resetBtn.addEventListener('click', ()=>{
			state.page=1; 
			state.channels.clear(); 
			state.includeTags.clear(); 
			state.excludeTags.clear(); 
			state.tagLogic="AND"; 
			state.timeFrom=null; 
			state.timeTo=null; 
			state.query=""; 
			state.sort="relevance"; 
			state.perPage=24;
			
			el.keyword.value=""; 
			el.sort.value="relevance"; 
			el.perPage.value="24"; 
			el.tagLogic.value="AND"; 
			el.timeFrom.value=""; 
			el.timeTo.value="";
			el.chWrap.querySelectorAll('input').forEach(i=> i.checked=false);
			el.inWrap.querySelectorAll('input').forEach(i=> i.checked=false);
			el.exWrap.querySelectorAll('input').forEach(i=> i.checked=false);
			setMultiLabel(el.chWrap, new Set()); 
			setMultiLabel(el.inWrap, new Set()); 
			setMultiLabel(el.exWrap, new Set());
			syncAndSearch();
		});
		el.sort.addEventListener('change', ()=>{ 
			state.sort=el.sort.value; 
			state.page=1; 
			syncAndSearch(); 
		});
		el.perPage.addEventListener('change', ()=>{ 
			state.perPage=+el.perPage.value||24; 
			state.page=1; 
			syncAndSearch(); 
		});
		el.tagLogic.addEventListener('change', ()=>{ 
			state.tagLogic=el.tagLogic.value; 
			state.page=1; 
			syncAndSearch(); 
		});
		el.pagination.addEventListener('click', (e)=>{ 
			const b = e.target.closest('button[data-page]'); 
			if(!b) return; 
			const p = +b.getAttribute('data-page'); 
			if(!isNaN(p)) { 
				state.page = p; 
				syncAndSearch(); 
			} 
		});
		
		// 点击作者跳转搜索
		el.results.addEventListener('click', (e)=>{
			const authorBadge = e.target.closest('.badge-author');
			if(authorBadge){
				const author = authorBadge.getAttribute('data-author');
				if(author){
					state.query = `author:${author}`;
					el.keyword.value = state.query;
					state.page = 1;
					syncAndSearch();
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
					el.inWrap.querySelectorAll('input').forEach(i=> {
						if(i.value === tagText) i.checked = true;
					});
					setMultiLabel(el.inWrap, state.includeTags);
					state.page = 1;
					syncAndSearch();
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
		
		window.addEventListener('popstate', ()=>{ 
			readFromURL(); 
			hydrateControls(); 
			fetchSearchResults(); 
		});
	}
	
	/** Discord 链接跳转 **/
	function openDiscordLink(guild, channel, thread){
		const appUrl = `discord://-/channels/${guild}/${thread}`;
		const webUrl = `https://discord.com/channels/${guild}/${thread}`;
		
		const iframe = document.createElement('iframe');
		iframe.style.display = 'none';
		iframe.src = appUrl;
		document.body.appendChild(iframe);
		
		let opened = false;
		const timeout = setTimeout(()=>{
			if(!opened){
				window.open(webUrl, '_blank', 'noopener,noreferrer');
			}
			document.body.removeChild(iframe);
		}, 1500);
		
		const onBlur = ()=>{
			opened = true;
			clearTimeout(timeout);
			setTimeout(()=> document.body.removeChild(iframe), 100);
			window.removeEventListener('blur', onBlur);
		};
		window.addEventListener('blur', onBlur);
		
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
		document.addEventListener('keydown', function onEsc(e){
			if(e.key==='Escape'){
				close();
				document.removeEventListener('keydown', onEsc);
			}
		});
	}

	function hydrateControls(){
		el.keyword.value = state.query;
		el.sort.value = state.sort;
		el.perPage.value = String(state.perPage);
		el.tagLogic.value = state.tagLogic;
		
		el.chWrap.querySelectorAll('input').forEach(i=> i.checked = state.channels.has(i.value));
		el.inWrap.querySelectorAll('input').forEach(i=> i.checked = state.includeTags.has(i.value));
		el.exWrap.querySelectorAll('input').forEach(i=> i.checked = state.excludeTags.has(i.value));
		setMultiLabel(el.chWrap, new Set([...state.channels].map(id=> (state.availableChannels.get(id)||`频道 ${id}`))));
		setMultiLabel(el.inWrap, state.includeTags);
		setMultiLabel(el.exWrap, state.excludeTags);
		el.timeFrom.value = state.timeFrom ? toISODate(state.timeFrom) : "";
		el.timeTo.value = state.timeTo ? toISODate(state.timeTo) : "";
	}
	
	function toISODate(d){
		const x = new Date(d);
		x.setHours(0,0,0,0);
		return x.toISOString().slice(0,10);
	}

	function syncAndSearch(replace=false){
		writeToURL(replace);
		fetchSearchResults();
	}

		  /** 登录 **/
		  async function login(){
		      window.location.href = window.AUTH_URL + "/login";
		  }

		  /** 检查认证 **/
		  async function checkAuth(){
		      try{
		          const res = await fetch(window.AUTH_URL + '/checkauth', {
		              credentials:'include',
		              headers: authHeaders()
		          });
		          if(res && res.ok){
		              const data = await res.json();
		              state.authed = data.loggedIn !== false;
		          }else{
		              state.authed = false;
		          }
		      }catch(e){
		          console.error('检查认证失败:', e);
		          state.authed = false;
		      }
		  }

	/** 启动 **/
	(async function init(){
		readFromURL();
		await checkAuth();
		await initChannels();
		await initTags();
		hydrateControls();
		
		if(state.authed){
			await fetchSearchResults();
		}else{
			render();
		}
		
		bindEvents();
	})();
})();
