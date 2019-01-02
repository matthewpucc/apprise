# -*- coding: utf-8 -*-
#
# Matrix WebHook Notify Wrapper
#
# Copyright (C) 2018 Chris Caron <lead2gold@gmail.com>, Wim de With <wf@dewith.io>
#
# This file is part of apprise.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

# To use this plugin, you need to first create a webhook using the
# matrix webhooks bridge at
# https://github.com/turt2live/matrix-appservice-webhooks. You'll need
# to follow the instructions to create a new webhook. You will receive a
# URL and you can then use that URL to configure this service.
# See the wiki for more details.

import re
import requests
from json import dumps
from time import time

from .NotifyBase import NotifyBase
from .NotifyBase import HTTP_ERROR_MAP
from ..common import NotifyImageSize
from ..utils import compat_is_basestring

# Token required as part of the API request
VALIDATE_TOKEN = re.compile(r'[A-Za-z0-9]{64}')

# Default User
MATRIX_DEFAULT_USER = 'apprise'

# Extend HTTP Error Messages
MATRIX_HTTP_ERROR_MAP = HTTP_ERROR_MAP.copy()
MATRIX_HTTP_ERROR_MAP.update({
    403: 'Unauthorized - Invalid Token.',
})

class MatrixNotificationMode(object):
    SLACK = 0
    MATRIX = 1

MATRIX_NOTIFICATION_MODES = (
    MatrixNotificationMode.SLACK,
    MatrixNotificationMode.MATRIX,
)

class NotifyMatrix(NotifyBase):
    """
    A wrapper for Matrix Notifications
    """

    # The default descriptive name associated with the Notification
    service_name = 'Matrix'

    # The services URL
    service_url = 'https://matrix.org/'

    # The default protocol
    protocol = 'matrix'

    # The default secure protocol
    secure_protocol = 'matrixs'

    # A URL that takes you to the setup/help of the specific protocol
    setup_url = 'https://github.com/caronc/apprise/wiki/Notify_matrix'

    # The maximum allowable characters allowed in the body per message
    body_maxlen = 1000

    def __init__(self, token, mode=None, **kwargs):
        """
        Initialize Matrix Object
        """
        super(NotifyMatrix, self).__init__(**kwargs)

        if self.secure:
            self.schema = 'https'

        else:
            self.schema = 'http'

        if not isinstance(self.port, int):
            self.notify_url = '%s://%s/api/v1/matrix/hook' % (self.schema, self.host)

        else:
            self.notify_url = '%s://%s:%d/api/v1/matrix/hook' % (self.schema, self.host, self.port)

        if not VALIDATE_TOKEN.match(token.strip()):
            self.logger.warning(
                'The API token specified (%s) is invalid.' % token,
            )
            raise TypeError(
                'The API token specified (%s) is invalid.' % token,
            )

        # The token associated with the webhook
        self.token = token.strip()

        if not self.user:
            self.logger.warning(
                'No user was specified; using %s.' % MATRIX_DEFAULT_USER)
            self.user = MATRIX_DEFAULT_USER

        if not mode:
            self.logger.warning(
                'No mode was specified, using Slack mode')
            self.mode = MatrixNotificationMode.SLACK

        else:
            self.mode = mode

        self._re_formatting_map = {
            # New lines must become the string version
            r'\r\*\n': '\\n',
            # Escape other special characters
            r'&': '&amp;',
            r'<': '&lt;',
            r'>': '&gt;',
        }

        # Iterate over above list and store content accordingly
        self._re_formatting_rules = re.compile(
            r'(' + '|'.join(self._re_formatting_map.keys()) + r')',
            re.IGNORECASE,
        )

    def notify(self, title, body, notify_type, **kwargs):
        """
        Perform Matrix Notification
        """

        headers = {
            'User-Agent': self.app_id,
            'Content-Type': 'application/json',
        }

        # error tracking (used for function return)
        notify_okay = True

        # Perform Formatting
        title = self._re_formatting_rules.sub(  # pragma: no branch
            lambda x: self._re_formatting_map[x.group()], title,
        )
        body = self._re_formatting_rules.sub(  # pragma: no branch
            lambda x: self._re_formatting_map[x.group()], body,
        )
        url = '%s/%s' % (
            self.notify_url,
            self.token,
        )

        if self.mode is MatrixNotificationMode.MATRIX:
            payload = self.__matrix_mode_payload(title, body, notify_type)

        else:
            payload = self.__slack_mode_payload(title, body, notify_type)

        self.logger.debug('Matrix POST URL: %s (cert_verify=%r)' % (
            url, self.verify_certificate,
        ))
        self.logger.debug('Matrix Payload: %s' % str(payload))
        try:
            r = requests.post(
                url,
                data=dumps(payload),
                headers=headers,
                verify=self.verify_certificate,
            )
            if r.status_code != requests.codes.ok:
                # We had a problem
                try:
                    self.logger.warning(
                        'Failed to send Matrix '
                        'notification: %s (error=%s).' % (
                            MATRIX_HTTP_ERROR_MAP[r.status_code],
                            r.status_code))

                except KeyError:
                    self.logger.warning(
                        'Failed to send Matrix '
                        'notification (error=%s).' %
                            r.status_code)

                # Return; we're done
                notify_okay = False

            else:
                self.logger.info('Sent Matrix notification.')

        except requests.RequestException as e:
            self.logger.warning(
                'A Connection error occured sending Matrix notification.'
            )
            self.logger.debug('Socket Exception: %s' % str(e))
            notify_okay = False

        return notify_okay

    def __slack_mode_payload(self, title, body, notify_type):
        # prepare JSON Object
        payload = {
            'username': self.user,
            # Use Markdown language
            'mrkdwn': True,
            'attachments': [{
                'title': title,
                'text': body,
                'color': self.color(notify_type),
                'ts': time(),
                'footer': self.app_id,
            }],
        }

        return payload

    def __matrix_mode_payload(self, title, body, notify_type):
        title = NotifyBase.escape_html(title)
        body = NotifyBase.escape_html(body)

        msg = '<h4>%s</h4>%s<br/>' % (title, body)

        payload = {
            'displayName': self.user,
            'format': 'html',
            'text': msg,
        }

        return payload

    @staticmethod
    def parse_url(url):
        """
        Parses the URL and returns enough arguments that can allow
        us to substantiate this object.

        """
        results = NotifyBase.parse_url(url)

        if not results:
            # We're done early as we couldn't load the results
            return results

        # Apply our settings now
        results['token'] = NotifyBase.unquote(results['query'])

        if 'mode' in results['qsd'] and len(results['qsd']['mode']):
            _map = {
                'slack': MatrixNotificationMode.SLACK,
                'matrix': MatrixNotificationMode.MATRIX,
            }
            try:
                results['mode'] = _map[results['qsd']['mode'].lower()]
            except KeyError:
                pass

        return results
