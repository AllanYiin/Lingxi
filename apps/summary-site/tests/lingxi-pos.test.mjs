import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { gunzipSync } from "node:zlib";

import { initSync, Segmenter } from "../app/lingxi/lingxi_wasm.js";

test("loads the distributed LingXi WASM and POS models", async () => {
  const [wasm, dictGzip, bmesGzip, ...posParts] = await Promise.all([
    readFile(new URL("../public/lingxi/lingxi_wasm_bg.wasm", import.meta.url)),
    readFile(new URL("../public/lingxi/dict.bin.gz", import.meta.url)),
    readFile(new URL("../public/lingxi/hmm_bmes.bin.gz", import.meta.url)),
    ...["00", "01", "02", "03"].map((part) =>
      readFile(new URL(`../public/lingxi/hmm_pos.bin.gz.part${part}`, import.meta.url)),
    ),
  ]);
  const pos = gunzipSync(Buffer.concat(posParts));
  assert.equal(pos.subarray(0, 4).toString("ascii"), "LXA3");
  initSync({ module: wasm });
  const segmenter = new Segmenter(gunzipSync(dictGzip), gunzipSync(bmesGzip), pos);
  const tokens = segmenter.tokenize("謝金河表示，今年企業獲利大幅成長。 ");
  assert.ok(tokens.some((token) => token.word === "謝金河" && token.tag === "Nb"));
  assert.ok(tokens.some((token) => token.word === "今年" && token.tag === "Nd"));
  assert.ok(tokens.some((token) => token.word === "大幅" && token.tag.startsWith("D")));
});
