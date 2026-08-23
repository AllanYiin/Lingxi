import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";

const appRoot = fileURLToPath(new URL(".", import.meta.url));
const appBoundary = appRoot.endsWith(sep) ? appRoot : `${appRoot}${sep}`;
const repoRoot = resolve(appRoot, "../..");
const defaultBinary = join(repoRoot, "target", "debug", process.platform === "win32" ? "lingxi.exe" : "lingxi");
const defaultAssets = join(repoRoot, "assets");
const maxBodyBytes = 2 * 1024 * 1024;

const mime = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml"
};

const securityHeaders = {
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY"
};

export function validateAnalyzePayload(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("請提供 JSON object");
  }
  const text = typeof value.text === "string" ? value.text : "";
  if (!text.trim()) throw new Error("請先填入要測試的內容");
  if (Buffer.byteLength(text, "utf8") > maxBodyBytes) throw new Error("內容超過 2 MB 上限");

  const maxClauses = Number(value.maxClauses ?? 12);
  const minExplainability = Number(value.minExplainability ?? 0.35);
  if (!Number.isInteger(maxClauses) || maxClauses < 1 || maxClauses > 100) {
    throw new Error("一般候選軟上限必須是 1 到 100 的整數");
  }
  if (!Number.isFinite(minExplainability) || minExplainability < 0 || minExplainability > 1) {
    throw new Error("可解釋性門檻必須介於 0 到 1");
  }
  return { text, maxClauses, minExplainability };
}

export function buildCliArgs({ maxClauses, minExplainability }, assetsDir) {
  return [
    "--assets",
    assetsDir,
    "--summary-report",
    String(maxClauses),
    "--min-explainability",
    String(minExplainability)
  ];
}

export function runSummaryReport(payload, options = {}) {
  const binary = options.binary || process.env.LINGXI_BIN || defaultBinary;
  const assetsDir = options.assetsDir || process.env.LINGXI_ASSETS || defaultAssets;
  if (!existsSync(binary)) {
    return Promise.reject(new Error(`找不到 LingXi CLI：${binary}。請先執行 cargo build -p lingxi-cli。`));
  }
  if (!existsSync(assetsDir)) {
    return Promise.reject(new Error(`找不到模型資產目錄：${assetsDir}。請設定 LINGXI_ASSETS。`));
  }

  return new Promise((resolveReport, rejectReport) => {
    const started = performance.now();
    const child = spawn(binary, buildCliArgs(payload, assetsDir), {
      cwd: repoRoot,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => child.kill(), 30_000);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      if (stdout.length > 10 * 1024 * 1024) child.kill();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      clearTimeout(timeout);
      rejectReport(error);
    });
    child.on("close", (code) => {
      clearTimeout(timeout);
      if (code !== 0) {
        rejectReport(new Error(stderr.trim() || `LingXi CLI 結束碼 ${code}`));
        return;
      }
      try {
        const report = JSON.parse(stdout);
        report.transportMs = performance.now() - started;
        resolveReport(report);
      } catch (error) {
        rejectReport(new Error(`無法解析 LingXi report：${error.message}`));
      }
    });
    child.stdin.end(payload.text, "utf8");
  });
}

function sendJson(response, status, value) {
  response.writeHead(status, { ...securityHeaders, "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(value));
}

function readJson(request) {
  return new Promise((resolveBody, rejectBody) => {
    let body = "";
    let size = 0;
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      size += Buffer.byteLength(chunk, "utf8");
      if (size > maxBodyBytes) {
        rejectBody(new Error("請求內容超過 2 MB 上限"));
        request.destroy();
        return;
      }
      body += chunk;
    });
    request.on("end", () => {
      try {
        resolveBody(JSON.parse(body));
      } catch {
        rejectBody(new Error("請求不是有效 JSON"));
      }
    });
    request.on("error", rejectBody);
  });
}

export function createSummaryLabServer(options = {}) {
  return createServer(async (request, response) => {
    const url = new URL(request.url, `http://${request.headers.host || "127.0.0.1"}`);
    if (request.method === "POST" && url.pathname === "/api/analyze") {
      try {
        const payload = validateAnalyzePayload(await readJson(request));
        const report = await runSummaryReport(payload, options);
        sendJson(response, 200, report);
      } catch (error) {
        sendJson(response, 400, { error: error.message });
      }
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/health") {
      sendJson(response, 200, {
        app: "lingxi-summary-lab",
        status: "ready",
        llmCalls: 0
      });
      return;
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      sendJson(response, 405, { error: "不支援此 HTTP method" });
      return;
    }
    const requested = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    const resolved = normalize(join(appRoot, requested));
    if (!resolved.startsWith(appBoundary) || !existsSync(resolved) || statSync(resolved).isDirectory()) {
      sendJson(response, 404, { error: "Not found" });
      return;
    }
    response.writeHead(200, {
      ...securityHeaders,
      "Content-Type": mime[extname(resolved)] || "application/octet-stream"
    });
    if (request.method === "HEAD") response.end();
    else createReadStream(resolved).pipe(response);
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 4174);
  createSummaryLabServer().listen(port, "127.0.0.1", () => {
    console.log(`LingXi Summary Lab: http://127.0.0.1:${port}`);
    console.log("Deterministic local mode: 0 LLM calls");
  });
}
