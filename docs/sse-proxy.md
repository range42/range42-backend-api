# SSE behind Kong / Nginx

SSE streams must not be buffered. In the Kong plugin config or the Nginx
location block for `/v1/deployments/*/events`, set:

```nginx
location ~ ^/v1/deployments/[^/]+/events$ {
    proxy_pass http://backend;
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding on;
    proxy_set_header X-Accel-Buffering no;
    proxy_read_timeout 86400s;
}
```

Uvicorn config:

```bash
uvicorn app.main:app \
  --workers 1 \
  --timeout-graceful-shutdown 30
```

Multi-worker deploys break the in-process `/v1/admin/stats` counter.
v1 ships with the single-worker invariant.
