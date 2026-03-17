const express = require("express");
const path = require("path");
const fs = require("fs/promises");
const fsSync = require("fs");
const crypto = require("crypto");
const { spawn } = require("child_process");
const dotenv = require("dotenv");

dotenv.config();

const app = express();
const rootDir = __dirname;
const tmpDir = path.join(rootDir, "tmp");
const publicDir = path.join(rootDir, "public");
const generatedDir = path.join(publicDir, "generated");
const VOICERSS_MAX_REQUEST_BYTES = 100 * 1024;

const PORT = Number(process.env.PORT || 3000);
const VOICERSS_API_KEY = process.env.VOICERSS_API_KEY || "759c79c9515242148848e58daaf0d74c";
const VOICERSS_LANG = process.env.VOICERSS_LANG || "en-us";
const VOICERSS_CODEC = process.env.VOICERSS_CODEC || "MP3";
const SADTALKER_DIR = path.resolve(rootDir, process.env.SADTALKER_DIR || "./SadTalker");
const PYTHON_EXECUTABLE = process.env.PYTHON_EXECUTABLE || "python";
const SADTALKER_EXTRA_ARGS = process.env.SADTALKER_EXTRA_ARGS
  ? process.env.SADTALKER_EXTRA_ARGS.split(" ").filter(Boolean)
  : ["--still", "--preprocess", "full"];
const INTERVIEWER_IMAGE = path.resolve(rootDir, process.env.INTERVIEWER_IMAGE || "./public/interviewer.svg");

app.use(express.json({ limit: "1mb" }));
app.use(express.static(publicDir));

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

  const url = `https://api.voicerss.org/?${params.toString()}`;
  const response = await fetch(url);

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

function runSadTalker({ sourceImage, drivenAudio, requestOutputDir }) {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(SADTALKER_DIR, "inference.py");

    if (!fsSync.existsSync(scriptPath)) {
      reject(
        new Error(
          `SadTalker not found at ${scriptPath}. Set SADTALKER_DIR in .env to your SadTalker clone path.`
        )
      );
      return;
    }

    const args = [
      scriptPath,
      "--driven_audio",
      drivenAudio,
      "--source_image",
      sourceImage,
      "--result_dir",
      requestOutputDir,
      ...SADTALKER_EXTRA_ARGS,
    ];

    const child = spawn(PYTHON_EXECUTABLE, args, {
      cwd: SADTALKER_DIR,
      shell: false,
      windowsHide: true,
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("error", (err) => {
      reject(new Error(`Failed to launch SadTalker: ${err.message}`));
    });

    child.on("close", async (code) => {
      if (code !== 0) {
        reject(
          new Error(
            `SadTalker failed (exit code ${code}).\n${stderr || stdout || "No logs captured."}`
          )
        );
        return;
      }

      try {
        const files = await fs.readdir(requestOutputDir, { withFileTypes: true });
        const mp4Files = [];

        for (const file of files) {
          if (file.isFile() && file.name.toLowerCase().endsWith(".mp4")) {
            const fullPath = path.join(requestOutputDir, file.name);
            const stat = await fs.stat(fullPath);
            mp4Files.push({ fullPath, mtimeMs: stat.mtimeMs });
          }
        }

        if (!mp4Files.length) {
          reject(new Error("SadTalker finished but no MP4 file was generated."));
          return;
        }

        mp4Files.sort((a, b) => b.mtimeMs - a.mtimeMs);
        resolve(mp4Files[0].fullPath);
      } catch (readErr) {
        reject(new Error(`Unable to collect SadTalker output: ${readErr.message}`));
      }
    });
  });
}

app.post("/api/interview/generate", async (req, res) => {
  const text = safeText(req.body?.text);

  if (!text) {
    res.status(400).json({ error: "Please enter interview text." });
    return;
  }

  if (text.length > 600) {
    res.status(400).json({ error: "Text is too long. Keep it under 600 characters." });
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
  const requestOutputDir = path.join(requestDir, "sadtalker_output");
  const audioPath = path.join(requestDir, "speech.mp3");

  try {
    await fs.mkdir(requestDir, { recursive: true });
    await fs.mkdir(requestOutputDir, { recursive: true });

    await synthesizeWithVoiceRSS(text, audioPath);

    const rawVideoPath = await runSadTalker({
      sourceImage: INTERVIEWER_IMAGE,
      drivenAudio: audioPath,
      requestOutputDir,
    });

    const finalVideoName = `${requestId}.mp4`;
    const finalVideoPath = path.join(generatedDir, finalVideoName);

    await fs.copyFile(rawVideoPath, finalVideoPath);

    res.json({
      ok: true,
      videoUrl: `/generated/${finalVideoName}`,
    });
  } catch (error) {
    res.status(500).json({
      error: error.message || "Failed to generate interview video.",
    });
  } finally {
    // Best effort cleanup for transient artifacts.
    fs.rm(requestDir, { recursive: true, force: true }).catch(() => {});
  }
});

app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

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
