import init, { Segmenter } from "./lingxi_wasm.js";

export type PosToken = {
  word: string;
  tag: string;
  start: number;
  end: number;
};

export type LingxiPosSegmenter = {
  tokenize(text: string): PosToken[];
};

let segmenterPromise: Promise<LingxiPosSegmenter> | null = null;

async function fetchBytes(path: string): Promise<Uint8Array> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`LingXi 模型載入失敗：${path} (${response.status})`);
  }
  return new Uint8Array(await response.arrayBuffer());
}

async function gunzipIfNeeded(payload: Uint8Array): Promise<Uint8Array> {
  // Vite／CDN 可能依副檔名自動送出 Content-Encoding: gzip；fetch 會先解壓。
  if (payload[0] !== 0x1f || payload[1] !== 0x8b) return payload;
  if (typeof DecompressionStream === "undefined") {
    throw new Error("目前瀏覽器不支援模型解壓縮");
  }
  const stream = new Blob([payload]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function fetchGzip(path: string): Promise<Uint8Array> {
  return gunzipIfNeeded(await fetchBytes(path));
}

async function fetchGzipParts(paths: string[]): Promise<Uint8Array> {
  const parts = await Promise.all(paths.map(fetchBytes));
  const payload = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    payload.set(part, offset);
    offset += part.length;
  }
  return gunzipIfNeeded(payload);
}

export function loadLingxiPos(): Promise<LingxiPosSegmenter> {
  segmenterPromise ??= (async () => {
    const [dict, bmes, pos] = await Promise.all([
      fetchGzip("/lingxi/dict.bin.gz"),
      fetchGzip("/lingxi/hmm_bmes.bin.gz"),
      fetchGzipParts([
        "/lingxi/hmm_pos.bin.gz.part00",
        "/lingxi/hmm_pos.bin.gz.part01",
        "/lingxi/hmm_pos.bin.gz.part02",
        "/lingxi/hmm_pos.bin.gz.part03",
      ]),
    ]);
    await init({ module_or_path: "/lingxi/lingxi_wasm_bg.wasm" });
    return new Segmenter(dict, bmes, pos);
  })();
  return segmenterPromise;
}
