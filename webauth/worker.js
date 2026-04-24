export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ✅ 登录入口
    if (url.pathname === "/login") {
      const authUrl = `https://discord.com/api/oauth2/authorize?client_id=${env.CLIENT_ID}&redirect_uri=${encodeURIComponent(env.REDIRECT_URI)}&response_type=code&scope=identify%20guilds.members.read`;
      return Response.redirect(authUrl, 302);
    }

    // ✅ Discord 回调
    if (url.pathname === "/callback") {
      const code = url.searchParams.get("code");
      if (!code) {
        const errorUrl = new URL(env.FRONTEND_URL);
        errorUrl.searchParams.set('error', '缺少授权代码');
        return Response.redirect(errorUrl.toString(), 302);
      }

      // 获取 access_token
      const tokenRes = await fetch("https://discord.com/api/oauth2/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: env.CLIENT_ID,
          client_secret: env.CLIENT_SECRET,
          grant_type: "authorization_code",
          code,
          redirect_uri: env.REDIRECT_URI,
        }),
      });
      const tokenData = await tokenRes.json();
      if (!tokenData.access_token) {
        const errorUrl = new URL(env.FRONTEND_URL);
        errorUrl.searchParams.set('error', '获取访问令牌失败');
        return Response.redirect(errorUrl.toString(), 302);
      }

      const headers = { Authorization: `Bearer ${tokenData.access_token}` };

      // 用户信息
      const user = await (await fetch("https://discord.com/api/users/@me", { headers })).json();

      // 验证身份组
      const memberRes = await fetch(`https://discord.com/api/users/@me/guilds/${env.GUILD_ID}/member`, { headers });
      if (memberRes.status !== 200) {
        const errorUrl = new URL(env.FRONTEND_URL);
        errorUrl.searchParams.set('error', '你不在社区内');
        return Response.redirect(errorUrl.toString(), 302);
      }
      const member = await memberRes.json();
      const roles = env.ROLE_ID.split(",");
      let hasRole = false;
      for (const role of roles) {
        if (member.roles.includes(role)) {
          hasRole = true;
          break;
        }
      }
      if (!hasRole) {
        const errorUrl = new URL(env.FRONTEND_URL);
        errorUrl.searchParams.set('error', '缺少指定身份组');
        return Response.redirect(errorUrl.toString(), 302);
      }

      // 签发 JWT
      const token = await signJWT({ id: user.id, username: user.username, roles: member.roles }, env.JWT_SECRET, 7 * 24 * 60 * 60);

      const cookieDomainAttr = env.COOKIE_DOMAIN ? `; Domain=${env.COOKIE_DOMAIN}` : "";
      const headersOut = new Headers({
        "Set-Cookie": `session=${token}; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=${7 * 24 * 60 * 60}${cookieDomainAttr}`,
      });

      // 为兼容 iOS 的第三方 Cookie 限制，同时在重定向 URL 片段中携带 token，前端读取后改用 Authorization 头
      headersOut.append('Location', `${env.FRONTEND_URL}#token=${encodeURIComponent(token)}`);

      return new Response(null, {
        status: 302,
        headers: headersOut,
      });
    }

    // ✅ 处理 CORS 预检
    if (request.method === "OPTIONS") {
      const h = corsHeaders(env);
      return new Response(null, { status: 204, headers: h });
    }

    // ✅ 检查 Session
    if (url.pathname === "/checkauth") {
      const payload = await checkAuth(request, env);
      if (!payload) {
        return new Response(JSON.stringify({ loggedIn: false }), { status: 200, headers: corsHeaders(env) });
      }
      try {
          // 验证 token
          const secret = new TextEncoder().encode(env.JWT_SECRET);
  
          // 再次检查 Discord 身份（Bot Token）
          const roleCheck = await validateGuildRole(env, payload.id);
          if (!roleCheck.ok) {
            return new Response(JSON.stringify({ loggedIn: false }), { status: 200, headers: {
              'Set-Cookie': `token=; Path=/; HttpOnly; Secure; Max-Age=0; SameSite=Strict`
            }});
          }
  
          // 刷新 token 时带上最新的 roles
          payload.roles = roleCheck.roles;
          const newToken = await signJWT(payload, secret, 7 * 24 * 60 * 60);
  
          const headers = new Headers();
          headers.append(
            'Set-Cookie',
            `token=${newToken}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${7 * 24 * 60 * 60}`
          );
  
          const h = corsHeaders(env);
          for (const [k, v] of headers.entries()) h.set(k, v);
          return new Response(JSON.stringify({ ok: true, id: payload.id }), { headers: h, status: 200 });
  
        } catch (e) {
          return new Response(JSON.stringify({ loggedIn: false }), { status: 200, headers: corsHeaders(env) });
        }
      }

    // 🔍 搜索请求代理
    if (url.pathname === "/api/search" && request.method === "POST") {
      const payload = await checkAuth(request, env);
      if (!payload) return new Response(JSON.stringify({ error: "未登录或会话失效" }), {
        status: 401,
        headers: corsHeaders(env)
      });

      try {
        // 读取请求体
        const searchRequest = await request.json();
        
        // 转发到后端 API
        const apiUrl = `${env.API_URL}/v1/search/`;
        const apiResponse = await fetch(apiUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": env.API_KEY
          },
          body: JSON.stringify(searchRequest)
        });

        if (!apiResponse.ok) {
          const errorText = await apiResponse.text();
          return new Response(JSON.stringify({
            error: "搜索请求失败",
            details: errorText
          }), {
            status: apiResponse.status,
            headers: corsHeaders(env)
          });
        }

        const searchResult = await apiResponse.json();
        const h = corsHeaders(env);
        h.set("Content-Type", "application/json");
        return new Response(JSON.stringify(searchResult), { headers: h });

      } catch (e) {
        return new Response(JSON.stringify({
          error: "处理搜索请求时出错",
          details: e.message
        }), {
          status: 500,
          headers: corsHeaders(env)
        });
      }
    }

    return new Response("Worker OK ✅", { status: 200 });
  }
};

