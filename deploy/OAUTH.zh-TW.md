# 華泰 Meta Ads：ChatGPT OAuth 連接

這個入口使用 FastMCP 3.4.9 的 OAuthProxy，與原始 MCP 程式獨立。
目前只開放指定帳戶 `981676801551252` 的帳戶、活動與成效查詢。
沒有建立、啟用、刪除廣告或修改預算的工具。

## Render 設定

沿用既有服務；切換前先完成所有環境變數，否則程式會拒絕啟動。

- Dockerfile：`deploy/Dockerfile.oauth`
- Docker build context：儲存庫根目錄
- Health check：`/healthz`
- Auto-Deploy：Off
- 保留 Free 方案

| 環境變數 | 值 |
|---|---|
| `PUBLIC_BASE_URL` | `https://meta-ads-mcp-bao6.onrender.com` |
| `META_APP_ID` | `28622598490723764` |
| `META_AD_ACCOUNT_ID` | `981676801551252` |
| `META_APP_SECRET` | 在 Render 的受保護欄位輸入 Meta App Secret；不要貼到聊天或 Git |
| `OAUTH_SIGNING_KEY` | 獨立產生至少 32 字元的隨機密鑰，在 Render 的受保護欄位保存 |
| `META_LOGIN_CONFIG_ID` | Meta 商家專用 Facebook 登入組態編號；使用用戶權杖並設定 `ads_read` |
| `PORT` | `8080` |

## Meta 設定

1. App `GPT` / `28622598490723764` 已有 Marketing API 使用案例。
2. 商家專用 Facebook 登入 → 設定 → 有效的 OAuth 重新導向 URI：
   `https://meta-ads-mcp-bao6.onrender.com/auth/callback`
3. 建立只含本階段所需 `ads_read` 的登入組態，把組態編號填入 Render。
4. 授權者必須對目標廣告帳戶有權限。程式在每次驗證時會檢查權限及帳戶。

## ChatGPT

- MCP Server URL：`https://meta-ads-mcp-bao6.onrender.com/mcp`
- Authentication：OAuth；支援 DCR / CIMD 與 S256 PKCE。
- 只接受 `https://chatgpt.com/` 下的回呼 URI；取得實際 ChatGPT 回呼網址後驗證。
- 先以 `get_ad_account` 確認帳戶名稱和幣別，再測試 `list_campaigns`、`get_insights`。

Meta 權杖只供伺服器呼叫 Meta Graph API；ChatGPT 接收不同的 MCP 權杖。
OAuth 狀態、授權碼、同意畫面、PKCE、JWT 驗證與加密儲存由 FastMCP 處理。

## 免費主機限制

預設 OAuth 儲存位於加密的本機檔案。Render Free 的磁碟不是永久儲存，
重新部署或主機重建後可能必須重新連線。此版本不額外建立付費資料庫。
Meta 權杖到期或撤銷後也需要重新授權。

## 驗證與目前進度

執行 `python -m pytest tests/test_huatai_oauth.py -q -o addopts=''`。
測試使用模擬 Meta 回應，確認無權限或帳戶不符時拒絕、OAuth 發現文件、
未授權 MCP 拒絕、回呼網域限制、必要 PKCE、唯讀工具、分頁不洩漏權杖及缺少設定時拒絕啟動。

程式尚未切換至 Render，尚未使用真實 Meta 憑證測試完整 OAuth。
Meta App Secret、登入組態及回呼設定完成後，才能驗證實際帳戶連線。

參考：
- https://developers.openai.com/plugins/build/auth
- https://github.com/PrefectHQ/fastmcp/blob/main/docs/servers/auth/oauth-proxy.mdx
