import base64
import logging
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config

logger = logging.getLogger(__name__)

ALLOWED_RECIPIENTS = {
    "promociones@lashojasresort.com",
    "sbartenfeld@lashojasresort.com",
    "acienfuegos@lashojasresort.com",
    "reservas@lashojasresort.com",
    "lnajera@lashojasresort.com",
    "recursoshumanos@lashojasresort.com"
}


def _get_credentials():
    """Creates and returns Google OAuth2 credentials from config."""
    # Check if essential credentials are set
    if not all([config.GMAIL_CLIENT_ID, config.GMAIL_CLIENT_SECRET, config.GMAIL_REFRESH_TOKEN, config.GMAIL_SENDER_EMAIL]):
        logger.error("Gmail OAuth2 credentials are not fully configured in the .env file.")
        raise ValueError("Missing Gmail OAuth2 configuration.")

    return Credentials.from_authorized_user_info(
        info={
            "client_id": config.GMAIL_CLIENT_ID,
            "client_secret": config.GMAIL_CLIENT_SECRET,
            "refresh_token": config.GMAIL_REFRESH_TOKEN,
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )


async def send_email(to_emails: list, subject: str, body: str) -> dict:
    """
    Sends an email using the Gmail API.

    Args:
        to_emails: A list of recipient email addresses.
        subject: The email subject.
        body: The email body content (HTML or plain text).

    Returns:
        A dictionary with the result of the operation.
    """
    # Security check: Ensure all recipients are in the allowed list
    for email in to_emails:
        if email not in ALLOWED_RECIPIENTS:
            error_message = f"Unauthorized recipient: {email}. Emails can only be sent to approved addresses."
            logger.error(error_message)
            return {"success": False, "error": error_message}

    try:
        creds = _get_credentials()
        service = build('gmail', 'v1', credentials=creds)

        message = MIMEText(body, 'html')
        message['To'] = ", ".join(to_emails)
        message['From'] = config.GMAIL_SENDER_EMAIL
        message['Subject'] = subject

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        create_message = {
            'raw': encoded_message
        }

        send_response = service.users().messages().send(
            userId="me",
            body=create_message
        ).execute()

        logger.info(f"Email sent successfully to {', '.join(to_emails)}. Message ID: {send_response['id']}")
        return {"success": True, "message_id": send_response['id']}

    except HttpError as error:
        logger.exception(f"An error occurred while sending email to {', '.join(to_emails)}: {error}")
        return {"success": False, "error": f"Gmail API Error: {error}"}
    except Exception as e:
        logger.exception(f"An unexpected error occurred while sending email: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}
