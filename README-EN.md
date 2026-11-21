# IYC 2026 Complete Archive

This folder contains the original IYC 2026 photos, course materials, participant documents and video files.

## Start the archive portal

From this folder, run:

```bash
python server.py
```

Then open [http://127.0.0.1:8765/](http://127.0.0.1:8765/) in a browser. The portal reads the real folders at runtime, so every file keeps its original name, extension and resolution.

## Sharing with international participants

For a public or password-protected deployment, upload the complete folder to a static file host or object storage service and keep the same folder structure. The supplied `server.py` is the reference implementation for local sharing and includes the full-archive download route.

The full archive is about 7 GB. Clicking **Download everything** creates an uncompressed ZIP on the server first; this avoids recompressing already-compressed photos and videos, but it needs at least another 7 GB of free disk space while the ZIP is being prepared.

## Originality note

There are 1,022 photo files. 927 are device originals. 95 were only available as compressed WeChat copies; they are labelled in `00_注意_这95张不是原图`. The package keeps those files visible so the photo timeline remains complete.
