"""Email sender (implements returnright.contracts.EmailSender).

With SMTP_HOST set (Compose: Mailpit) it sends real mail; otherwise it only logs.
Either way the last message per address is kept in memory so demo/test helpers
can read it (never exposed outside DEMO_MODE).
"""
from __future__ import annotations

import logging
import smtplib
import threading
from email.message import EmailMessage

log = logging.getLogger("returnright.mail")


class Mailer:
    def __init__(self, host: str = "", port: int = 1025, sender: str = "returns@returnright.test") -> None:
        self.host, self.port, self.sender = host, port, sender
        self.last: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    def send(self, to: str, subject: str, body: str) -> None:
        with self._lock:
            self.last[to] = (subject, body)
        if not self.host:
            log.info("mail (no SMTP configured) to=%s subject=%s", to, subject)
            return
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=5) as smtp:
                smtp.send_message(msg)
        except OSError:
            log.exception("could not send mail to %s", to)
