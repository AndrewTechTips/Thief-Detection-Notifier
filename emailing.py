import smtplib
from email.message import EmailMessage
import mimetypes
import os

PASSWORD = os.getenv("PASSWORD")
SENDER = os.getenv("EMAIL")


def send_email(image_path: str) -> None:
    """
    Sends an email with the captured intruder image attached,
    and then securely deletes the temporary image from local storage.
    """

    if not PASSWORD or not SENDER:
        print("Email credentials are missing! Check your environment variables.")
        return

    email_message = EmailMessage()
    email_message["Subject"] = "🚨 Security Alert: Intruder Detected!"
    email_message.set_content(
        "Motion was detected by the security system. Please review the attached evidence."
    )

    try:
        with open(image_path, "rb") as file:
            content = file.read()

        mime_type, _ = mimetypes.guess_type(image_path)
        maintype, subtype = (mime_type or "image/png").split("/")

        email_message.add_attachment(content, maintype=maintype, subtype=subtype)

        # Using "with" ensures the SMTP connection is safely closed afterward
        with smtplib.SMTP("smtp.gmail.com", 587) as gmail:
            gmail.ehlo()
            gmail.starttls()
            gmail.login(SENDER, PASSWORD)
            gmail.sendmail(SENDER, SENDER, email_message.as_string())

        print("Alert email sent successfully.")

    except Exception as e:
        print(f"Failed to send email: {e}")

    finally:
        if os.path.exists(image_path):
            os.remove(image_path)


if __name__ == "__main__":
    send_email(image_path="images/19.png")
