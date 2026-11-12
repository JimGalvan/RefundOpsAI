# RefundOps demo video

Target **2:50 or less**; the public submission must be **under 3:00**. Use the
[shot list](shotlist.md) to plan real application footage. Tools require the existing
Python environment, Playwright, python-dotenv, ffmpeg and ffprobe on PATH. No install
step is needed. Run commands from the repository root in PowerShell:

```powershell
$py = 'C:/Users/jimmy/PycharmProjects/RefundOpsAI/.venv/Scripts/python.exe'
ffmpeg -version
```

1. Copy `video/lines.example.json` to `video/lines.json` and write narration as a
   JSON array of objects with unique `id` and nonempty `text`. IDs allow letters,
   digits, underscores and hyphens. Rehearse against the shot timings.
2. Set `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in your environment using your
   normal secret-management workflow, or put them in the repo-root `.env` outside
   the recording session. Neither value has a tool default. Environment values
   take precedence. This command contacts ElevenLabs and may incur API usage:

   ```powershell
   & $py video/tools/tts.py video/lines.json
   & $py video/tools/tts.py video/lines.json --model eleven_multilingual_v2 --only intro
   ```

   Output is `video/voice/<id>.mp3`. Listen to each line before recording. Do not
   show the terminal while setting secrets or open `.env` during capture.
3. Launch a dedicated Chrome instance yourself, using a separate disposable profile
   outside the repo. Example (change the app URL to your running instance):

   ```powershell
   & 'C:/Program Files/Google/Chrome/Application/chrome.exe' --remote-debugging-port=9333 --remote-debugging-address=127.0.0.1 --user-data-dir="$env:TEMP/refundops-video-chrome" --force-device-scale-factor=1 --window-size=1936,1120 --no-first-run --app=http://127.0.0.1:8101/
   ```

   Aim for a 1920×1080 content viewport; the build step scales and letterboxes other
   sizes. Chrome screencast captures the page, not browser chrome or native dialogs.
   Use only demo accounts. Keep the debugging endpoint on loopback.
4. Record a new take. `--page-index` selects the zero-based page across Chrome contexts;
   default is the first page. `--cdp-url` overrides the local endpoint.

   ```powershell
   & $py video/tools/recorder.py record video/footage/take-01
   ```

   Operate the live UI in Chrome. In another terminal stop gracefully:

   ```powershell
   New-Item video/footage/take-01/STOP -ItemType File
   ```

   Ctrl+C also stops and finalizes the timing log. Use a new take name for each
   recording. Frames and `frames.jsonl` preserve static-screen timing. Build the MP4:

   ```powershell
   & $py video/tools/recorder.py build video/footage/take-01 video/footage/take-01.mp4
   ```
5. Copy `video/edit.example.json` to `video/edit.json`. Paths in edit lists are
   **relative to the repository root**, independent of the JSON file's location.
   Every segment has `clip`, `start`, `end` (source seconds), `speed` (default 1),
   optional `label` (literal text) and optional `narration` (one audio file starting
   at the segment's beginning). Output length is `(end-start)/speed`, rounded up
   to a 30 fps frame. Every speed other than 1 automatically gets a visible label,
   for example `3x | Investigation`. Narration plays at natural speed; narration
   longer than its segment fails validation. Extend the segment or rewrite the line.
   Source audio is discarded; segments without narration have silence.
6. Review source footage frame by frame for secrets **before export**, then assemble:

   ```powershell
   & $py video/tools/cut.py export video/edit.json
   ```

   Default output: `video/exports/demo.mp4`. `--out video/exports/review.mp4` changes
   the name. Intermediate media stays inside exports and is removed automatically.
   Output is 1920×1080, 30 fps, H.264/yuv420p with stereo 48 kHz AAC. Labels require
   ffmpeg's drawtext filter and system Arial (Windows/macOS) or DejaVu Sans (Linux).
   A temporary font copy avoids Windows fontconfig configuration issues.
7. The exporter prints the measured final duration and warns at 180 seconds.
   Independently check it, then watch the entire export with sound, checking joins
   and every frame for secrets:

   ```powershell
   ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 video/exports/demo.mp4
   ```

8. Upload the reviewed MP4 to YouTube, set visibility to **Public**, and verify the
   link in a signed-out browser. Use that public URL for submission.

Checklist before upload:

- Real footage only; no fabricated app states or synthetic evidence.
- Label every sped-up part; keep evidence readable long enough to inspect.
- No real brands in merchant/carrier demo fixtures and no copyrighted music. The
  required PayPal integration is shown only as needed for the truthful workflow.
- Check every frame for API keys, passwords, tokens and `.env` contents. Passwords
  must never be shown, including saved-login flows. Re-record any exposed secret.
- Verify merchant approval precedes payment and the final PayPal re-read is visible.
- Confirm duration is under 3:00, narration is intelligible and the upload is public.
- Never commit MP4, WebM, MP3, WAV or frames. Tools enforce output roots:
  `video/footage/`, `video/voice/`, `video/exports/` (including temporary media).
