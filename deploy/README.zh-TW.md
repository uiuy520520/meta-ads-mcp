# 華泰 Meta Ads MCP 部署準備

目標帳戶：981676801551252。此目錄僅新增部署設定，不會建立、啟用廣告或調高預算。

## 本機啟動

在程式庫根目錄執行：

```bash
docker compose -f deploy/compose.yaml up --build -d
```

MCP 路徑為 `http://127.0.0.1:8080/mcp`。主機埠只綁定 loopback；容器以非 root 使用者執行。沒有設定 Meta 權杖，也不複製本機 .env 或憑證。

## 尚需完成的授權與部署

1. 建立自己的 Meta Developer App，確認對目標廣告帳戶具存取權，依功能申請 ads_read / ads_management 等必要權限。
2. 先以唯讀帳戶查詢驗證；權杖僅存於密鑰管理系統或輸入受保護的設定表單，不可提交到 Git 或貼入聊天。
3. HTTP 版本要求每次請求帶 Authorization: Bearer <Meta access token>，缺少時應回傳 401；單純設定伺服器 META_ACCESS_TOKEN 不會替 HTTP 請求完成授權。
4. ChatGPT 遠端連接還需要公開 HTTPS 位址，以及與客戶端相容的授權入口。現有程式沒有完整的遠端 OAuth 發行與回呼流程；必須先實作並驗證 OAuth 閘道，不能把這份 compose 的內部端點當成已完成的 ChatGPT 連線。
5. 選定可執行 Python 容器的主機後，先確認免費額度、休眠與費用，再部署。Sites 的 Workers 執行環境無法直接執行這個 Python 容器，不能原樣移植。

## 驗證標準

- 未授權的 /mcp 請求回傳 401；不可用伺服器共用權杖繞過。
- 授權後僅確認目標帳戶與唯讀資料；不可用建立或啟用廣告作為連線測試。
- 遠端 OAuth：驗證 state、PKCE、redirect URI、權杖撤銷與隔離。
- 實際啟用廣告或提高預算前，需 Nifty 確認具體帳戶、活動、金額和期間。

Google Ads 不在此程式庫的自架功能範圍，需要獨立的 MCP / API 串接。

目前狀態：部署來源設定已準備；尚未部署、未配置憑證、未完成 ChatGPT OAuth；未執行廣告變更。
