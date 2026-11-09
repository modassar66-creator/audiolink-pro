# AudioLink Pro ⚡ — High-Performance Audio Converter

A complete, production-ready, asynchronous audio extraction and conversion web application powered by **Flask 3**, **FFmpeg**, **Tailwind CSS**, and **WaveSurfer.js**.

---

## 🚀 Key Improvements in this Edition

1. **Non-blocking Asynchronous Task Engine**:
   - HTTP conversion requests return immediately (`< 150ms`) with a unique `job_id`.
   - Prevents web request timeouts (HTTP 504 / 502) even for large video files.
   - Frontend polls real-time progress (`0% -> 100%`) with descriptive step messages.

2. **100% Free Hosting Optimized**:
   - Concurrency Semaphore limits active simultaneous FFmpeg jobs to prevent Out-Of-Memory (OOM) crashes on 512MB RAM free tiers.
   - Optimized `render.yaml` set to `plan: free`.
   - 100% compatible with Hugging Face Spaces (2 vCPU, 16GB RAM for FREE).

3. **Leak-Free Memory & Storage Management**:
   - Replaced per-request sleeping threads with a single background garbage-collector daemon.
   - Automatically cleans up converted audio files and temporary scratch folders older than 15 minutes.

4. **Dual Conversion Modes**:
   - **Mode 1 (Media URL)**: Streamlined, SSRF-safe chunked streaming download.
   - **Mode 2 (Local File Upload)**: Direct drag-and-drop file upload up to 250MB.

5. **Advanced Audio Features**:
   - **Custom Bitrates**: 128 kbps, 192 kbps, 256 kbps, 320 kbps.
   - **Audio Normalization**: Integrated `loudnorm` filter (EBU R128 standard) to balance volume levels across uneven audio.
   - **Supported Formats**: MP3, WAV, M4A, FLAC, OGG, AAC.

6. **Top-Notch Interactive Frontend**:
   - Modern dark SaaS interface built with Tailwind CSS.
   - Built-in **WaveSurfer.js** interactive waveform player with seeking and playback time tracker.
   - Clipboard auto-paste button and smooth drag-and-drop animation.

---

## 🛠️ Local Setup & Run

### Method 1: Using Docker (Recommended)
```bash
# Build Docker image
docker build -t audio-link-converter-pro .

# Run container on port 8080
docker run -p 8080:8080 audio-link-converter-pro
```
Open [http://localhost:8080](http://localhost:8080) in your browser.

### Method 2: Standard Python Virtualenv
Ensure `ffmpeg` is installed on your system (`sudo apt install ffmpeg` or `brew install ffmpeg`).
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

---

## 🌐 100% Free Cloud Deployment Guide

### Option A: Hugging Face Spaces (Best Performance — 16GB RAM Free!)
1. Go to [huggingface.co/spaces](https://huggingface.co/spaces) and click **Create new Space**.
2. Name your space (e.g., `audiolink-pro`).
3. Select **Docker** as the Space SDK and **Blank** template.
4. Keep the hardware as **Free (2 vCPU · 16 GB RAM)**.
5. Clone the space repo and push all these files:
   ```bash
   git init
   git remote add origin https://huggingface.co/spaces/YOUR_USERNAME/audiolink-pro
   git add .
   git commit -m "Deploy AudioLink Pro"
   git push -u origin main
   ```
6. Hugging Face will automatically build and give you a permanent free HTTPS link!

### Option B: Render Free Tier
1. Push this folder to your GitHub repository.
2. Sign up on [Render.com](https://render.com).
3. Click **New +** -> **Web Service** -> Connect your GitHub repo.
4. Select **Docker** environment and **Free** instance plan.
5. Deploy!