async function validateGuildRole(env, userId) {
  const memberRes = await fetch(`https://discord.com/api/guilds/${env.GUILD_ID}/members/${userId}`, { headers: { Authorization: `Bot ${env.BOT_TOKEN}` } });
  if (memberRes.status !== 200) return { ok: false };
  const member = await memberRes.json();
  const roles = env.ROLE_ID.split(",");
  let hasRole = false;
  for (const role of roles) {
    if (member.roles.includes(role)) {
      hasRole = true;
      break;
    }
  }
  return { ok: hasRole, roles: member.roles };
}

// -------------- JWT 工具 ------------------

function corsHeaders(env) {
  const h = new Headers();
  let allowOrigin = "*";
  if (env.FRONTEND_URL && env.FRONTEND_URL.startsWith("http")) {
    try {
      allowOrigin = new URL(env.FRONTEND_URL).origin;
    } catch {}
  }
  h.set("Access-Control-Allow-Origin", allowOrigin);
  h.set("Access-Control-Allow-Credentials", "true");
  h.set("Access-Control-Allow-Headers", "Content-Type, Authorization");
  h.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  return h;
}

async function signJWT(payload, secret, expiresInSec) {
  const header = { alg: "HS256", typ: "JWT" };
  const exp = Math.floor(Date.now() / 1000) + expiresInSec;
  const fullPayload = { ...payload, exp };

  const encoder = new TextEncoder();
  const secretKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"]
  );

  const headerB64 = base64url(JSON.stringify(header));
  const payloadB64 = base64url(JSON.stringify(fullPayload));
  const data = `${headerB64}.${payloadB64}`;
  const signature = await crypto.subtle.sign("HMAC", secretKey, encoder.encode(data));
  const signatureB64 = base64urlBuffer(signature);

  return `${data}.${signatureB64}`;
}

async function verifyJWT(token, secret) {
  try {
    const [headerB64, payloadB64, signatureB64] = token.split(".");
    const data = `${headerB64}.${payloadB64}`;

    const encoder = new TextEncoder();
    const secretKey = await crypto.subtle.importKey(
      "raw",
      encoder.encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign", "verify"]
    );

    const valid = await crypto.subtle.verify(
      "HMAC",
      secretKey,
      base64urlDecode(signatureB64),
      encoder.encode(data)
    );

    if (!valid) return null;

    const payload = JSON.parse(atob(payloadB64.replace(/-/g, '+').replace(/_/g, '/')));
    if (payload.exp < Math.floor(Date.now() / 1000)) return null;
    return payload;
  } catch {
    return null;
  }
}

async function checkAuth(request, env) {
  // 1) 优先从 Authorization Bearer 中读取（适配 iOS 无第三方 Cookie）
  const auth = request.headers.get("Authorization") || "";
  let token = "";
  if (auth.startsWith("Bearer ")) {
    token = auth.slice(7).trim();
  }
  // 2) 回退到 Cookie 会话
  if (!token) {
    const cookie = request.headers.get("Cookie") || "";
    if (cookie && cookie.includes("session=")) {
      token = cookie.split("session=")[1].split(";")[0];
    }
  }
  if (!token) return null;
  return await verifyJWT(token, env.JWT_SECRET);
}

// -------------- 辅助函数 ------------------

function base64url(input) {
  return btoa(input).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64urlBuffer(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let b of bytes) binary += String.fromCharCode(b);
  return base64url(binary);
}

function base64urlDecode(str) {
  const pad = str.length % 4 === 0 ? "" : "=".repeat(4 - (str.length % 4));
  const base64 = str.replace(/-/g, "+").replace(/_/g, "/") + pad;
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
