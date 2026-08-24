import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://lingxi-summary.allanyiin.chatgpt.site"),
  title: "LingXi Summary｜零 LLM 繁中摘要",
  description: "完全在瀏覽器本機執行的繁體中文抽取式摘要工具，不改寫原文、不外送內容。",
  openGraph: {
    title: "LingXi Summary｜零 LLM 繁中摘要",
    description: "留下原文重點，不創造新的句子。",
    type: "website",
    url: "https://lingxi-summary.allanyiin.chatgpt.site",
    images: [
      {
        url: "https://lingxi-summary.allanyiin.chatgpt.site/og.png",
        width: 1536,
        height: 1024,
        alt: "LingXi Summary—留下原文重點，不創造新的句子。",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "LingXi Summary｜零 LLM 繁中摘要",
    description: "留下原文重點，不創造新的句子。",
    images: ["https://lingxi-summary.allanyiin.chatgpt.site/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
