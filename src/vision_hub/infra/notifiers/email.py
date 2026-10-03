"""Email alerts over async SMTP. The snapshot is attached straight from memory: unlike the
original script, nothing is written to (or deleted from) disk."""

import html
import socket
from email.message import EmailMessage
from email.utils import make_msgid

import aiosmtplib

from vision_hub.core.config import SmtpConfig
from vision_hub.domain.notifications import Alert, NotificationError

# Retrying cannot fix these: wrong credentials, or the server refusing sender/recipients.
_PERMANENT = (
    aiosmtplib.SMTPAuthenticationError,
    aiosmtplib.SMTPRecipientsRefused,
    aiosmtplib.SMTPSenderRefused,
    aiosmtplib.SMTPNotSupported,
)


class EmailNotifier:
    def __init__(self, config: SmtpConfig) -> None:
        sender = config.effective_sender
        if not config.enabled or sender is None or not config.effective_recipients:
            msg = "EmailNotifier needs SMTP enabled with a sender and at least one recipient"
            raise ValueError(msg)
        self._config = config
        self._sender = sender

    @property
    def name(self) -> str:
        return "email"

    async def send(self, alert: Alert) -> None:
        config = self._config
        password = config.password.get_secret_value() if config.password else None
        try:
            await aiosmtplib.send(
                self.build_message(alert),
                hostname=config.host,
                port=config.port,
                username=config.username,
                password=password,
                start_tls=config.tls == "starttls",
                use_tls=config.tls == "implicit",
                timeout=config.timeout_seconds,
                # aiosmtplib would otherwise call socket.getfqdn(), which blocks on a reverse
                # DNS lookup that can take seconds (5 s on a stock macOS hostname).
                local_hostname=config.local_hostname or socket.gethostname(),
            )
        except _PERMANENT as exc:
            raise NotificationError(f"SMTP rejected the alert: {exc}", retryable=False) from exc
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            raise NotificationError(f"SMTP delivery failed: {exc}", retryable=True) from exc

    def build_message(self, alert: Alert) -> EmailMessage:
        event = alert.event
        started = event.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        duration = (event.ended_at - event.started_at).total_seconds() if event.ended_at else 0.0
        filename = f"motion-{alert.device_id}-{event.started_at:%Y%m%dT%H%M%SZ}.jpg"

        message = EmailMessage()
        message["Subject"] = f"Motion detected: {alert.device_name}"
        message["From"] = self._sender
        message["To"] = ", ".join(self._config.effective_recipients)
        message["X-Vision-Hub-Event"] = event.id

        summary = (
            f"Motion was detected by {alert.device_name} at {started} "
            f"and lasted {duration:.1f} s. The best frame of the event is attached."
        )
        message.set_content(f"{summary}\n\nEvent ID: {event.id}\n")

        image_cid = make_msgid(domain="vision-hub.local")
        message.add_alternative(
            f"<p>{html.escape(summary)}</p>"
            f'<p><img src="cid:{image_cid[1:-1]}" alt="Motion snapshot" style="max-width:100%"></p>'
            f'<p style="color:#666;font-size:12px">Event ID: {html.escape(event.id)}</p>',
            subtype="html",
        )
        html_part = message.get_body(preferencelist=("html",))
        if html_part is None:  # pragma: no cover - added just above
            msg = "HTML body missing"
            raise RuntimeError(msg)
        html_part.add_related(alert.image_jpeg, "image", "jpeg", cid=image_cid)
        message.add_attachment(alert.image_jpeg, "image", "jpeg", filename=filename)
        return message
