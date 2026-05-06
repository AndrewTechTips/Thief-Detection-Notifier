import smtplib
from email.message import EmailMessage
import mimetypes
import os

PASSWORD = os.getenv("PASSWORD")
SENDER = os.getenv("EMAIL")


def send_email(image_path):
    email_message = EmailMessage()
    email_message["Subject"] = "New customer showed up!"
    email_message.set_content("Hey, we just saw a new customer!")

    with open(image_path, "rb") as file:
        content = file.read()

    mime_type, _ = mimetypes.guess_type(image_path)
    maintype, subtype = mime_type.split("/")

    email_message.add_attachment(content, maintype=maintype, subtype=subtype)

    gmail = smtplib.SMTP("smtp.gmail.com", 587)
    gmail.ehlo()
    gmail.starttls()
    gmail.login(SENDER, PASSWORD)
    gmail.sendmail(SENDER, SENDER, email_message.as_string())
    gmail.quit()


if __name__ == "__main__":
    send_email(image_path="images/19.png")
