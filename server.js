const express = require("express");
const path = require("path");
const fs = require("fs/promises");
const crypto = require("crypto");
const dotenv = require("dotenv");

dotenv.config();

const app = express();
const rootDir = __dirname;
const isVercelRuntime = Boolean(process.env.VERCEL);
const runtimeBaseDir = isVercelRuntime ? path.join("/tmp", "interviewer") : rootDir;
const tmpDir = path.join(runtimeBaseDir, "tmp");
const publicDir = path.join(rootDir, "public");
const generatedDir = path.join(runtimeBaseDir, "generated");
const VOICERSS_MAX_REQUEST_BYTES = 100 * 1024;

const PORT = Number(process.env.PORT || 3000);
const VOICERSS_API_KEY = process.env.VOICERSS_API_KEY || "759c79c9515242148848e58daaf0d74c";
const VOICERSS_LANG = process.env.VOICERSS_LANG || "en-us";
const VOICERSS_CODEC = process.env.VOICERSS_CODEC || "MP3";

app.use(express.json({ limit: "1mb" }));
app.use(express.static(publicDir));

app.get("/generated/:fileName", async (req, res) => {
  const fileName = path.basename(req.params.fileName || "");
  const fullPath = path.join(generatedDir, fileName);

  try {
    await fs.access(fullPath);
    res.sendFile(fullPath);
  } catch {
    res.status(404).json({ error: "Generated file not found." });
  }
});

async function ensureDirs() {
  await fs.mkdir(tmpDir, { recursive: true });
  await fs.mkdir(generatedDir, { recursive: true });
}

function safeText(text) {
  if (typeof text !== "string") {
    return "";
  }
  return text.trim();
}

function estimateVoiceRssRequestBytes(text) {
  const params = new URLSearchParams({
    key: VOICERSS_API_KEY,
    hl: VOICERSS_LANG,
    src: text,
    c: VOICERSS_CODEC,
    f: "44khz_16bit_stereo",
  });

  return Buffer.byteLength(params.toString(), "utf8");
}

async function synthesizeWithVoiceRSS(text, outAudioPath) {
  const params = new URLSearchParams({
    key: VOICERSS_API_KEY,
    hl: VOICERSS_LANG,
    src: text,
    c: VOICERSS_CODEC,
    f: "44khz_16bit_stereo",
  });

  const response = await fetch("https://api.voicerss.org/", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: params.toString(),
  });

  if (!response.ok) {
    throw new Error(`VoiceRSS request failed with ${response.status}`);
  }

  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  const buffer = Buffer.from(await response.arrayBuffer());

  // VoiceRSS returns text/plain on API errors.
  if (contentType.includes("text/plain") || contentType.includes("application/json")) {
    const errorText = buffer.toString("utf8");
    if (errorText.startsWith("ERROR")) {
      throw new Error(`VoiceRSS error: ${errorText}`);
    }
  }

  await fs.writeFile(outAudioPath, buffer);
}

app.post("/api/interview/generate", async (req, res) => {
  const text = safeText(req.body?.text);

  if (!text) {
    res.status(400).json({ error: "Please enter interview text." });
    return;
  }

  const requestBytes = estimateVoiceRssRequestBytes(text);
  if (requestBytes > VOICERSS_MAX_REQUEST_BYTES) {
    res.status(400).json({
      error: "Text is too long for VoiceRSS 100KB request limit. Please shorten your message.",
    });
    return;
  }

  const requestId = crypto.randomUUID();
  const requestDir = path.join(tmpDir, requestId);
  const audioPath = path.join(requestDir, "speech.mp3");

  try {
    await ensureDirs();
    await fs.mkdir(requestDir, { recursive: true });

    await synthesizeWithVoiceRSS(text, audioPath);

    const audioExtension = VOICERSS_CODEC.toLowerCase() === "wav" ? "wav" : "mp3";
    const finalAudioName = `${requestId}.${audioExtension}`;
    const finalAudioPath = path.join(generatedDir, finalAudioName);

    await fs.copyFile(audioPath, finalAudioPath);

    res.json({
      ok: true,
      audioUrl: `/generated/${finalAudioName}`,
    });
  } catch (error) {
    res.status(500).json({
      error: error.message || "Failed to generate interview audio.",
    });
  } finally {
    // Best effort cleanup for transient artifacts.
    fs.rm(requestDir, { recursive: true, force: true }).catch(() => {});
  }
});

app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

if (require.main === module) {
  ensureDirs()
    .then(() => {
      app.listen(PORT, () => {
        console.log(`Interview app running at http://localhost:${PORT}`);
      });
    })
    .catch((err) => {
      console.error("Failed to initialize app directories:", err);
      process.exit(1);
    });
}

module.exports = app;
