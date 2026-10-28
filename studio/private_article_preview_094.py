"""Session 09.4-A: deterministic, offline, safe preview of editorially reviewed articles.

Preview is for human inspection only. No CMS write, network call, AI generation,
HTML execution from the supplied draft, or public authorization occurs.
"""
from __future__ import annotations
import hashlib
import html
import re
from .workflows import WorkflowError

class PrivateArticlePreview:
    def __init__(self, release):
        self.release = release

    def render(self, draft_id: str) -> dict:
        package = self.release.read(draft_id)  # Rejects unreviewed/corrected/tampered versions
        draft = self.release.intake.get(draft_id)
        content = draft["body_markdown"]
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != package["body_sha256"]:
            raise WorkflowError("Draft changed since private editorial decision")
        fragments = []
        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                continue
            escaped = html.escape(line, quote=True)
            match = re.match(r"^(#{1,3})\s+(.+)$", escaped)
            if match:
                level = len(match.group(1))
                fragments.append(f"<h{level}>{match.group(2)}</h{level}>")
            else:
                fragments.append(f"<p>{escaped}</p>")
        sources = "".join(f'<li>{html.escape(url, quote=True)}</li>' for url in package['source_urls'])
        body = "\n".join(fragments)
        page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<meta name='robots' content='noindex,nofollow'>"
                "<title>PRIVATE ARTICLE PREVIEW</title>"
                "<style>body{font-family:system-ui,sans-serif;max-width:820px;margin:3rem auto;"
                "padding:0 1rem;line-height:1.65;color:#142337}header{background:#fff4cc;"
                "padding:1rem;border:2px solid #9b7a1c}article{white-space:normal}"
                "footer{margin-top:3rem;border-top:1px solid #bbb}</style></head><body>"
                "<header><strong>PRIVATE REVIEW ONLY — NOT APPROVED FOR PUBLICATION</strong>"
                "<p>Source findings are editorial records, not independent fact checks.</p></header>"
                f"<article>{body}</article><footer><h2>Source references</h2><ul>{sources}</ul>"
                "<p>Draft is a private candidate. Human inspection required.</p></footer></body></html>")
        return {"draft_id":draft_id,"body_sha256":package["body_sha256"],
                "package_sha256":package["package_sha256"],
                "preview_html":page,"preview_sha256":hashlib.sha256(page.encode()).hexdigest(),
                "private_only":True,"public_publishing_enabled":False,
                "delivery_performed":False,"ai_generation_performed":False}
