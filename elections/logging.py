import logging
import re


class RedactSensitivePaths(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = re.sub(r"/receipt/[0-9a-fA-F-]{36}/", "/receipt/<receipt-id>/", message)
        message = re.sub(r"/password-reset/[^/\s]+/[^/\s]+/", "/password-reset/<user>/<token>/", message)
        record.msg = message
        record.args = ()
        return True
