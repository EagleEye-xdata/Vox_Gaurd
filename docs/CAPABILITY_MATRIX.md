# Capability Matrix

Generated: 2026-09-07 · Re-verified: 2026-09-08 · Environment: Windows 11, Python 3.11.9, Node 24.14.0, PowerShell 5.1 +
Git Bash · Repo on `F:\vox_gaurd`

> Required by `docs/AGENT_PROMPT.md` STEP 0. Re-verify at the start of each session; tooling
> changes. An entry with an empty "Do NOT use for" column is incomplete.

---

## Environment

| Resource | Status | Implication |
|---|---|---|
| GPU | **NVIDIA RTX 4060 Laptop, 8 GB VRAM**, driver 592.82, CUDA 13.1 | Fine-tuning a ~300 M-param speech encoder + head is comfortable. A 7 B audio LLM (D-1) requires 4-bit QLoRA, gradient checkpointing, batch size 1–2 — and will not hit the 800 ms budget (DEV-2). |
| PyTorch | **2.11.0+cu128**, `torch.cuda.is_available() == True` | GPU training ready with no further setup. |
| CPU | Intel i7-13620H | Adequate for preprocessing and dataloading. |
| RAM | 15.6 GB total, **1.9 GB free** at check time | Close applications before a training run; dataloader workers will contend. |
| Disk | C: 22.7 GB free ⚠️ · D: 205 GB · E: **243 GB** · F: 236.7 GB (repo) | **Datasets go on E:.** MLAAD alone is 183 GB. Never stage datasets on C:. |
| Docker | **29.5.3** | Asterisk-in-Docker for D-2 live call ingest. |
| WSL2 | Ubuntu, currently **Stopped** | Backend for Docker; also a fallback shell for POSIX-only tooling. |
| ffmpeg | **8.1.1** (winget Gyan build) | Codec laundering for T-5.5: G.711, Opus, AMR-NB. Also 8 kHz narrowband channel augmentation for D-2. |
| git-lfs | **3.7.1** | Required for Hugging Face dataset pulls. |
| Network egress | Available (web search + HF reachable) | Dataset download viable; mirror early per `12` §0. |
| Go toolchain | ✅ **go1.27.1 windows/amd64** at `C:\Program Files\Go\bin\go.exe` (re-verified 2026-09-08; the 2026-09-07 entry saying it was absent is superseded) | `go -C gateway test ./...` and `go -C telephony build ./...` both run on this host. |
| Go race detector | ❌ **Unavailable on this box** | `-race` needs cgo. The only gcc on PATH is `C:\MinGW` (`mingw32`, 32-bit: *"sorry, unimplemented: 64-bit mode not compiled in"*), and WSL2's gcc has no libc headers (`libc6-dev` absent, `sudo` needs a password). See Gaps. |
| Training stack | ✅ transformers 4.57.6 · datasets 5.0.1 · peft 0.20.0 · accelerate 1.14.0 · **bitsandbytes 0.50.2** | Installed 2026-09-07. |
| bnb 4-bit CUDA backend | ✅ **Verified**: `Linear4bit` fp16 forward pass on the 4060 succeeds | The risky part on Windows. QLoRA is viable here. |
| `Qwen2AudioForConditionalGeneration` | ✅ Available in transformers 4.57.6; NF4 + double-quant config accepted; `peft.LoraConfig` OK | D-1 is buildable on this box. |
| VRAM headroom | **7.44 GB free of 8.59 GB** | Qwen2-Audio-7B in NF4 is ~4.5 GB weights + ~0.2 GB LoRA + ~1.5–2.5 GB activations ≈ **6.5–7 GB**. Feasible but tight: batch size 1 and gradient checkpointing are **mandatory**, and heavy desktop GPU use during a run will OOM it. |
| TensorFlow interference | ⚠️ TF is installed and partially broken (protobuf `MessageFactory` errors on import) | Harmless but noisy. Set `USE_TF=0 TRANSFORMERS_NO_TF=1` for all training/inference commands. |

---

## Skills discovered

