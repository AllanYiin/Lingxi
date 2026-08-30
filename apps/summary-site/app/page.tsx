"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { loadLingxiPos, type LingxiPosSegmenter } from "./lingxi/loader";
import { summarize, type Decision } from "./summary-engine";

const decisionLabels: Record<Decision, string> = {
  preserve_exact: "結構原文保留",
  select_exact: "短段原文保留",
  summarize_within: "段內濃縮",
  compact_pos: "詞性精簡",
  context_only: "脈絡標題",
  omit: "省略",
};

const sample = `LingXi Summary 完全在瀏覽器本機執行，不會呼叫任何 LLM，也不會把原文傳送到外部服務。
版權頁記載本文件建立於2026年。研究核心發現是：抽取式摘要可以維持原文忠實性，並避免生成不存在的資訊。
系統會優先辨識結論、限制、關鍵數值與縮略語。Fear Of Missing Out（FOMO）在投資市場，是指看到別人獲利、自己沒跟上而感到焦慮與恐慌。
團隊決定先保留完整定義，再降低重複內容，最後依原文順序輸出結果。`;

export default function Home() {
  const [text, setText] = useState(sample);
  const [limit, setLimit] = useState(3);
  const [submitted, setSubmitted] = useState(sample);
  const [activeView, setActiveView] = useState<"tool" | "about">("tool");
  const [report, setReport] = useState(() => summarize(sample, 3));
  const [modelStatus, setModelStatus] = useState<"loading" | "ready" | "error">("loading");
  const segmenterRef = useRef<LingxiPosSegmenter | null>(null);

  useEffect(() => {
    let active = true;
    loadLingxiPos().then((segmenter) => {
      if (!active) return;
      segmenterRef.current = segmenter;
      setModelStatus("ready");
      setReport(summarize(sample, 3, segmenter.tokenize(sample)));
    }).catch(() => {
      if (active) setModelStatus("error");
    });
    return () => { active = false; };
  }, []);

  const handleGenerate = () => {
    const segmenter = segmenterRef.current;
    setSubmitted(text);
    setReport(summarize(text, limit, segmenter ? segmenter.tokenize(text) : []));
  };

  const handleViewTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const nextView = event.key === 'Home'
      ? "tool"
      : event.key === 'End'
        ? "about"
        : activeView === "tool" ? "about" : "tool";
    setActiveView(nextView);
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`#view-tab-${nextView}`)?.focus());
  };

  return (
    <main>
      <a className="skip-link" href="#summary-workspace">跳至摘要工具</a>
      <header className="site-header" id="top">
        <a className="brand" href="#top" aria-label="LingXi Summary 首頁"><span className="brand-mark">靈</span><span>LingXi Summary</span></a>
        <div className="view-tabs" role="tablist" aria-label="頁面檢視">
          <button id="view-tab-tool" type="button" role="tab" aria-selected={activeView === "tool"} aria-controls="summary-workspace" tabIndex={activeView === "tool" ? 0 : -1} onClick={() => setActiveView("tool")} onKeyDown={handleViewTabKeyDown}>摘要工具</button>
          <button id="view-tab-about" type="button" role="tab" aria-selected={activeView === "about"} aria-controls="about-panel" tabIndex={activeView === "about" ? 0 : -1} onClick={() => setActiveView("about")} onKeyDown={handleViewTabKeyDown}>工具說明</button>
        </div>
        <div className="privacy-badge"><span aria-hidden="true" />0 LLM · 本機計算</div>
      </header>

      <section className="hero" id="about-panel" role="tabpanel" aria-labelledby="view-tab-about" hidden={activeView !== "about"}>
        <div className="hero-copy">
          <p className="eyebrow">繁體中文抽取式摘要</p>
          <h2>留下原文重點，不創造新的句子。</h2>
          <p className="lede">文字只在你的瀏覽器中處理，適合會議紀錄、研究筆記與長篇文章快速減量。</p>
        </div>
        <div className="principles" aria-label="摘要原則">
          <span>原文可追溯</span><span>零資料外送</span><span>結果可稽核</span>
        </div>
      </section>

      <section className="workspace" id="summary-workspace" role="tabpanel" aria-labelledby="view-tab-tool" hidden={activeView !== "tool"}>
        <div className="panel input-panel">
          <div className="panel-heading"><div><span className="step">01</span><h1>貼入原文</h1></div><span className="counter">{[...text].length.toLocaleString("zh-TW")} 字</span></div>
          <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="在這裡貼入繁體中文內容…" aria-label="要摘要的原文" />
          <div className="controls">
            <label>摘要段落上限<input type="number" min="1" max="12" value={limit} onChange={(event) => setLimit(Math.max(1, Math.min(12, Number(event.target.value) || 1)))} /></label>
            <div className="run-control">
              <span className={`model-status ${modelStatus}`} role="status">{modelStatus === "loading" ? "載入 LingXi 詞性模型…" : modelStatus === "ready" ? "LingXi POS 已就緒" : "詞性模型載入失敗，改用規則備援"}</span>
              <button type="button" onClick={handleGenerate} disabled={!text.trim() || modelStatus === "loading"}>產生摘要 <span aria-hidden="true">→</span></button>
            </div>
          </div>
        </div>

        <div className="panel output-panel" aria-live="polite">
          <div className="panel-heading"><div><span className="step">02</span><h2>摘要結果</h2></div><span className="mode">選段 · 子句 · POS 精簡</span></div>
          <div className="summary-paper">
            {report.blocks.some((item) => item.decision !== "omit") ? report.blocks.filter((item) => item.decision !== "omit").map((item) => (
              <article key={item.index} className="summary-item"><p>{item.outputText}</p><div className="reason-row"><span>{item.kind}</span><span>{decisionLabels[item.decision]}</span>{item.removedTokens.length ? <span>詞性精簡 {item.removedTokens.length} 詞</span> : null}</div></article>
            )) : <p className="empty">{submitted.trim() ? "沒有段落達到門檻，也沒有必須完整保留的結構區塊。" : "貼入內容後產生摘要。"}</p>}
          </div>
          <dl className="metrics"><div><dt>原文字數</dt><dd>{report.inputChars.toLocaleString("zh-TW")}</dd></div><div><dt>摘要字數</dt><dd>{report.outputChars.toLocaleString("zh-TW")}</dd></div><div><dt>減量比例</dt><dd>{report.reductionPercent.toFixed(1)}%</dd></div></dl>
        </div>
      </section>

      <footer><p>LingXi POS deterministic edition</p><p>內容不會離開此頁面，也不會保存。</p></footer>
    </main>
  );
}
