# TextDrop (Flask)

Upload and download `.txt` files. Every file is automatically deleted
20 days after it's uploaded.

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000

## How it works

- Uploaded files are saved in `uploads/`, with metadata (original name,
  size, upload time) kept in `metadata.json`.
- A background thread checks every hour and deletes any file whose age
  has passed 20 days. Expiry is also checked on every request that
  touches the file list, so nothing stale is ever shown even if the
  hourly check hasn't run yet.
- `.txt` extension, UTF-8 text content, and a 2 MB size limit are all
  enforced server-side, not just in the browser.

## Changing settings

Edit the constants at the top of `app.py`:

- `DAYS_TO_LIVE` — how many days a file lives before deletion (default 20)
- `MAX_SIZE_BYTES` — max upload size (default 2 MB)
- `CLEANUP_INTERVAL_SECONDS` — how often the background sweep runs

## Notes for production

This stores files on local disk and metadata in a JSON file, which is
fine for a small personal or internal tool. For a public-facing
deployment you'd want: a real database instead of the JSON file,
a proper WSGI server (gunicorn/uwsgi) instead of the Flask dev server,
and rate limiting on the upload endpoint.
