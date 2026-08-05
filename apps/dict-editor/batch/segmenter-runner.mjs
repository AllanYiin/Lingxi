import { spawn } from "node:child_process";
import { open } from "node:fs/promises";

export async function runLingxiSegmenter({
  executable,
  assets,
  userDictionary,
  input,
  output
}) {
  const outputHandle = await open(output, "wx");
  try {
    await new Promise((resolve, reject) => {
      const child = spawn(
        executable,
        [
          "--assets",
          assets,
          "--user-dict",
          userDictionary,
          "--format",
          "jsonl",
          input
        ],
        { windowsHide: true, stdio: ["ignore", outputHandle.fd, "pipe"] }
      );
      let standardError = "";
      child.stderr.setEncoding("utf8");
      child.stderr.on("data", (chunk) => {
        if (standardError.length < 16_384) standardError += chunk;
      });
      child.once("error", reject);
      child.once("close", (code) => {
        if (code === 0) resolve();
        else reject(new Error(`LingXi 分詞器結束碼 ${code}：${standardError.trim()}`));
      });
    });
  } finally {
    await outputHandle.close();
  }
}