| Skill | Use in this project for | Do NOT use for |
|---|---|---|
| `pdf-official`, `docx-official`, `pptx-official`, `xlsx-official` | Final submission write-up, model card export, the demo-day deck, bias/cost result tables | Spec docs and the handoff — those stay markdown in the repo so they diff in git |
| `artifact-design` / Artifact tool | A shareable read-only view of the model card or bias report for teammates/judges | The live dashboard — that is a real React app in `frontend/`, not an artifact |
| `dataviz` | Reliability diagram, confusion matrix, per-subgroup FAR/FRR charts, band timeline | Anything the running app must compute live |
| `webapp-testing`, `playwright-skill` | Verifying the `09` UX requirements (degraded strip, aria-live, band glyphs) render correctly | Backend scoring logic — that is pytest's job |
| `security-review`, `code-review` | Pre-submission pass over the gateway and schema validation | Replacing the `08` test IDs; a review is not a test |
| `skill-creator` | A repeatable "run eval + emit model card" skill, **only if** it runs more than 3× | Anything used once |

## Plugins / MCP servers

| Capability | Status | Use for | Do NOT use for |
|---|---|---|---|
| MCP servers | None connected | — | — |
| ElevenLabs TTS API | ⚠️ Code path built and tested against a fake; **never exercised against the real API** (no `ELEVENLABS_API_KEY` on this box) | Synthesising the scripted attacker for a live demo | Any claim about how the detector scores real ElevenLabs audio — that is unmeasured |
| WebSearch / WebFetch | ✅ Available | Dataset licence verification, model card references, published EER baselines | Anything that must be reproducible offline at demo time |

## Subagents / parallelism

| Capability | Use for | Do NOT use for |
|---|---|---|
| `Explore` agent | Broad sweeps over an unfamiliar corpus layout or a large downloaded dataset tree | The scoring formula — one owner, one head |
| `general-purpose` agent | Independent tracks: dataset acquisition vs frontend work | Anything touching `risk_scoring.py`; concurrent edits to the fusion are how invariants get broken |
| Background Bash | Dataset downloads, training runs, `npm ci` | Anything whose output the next step depends on immediately |

---

## Gaps

| Expected capability | Missing | Fallback |
|---|---|---|
| ≥16 GB VRAM for comfortable 7 B fine-tuning | Only 8 GB | 4-bit QLoRA + gradient checkpointing + short clips; accept slow training. If it will not converge, fall back to XLS-R-300m + AASIST head and say so plainly. |
| Native Asterisk on Windows | Not supported | Asterisk in Docker with the **AudioSocket** channel driver streaming 8 kHz PCM over TCP. |
| Indian-language spoof corpus | Does not exist off the shelf | Generate in-house with Indic Parler-TTS (Apache 2.0) / IndicF5, bound by invariant 14. |
| CI runner | None configured | Tests run locally; record results in `docs/verification.md` with the date and the command. |
| `go test -race` | No 64-bit C toolchain | Concurrency in `gateway/internal/session` is currently guarded by review and by the `httptest` suite, **not** by the race detector. To close this, either `winget install BrechtSanders.WinLibs.POSIX.UCRT` (64-bit mingw-w64 on Windows) or, in WSL2, `sudo apt install build-essential` and then `go test -race ./...`. Until one of those is done, do not claim the gateway is race-free. |

---

## Task → capability routing

| Task (from `12-BUILD_CHECKLIST` §1) | Primary capability | Fallback |
|---|---|---|
| Fix DEF-1 (HIGH reachability) | plain Python + pytest | — |
| Dataset acquisition | `huggingface_hub` + git-lfs, background Bash, staged on E: | Manual browser download |
| Synthetic generation (6 languages) | Indic Parler-TTS on the 4060 | Windows `System.Speech` for a degraded stand-in |
| Channel augmentation | ffmpeg (G.711 / Opus / AMR-NB) | `scipy.signal` resample-only approximation |
| Detector training | PyTorch + peft QLoRA on the 4060 | XLS-R-300m + AASIST head |
| Calibration + ECE | scikit-learn / plain NumPy | — |
| Charts for the model card | `dataviz` skill | matplotlib directly |
| Live call ingest | Docker + Asterisk AudioSocket | `backend/app/ai_caller.py` simulated bridge: a scripted attacker speaking the AudioSocket wire protocol into the same listener, so the live path runs with no PBX |
| Demo attacker voice | ElevenLabs TTS (stock voices only, invariant 14 enforced in code) | Deterministic local DSP signal, labelled `local-synthetic@1.0.0` and never presented as speech |
| Dashboard changes | React in `frontend/` | — |
| Submission deck / write-up | `pptx-official` / `docx-official` | Markdown |
