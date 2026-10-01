"""Deliver bundled assets inside WebView2, without a loopback HTTP transfer."""

import mimetypes
from urllib.parse import unquote, urlsplit


def attach_local_assets(window, server, query=""):
    # before_show runs on the WinForms UI thread, before app navigation begins.
    from System import Array, Byte, Uri
    from System.IO import MemoryStream
    from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext

    view = window.native.webview
    document, policy = server.document()
    root = server.assets.resolve()

    def resource_requested(sender, args):
        parsed = urlsplit(str(args.Request.Uri))
        if f"{parsed.scheme}://{parsed.netloc}" != server.url:
            return
        path = unquote(parsed.path)
        if path.startswith("/api/") or str(args.Request.Method) != "GET":
            return
        if path in ("/", "/index.html"):
            body, content_type = document, "text/html; charset=utf-8"
        else:
            target = (root / path.lstrip("/")).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                body, content_type = b"Not found", "text/plain"
                args.Response = sender.Environment.CreateWebResourceResponse(
                    MemoryStream(Array[Byte](body)), 404, "Not Found", "Content-Type: " + content_type)
                return
            body = target.read_bytes()
            content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        headers = (f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
                   "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                   f"Content-Security-Policy: {policy}\r\nReferrer-Policy: strict-origin-when-cross-origin")
        args.Response = sender.Environment.CreateWebResourceResponse(
            MemoryStream(Array[Byte](body)), 200, "OK", headers)

    def ready(sender, args=None):
        if args is not None and not args.IsSuccess:
            return
        core = view.CoreWebView2
        core.Settings.UserAgent = str(core.Settings.UserAgent) + " BloomTraveler (+https://github.com/CyleAR/bloomTraveler)"
        core.AddWebResourceRequestedFilter(server.url + "/*", CoreWebView2WebResourceContext.All)
        core.WebResourceRequested += resource_requested
        view.Source = Uri(server.url + "/" + query)

    if view.CoreWebView2:
        ready(view)
    else:
        view.CoreWebView2InitializationCompleted += ready
